"""Linking cog — /link and /unlink slash commands.

Implemented in v0.4.0. This stub registers the commands and returns a
"coming soon" response so the command tree is populated from the start.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)


class LinkingCog(commands.Cog, name="Linking"):
    """Slash commands for linking Discord accounts to Quest Board."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="link",
        description="Link your Discord account to Quest Board.",
    )
    async def link(self, interaction: discord.Interaction) -> None:
        # Full implementation in v0.4.0
        await interaction.response.send_message(
            "Account linking is coming soon. Stay tuned!",
            ephemeral=True,
        )

    @app_commands.command(
        name="unlink",
        description="Unlink your Discord account from Quest Board.",
    )
    async def unlink(self, interaction: discord.Interaction) -> None:
        # Full implementation in v0.4.0
        await interaction.response.send_message(
            "Account unlinking is coming soon. Stay tuned!",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LinkingCog(bot))
