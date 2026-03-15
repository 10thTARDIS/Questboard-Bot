"""Recording cog — /record start|stop and the full voice pipeline.

Implemented in v0.6.0. This stub registers the slash command group so
it appears in Discord's command list from the start.
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)


class RecordingCog(commands.Cog, name="Recording"):
    """Voice recording, transcription, and summarisation pipeline."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    record = app_commands.Group(
        name="record",
        description="Record a voice session for transcription.",
    )

    @record.command(name="start", description="Start recording the current voice session.")
    @app_commands.describe(session_id="Quest Board session ID to attach the recording to.")
    async def record_start(
        self, interaction: discord.Interaction, session_id: str
    ) -> None:
        # Full implementation in v0.6.0
        await interaction.response.send_message(
            "Session recording is coming soon. Stay tuned!",
            ephemeral=True,
        )

    @record.command(name="stop", description="Stop recording and process the transcript.")
    async def record_stop(self, interaction: discord.Interaction) -> None:
        # Full implementation in v0.6.0
        await interaction.response.send_message(
            "Session recording is coming soon. Stay tuned!",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RecordingCog(bot))
