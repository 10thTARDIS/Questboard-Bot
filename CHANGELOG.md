# Changelog

All notable changes to Quest Board Bot are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

_Nothing yet._

---

## [0.8.0] — 2026-03-14

Attendance RSVP. Session-confirmed embeds now include ✅ / ❌ reactions so
players can indicate attendance directly from Discord. Reactions write to
Quest Board's existing attendance table — no new backend endpoints required.

### Changed

- **`bot/cogs/notifications.py`**
  - `_handle_confirmed` — adds an "Attendance" field to the confirmed embed,
    seeds ✅ then ❌ reactions (0.5 s apart), and stores the message→session
    mapping with `type: "attendance"` so the voting cog routes reactions
    correctly
  - `_store_message_map` — accepts a new `map_type` parameter (default
    `"voting"`); stores `"type"` key in the mapping payload; backwards-compatible
    with existing Redis entries (missing key defaults to `"voting"`)
  - Added `_ATTENDANCE_EMOJIS = ["✅", "❌"]` constant

- **`bot/cogs/voting.py`**
  - `_handle_reaction` now reads `mapping["type"]` and branches:
    - `"attendance"` → delegates to new `_handle_attendance_reaction`:
      ✅ calls `put_attendance(attended=True)`, ❌ calls
      `put_attendance(attended=False)`; other emojis ignored
    - `"voting"` (default) → delegates to new `_handle_vote_reaction`
      (existing logic, now extracted to its own method)
  - Added `_ATTEND_YES` / `_ATTEND_NO` constants

- **`docs/questboard-improvements.md`** — added four new entries for the
  Quest Board backend endpoints required by bot v0.9.0 and v0.10.0

---

## [0.7.0] — 2026-03-14

Polish and hardening. Remote settings, pre-commit security hooks, and a full
setup guide in the README.

### Added

- **`.pre-commit-config.yaml`** — pre-commit hooks: trailing-whitespace,
  end-of-file-fixer, check-yaml, check-added-large-files (500 KB limit),
  check-merge-conflict, detect-private-key, and a local `pip-audit` hook
  that runs on every change to `requirements.txt`
- **README** — full setup guide covering: Discord Developer Portal walkthrough,
  Quest Board admin configuration, account linking, reaction voting, recording
  pipeline, Whisper model trade-offs, Ollama model recommendations, and a
  troubleshooting section

### Changed

- **`bot/main.py`** — `on_ready` now calls `_apply_remote_settings`, which
  fetches `GET /api/bot/settings` from Quest Board and applies admin-configured
  Whisper/LLM endpoints, models, and API keys on top of env-var defaults;
  failure is logged as a warning and does not prevent the bot from starting;
  API keys are never logged (placeholder string used in log line)

---

## [0.6.0] — 2026-03-14

Voice recording, transcription, and summarisation pipeline.  GMs can record
a voice session directly from Discord; the audio is automatically transcribed
and summarised, and the results are uploaded to Quest Board.

### Added

- **`bot/cogs/recording.py`** — full implementation replacing the v0.1.0 stub:
  - **`/record start <session_id>`** — validates the UUID, requires the GM to
    be in a voice channel, fetches session metadata from the API for the
    announcement embed, connects to voice and starts a `discord.sinks.WaveSink`;
    schedules an auto-stop at `MAX_RECORDING_HOURS`
  - **`/record stop`** — stops the sink, disconnects from voice, hands off to
    the processing pipeline as a background task
  - Processing pipeline: collects per-user WAV files from the sink → mixes to
    mono MP3 (`utils/audio`) → deletes WAVs → transcribes (`services/transcription`)
    → summarises (`services/summarisation`) → uploads via
    `POST /api/bot/sessions/{id}/transcript` → posts a summary embed; cleans up
    the MP3 on completion or error
  - Auto-stop fires after `MAX_RECORDING_HOURS` with a channel notice
  - One active recording per guild enforced; clear errors if already recording
    or if the GM is not in a voice channel
  - Transcript content is never logged — only metadata (duration, word count,
    file sizes)

