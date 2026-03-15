"""Transcription service.

Converts an MP3 file to text using either:

  WHISPER_MODE=local  →  POST to a local ahmetoner/whisper-asr-webservice
                          container (default: http://whisper:9000).
                          Endpoint: POST /asr?encode=true&task=transcribe&output=txt
                          Body: multipart/form-data, field "audio_file".

  WHISPER_MODE=api    →  OpenAI Whisper API (POST /v1/audio/transcriptions).
                          Requires OPENAI_API_KEY.

The caller receives the full transcript as a plain string.  This module never
logs transcript content — only metadata (file path, duration hint, char count).

Raises RuntimeError on any unrecoverable failure so the recording cog can
report the error to Discord and clean up temp files.
"""

import logging
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

# Generous timeout — large files can take a while to transcribe.
_TIMEOUT = 600.0  # 10 minutes


async def transcribe(mp3_path: Path, settings) -> str:
    """Return the transcript of *mp3_path* as a plain string.

    *settings* is the bot's `Settings` instance (bot/config.py).
    Raises RuntimeError on failure.
    """
    mode = settings.whisper_mode
    log.info("Transcribing %s (mode=%s)", mp3_path.name, mode)

    if mode == "local":
        return await _transcribe_local(mp3_path, settings.whisper_api_url)
    else:
        return await _transcribe_openai(mp3_path, settings.openai_api_key)


async def _transcribe_local(mp3_path: Path, whisper_url: str) -> str:
    """POST to the local Whisper ASR container."""
    url = f"{whisper_url.rstrip('/')}/asr"
    params = {"encode": "true", "task": "transcribe", "output": "txt"}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        with mp3_path.open("rb") as fh:
            resp = await client.post(
                url,
                params=params,
                files={"audio_file": (mp3_path.name, fh, "audio/mpeg")},
            )

    if resp.status_code != 200:
        raise RuntimeError(
            f"Whisper container returned {resp.status_code}: {resp.text[:200]}"
        )

    transcript = resp.text.strip()
    log.info("Transcription complete: %d chars", len(transcript))
    return transcript


async def _transcribe_openai(mp3_path: Path, api_key: str) -> str:
    """POST to the OpenAI Whisper API."""
    if not api_key:
        raise RuntimeError("WHISPER_MODE=api requires OPENAI_API_KEY to be set")

    url = "https://api.openai.com/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {api_key}"}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        with mp3_path.open("rb") as fh:
            resp = await client.post(
                url,
                headers=headers,
                data={"model": "whisper-1"},
                files={"file": (mp3_path.name, fh, "audio/mpeg")},
            )

    if resp.status_code != 200:
        raise RuntimeError(
            f"OpenAI Whisper API returned {resp.status_code}: {resp.text[:200]}"
        )

    transcript = resp.json().get("text", "").strip()
    log.info("Transcription complete: %d chars", len(transcript))
    return transcript
