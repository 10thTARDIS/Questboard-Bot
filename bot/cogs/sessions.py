"""Sessions cog — /next, /recap, /note, /ask slash commands.

Provides players and GMs with quick access to session information and campaign
memory from within Discord:

  /next          — upcoming confirmed session with countdown
  /recap <id>    — summary and GM notes for a specific session
  /note <text>   — append a private note to the next (or a specified) session
  /ask <question>— answer a question using campaign session history

/note and /ask require the user to have linked their Discord account to Quest
Board via /link.  /note and /ask also require the Quest Board backend endpoints
added in v0.9.0 and v0.10.0 respectively — until those are deployed the
commands will return a clear "not yet available" message rather than crashing.
"""

import logging
import uuid
from datetime import timezone

import discord
from discord import app_commands
from discord.ext import commands

from bot.services import qa

log = logging.getLogger(__name__)

# Embed colours (shared with notifications.py conventions).
_COLOR_SESSION = 0x57F287   # Green — confirmed / summary
_COLOR_NOTE = 0x5865F2      # Blurple — neutral info
_COLOR_QA = 0x5865F2        # Blurple — Q&A


class SessionsCog(commands.Cog, name="Sessions"):
    """Slash commands for browsing session info and campaign history."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /next ─────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="next",
        description="Show the next scheduled session for this server.",
    )
    async def next_session(self, interaction: discord.Interaction) -> None:
        """Post an embed showing the next confirmed session with a countdown."""
        if interaction.guild is None:
            await interaction.response.send_message(
                "This command must be used in a server.", ephemeral=True
            )
            return

        await interaction.response.defer()

        try:
            session = await self.bot.api.get_next_session(str(interaction.guild.id))
        except Exception as exc:
            log.warning("Failed to fetch next session for guild %s: %s", interaction.guild.id, exc)
            await interaction.followup.send(
                "Could not reach Quest Board right now. Please try again shortly.",
                ephemeral=True,
            )
            return

        if session is None:
            await interaction.followup.send(
                "No upcoming sessions are scheduled for this server.",
                ephemeral=True,
            )
            return

        dt = session.confirmed_time
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ts = int(dt.timestamp())

        title = session.title or "Untitled Session"
        footer = session.campaign_name
        if session.game_system:
            footer += f" • {session.game_system}"

        embed = discord.Embed(
            title=f"📅 Next Session — {title}",
            color=_COLOR_SESSION,
        )
        embed.add_field(
            name="When",
            value=f"<t:{ts}:F>\n<t:{ts}:R>",
            inline=False,
        )
        embed.set_footer(text=footer)

        await interaction.followup.send(embed=embed)

    # ── /recap ────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="recap",
        description="Show the summary and GM notes for a session.",
    )
    @app_commands.describe(session_id="Quest Board session ID (UUID from the session page URL).")
    async def recap(self, interaction: discord.Interaction, session_id: str) -> None:
        """Fetch and post a session's stored summary and/or GM notes."""
        try:
            session_uuid = uuid.UUID(session_id)
        except ValueError:
            await interaction.response.send_message(
                f"`{session_id}` is not a valid session ID. "
                "Copy the UUID from the Quest Board session page.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        try:
            session = await self.bot.api.get_session_summary(session_uuid)
        except Exception as exc:
            log.warning("Failed to fetch session summary for %s: %s", session_id, exc)
            await interaction.followup.send(
                "Could not fetch that session from Quest Board. "
                "Check that the session ID is correct and try again.",
                ephemeral=True,
            )
            return

        has_summary = bool(session.summary and session.summary.strip())
        has_notes = bool(session.session_notes and session.session_notes.strip())

        if not has_summary and not has_notes:
            await interaction.followup.send(
                "No transcript or GM notes have been recorded for this session yet.\n"
                "Use `/record start` during a session to generate a summary automatically.",
                ephemeral=True,
            )
            return

        title = session.title or "Untitled Session"
        embed = discord.Embed(title=f"📜 Session Recap — {title}", color=_COLOR_SESSION)

        if session.confirmed_time:
            dt = session.confirmed_time
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ts = int(dt.timestamp())
            embed.add_field(name="Session date", value=f"<t:{ts}:F>", inline=False)

        if has_summary:
            # Discord description limit is 4096 chars.
            embed.description = session.summary[:4000]

        if has_notes:
            embed.add_field(
                name="GM Notes",
                value=session.session_notes[:1000],
                inline=False,
            )

        embed.set_footer(text=session.campaign_name)
        await interaction.followup.send(embed=embed)

    # ── /note ─────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="note",
        description="Add a private note to the next session (or a specific one).",
    )
    @app_commands.describe(
        text="The note to save.",
        session_id="Session ID to attach the note to (optional — defaults to the next session).",
    )
    async def note(
        self,
        interaction: discord.Interaction,
        text: str,
        session_id: str | None = None,
    ) -> None:
        """Append a private note to a session on behalf of the linked Discord user."""
        if interaction.guild is None:
            await interaction.response.send_message(
                "This command must be used in a server.", ephemeral=True
            )
            return

        discord_user_id = str(interaction.user.id)

        # All responses are ephemeral — notes are private.
        await interaction.response.defer(ephemeral=True)

        # Verify the user is linked.
        try:
            link = await self.bot.api.get_platform_link("discord", discord_user_id)
        except Exception as exc:
            log.warning("Failed to check platform link for user %s: %s", discord_user_id, exc)
            await interaction.followup.send(
                "Could not reach Quest Board right now. Please try again shortly.",
                ephemeral=True,
            )
            return

        if link is None:
            await interaction.followup.send(
                "Your Discord account isn't linked to Quest Board yet.\n"
                "Run `/link` to connect your accounts, then try `/note` again.",
                ephemeral=True,
            )
            return

        # Resolve the target session.
        resolved_session_id: uuid.UUID | None = None
        session_title = "the session"

        if session_id is not None:
            try:
                resolved_session_id = uuid.UUID(session_id)
            except ValueError:
                await interaction.followup.send(
                    f"`{session_id}` is not a valid session ID.",
                    ephemeral=True,
                )
                return
        else:
            # Default to the next upcoming session for this guild.
            try:
                next_session = await self.bot.api.get_next_session(
                    str(interaction.guild.id)
                )
            except Exception as exc:
                log.warning(
                    "Failed to fetch next session for /note (guild=%s): %s",
                    interaction.guild.id, exc,
                )
                await interaction.followup.send(
                    "Could not fetch the next session from Quest Board. "
                    "Try again shortly, or specify a session ID explicitly.",
                    ephemeral=True,
                )
                return

            if next_session is None:
                await interaction.followup.send(
                    "No upcoming session found for this server. "
                    "Specify a session ID explicitly if you want to add a note "
                    "to a past session.",
                    ephemeral=True,
                )
                return

            resolved_session_id = next_session.session_id
            session_title = f"*{next_session.title or 'Untitled Session'}*"

        # Save the note.
        try:
            await self.bot.api.post_session_note(
                resolved_session_id, discord_user_id, text
            )
        except Exception as exc:
            log.warning(
                "Failed to save note for user %s session %s: %s",
                discord_user_id, resolved_session_id, exc,
            )
            await interaction.followup.send(
                "Could not save the note to Quest Board. Please try again shortly.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"✅ Note saved to {session_title}.", ephemeral=True
        )

    # ── /ask ──────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="ask",
        description="Ask a question about your campaign's history.",
    )
    @app_commands.describe(question="Your question about the campaign.")
    async def ask(self, interaction: discord.Interaction, question: str) -> None:
        """Answer a question using stored session summaries as context."""
        if interaction.guild is None:
            await interaction.response.send_message(
                "This command must be used in a server.", ephemeral=True
            )
            return

        await interaction.response.defer()

        guild_id = str(interaction.guild.id)

        # Get campaign context (also confirms a campaign is linked to this guild).
        try:
            next_session = await self.bot.api.get_next_session(guild_id)
        except Exception as exc:
            log.warning("Failed to fetch campaign context for /ask (guild=%s): %s", guild_id, exc)
            await interaction.followup.send(
                "Could not reach Quest Board right now. Please try again shortly.",
                ephemeral=True,
            )
            return

        # get_next_session returns None when there is no upcoming session, but
        # we still want the campaign name.  Use a fallback label if needed.
        campaign_name = next_session.campaign_name if next_session else "your campaign"
        game_system = next_session.game_system if next_session else None

        # Fetch session history with summaries.
        try:
            sessions = await self.bot.api.get_session_history(guild_id, limit=10)
        except Exception as exc:
            log.warning("Failed to fetch session history for /ask (guild=%s): %s", guild_id, exc)
            await interaction.followup.send(
                "Could not fetch session history from Quest Board. "
                "Please try again shortly.",
                ephemeral=True,
            )
            return

        if not sessions:
            await interaction.followup.send(
                "No session recordings found for this campaign yet.\n"
                "Record a session with `/record start` to build campaign memory — "
                "once a transcript is uploaded, `/ask` can search it.",
            )
            return

        # Query the LLM.
        try:
            answer = await qa.answer_question(
                question=question,
                sessions=sessions,
                campaign_name=campaign_name,
                game_system=game_system,
                settings=self.bot.settings,
            )
        except Exception as exc:
            log.warning("Q&A LLM call failed for guild %s: %s", guild_id, exc)
            await interaction.followup.send(
                f"The language model returned an error: {exc}\n"
                "Check the bot logs or try again shortly.",
                ephemeral=True,
            )
            return

        # Truncate question for title if very long.
        title_question = question if len(question) <= 100 else question[:97] + "…"

        embed = discord.Embed(
            title=f"❓ {title_question}",
            description=answer[:4000],
            color=_COLOR_QA,
        )
        embed.set_footer(
            text=f"{campaign_name} • Answer drawn from {len(sessions)} session "
                 f"{'summary' if len(sessions) == 1 else 'summaries'}"
        )

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SessionsCog(bot))
