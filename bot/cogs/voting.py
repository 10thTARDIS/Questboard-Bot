"""Voting cog — reaction add/remove → Quest Board vote and attendance APIs.

Handles two types of reaction messages, distinguished by the "type" field in
the message mapping stored by NotificationsCog:

  "voting"     (session_proposed embeds)
    🇦–🇪 emojis map to time slots.
    Add → availability="yes", Remove → availability="no"
    Calls PUT /api/bot/sessions/{id}/timeslots/{slot_id}/vote

  "attendance" (session_confirmed embeds)
    ✅ → attended=True, ❌ → attended=False
    Calls PUT /api/bot/sessions/{id}/attendance/{discord_user_id}
    All other emojis are ignored.

Old message mappings without a "type" key default to "voting" for
backwards-compatibility with Redis entries written before v0.8.0.

If the reacting user has no Quest Board platform link, they receive a DM
prompting them to run /link.  All other errors are logged and swallowed so
that a transient API failure does not surface as a visible Discord error.
"""

import logging
import uuid

import discord
from discord.ext import commands

log = logging.getLogger(__name__)

# Must match _SLOT_EMOJIS in notifications.py.
_SLOT_EMOJIS = ["🇦", "🇧", "🇨", "🇩", "🇪"]
_EMOJI_TO_INDEX: dict[str, int] = {e: i for i, e in enumerate(_SLOT_EMOJIS)}

# Must match _ATTENDANCE_EMOJIS in notifications.py.
_ATTEND_YES = "✅"
_ATTEND_NO = "❌"


class VotingCog(commands.Cog, name="Voting"):
    """Translates Discord emoji reactions into Quest Board availability votes."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _notifications_cog(self):
        """Return the NotificationsCog, or None if it is not loaded."""
        return self.bot.get_cog("Notifications")

    async def _handle_reaction(
        self,
        payload: discord.RawReactionActionEvent,
        reaction_added: bool,
    ) -> None:
        """Core logic shared by add and remove handlers.

        reaction_added=True when a reaction was added; False when removed.
        """
        # Ignore the bot's own seed reactions.
        if payload.user_id == self.bot.user.id:
            return

        notifications = self._notifications_cog()
        if notifications is None:
            log.warning("VotingCog: NotificationsCog not loaded, cannot resolve message mapping.")
            return

        mapping = await notifications.get_message_mapping(payload.message_id)
        if mapping is None:
            return  # Not one of our tracked messages — ignore.

        try:
            session_id_str: str = mapping["session_id"]
            map_type: str = mapping.get("type", "voting")
        except (KeyError, TypeError) as exc:
            log.warning(
                "Malformed message mapping for message_id=%s: %s", payload.message_id, exc
            )
            return

        try:
            session_uuid = uuid.UUID(session_id_str)
        except ValueError as exc:
            log.warning(
                "Invalid session UUID in mapping for message_id=%s: %s", payload.message_id, exc
            )
            return

        discord_user_id = str(payload.user_id)

        # Check platform link — required for both voting and attendance.
        try:
            link = await self.bot.api.get_platform_link("discord", discord_user_id)
        except Exception as exc:
            log.warning("Could not check platform link for user %s: %s", discord_user_id, exc)
            return

        if link is None:
            await self._prompt_link(payload.user_id)
            return

        if map_type == "attendance":
            await self._handle_attendance_reaction(
                payload, session_uuid, discord_user_id, reaction_added
            )
        else:
            availability = "yes" if reaction_added else "no"
            await self._handle_vote_reaction(
                payload, session_uuid, discord_user_id, availability, mapping
            )

    async def _handle_attendance_reaction(
        self,
        payload: discord.RawReactionActionEvent,
        session_uuid: uuid.UUID,
        discord_user_id: str,
        reaction_added: bool,
    ) -> None:
        """Record an attendance RSVP when a user reacts to a session_confirmed embed."""
        emoji_str = str(payload.emoji)
        if emoji_str == _ATTEND_YES:
            attended = True
        elif emoji_str == _ATTEND_NO:
            attended = False
        else:
            return  # Ignore emojis other than ✅ / ❌

        try:
            await self.bot.api.put_attendance(
                session_id=session_uuid,
                discord_user_id=discord_user_id,
                attended=attended,
            )
            log.info(
                "Attendance recorded: user=%s session=%s attended=%s",
                discord_user_id, session_uuid, attended,
            )
        except Exception as exc:
            log.warning(
                "Failed to record attendance for user %s session %s: %s",
                discord_user_id, session_uuid, exc,
            )

    async def _handle_vote_reaction(
        self,
        payload: discord.RawReactionActionEvent,
        session_uuid: uuid.UUID,
        discord_user_id: str,
        availability: str,
        mapping: dict,
    ) -> None:
        """Record a time-slot vote when a user reacts to a session_proposed embed."""
        emoji_str = str(payload.emoji)
        slot_index = _EMOJI_TO_INDEX.get(emoji_str)
        if slot_index is None:
            return  # Not one of our voting emojis — ignore.

        try:
            slot_order: list[str] = mapping["slot_order"]
        except (KeyError, TypeError) as exc:
            log.warning("Malformed slot_order in mapping for message_id=%s: %s",
                        payload.message_id, exc)
            return

        if slot_index >= len(slot_order):
            log.warning(
                "Reaction index %d out of range for session %s (slots=%d).",
                slot_index, session_uuid, len(slot_order),
            )
            return

        slot_id_str = slot_order[slot_index]
        try:
            slot_uuid = uuid.UUID(slot_id_str)
        except ValueError as exc:
            log.warning(
                "Invalid slot UUID in mapping for message_id=%s: %s", payload.message_id, exc
            )
            return

        try:
            await self.bot.api.put_vote(
                session_id=session_uuid,
                slot_id=slot_uuid,
                discord_user_id=discord_user_id,
                availability=availability,
            )
            log.info(
                "Vote recorded: user=%s session=%s slot=%s availability=%s",
                discord_user_id, session_uuid, slot_id_str, availability,
            )
        except Exception as exc:
            log.warning(
                "Failed to record vote for user %s session %s slot %s: %s",
                discord_user_id, session_uuid, slot_id_str, exc,
            )

    async def _prompt_link(self, user_id: int) -> None:
        """DM an unlinked user to run /link."""
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            await user.send(
                "Your reaction was noted, but your Discord account isn't linked to "
                "Quest Board yet, so your vote wasn't recorded.\n\n"
                "Run `/link` in the server to connect your accounts — it only takes "
                "a moment!"
            )
        except discord.Forbidden:
            log.debug("Cannot DM user %s (DMs disabled).", user_id)
        except Exception as exc:
            log.warning("Failed to DM unlinked user %s: %s", user_id, exc)

    # ── Event listeners ───────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_raw_reaction_add(
        self, payload: discord.RawReactionActionEvent
    ) -> None:
        """Handle a reaction being added (vote yes / attendance confirmed)."""
        await self._handle_reaction(payload, reaction_added=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(
        self, payload: discord.RawReactionActionEvent
    ) -> None:
        """Handle a reaction being removed (vote no / attendance withdrawn)."""
        await self._handle_reaction(payload, reaction_added=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VotingCog(bot))
