# Quest Board Bot — Build Plan

## Purpose

This is a companion bot to Quest Board (https://github.com/10thTARDIS/Questboard).
It is a separate repository and a separate deployable service. It does three things:

1. **Rich Discord notifications** — takes over session notifications on servers where
   the bot is present, replacing plain webhook posts with interactive bot messages
   that players can react to directly.
2. **Reaction voting** — watches for emoji reactions on scheduling notifications and
   writes votes back to the Quest Board API on behalf of linked users.
3. **Session recording & transcription** — joins a Discord voice channel, records
   audio with group-level consent already assumed, transcribes with Whisper,
   summarises with an LLM, and posts the result back to the Quest Board API.

---

## What Already Exists in Quest Board v1 (Do Not Recreate)

Before writing any code, read the Quest Board repo carefully. The following are
**already built** in v1 and the bot must use them as-is:

- `platform_links` table — already in the v1 schema:
  `(id, user_id, platform ENUM('discord','matrix'), platform_user_id, created_at)`
  with a UNIQUE constraint on `(platform, platform_user_id)`
- `transcript` and `summary` columns — already exist on the `sessions` table
- `PUT /api/sessions/{id}/attendance/{user_id}` — already implemented and
  explicitly noted as "ready for v2 bot"
- Admin panel with `app_settings` — bot token and Whisper/LLM config must be
  stored here (admin-configurable), not exclusively in env vars

---

## Architecture Overview

```
Quest Board Backend
  │  Celery task fires on session event
  │  Checks: does this campaign's guild have the bot?
  ├─ YES → POST /api/bot/notify (bot sends rich message via Discord API)
  └─ NO  → POST webhook as before (plain embed, no reactions)

Discord Gateway
  │  reaction events / voice audio / slash commands
  ▼
questboard-bot
  ├── cogs/notifications.py  ← receives notify calls, sends rich messages,
  │                             adds seed reactions
  ├── cogs/voting.py         ← reaction add/remove → Quest Board votes API
  ├── cogs/linking.py        ← /link command → account linking flow
  └── cogs/recording.py      ← /record start|stop + voice pipeline
        │
        ▼
  services/
    ├── transcription.py     ← Whisper (local or OpenAI API)
    └── summarisation.py     ← Ollama or Anthropic/OpenAI API
        │
        ▼
Quest Board Backend API      ← bot-facing endpoints (BOT_API_KEY auth)
```

---

## Repository Layout

```
questboard-bot/
├── bot/
│   ├── main.py              # Entry point — creates and starts Discord client
│   ├── config.py            # Pydantic Settings from env vars
│   ├── api_client.py        # Async httpx wrapper for Quest Board API
│   ├── cogs/
│   │   ├── notifications.py # Handles /api/bot/notify, sends rich messages
│   │   ├── voting.py        # on_raw_reaction_add/remove handlers
│   │   ├── linking.py       # /link slash command + DM-based linking flow
│   │   └── recording.py     # /record start|stop + voice pipeline
│   ├── services/
│   │   ├── transcription.py # Whisper: local container or OpenAI API
│   │   └── summarisation.py # LLM summary: Ollama or Anthropic/OpenAI API
│   └── utils/
│       └── audio.py         # FFmpeg mixing helpers
├── .env.example
├── .gitignore
├── requirements.txt         # Pinned with pip-compile
├── requirements-dev.txt
├── Dockerfile
├── docker-compose.yml       # Bot + Whisper (+ optional Ollama)
└── README.md
```

---

## How the Bot Authenticates with Quest Board

The bot is a trusted internal service. It uses a shared secret (`BOT_API_KEY`)
presented as `Authorization: Bearer <BOT_API_KEY>` on every API request.

### Changes required in Quest Board repo

1. Add `BOT_API_KEY` to `config.py` and `.env.example`
   (generate with `openssl rand -hex 32`).
2. Add `require_bot_key` FastAPI dependency that validates the bearer token.
3. Add a new `bot.py` router mounted at `/api/bot/` using this dependency.
4. Remove `DISCORD_BOT_TOKEN` from Quest Board's notification service — the bot
   now owns all Discord message sending on guilds where it is present. Quest Board
   only needs to know whether to call the bot or fall back to a plain webhook.
5. Store bot endpoint URL, Whisper/LLM settings, and bot token in `app_settings`
   (admin-configurable via Admin → Settings UI), with env vars as fallback.

The `BOT_API_KEY` must be rotatable independently of all other secrets.

---

## Feature 1: Bot-Driven Notifications

### Notification routing logic (Quest Board repo)

When a Celery task fires a notification, the notification service checks whether
the relevant guild has the bot installed:

```
notification_service sends a notification
         │
         ▼
Is QUESTBOARD_BOT_URL configured AND does this campaign have a guild_id set?
         │
    YES  │  NO
         │   └──► POST to discord_webhook_url as before (plain embed)
         ▼
POST /api/bot/notify
Bot sends a rich interactive message via Discord API
Bot adds seed emoji reactions (one per time slot, for vote-type sessions)
```

### New field: `guild_id` on Campaign

Add a `guild_id` (TEXT, nullable) column to the `campaigns` table via Alembic
migration. GMs set this in campaign settings — it is the Discord server ID where
the bot will send notifications for that campaign. If blank, the fallback webhook
is used.

### New endpoint: `POST /api/bot/notify`

```
POST /api/bot/notify
Body: {
  event_type: "session_proposed" | "session_confirmed" | "session_reminder"
            | "session_cancelled" | "vote_update",
  session_id: UUID,
  campaign_id: UUID,
  guild_id: str,           ← Discord server ID
  channel_id: str,         ← Discord channel ID (from campaign settings)
  extra: {}                ← event-specific data (slot counts, hours_until, etc.)
}
```

The bot sends an appropriately formatted embed for each event type. For
`session_proposed`, it adds seed emoji reactions immediately after posting.
Returns 200 once the message has been sent. Quest Board's Celery task should
treat a non-200 response as a failure and optionally fall back to the webhook.

### New campaign settings fields

Add to Campaign model and campaign settings UI in Quest Board:

- `guild_id` (TEXT nullable) — Discord server ID
- `notification_channel_id` (TEXT nullable) — Discord channel ID for bot messages
- `discord_webhook_url` remains — used as fallback when bot is not configured

---

## Feature 2: Account Linking via `/link` Command

Players link their Discord account to Quest Board directly in Discord, without
needing to visit the web UI.

### Flow

```
Player runs /link in Discord
         │
         ▼
Bot generates a one-time token (random hex, 10-minute TTL)
Stores token → discord_user_id in Redis (bot's own Redis, or Quest Board's)
         │
         ▼
Bot sends a DM to the player:
"Click this link to link your Discord account to Quest Board:
https://questboard.example.com/link?token=<token>
This link expires in 10 minutes."
         │
         ▼
Player clicks the link — must be logged in to Quest Board via OIDC
Quest Board's /auth/link?token=<token> endpoint:
  - Validates token exists in Redis (GETDEL — consumed on first use)
  - Reads discord_user_id from the token payload
  - Creates a platform_links record: (user_id, 'discord', discord_user_id)
  - Redirects to profile page with a success message
         │
         ▼
Bot receives confirmation via GET /api/bot/link-status/{token}
(polled once after a short delay, or the Quest Board endpoint notifies
the bot via a callback — polling is simpler for v1)
         │
         ▼
Bot sends a DM confirmation: "Your Discord account is now linked to Quest Board."
```

### Why a web redirect rather than pure bot flow?

The link must be tied to an authenticated Quest Board session (OIDC). The web
redirect ensures the user is genuinely logged in before the link is created.
The bot cannot verify OIDC identity itself.

### New endpoints required in Quest Board repo

```
GET  /auth/link?token=<token>
     Validates the token from Redis, creates the platform_links record,
     redirects to /profile?linked=discord.
     Requires the user to already be logged in (get_current_user dependency).
     Returns 400 if token is invalid or expired.
     Returns 409 if this Discord account is already linked to a different user.

GET  /api/bot/link-status/{token}
     Returns { linked: true, user_id: UUID } if the token has been consumed,
     or { linked: false } if still pending.
     Uses require_bot_key auth.

DELETE /api/me/links/{platform}
     Removes the platform_links record for the current user + platform.
     Existing endpoint (or needs adding) — confirm in Quest Board repo.
```

### Token storage

Linking tokens can use Quest Board's existing Redis instance (DB 0 alongside
OIDC state), with a key format of `discord_link:<token>` and a 10-minute TTL.
No new Redis instance needed.

