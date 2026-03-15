# Changelog

All notable changes to Quest Board Bot are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

_Nothing yet._

---

## [0.3.0] — 2026-03-15

Bot-driven Discord notifications. Quest Board's Celery tasks now call
the bot's HTTP server instead of posting plain webhook embeds on campaigns
that have a `guild_id` configured.

### Added

- **`bot/cogs/notifications.py`** — full implementation replacing the v0.1.0
  stub:
  - `on_bot_notify` listener routes incoming payloads to per-event handlers
  - `_resolve_channel` — fetches `TextChannel` by ID with clear logging on
    NotFound / Forbidden errors
  - **`session_proposed`** — rich embed with one field per time slot using
    Discord `<t:timestamp:F>` localised timestamps; slot details fetched from
    `GET /api/bot/sessions/{id}/timeslots`; seed reactions 🇦–🇪 added
    immediately after posting (0.5 s apart to respect rate limits)
  - **`session_confirmed`** — embed with confirmed time; campaign name
    fetched from API
  - **`session_reminder`** — embed with human-readable label
    (`_reminder_label` converts `hours_until` to "2 hours", "3 days", etc.)
    and confirmed time; campaign name fetched from API
  - **`session_cancelled`** — embed using title and campaign name from payload
  - **`vote_update`** — embed with per-slot yes / maybe / no counts fetched
    from API
  - **`get_message_mapping(message_id)`** — public method used by the voting
    cog (v0.5.0) to resolve a Discord message ID back to a session ID and
    ordered slot list
  - Redis-backed message→session store (`qb_msg:{message_id}`, 30-day TTL);
    falls back to an in-memory dict if `REDIS_URL` is not set or Redis is
    unreachable

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

[Unreleased]: https://github.com/10thTARDIS/Questboard-Bot/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/10thTARDIS/Questboard-Bot/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/10thTARDIS/Questboard-Bot/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/10thTARDIS/Questboard-Bot/releases/tag/v0.1.0
