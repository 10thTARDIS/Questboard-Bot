"""Recording cog — /record start|stop and the full voice pipeline.

Flow:
  1. GM runs /record start <session_id> in a text channel.
  2. Bot joins the GM's current voice channel, starts a WaveSink per user.
  3. An auto-stop is scheduled for MAX_RECORDING_HOURS.
  4. GM runs /record stop (or auto-stop fires).
  5. Bot leaves voice, mixes WAV files to a mono MP3 (utils/audio.py).
  6. Raw WAV files are deleted.
  7. MP3 is transcribed (services/transcription.py).
  8. Transcript is summarised (services/summarisation.py).
  9. Transcript + summary are uploaded to Quest Board via POST /api/bot/sessions/{id}/transcript.
 10. Summary embed is posted to the text channel.
 11. MP3 is deleted.

Only one recording can be active per guild at a time.  The session_id is
optional on /record start — if omitted the cog asks the GM to provide it.

Nothing from the transcript is ever written to logs.  Only metadata:
session_id, word count, duration in seconds, and file sizes.
"""

import asyncio
import logging
import time
import uuid
from pathlib import Path

import discord
import discord.sinks
from discord import app_commands
from discord.ext import commands

from bot.services import summarisation, transcription
from bot.utils.audio import mix_to_mp3

log = logging.getLogger(__name__)


