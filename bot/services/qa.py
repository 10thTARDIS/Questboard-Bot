"""Campaign Q&A service.

Answers natural-language questions about campaign history using stored session
summaries as a context window (a lightweight RAG approach).

The LLM is explicitly instructed to answer only from the provided summaries
and to say so clearly when it does not have the information — the goal is a
grounded, trustworthy assistant, not a generative storyteller.

Uses the same backends and settings as summarisation.py (SUMMARISER_MODE,
OLLAMA_URL, OLLAMA_MODEL, ANTHROPIC_API_KEY, OPENAI_API_KEY).  No new
configuration is required.

Raises RuntimeError on any unrecoverable LLM failure.
"""

import logging
from datetime import timezone

import httpx

from bot.api_client import SessionHistoryItem

log = logging.getLogger(__name__)

_TIMEOUT = 300.0  # 5 minutes

_SYSTEM_PROMPT = (
    "You are a campaign knowledge assistant for a tabletop RPG group. "
    "Answer questions about the campaign's history using ONLY the session "
    "summaries provided below. "
    "If the answer cannot be determined from those summaries, say clearly "
    "that you don't have that information — do not invent or guess details. "
    "Be concise and specific."
)

_MAX_SUMMARY_CHARS = 800  # per session — keeps context within model limits


async def answer_question(
    question: str,
    sessions: list[SessionHistoryItem],
    campaign_name: str,
    game_system: str | None,
    settings,
) -> str:
    """Return an answer to *question* grounded in *sessions*.

    *settings* is the bot's `Settings` instance (bot/config.py).
    Raises RuntimeError on failure.
    """
    if not sessions:
        raise ValueError("answer_question requires at least one session summary")

    context = campaign_name + (f" ({game_system})" if game_system else "")
    log.info(
        "Answering Q&A question for %r using %d session(s)", context, len(sessions)
    )

    prompt = _build_prompt(question, sessions, context)
    mode = settings.summariser_mode

    if mode == "ollama":
        return await _query_ollama(prompt, settings.ollama_url, settings.ollama_model)
    else:
        if settings.anthropic_api_key:
            return await _query_anthropic(prompt, settings.anthropic_api_key)
        elif settings.openai_api_key:
            return await _query_openai(prompt, settings.openai_api_key)
        else:
            raise RuntimeError(
                "SUMMARISER_MODE=api requires either ANTHROPIC_API_KEY or OPENAI_API_KEY"
            )


def _build_prompt(
    question: str, sessions: list[SessionHistoryItem], context: str
) -> str:
    """Build the user-turn prompt from question and session summaries."""
    lines: list[str] = [
        f"Campaign: {context}",
        f"{len(sessions)} session summary/summaries (most recent first):",
        "",
    ]
    for i, s in enumerate(sessions, 1):
        title = s.title or "Untitled Session"
        dt = s.confirmed_time
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        date_str = dt.strftime("%Y-%m-%d")
        # Truncate long summaries to stay within typical context limits.
        summary_text = s.summary[:_MAX_SUMMARY_CHARS]
        if len(s.summary) > _MAX_SUMMARY_CHARS:
            summary_text += " [...]"
        lines.append(f"[Session {i}: {title} — {date_str}]")
        lines.append(summary_text)
        lines.append("")

    lines.append(f"Question: {question}")
    return "\n".join(lines)


async def _query_ollama(prompt: str, ollama_url: str, model: str) -> str:
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


async def _query_anthropic(prompt: str, api_key: str) -> str:
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
    return "".join(
        block.get("text", "") for block in content if block.get("type") == "text"
    ).strip()


async def _query_openai(prompt: str, api_key: str) -> str:
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