- **`bot/utils/audio.py`** — `mix_to_mp3(wav_paths, output_path)` wraps an
  FFmpeg subprocess; single-input path skips `amix` filter; logs file size on
  completion; raises `RuntimeError` on non-zero FFmpeg exit

- **`bot/services/transcription.py`** — `transcribe(mp3_path, settings)`:
  - `WHISPER_MODE=local` — multipart POST to ahmetoner/whisper-asr-webservice
    at `WHISPER_API_URL/asr`
  - `WHISPER_MODE=api` — OpenAI `POST /v1/audio/transcriptions` (whisper-1)
  - 10-minute timeout; raises `RuntimeError` on failure

- **`bot/services/summarisation.py`** — `summarise(transcript, campaign_name, game_system, settings)`:
  - `SUMMARISER_MODE=ollama` — `POST /api/generate` to local Ollama
  - `SUMMARISER_MODE=api` — Anthropic claude-haiku-4-5-20251001 if
    `ANTHROPIC_API_KEY` set, otherwise OpenAI gpt-4o-mini
  - TTRPG-focused system prompt: key events, NPC introductions, player
    decisions, and a "What's next?" hook
  - 5-minute timeout; raises `RuntimeError` on failure

---

## [0.5.0] — 2026-03-14

Reaction voting. Emoji reactions on session_proposed messages are now
translated into Quest Board availability votes in real time.

### Changed

- **`bot/cogs/voting.py`** — full implementation replacing the v0.1.0 stub:
  - `on_raw_reaction_add` — records `availability="yes"` when a user adds a
    slot emoji (🇦–🇪) to a session_proposed message
  - `on_raw_reaction_remove` — records `availability="no"` when a user
    removes a slot emoji
  - Looks up the message→session mapping via `NotificationsCog.get_message_mapping`
    (Redis-backed, 30-day TTL) to resolve emoji position to a `slot_id` without
    a Quest Board round-trip
  - Calls `GET /api/bot/platform-links/discord/{user_id}` before voting; on
    404 sends the user a DM prompting them to run `/link`
  - Ignores the bot's own seed reactions, non-slot emojis, and messages with
    no stored mapping; logs and swallows API errors so transient failures don't
    surface as Discord errors

---

## [0.4.0] — 2026-03-14

Discord account linking. Players can connect their Discord identity to Quest
Board so that emoji reactions on session messages are recorded as votes against
their Quest Board account.

### Changed

- **`bot/cogs/linking.py`** — full implementation replacing the v0.1.0 stub:
  - **`/link`** — generates a `secrets.token_hex(32)` token, registers it in
    Quest Board's Redis via `POST /api/bot/linking-tokens` (TTL 10 min), sends
    the user a DM with `{questboard_public_url}/auth/link?token=<token>`, then
    polls `GET /api/bot/link-status/{token}` every 30 s for up to 10 min;
    sends a confirmation DM on success or an expiry DM on timeout; handles
    `discord.Forbidden` (DMs disabled) and Quest Board API errors gracefully
  - **`/unlink`** — directs the user to their Quest Board profile page; a
    dedicated bot-facing unlink endpoint is tracked in
    `docs/questboard-improvements.md` (Priority 3)

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

[Unreleased]: https://github.com/10thTARDIS/Questboard-Bot/compare/v0.8.0...HEAD
[0.8.0]: https://github.com/10thTARDIS/Questboard-Bot/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/10thTARDIS/Questboard-Bot/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/10thTARDIS/Questboard-Bot/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/10thTARDIS/Questboard-Bot/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/10thTARDIS/Questboard-Bot/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/10thTARDIS/Questboard-Bot/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/10thTARDIS/Questboard-Bot/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/10thTARDIS/Questboard-Bot/releases/tag/v0.1.0