### `/unlink` command

Add a `/unlink` slash command that calls `DELETE /api/me/links/discord` via
the bot API, or directs the user to their profile page if the bot API doesn't
expose a user-scoped unlink endpoint.

---

## Feature 3: Reaction-Based Voting

Once the bot sends the notification message (Feature 1), it already has the
message object and can add seed reactions immediately. The voting flow then is:

1. Bot has sent a `session_proposed` message and added 🇦 🇧 🇨 reactions.
2. Bot stores `(message_id → session_id)` in memory (or a lightweight Redis key)
   so reaction events can be resolved back to a session.
3. `on_raw_reaction_add` fires:
   - Look up session_id from message_id.
   - Map emoji index → time slot ID via
     `GET /api/bot/sessions/{session_id}/timeslots`.
   - Resolve Discord user_id via
     `GET /api/bot/platform-links/discord/{user_id}`.
   - If 404 (not linked): DM the user *"Run /link to connect your Discord
     account to Quest Board so your vote can be recorded."*
   - If found: `POST /api/bot/votes` with `availability: "yes"`.
4. `on_raw_reaction_remove`: same flow but `availability: "no"`.

### Emoji → availability mapping

- React = `yes`
- Remove reaction = `no`

Full yes/maybe/no granularity remains available on the Quest Board web UI.
The bot reaction flow prioritises frictionless participation.

