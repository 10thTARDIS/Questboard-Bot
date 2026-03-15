# Quest Board Bot

A Discord bot companion for [Quest Board](https://github.com/10thTARDIS/Questboard),
the TTRPG session scheduler. It replaces plain webhook notifications with rich
interactive Discord messages, records emoji reactions as scheduling votes, and
joins voice channels to record, transcribe, and summarise game sessions.

## Status

| Version | Feature | Status |
|---|---|---|
| v0.1.0 | Scaffold & API client | ✅ Done |
| v0.2.0 | Quest Board additions | ✅ Done |
| v0.3.0 | Discord notifications | ✅ Done |
| v0.4.0 | Account linking | ✅ Done |
| v0.5.0 | Reaction voting | ✅ Done |
| v0.6.0 | Recording & transcription | ✅ Done |
| v0.7.0 | Polish & hardening | ✅ Done |

## Prerequisites

- Docker and Docker Compose
- A running [Quest Board](https://github.com/10thTARDIS/Questboard) instance (v0.2.0+)
- A Discord application with a bot token (see [Discord Developer Portal Setup](#discord-developer-portal-setup) below)
- Optional: A running [Ollama](https://ollama.ai) instance for local LLM summarisation

## Quick Start

```bash
# 1. Copy the example env file
cp .env.example .env

# 2. Fill in required values (see Configuration below)
$EDITOR .env

# 3. Start the bot
docker compose up -d
```

## Configuration

All settings are documented in [.env.example](.env.example).

**Required:**
- `DISCORD_BOT_TOKEN` — from Discord Developer Portal → Bot → Token
- `BOT_API_KEY` — generate with `openssl rand -hex 32`; set the same value
  in Quest Board Admin → Bot Settings → Bot API Key

**In Quest Board's `.env`:**
- `QUESTBOARD_BOT_URL=http://<bot-host>:8080` — the bot's HTTP server address

**Optional overrides** (can also be set in Quest Board Admin → Bot Settings):
- `WHISPER_MODE` — `local` (default, uses bundled Whisper container) or `api` (OpenAI)
- `SUMMARISER_MODE` — `ollama` (default) or `api` (Anthropic/OpenAI)
- `REDIS_URL` — enables persistent message→session mapping across bot restarts

## Discord Developer Portal Setup

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications) → **New Application**
2. Name it (e.g. "Quest Board Bot") and click **Create**
3. **Bot** tab:
   - Click **Add Bot** → **Yes, do it!**
   - Under **Privileged Gateway Intents**, enable:
     - ✅ **Server Members Intent**
     - ✅ **Message Content Intent**
   - Click **Reset Token** → copy the token to `DISCORD_BOT_TOKEN` in `.env`
4. **OAuth2 → URL Generator**:
   - Scopes: `bot` + `applications.commands`
   - Bot permissions: **Read Messages/View Channels**, **Send Messages**,
     **Send Messages in Threads**, **Add Reactions**, **Connect**, **Speak**
   - Copy the generated URL and open it in a browser to invite the bot to your server

## Quest Board Admin Setup

After starting the bot, configure it in Quest Board's admin panel:

1. **Admin → Bot Settings**:
   - Paste your `BOT_API_KEY` into **Bot API Key**
   - Optionally configure Whisper and LLM endpoints (overrides `.env` values)

2. **Campaign Settings** (per campaign):
   - **Guild ID** — your Discord server's ID (right-click server icon → Copy Server ID,
     requires Developer Mode enabled in Discord Settings → Advanced)
   - **Notification Channel ID** — the text channel where session embeds should appear
     (right-click channel → Copy Channel ID)

Once both are set, Quest Board will route session notifications through the bot
instead of plain webhooks.

## Account Linking

Players connect their Discord identity to Quest Board so reactions are recorded
as votes:

1. Run `/link` in any server channel where the bot is present
2. Click the link in the DM the bot sends (expires in 10 minutes)
3. Log in to Quest Board if prompted, then confirm
4. The bot sends a confirmation DM — reactions now count as votes

## Reaction Voting

Once linked, players vote on proposed session times by reacting to the session
embed with the regional indicator emojis (🇦, 🇧, 🇨 …). Adding a reaction
records a **yes** vote; removing it records a **no** vote.

## Recording a Session

Only the GM needs to run these commands:

```
/record start <session_id>   — join your voice channel and begin recording
/record stop                 — stop, transcribe, summarise, and upload
```

The `session_id` is the UUID shown in the Quest Board session URL. The bot
auto-stops after `MAX_RECORDING_HOURS` (default: 6) if `/record stop` is
not run manually.

After stopping, the bot:
1. Mixes all participants' audio to a single mono MP3
2. Transcribes it (Whisper local or OpenAI API)
3. Summarises it (Ollama or Anthropic/OpenAI)
4. Uploads the transcript and summary to Quest Board
5. Posts a summary embed in the text channel

### Whisper model trade-offs

| Model | Speed | Accuracy | RAM |
|---|---|---|---|
| `base` | Fastest | Good for clear audio | ~1 GB |
| `small` | Moderate | Better with accents | ~2 GB |
| `medium` | Slow | Most accurate | ~5 GB |

Set via `ASR_MODEL` in `docker-compose.yml` (whisper service). `base` is the
default and works well for most home gaming groups.

### Ollama model recommendations

| Model | Notes |
|---|---|
| `llama3` | Good general-purpose default |
| `mistral` | Fast, good for shorter sessions |
| `mixtral` | Higher quality, needs 16 GB+ VRAM |

Set via `OLLAMA_MODEL` in `.env` or Quest Board Admin → Bot Settings.

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt

# Install pre-commit hooks (runs pip-audit + linters on commit)
pre-commit install

# Re-pin dependencies after editing requirements.in
pip-compile requirements.in --output-file requirements.txt

# Manual security audit
pip-audit -r requirements.txt

# Run tests
pytest
```

## Architecture

See [BotBuildPlan.md](BotBuildPlan.md) for the full design specification.

```
Quest Board Backend
  │  Celery task fires on session event
  │  POST /notify  →  Bot HTTP server (:8080)
  ▼
questboard-bot
  ├── cogs/notifications.py  ← rich embeds + seed reactions
  ├── cogs/voting.py         ← reaction add/remove → votes API
  ├── cogs/linking.py        ← /link /unlink slash commands
  └── cogs/recording.py      ← /record start|stop + voice pipeline
        │
        ▼
  services/transcription.py  ← Whisper (local container or OpenAI API)
  services/summarisation.py  ← Ollama, Anthropic, or OpenAI
        │
        ▼
Quest Board API  (BOT_API_KEY auth via X-Bot-Key header)
```

## Troubleshooting

**Bot connects but no notifications appear**
- Confirm `QUESTBOARD_BOT_URL` is set in Quest Board's `.env` and points to the bot's host/port
- Confirm the campaign has `Guild ID` and `Notification Channel ID` set
- Check `BOT_API_KEY` matches in both `.env` files
- Check `docker compose logs bot` for any `401 Unauthorized` or connection errors

**Reactions aren't being recorded as votes**
- The user must run `/link` first — the bot will DM them a prompt if they react unlinked
- Confirm `REDIS_URL` is set if you want vote mappings to survive bot restarts

**`/record start` says "You need to be in a voice channel"**
- The GM running the command must already be connected to a voice channel in the server

**Whisper transcription times out or returns empty**
- Check `docker compose logs whisper` — the container may still be loading the model
- Try a smaller `ASR_MODEL` (e.g. `base`) if RAM is limited
- For long sessions, the 10-minute transcription timeout may need increasing in
  `bot/services/transcription.py` (`_TIMEOUT`)

**Ollama summarisation fails**
- Confirm Ollama is running and the model is pulled: `ollama pull llama3`
- Check `OLLAMA_URL` points to the correct host

## License

MIT — see [LICENSE](LICENSE).
