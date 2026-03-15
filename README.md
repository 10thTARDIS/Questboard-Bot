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
| v0.6.0 | Recording & transcription | 🔜 Next |
| v0.7.0 | Polish & hardening | 🔜 Planned |

## Prerequisites

- Docker and Docker Compose
- A running [Quest Board](https://github.com/10thTARDIS/Questboard) instance (v0.1.1+)
- A Discord application with a bot token
  (see [Discord Developer Portal setup](#discord-developer-portal-setup) below)
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

## Discord Developer Portal Setup

Full walkthrough coming in v0.7.0. Quick reference:

1. [discord.com/developers/applications](https://discord.com/developers/applications) → New Application
2. **Bot** tab → enable **Message Content Intent** and **Server Members Intent**
3. **OAuth2 → URL Generator** → scopes: `bot` + `applications.commands`
   → permissions: Read Messages, Send Messages, Add Reactions,
   Send Messages in Threads, Connect, Speak
4. Invite the bot to your server via the generated URL
5. Copy the bot token to `DISCORD_BOT_TOKEN` in your `.env`

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt

# Re-pin dependencies after changing requirements.in
pip-compile requirements.in --output-file requirements.txt

# Security audit
pip-audit -r requirements.txt
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
  services/transcription.py  ← Whisper (local or OpenAI API)
  services/summarisation.py  ← Ollama or Anthropic/OpenAI API
        │
        ▼
Quest Board API  (BOT_API_KEY auth via X-Bot-Key header)
```

## License

MIT — see [LICENSE](LICENSE).