---

## Feature 4: Session Recording & Transcription

### Consent model

All members of the group have given standing consent to be recorded. The bot
does not need per-session individual opt-in. It must:

- Announce clearly in the channel when recording begins: *"🔴 Recording started
  for [Session Title]. Audio will be transcribed and summarised automatically."*
- Announce when recording ends: *"⏹ Recording stopped."*

No consent gating, no ✅ reaction flow, no per-user filtering. Record all
participants in the voice channel.

### Recording flow

```
GM runs /record start [session_id]
         │
         ▼
Bot announces in the channel: "🔴 Recording started for [Session Title]."
Bot joins the voice channel
Starts recording all participants via AudioSink
         │
         ▼
GM runs /record stop  (or bot auto-stops at MAX_RECORDING_HOURS)
         │
         ▼
Bot leaves voice channel
Bot announces: "⏹ Recording stopped. Processing transcript..."
Bot mixes per-user PCM to mono MP3 with FFmpeg
         │
         ▼
Sends MP3 to Whisper for transcription
         │
         ▼
Sends transcript + campaign_name + game_system to LLM for summary
         │
         ▼
POST /api/bot/sessions/{session_id}/transcript
Bot posts summary embed to the Discord channel
Bot deletes all temp audio files
```

### Audio format

- PCM, 48kHz, one stream per participant (discord.py native format)
- Mixed to mono MP3 with FFmpeg before sending to Whisper
- Raw PCM deleted immediately after mixing
- No permanent audio storage — transcript only

### Whisper integration

Controlled by `WHISPER_MODE` (env var or admin settings):

| Mode | Description |
|------|-------------|
| `local` | Self-hosted `ahmetoner/whisper-asr-webservice` (recommended) |
| `api` | OpenAI Whisper API |

### LLM summarisation

Controlled by `SUMMARISER_MODE` (env var or admin settings):