class RecordingCog(commands.Cog, name="Recording"):
    """Voice recording, transcription, and summarisation pipeline."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # guild_id → _RecordingSession  (one active recording per guild)
        self._active: dict[int, "_RecordingSession"] = {}
        # guild IDs currently going through the setup awaits (between guard check and
        # state insertion) — prevents a second /record start slipping through the gap.
        self._starting: set[int] = set()

    # ── /record group ─────────────────────────────────────────────────────────

    record = app_commands.Group(
        name="record",
        description="Record a voice session for transcription.",
    )

    @record.command(name="start", description="Start recording the current voice session.")
    @app_commands.describe(session_id="Quest Board session ID to attach the recording to.")
    async def record_start(
        self, interaction: discord.Interaction, session_id: str
    ) -> None:
        """Join the GM's voice channel and begin recording."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command must be used in a server.", ephemeral=True
            )
            return

        if guild.id in self._active or guild.id in self._starting:
            await interaction.response.send_message(
                "A recording is already in progress for this server. "
                "Run `/record stop` to finish it first.",
                ephemeral=True,
            )
            return

        # Reserve the guild slot before any awaits so a concurrent /record start
        # from the same guild fails the guard above rather than racing through.
        self._starting.add(guild.id)

        # Validate the session_id format early.
        try:
            session_uuid = uuid.UUID(session_id)
        except ValueError:
            await interaction.response.send_message(
                f"`{session_id}` is not a valid session ID. "
                "Copy the UUID from the Quest Board session page.",
                ephemeral=True,
            )
            return

        # The GM must be in a voice channel.
        member = interaction.user
        if not isinstance(member, discord.Member) or member.voice is None or member.voice.channel is None:
            await interaction.response.send_message(
                "You need to be in a voice channel to start a recording.",
                ephemeral=True,
            )
            return

        voice_channel = member.voice.channel

        try:
            # Fetch session metadata for the announcement (best-effort).
            session_title = "Unknown Session"
            campaign_name = "Quest Board"
            game_system: str | None = None
            try:
                timeslots = await self.bot.api.get_session_timeslots(session_uuid)
                campaign_name = timeslots.campaign_name or campaign_name
                game_system = timeslots.game_system
            except Exception as exc:
                log.warning("Could not fetch session metadata for %s: %s", session_id, exc)

            await interaction.response.defer()

            # Join the voice channel.
            try:
                vc = await voice_channel.connect()
            except discord.ClientException as exc:
                await interaction.followup.send(
                    f"Could not join voice channel: {exc}", ephemeral=True
                )
                return
            except Exception as exc:
                log.error("Unexpected error joining voice channel: %s", exc)
                await interaction.followup.send(
                    "An unexpected error occurred while joining the voice channel.",
                    ephemeral=True,
                )
                return

            # Set up the sink and start recording.
            audio_dir = Path(self.bot.settings.audio_temp_dir)
            audio_dir.mkdir(parents=True, exist_ok=True)
            audio_dir.chmod(0o700)

            sink = discord.sinks.WaveSink()
            vc.start_recording(sink, self._on_recording_done, interaction.channel)

            rec = _RecordingSession(
                session_id=session_uuid,
                session_title=session_title,
                campaign_name=campaign_name,
                game_system=game_system,
                voice_client=vc,
                sink=sink,
                text_channel=interaction.channel,
                audio_dir=audio_dir,
                started_at=time.monotonic(),
            )
            self._active[guild.id] = rec

            # Schedule the auto-stop.
            max_seconds = self.bot.settings.max_recording_hours * 3600
            rec.auto_stop_handle = asyncio.get_event_loop().call_later(
                max_seconds,
                lambda: asyncio.create_task(
                    self._auto_stop(guild.id, interaction.channel),
                    name=f"auto-stop-{guild.id}",
                ),
            )

            await interaction.followup.send(
                embed=_recording_started_embed(session_title, campaign_name, voice_channel.name)
            )
            log.info(
                "Recording started: session=%s guild=%s voice_channel=%s",
                session_id, guild.id, voice_channel.name,
            )
        finally:
            self._starting.discard(guild.id)

    @record.command(name="stop", description="Stop recording and process the transcript.")
    async def record_stop(self, interaction: discord.Interaction) -> None:
        """Stop the active recording and kick off the processing pipeline."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "This command must be used in a server.", ephemeral=True
            )
            return

        if guild.id not in self._active:
            await interaction.response.send_message(
                "No recording is currently active in this server.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        await self._stop_recording(guild.id, interaction.channel)

    # ── Internal stop / pipeline ──────────────────────────────────────────────

    async def _auto_stop(self, guild_id: int, channel: discord.TextChannel) -> None:
        """Called by the scheduled auto-stop when MAX_RECORDING_HOURS elapses."""
        if guild_id not in self._active:
            return
        log.info("Auto-stopping recording for guild %s (max hours reached).", guild_id)
        await channel.send(
            "⏱️ Maximum recording time reached — stopping automatically."
        )
        await self._stop_recording(guild_id, channel)

    async def _stop_recording(
        self, guild_id: int, channel: discord.abc.Messageable
    ) -> None:
        """Disconnect from voice and hand off to the processing pipeline."""
        rec = self._active.pop(guild_id, None)
        if rec is None:
            return

        if rec.auto_stop_handle is not None:
            rec.auto_stop_handle.cancel()

        duration_s = int(time.monotonic() - rec.started_at)

        try:
            rec.voice_client.stop_recording()
        except Exception as exc:
            log.warning("Error stopping recording sink: %s", exc)

        try:
            await rec.voice_client.disconnect(force=True)
        except Exception as exc:
            log.warning("Error disconnecting from voice: %s", exc)

        await channel.send(
            "⏹️ Recording stopped. Processing transcript — this may take a few minutes…"
        )

        asyncio.create_task(
            self._process(rec, channel, duration_s),
            name=f"process-{rec.session_id}",
        )

    async def _on_recording_done(
        self,
        sink: discord.sinks.WaveSink,
        channel: discord.abc.Messageable,
        *args,
    ) -> None:
        """Callback fired by discord.py after stop_recording() flushes the sink.

        Not used directly — we pass `channel` as an extra arg but handle
        everything in _process via the _RecordingSession object.
        """

    async def _process(
        self,
        rec: "_RecordingSession",
        channel: discord.abc.Messageable,
        duration_s: int,
    ) -> None:
        """Mix → transcribe → summarise → upload → post summary embed."""
        wav_files: list[Path] = []
        mp3_path: Path | None = None

        try:
            # ── Collect WAV files from the sink ───────────────────────────────
            for user_id, audio in rec.sink.audio_data.items():
                wav_path = rec.audio_dir / f"{rec.session_id}_{user_id}.wav"
                with wav_path.open("wb") as fh:
                    fh.write(audio.file.getbuffer())
                wav_files.append(wav_path)
                log.debug("Wrote WAV for user %s: %s", user_id, wav_path.name)

            if not wav_files:
                await channel.send(
                    "⚠️ No audio was captured. The recording may have been too short "
                    "or the bot had no voice data to process."
                )
                return

            # ── Mix to MP3 ────────────────────────────────────────────────────
            mp3_path = rec.audio_dir / f"{rec.session_id}.mp3"
            await mix_to_mp3(wav_files, mp3_path)

            # Delete raw WAV files immediately after mixing.
            for p in wav_files:
                p.unlink(missing_ok=True)
            wav_files.clear()

            # ── Transcribe ────────────────────────────────────────────────────
            transcript = await transcription.transcribe(mp3_path, self.bot.settings)
            word_count = len(transcript.split())
            log.info(
                "Transcription done: session=%s duration=%ds words=%d",
                rec.session_id, duration_s, word_count,
            )

            if not transcript.strip():
                await channel.send(
                    "⚠️ The transcript came back empty — the audio may have been "
                    "too quiet or contained no speech."
                )
                return

            # ── Summarise ─────────────────────────────────────────────────────
            summary = await summarisation.summarise(
                transcript,
                rec.campaign_name,
                rec.game_system,
                self.bot.settings,
            )

            # ── Upload to Quest Board ─────────────────────────────────────────
            try:
                await self.bot.api.post_transcript(
                    session_id=rec.session_id,
                    transcript=transcript,
                    summary=summary,
                )
                log.info("Transcript uploaded for session %s.", rec.session_id)
            except Exception as exc:
                log.error("Failed to upload transcript for session %s: %s", rec.session_id, exc)
                await channel.send(
                    "⚠️ The transcript was generated but could not be uploaded to "
                    "Quest Board. Check the bot logs and upload manually if needed."
                )
                # Still post the summary embed — the GM has the info.

            # ── Post summary embed ────────────────────────────────────────────
            await channel.send(
                embed=_summary_embed(
                    rec.session_title,
                    rec.campaign_name,
                    summary,
                    duration_s,
                    word_count,
                )
            )

        except Exception as exc:
            log.exception(
                "Unhandled error in recording pipeline for session %s: %s",
                rec.session_id, exc,
            )
            await channel.send(
                f"⚠️ An error occurred while processing the recording: {exc}\n"
                "Check the bot logs for details."
            )

        finally:
            # Always clean up any remaining temp files.
            for p in wav_files:
                p.unlink(missing_ok=True)
            if mp3_path is not None:
                mp3_path.unlink(missing_ok=True)


# ── Data class ─────────────────────────────────────────────────────────────────


class _RecordingSession:
    """Holds all state for one active or recently-completed recording."""

    __slots__ = (
        "session_id", "session_title", "campaign_name", "game_system",
        "voice_client", "sink", "text_channel", "audio_dir",
        "started_at", "auto_stop_handle",
    )

    def __init__(
        self,
        session_id: uuid.UUID,
        session_title: str,
        campaign_name: str,
        game_system: str | None,
        voice_client: discord.VoiceClient,
        sink: discord.sinks.WaveSink,
        text_channel,
        audio_dir: Path,
        started_at: float,
    ) -> None:
        self.session_id = session_id
        self.session_title = session_title
        self.campaign_name = campaign_name
        self.game_system = game_system
        self.voice_client = voice_client
        self.sink = sink
        self.text_channel = text_channel
        self.audio_dir = audio_dir
        self.started_at = started_at
        self.auto_stop_handle: asyncio.TimerHandle | None = None


# ── Embed helpers ─────────────────────────────────────────────────────────────

_COLOR_RECORDING = 0xED4245   # Red — live
_COLOR_SUMMARY = 0x57F287     # Green — done


def _recording_started_embed(
    session_title: str, campaign_name: str, voice_channel_name: str
) -> discord.Embed:
    embed = discord.Embed(
        title=f"🔴 Recording started — {session_title}",
        color=_COLOR_RECORDING,
    )
    embed.add_field(name="Voice channel", value=voice_channel_name, inline=True)
    embed.set_footer(
        text=f"{campaign_name} • Run /record stop when the session ends"
    )
    return embed


def _summary_embed(
    session_title: str,
    campaign_name: str,
    summary: str,
    duration_s: int,
    word_count: int,
) -> discord.Embed:
    hours, remainder = divmod(duration_s, 3600)
    minutes = remainder // 60
    duration_label = (
        f"{hours}h {minutes}m" if hours else f"{minutes}m"
    )

    embed = discord.Embed(
        title=f"📜 Session Summary — {session_title}",
        description=summary[:4000],  # Discord embed description limit
        color=_COLOR_SUMMARY,
    )
    embed.add_field(name="Duration", value=duration_label, inline=True)
    embed.add_field(name="Words transcribed", value=f"{word_count:,}", inline=True)
    embed.set_footer(text=f"{campaign_name} • Full transcript saved to Quest Board")
    return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RecordingCog(bot))
