"""Pydantic Settings for the Quest Board Bot.

All configuration is read from environment variables or a .env file.
Admin-configured overrides (Whisper/LLM modes) are fetched from the
Quest Board API at startup and applied on top of these defaults.
"""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── Discord ───────────────────────────────────────────────────────────
    # Required Gateway Intents (Discord Developer Portal):
    #   GUILDS, GUILD_MESSAGES, GUILD_MESSAGE_REACTIONS,
    #   GUILD_VOICE_STATES, MESSAGE_CONTENT (privileged)
    discord_bot_token: str

    # ── Quest Board API ───────────────────────────────────────────────────
    # Public HTTPS URL of your Quest Board instance (no trailing slash).
    # Nginx proxies /api to the backend — use the root URL, not /api directly.
    questboard_api_url: str = "https://questboard.example.com"
    # Copy from Quest Board Admin → Bot Settings → Bot API Key.
    bot_api_key: str

    # ── HTTP server ───────────────────────────────────────────────────────
    # Incoming calls from Quest Board (POST /notify etc.)
    http_host: str = "0.0.0.0"
    http_port: int = 8080

    # ── Linking ───────────────────────────────────────────────────────────
    # Used to construct the /auth/link?token=... URL sent to users in DMs
    questboard_public_url: str = "https://questboard.example.com"

    # ── Transcription ─────────────────────────────────────────────────────
    whisper_mode: Literal["local", "api"] = "local"
    whisper_api_url: str = "http://whisper:9000"
    openai_api_key: str = ""  # Only if whisper_mode=api

    # ── Summarisation ─────────────────────────────────────────────────────
    summariser_mode: Literal["ollama", "api"] = "ollama"
    ollama_url: str = "http://ollama:11434"
    ollama_model: str = "llama3"
    anthropic_api_key: str = ""  # Only if summariser_mode=api (Anthropic)

    # ── Audio ─────────────────────────────────────────────────────────────
    audio_temp_dir: str = "/tmp/questboard-audio"
    max_recording_hours: int = 6

    # ── Redis (optional) ──────────────────────────────────────────────────
    # Used for message→session mapping (voting) and linking token storage.
    # Falls back to in-memory dict if not set.
    redis_url: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