| Mode | Description |
|------|-------------|
| `ollama` | Local Ollama instance (`OLLAMA_URL`, `OLLAMA_MODEL`) |
| `api` | Anthropic or OpenAI API |

Summary prompt:
```
You are summarising a tabletop RPG session.
Game system: {game_system}
Campaign: {campaign_name}

Given the transcript below, write a session summary covering:
- What happened in the story
- Key decisions the players made
- Important NPCs or locations introduced
- A cliffhanger or hook for the next session, if one was established

Write in past tense. 3–5 paragraphs.

Transcript:
{transcript}
```

`game_system` and `campaign_name` are returned by the
`GET /api/bot/sessions/{id}/timeslots` endpoint.

---

## All New Bot-Facing API Endpoints (Quest Board repo)

All use `require_bot_key` authentication.

```
POST /api/bot/notify
     Receives a notification event from Quest Board's Celery tasks.
     Bot sends the appropriate Discord message and adds reactions.

GET  /api/bot/sessions/{session_id}/timeslots
     Returns time slots with vote counts, campaign_name, game_system,
     and reminder_offsets_minutes.

GET  /api/bot/platform-links/{platform}/{platform_user_id}
     Resolves a platform user ID to a Quest Board user_id.
     Returns 404 if not linked.

POST /api/bot/votes
     Body: { time_slot_id, platform, platform_user_id, availability }
     Returns 403 (not a campaign member), 404 (not linked),
     or 409 (voting already closed).

POST /api/bot/sessions/{session_id}/transcript
     Body: { transcript: str, summary: str }
     Requires session status to be 'completed'.

GET  /api/bot/link-status/{token}
     Returns { linked: bool, user_id?: UUID }.

GET  /api/bot/settings
     Returns admin-configured Whisper/LLM/bot settings from app_settings,
     with env var values as documented defaults.
```

Also confirm and use the existing:
```
PUT  /api/sessions/{session_id}/attendance/{user_id}
     Already implemented. Confirm exact path and body before using.
```

---

## Reminder Schedule: Per-Campaign

Quest Board stores reminder offsets as `reminder_offsets_minutes` (JSONB) on
the Campaign — up to 3 reminders, GM-configurable. The bot must never hardcode
7d/24h/1h. Always read the per-campaign schedule from the API and reference it
accurately in any Discord messages.

---

## New Campaign Settings Fields (Quest Board repo)

Add to Campaign model via Alembic migration, and expose in the campaign
settings UI:

| Field | Type | Purpose |
|---|---|---|
| `guild_id` | TEXT nullable | Discord server ID — enables bot notifications |
| `notification_channel_id` | TEXT nullable | Discord channel for bot messages |

`discord_webhook_url` remains and is used when `guild_id` is not set.

---

## Environment Variables (`.env.example`)

```bash
# ── Discord ───────────────────────────────────────────────────────
DISCORD_BOT_TOKEN=your-bot-token-here
# Required Gateway Intents (Discord Developer Portal):
#   GUILDS, GUILD_MESSAGES, GUILD_MESSAGE_REACTIONS,
#   GUILD_VOICE_STATES, MESSAGE_CONTENT (privileged)

# ── Quest Board API ───────────────────────────────────────────────
QUESTBOARD_API_URL=http://questboard-backend:8000
BOT_API_KEY=replace-with-openssl-rand-hex-32
# Must match BOT_API_KEY in the Quest Board .env

# ── Linking ───────────────────────────────────────────────────────
QUESTBOARD_PUBLIC_URL=https://questboard.example.com
# Used to construct the /auth/link?token=... URL sent to users in DMs

# ── Transcription ─────────────────────────────────────────────────
WHISPER_MODE=local              # 'local' or 'api'
WHISPER_API_URL=http://whisper:9000
OPENAI_API_KEY=                 # Only if WHISPER_MODE=api

# ── Summarisation ─────────────────────────────────────────────────
SUMMARISER_MODE=ollama          # 'ollama' or 'api'
OLLAMA_URL=http://ollama:11434
OLLAMA_MODEL=llama3
ANTHROPIC_API_KEY=              # Only if SUMMARISER_MODE=api (Anthropic)

# ── Audio ─────────────────────────────────────────────────────────
AUDIO_TEMP_DIR=/tmp/questboard-audio
MAX_RECORDING_HOURS=6
```

