"""Voting cog — reaction add/remove → Quest Board vote API.

Implemented in v0.5.0. This stub registers the event listeners so the
bot's intent declarations are exercised from the start.
"""

import logging

import discord
from discord.ext import commands

log = logging.getLogger(__name__)


class VotingCog(commands.Cog, name="Voting"):
    """Translates Discord emoji reactions into Quest Board availability votes."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_raw_reaction_add(
        self, payload: discord.RawReactionActionEvent
    ) -> None:
        # Full implementation in v0.5.0
        pass

    @commands.Cog.listener()
    async def on_raw_reaction_remove(
        self, payload: discord.RawReactionActionEvent
    ) -> None:
        # Full implementation in v0.5.0
        pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VotingCog(bot))
