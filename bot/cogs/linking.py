"""Linking cog — /link and /unlink slash commands.

Lets Discord users connect their Discord identity to Quest Board so that
emoji reactions are recorded as votes against their Quest Board account.

Flow for /link:
  1. Bot generates a one-time token and registers it in Quest Board's Redis
     via POST /api/bot/linking-tokens (TTL 10 min).
  2. User is sent a DM with a link to {questboard_public_url}/auth/link?token=<token>.
  3. Bot polls GET /api/bot/link-status/{token} every 30 s for up to 10 min.
  4. When linked: confirmation DM.  On timeout: expiry DM.

Flow for /unlink:
  No bot-facing delete endpoint exists yet (tracked in docs/questboard-improvements.md).
  User is directed to their Quest Board profile page.
"""

import asyncio
import logging
import secrets

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)

# Total time to wait for the user to click the link (matches the token TTL set
# in Quest Board's Redis by POST /api/bot/linking-tokens).
_LINK_TIMEOUT_SECONDS = 600  # 10 minutes
_POLL_INTERVAL_SECONDS = 30


class LinkingCog(commands.Cog, name="Linking"):
    """Slash commands for linking Discord accounts to Quest Board."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /link ─────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="link",
        description="Link your Discord account to Quest Board.",
    )
    async def link(self, interaction: discord.Interaction) -> None:
        """Generate a one-time link token and guide the user through linking."""
        # Acknowledge immediately — the DM send may take a moment.
        await interaction.response.send_message(
            "Check your DMs for a Quest Board link!", ephemeral=True
        )

        user = interaction.user
        token = secrets.token_hex(32)

        # Register the token in Quest Board's Redis so /auth/link can consume it.
        try:
            await self.bot.api.post_linking_token(token, str(user.id))
        except Exception as exc:
            log.warning("Failed to register linking token for user %s: %s", user.id, exc)
            try:
                await user.send(
                    "⚠️ Could not reach Quest Board right now. Please try `/link` again later."
                )
            except discord.Forbidden:
                pass
            return

        link_url = (
            f"{self.bot.settings.questboard_public_url.rstrip('/')}"
            f"/auth/link?token={token}"
        )

        try:
            await user.send(
                f"**Link your Quest Board account**\n\n"
                f"Click the link below to connect your Discord account:\n"
                f"{link_url}\n\n"
                f"This link expires in **10 minutes**. "
                f"If it expires, run `/link` again."
            )
        except discord.Forbidden:
            log.warning(
                "Cannot DM user %s — they have DMs disabled.", user.id
            )
            await interaction.followup.send(
                "I couldn't send you a DM. Please enable DMs from server members "
                "and try again.",
                ephemeral=True,
            )
            return

        # Poll for confirmation in the background so we don't block the gateway.
        asyncio.create_task(
            self._poll_link_status(user, token),
            name=f"link-poll-{user.id}",
        )

    async def _poll_link_status(self, user: discord.User, token: str) -> None:
        """Poll Quest Board until the token is consumed or the window expires."""
        elapsed = 0
        while elapsed < _LINK_TIMEOUT_SECONDS:
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            elapsed += _POLL_INTERVAL_SECONDS

            try:
                status = await self.bot.api.get_link_status(token)
            except Exception as exc:
                log.warning(
                    "Error polling link-status for user %s (elapsed=%ds): %s",
                    user.id, elapsed, exc,
                )
                # Transient error — keep trying until the window closes.
                continue

            if status.linked:
                log.info("Discord user %s successfully linked (elapsed=%ds).", user.id, elapsed)
                try:
                    await user.send(
                        "✅ Your Discord account is now linked to Quest Board! "
                        "Your emoji reactions on session messages will be recorded as votes."
                    )
                except discord.Forbidden:
                    pass
                return

        # Window closed without a successful link.
        log.info("Link token expired for user %s after %ds.", user.id, elapsed)
        try:
            await user.send(
                "The Quest Board link expired before it was used. "
                "Run `/link` again whenever you're ready."
            )
        except discord.Forbidden:
            pass

    # ── /unlink ───────────────────────────────────────────────────────────────

    @app_commands.command(
        name="unlink",
        description="Unlink your Discord account from Quest Board.",
    )
    async def unlink(self, interaction: discord.Interaction) -> None:
        """Direct the user to their Quest Board profile to remove the link.

        A dedicated bot-facing unlink endpoint is tracked in
        docs/questboard-improvements.md (Priority 3).  Until that endpoint
        exists the user needs to visit the web UI.
        """
        profile_url = (
            f"{self.bot.settings.questboard_public_url.rstrip('/')}/profile"
        )
        await interaction.response.send_message(
            f"To unlink your Discord account, visit your Quest Board profile:\n"
            f"{profile_url}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LinkingCog(bot))