---

## Docker Compose Services

```yaml
services:
  bot:
    build: .
    env_file: .env
    depends_on:
      - whisper
    restart: unless-stopped
    volumes:
      - audio_temp:/tmp/questboard-audio

  whisper:
    image: ahmetoner/whisper-asr-webservice:latest
    environment:
      - ASR_MODEL=base   # 'small' or 'medium' for better accuracy
    restart: unless-stopped

  # Uncomment only if Ollama is not already running on your home server
  # ollama:
  #   image: ollama/ollama
  #   volumes:
  #     - ollama_data:/root/.ollama
  #   restart: unless-stopped

volumes:
  audio_temp:
```

---

## Discord Bot Setup (Developer Portal)

1. https://discord.com/developers/applications → New Application
2. **Bot** → enable Privileged Gateway Intents:
   `Message Content Intent`, `Server Members Intent`
3. **OAuth2 → URL Generator** → scopes: `bot` + `applications.commands`
   → permissions: Read Messages, Send Messages, Add Reactions,
   Send Messages in Threads, Connect, Speak
4. Invite bot via generated URL
5. Copy bot token to `DISCORD_BOT_TOKEN`

---

## Stack

| Layer | Technology |
|---|---|
| Bot framework | Python 3.12, discord.py 2.x (`discord.py[voice]`) |
| Voice recording | discord.py voice client + AudioSink |
| Audio mixing | FFmpeg (via subprocess) |
| Transcription | Self-hosted Whisper or OpenAI API |
| Summarisation | Ollama or Anthropic/OpenAI API |
| API client | httpx (async) |
| Config | Pydantic Settings (same pattern as Quest Board) |
| Containerisation | Docker, Docker Compose |

---

## Security Requirements

- `BOT_API_KEY` — treat as a high-value secret, rotate independently.
- `DISCORD_BOT_TOKEN` — never log, never expose in error messages.
- Linking tokens are single-use (GETDEL) with 10-minute TTL.
- Audio temp files deleted immediately after transcription.
- Startup cleanup removes orphaned audio files from crashed previous runs.
- Never log transcript content or summaries — only metadata (session ID,
  word count, duration).
- Whisper and Ollama must not be exposed on public ports.
- All containers run as non-root.
- `.env` gitignored; `.env.example` committed with placeholders only.
- `pip-audit` pre-commit hook.

---

## Build Order

### Phase A — Scaffold & API client
1. Project structure, `config.py`, `Dockerfile`, `docker-compose.yml`, `.env.example`
2. `api_client.py` — async httpx with bot key auth, typed methods for all endpoints
3. Smoke test: successfully call `GET /api/bot/sessions/{id}/timeslots`

### Phase B — Changes required in Quest Board repo

**Verify first — confirm what already exists:**

4. Confirm `platform_links` schema (already v1 — no new migration)
5. Confirm `transcript`/`summary` columns on sessions (already v1)
6. Confirm exact path + request body of the existing attendance endpoint
7. Add `BOT_API_KEY` + `require_bot_key` dependency
8. Add `guild_id` + `notification_channel_id` to Campaign model + migration +
   campaign settings UI
9. Update notification service routing: check `guild_id` → call bot or fall
   back to webhook
10. Add all bot-facing endpoints in a new `bot.py` router
11. Add `/auth/link?token=...` endpoint (OIDC-authenticated, consumes Redis token)
12. Add linking token Redis key format (`discord_link:<token>`, DB 0, 10-min TTL)
13. Add Whisper/LLM/bot settings to `app_settings` + `GET /api/bot/settings`

