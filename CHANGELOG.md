# Changelog

All notable changes to Quest Board Bot are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

_Nothing yet._

---

## [0.2.0] — 2026-03-15

Aligns the API client with the Quest Board v0.2.0 bot endpoints now live
on the backend. No new bot behaviour in this release — all functional work
was Quest Board-side (Campaign fields, five new bot endpoints, `/auth/link`,
linking token Redis flow, and Celery notification routing).

### Changed

- **`bot/api_client.py`** — `BotSettingsResponse` updated to match the
  actual shape returned by `GET /api/bot/settings`: fields renamed from
  bot-centric names (`whisper_mode`, `ollama_url`, …) to the names Quest
  Board uses (`whisper_endpoint_url`, `llm_endpoint_url`, `llm_model`, …)

---

## [0.1.0] — 2026-03-14

Initial scaffold. Project structure, configuration, typed API client, and
stub cogs. The bot connects to Discord and starts an HTTP server but does
not yet send messages or record votes.

### Added

- **Project structure** — `bot/` package with `main.py`, `config.py`,
  `api_client.py`; `cogs/`, `services/`, `utils/` sub-packages
- **`bot/config.py`** — Pydantic Settings reading from `.env`; all
  variables documented with defaults
- **`bot/api_client.py`** — Async `httpx` wrapper for all Quest Board bot
  endpoints; sends `X-Bot-Key` header; typed Pydantic response models for
  every endpoint (existing and to-be-added in v0.2.0)
- **`bot/main.py`** — `QuestBoardBot` (discord.py `commands.Bot` subclass)
  with required intents; aiohttp HTTP server at `POST /notify` and
  `GET /health`; startup audio-temp cleanup; graceful API client shutdown
- **Cog stubs** — `notifications`, `linking`, `voting`, `recording` cogs
  all load cleanly; slash commands `/link`, `/unlink`, `/record start`,
  `/record stop` registered but return "coming soon" responses
- **`Dockerfile`** — Python 3.12-slim, FFmpeg, non-root `botuser`
- **`docker-compose.yml`** — `bot` + `whisper` services; audio temp volume;
  `ollama` service commented out (assumed running on home server)
- **`requirements.txt`** — pinned direct and transitive dependencies
- **`requirements.in`** — unpinned direct dependencies for `pip-compile`
- **`requirements-dev.txt`** — `pip-audit`, `pip-tools`, `pytest`,
  `pytest-asyncio`, `pre-commit`
- **`.env.example`** — all variables with inline documentation
- **`.gitignore`** — `.env`, audio files, transcripts, Python artefacts

---

[Unreleased]: https://github.com/10thTARDIS/Questboard-Bot/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/10thTARDIS/Questboard-Bot/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/10thTARDIS/Questboard-Bot/releases/tag/v0.1.0
