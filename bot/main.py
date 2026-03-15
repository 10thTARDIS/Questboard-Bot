"""Quest Board Bot — entry point.

Starts two concurrent services:
  1. Discord gateway client (discord.py) — handles reactions, slash commands,
     voice recording.
  2. aiohttp HTTP server — receives POST /notify calls from Quest Board's
     Celery tasks and dispatches them as Discord bot events.
"""

import asyncio
import logging
import secrets
from pathlib import Path

import aiohttp.web as web
import discord
from discord.ext import commands

from bot.api_client import QuestBoardClient
from bot.config import Settings

log = logging.getLogger(__name__)

_COGS = [
    "bot.cogs.notifications",
    "bot.cogs.linking",
    "bot.cogs.voting",
    "bot.cogs.recording",
]


class QuestBoardBot(commands.Bot):
    """Discord bot client with Quest Board API client attached."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.api = QuestBoardClient(settings.questboard_api_url, settings.bot_api_key)

        intents = discord.Intents.default()
        intents.message_content = True  # Privileged — enable in Developer Portal
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        for cog in _COGS:
            await self.load_extension(cog)
        await self.tree.sync()
        log.info("Cogs loaded and slash commands synced.")

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s)", self.user, self.user.id)
        _cleanup_audio_temp(self.settings.audio_temp_dir)

    async def close(self) -> None:
        await self.api.close()
        await super().close()


# ── HTTP server ────────────────────────────────────────────────────────────────


async def _build_web_app(bot: QuestBoardBot) -> web.Application:
    """Build the aiohttp app that receives incoming calls from Quest Board."""

    app = web.Application()
    app["bot"] = bot

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def notify(request: web.Request) -> web.Response:
        """Receive a notification event from Quest Board's Celery tasks.

        Validates the X-Bot-Key header, then dispatches an on_bot_notify
        event on the Discord bot so the notifications cog can handle it.
        """
        key = request.headers.get("X-Bot-Key", "")
        stored = bot.settings.bot_api_key
        if not stored or not secrets.compare_digest(key, stored):
            return web.json_response({"detail": "Unauthorized"}, status=401)

        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"detail": "Invalid JSON"}, status=400)

        bot.dispatch("bot_notify", payload)
        return web.json_response({"detail": "ok"})

    app.router.add_get("/health", health)
    app.router.add_post("/notify", notify)

    return app


# ── Audio cleanup ──────────────────────────────────────────────────────────────


def _cleanup_audio_temp(audio_temp_dir: str) -> None:
    """Delete orphaned audio files from a previous crashed run."""
    p = Path(audio_temp_dir)
    if not p.exists():
        return
    removed = 0
    for pattern in ("*.wav", "*.mp3"):
        for f in p.glob(pattern):
            f.unlink(missing_ok=True)
            removed += 1
    if removed:
        log.info("Cleaned up %d orphaned audio file(s) from %s", removed, audio_temp_dir)


# ── Entrypoint ─────────────────────────────────────────────────────────────────


async def _main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    settings = Settings()

    bot = QuestBoardBot(settings)
    app = await _build_web_app(bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, settings.http_host, settings.http_port)

    log.info(
        "Starting HTTP server on %s:%d", settings.http_host, settings.http_port
    )

    async with bot:
        await asyncio.gather(
            bot.start(settings.discord_bot_token),
            site.start(),
        )


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
