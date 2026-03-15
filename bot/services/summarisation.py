"""Summarisation service.

Produces a concise session summary from a full transcript using either:

  SUMMARISER_MODE=ollama  →  Ollama local inference (default: http://ollama:11434).
                              POST /api/generate  {model, prompt, stream: false}

  SUMMARISER_MODE=api     →  Anthropic Claude or OpenAI Chat Completion API,
                              chosen by which key is set:
                                ANTHROPIC_API_KEY  → claude-haiku-4-5-20251001
                                OPENAI_API_KEY     → gpt-4o-mini

The system prompt asks for a structured summary suitable for a TTRPG campaign
log: key events, NPC introductions, player decisions, and a short "what's next"
hook.  The full transcript is never logged — only char counts.

Raises RuntimeError on any unrecoverable failure.
"""

import logging

import httpx

log = logging.getLogger(__name__)

_TIMEOUT = 300.0  # 5 minutes

_SYSTEM_PROMPT = (
    "You are a campaign scribe for a tabletop RPG group. "
    "Write a concise session summary (3–6 paragraphs) from the transcript below. "
    "Cover: key events and encounters, important NPC introductions or revelations, "
    "significant player decisions, and end with a short 'What's next?' hook. "
    "Use plain prose — no bullet lists. Do not repeat filler or crosstalk."
)


async def summarise(
    transcript: str,
    campaign_name: str,
    game_system: str | None,
    settings,
) -> str:
    """Return a summary string for *transcript*.

    *settings* is the bot's `Settings` instance (bot/config.py).
    Raises RuntimeError on failure.
    """
    mode = settings.summariser_mode
    context = f"{campaign_name}" + (f" ({game_system})" if game_system else "")
    log.info("Summarising transcript for %r (mode=%s, chars=%d)", context, mode, len(transcript))

    prompt = (
        f"Campaign: {context}\n\n"
        f"Transcript:\n{transcript}\n\n"
        f"Write the session summary now."
    )

    if mode == "ollama":
        summary = await _summarise_ollama(prompt, settings.ollama_url, settings.ollama_model)
    else:
        if settings.anthropic_api_key:
            summary = await _summarise_anthropic(prompt, settings.anthropic_api_key)
        elif settings.openai_api_key:
            summary = await _summarise_openai(prompt, settings.openai_api_key)
        else:
            raise RuntimeError(
                "SUMMARISER_MODE=api requires either ANTHROPIC_API_KEY or OPENAI_API_KEY"
            )

    log.info("Summary complete: %d chars", len(summary))
    return summary


async def _summarise_ollama(prompt: str, ollama_url: str, model: str) -> str:
    url = f"{ollama_url.rstrip('/')}/api/generate"
    body = {
        "model": model,
        "system": _SYSTEM_PROMPT,
        "prompt": prompt,
        "stream": False,
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(url, json=body)

    if resp.status_code != 200:
        raise RuntimeError(f"Ollama returned {resp.status_code}: {resp.text[:200]}")

    return resp.json().get("response", "").strip()


async def _summarise_anthropic(prompt: str, api_key: str) -> str:
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 1024,
        "system": _SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(url, headers=headers, json=body)

    if resp.status_code != 200:
        raise RuntimeError(f"Anthropic API returned {resp.status_code}: {resp.text[:200]}")

    content = resp.json().get("content", [])
    return "".join(block.get("text", "") for block in content if block.get("type") == "text").strip()


async def _summarise_openai(prompt: str, api_key: str) -> str:
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 1024,
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(url, headers=headers, json=body)

    if resp.status_code != 200:
        raise RuntimeError(f"OpenAI API returned {resp.status_code}: {resp.text[:200]}")

    choices = resp.json().get("choices", [])
    if not choices:
        raise RuntimeError("OpenAI returned no choices")
    return choices[0]["message"]["content"].strip()
