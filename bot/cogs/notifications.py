"""Notification cog — receives POST /notify events from Quest Board.

Implemented in v0.3.0. This stub loads cleanly and logs received events.
"""

import logging

from discord.ext import commands

log = logging.getLogger(__name__)


class NotificationsCog(commands.Cog, name="Notifications"):
    """Handles notification events dispatched by the aiohttp /notify endpoint."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_bot_notify(self, payload: dict) -> None:
        # Full implementation in v0.3.0
        log.info(
            "Notification received (not yet implemented): event_type=%s session_id=%s",
            payload.get("event_type"),
            payload.get("session_id"),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(NotificationsCog(bot))