### Phase C — Bot notifications
14. `cogs/notifications.py` — HTTP listener or internal trigger for notify events
15. Rich embed formatting for each event type
16. Seed emoji reactions on `session_proposed` messages
17. Store `message_id → session_id` mapping in memory / Redis
18. End-to-end test: confirm session in Quest Board → bot posts rich message
    with reactions in Discord

### Phase D — Account linking
19. `cogs/linking.py` — `/link` slash command
20. Token generation + DM with link URL
21. `/unlink` slash command
22. Poll `GET /api/bot/link-status/{token}` and send DM confirmation
23. End-to-end test: `/link` → click URL → logged in → account linked →
    confirmation DM received

### Phase E — Reaction voting
24. `cogs/voting.py` — `on_raw_reaction_add` / `on_raw_reaction_remove`
25. Session ID resolution from stored `message_id → session_id` map
26. User resolution + unlinked-user DM ("Run /link to connect your account")
27. End-to-end test: react → vote appears in Quest Board UI

### Phase F — Recording & transcription
28. `cogs/recording.py` — `/record start [session_id]` and `/record stop`
29. Channel announcement on start and stop
30. Voice client join + full-channel AudioSink recording
31. `utils/audio.py` — FFmpeg mix to mono MP3
32. `services/transcription.py` — Whisper local + API modes
33. `services/summarisation.py` — Ollama + API modes with prompt injection
34. Full pipeline: record → mix → transcribe → summarise → POST to API →
    post summary embed → delete temp files
35. Startup cleanup for orphaned audio files

### Phase G — Polish & hardening
36. `MAX_RECORDING_HOURS` auto-stop
37. Error handling throughout: voice disconnects, Whisper/LLM timeouts,
    API failures — all reported to Discord channel gracefully
38. Startup fetch of admin settings from `GET /api/bot/settings`
39. README: setup guide, Developer Portal walkthrough, Whisper model
    trade-offs, Ollama model recommendations
40. `pip-audit` pre-commit hook

---

## Quest Board Architecture Reference

Key details from ARCHITECTURE.md to stay consistent with:

- **Redis DB allocation:** 0 = sessions + OIDC state (linking tokens go here too),
  1 = Celery broker, 2 = Celery result backend
- **OIDC state key format:** `oidc_state:<state>` — linking tokens use
  `discord_link:<token>` in the same DB to avoid conflicts
- **Error responses:** `{"detail": "Internal server error"}` only — bot endpoints
  must follow the same pattern
- **Webhook URL validation:** `discord_webhook_url` validated against
  `https://discord.com/api/webhooks/` prefix — apply same pattern to any
  Discord URLs the bot handles
- **Reminder schedule:** per-campaign `reminder_offsets_minutes` JSONB —
  never hardcode timing values
- **Session auto-complete:** Celery Beat transitions `confirmed` → `completed`
  once `confirmed_time` passes — the bot can use `session_confirmed` and
  `completed` status transitions as triggers without GM manual action
- **`celery_task_ids`** JSONB on sessions — reminder tasks are already
  scheduled and revocable by Quest Board; bot does not need to manage these

---

## Notes for Claude Code

- Read the Quest Board repo in full — `ARCHITECTURE.md`, `todo.md`, existing
  models and routers — before writing any code. Several things the bot needs
  are already built.
- `platform_links`, `transcript`/`summary` columns, and the attendance endpoint
  all already exist in v1. Do not create new migrations for them.
- Phase B changes go in the Quest Board repo, not this one. Build and test those
  first, then build the bot against the updated API.
- The consent model is group-level and pre-agreed. There is no per-session
  individual consent flow. The only requirement is a channel announcement when
  recording starts.
- Use `discord.py[voice]` in requirements.txt.
- FFmpeg must be in the Dockerfile: `apt-get install -y ffmpeg`.
- Follow Quest Board's code style: Pydantic v2, full async/await, services layer.
- No TLS handling — the home server's reverse proxy handles that.
- Never commit audio files, transcripts, or `.env`.