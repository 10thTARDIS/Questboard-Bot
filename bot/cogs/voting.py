"""Voting cog — reaction add/remove → Quest Board vote API.

Watches emoji reactions on session_proposed messages and writes availability
votes to Quest Board via PUT /api/bot/sessions/{id}/timeslots/{slot_id}/vote.

The NotificationsCog stores a message_id → {session_id, slot_order} mapping
in Redis (key: qb_msg:{message_id}) after posting each session_proposed embed.
This cog reads that mapping on every reaction event to resolve the emoji back
to a specific time slot without a round-trip to Quest Board.

Reaction semantics:
  - Add reaction   → availability = "yes"
  - Remove reaction → availability = "no"

Only the regional indicator emojis 🇦–🇪 are acted on (matching the seed
reactions added by the notifications cog). All other emojis are ignored.

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
        availability: str,
    ) -> None:
        """Core logic shared by add and remove handlers."""
        # Ignore the bot's own seed reactions.
        if payload.user_id == self.bot.user.id:
            return

        emoji_str = str(payload.emoji)
        slot_index = _EMOJI_TO_INDEX.get(emoji_str)
        if slot_index is None:
            return  # Not one of our voting emojis — ignore.

        notifications = self._notifications_cog()
        if notifications is None:
            log.warning("VotingCog: NotificationsCog not loaded, cannot resolve message mapping.")
            return

        mapping = await notifications.get_message_mapping(payload.message_id)
        if mapping is None:
            return  # Not a session_proposed message — ignore.

        session_id_str: str = mapping["session_id"]
        slot_order: list[str] = mapping["slot_order"]

        if slot_index >= len(slot_order):
            log.warning(
                "Reaction index %d out of range for session %s (slots=%d).",
                slot_index, session_id_str, len(slot_order),
            )
            return

        slot_id_str = slot_order[slot_index]
        discord_user_id = str(payload.user_id)

        # Check that the user has a Quest Board platform link.
        try:
            link = await self.bot.api.get_platform_link("discord", discord_user_id)
        except Exception as exc:
            log.warning(
                "Could not check platform link for user %s: %s", discord_user_id, exc
            )
            return

        if link is None:
            await self._prompt_link(payload.user_id)
            return

        # Submit the vote.
        try:
            await self.bot.api.put_vote(
                session_id=uuid.UUID(session_id_str),
                slot_id=uuid.UUID(slot_id_str),
                discord_user_id=discord_user_id,
                availability=availability,
            )
            log.info(
                "Vote recorded: user=%s session=%s slot=%s availability=%s",
                discord_user_id, session_id_str, slot_id_str, availability,
            )
        except Exception as exc:
            log.warning(
                "Failed to record vote for user %s session %s slot %s: %s",
                discord_user_id, session_id_str, slot_id_str, exc,
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
        """Record a 'yes' vote when a user adds a slot emoji."""
        await self._handle_reaction(payload, "yes")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(
        self, payload: discord.RawReactionActionEvent
    ) -> None:
        """Record a 'no' vote when a user removes a slot emoji."""
        await self._handle_reaction(payload, "no")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VotingCog(bot))
