"""Async httpx client for the Quest Board API.

All bot → Quest Board calls go through this module.
Auth: X-Bot-Key header (shared secret, must match Quest Board's bot_api_key
app_setting).

Quest Board base URL: QUESTBOARD_API_URL (e.g. http://questboard-backend:8000)
All bot endpoints are mounted at /api/bot/ in Quest Board.
"""

import uuid
from datetime import datetime
from typing import Literal

import httpx
from pydantic import BaseModel


# ── Response models ───────────────────────────────────────────────────────────


class UpcomingSession(BaseModel):
    session_id: uuid.UUID
    campaign_id: uuid.UUID
    campaign_name: str
    title: str | None
    confirmed_time: datetime
    webhook_url: str | None

    model_config = {"from_attributes": True}


class LinkedUser(BaseModel):
    user_id: uuid.UUID
    display_name: str
    discord_user_id: str

    model_config = {"from_attributes": True}


class TimeSlotVoteCounts(BaseModel):
    yes: int = 0
    maybe: int = 0
    no: int = 0


class TimeSlotItem(BaseModel):
    slot_id: uuid.UUID
    proposed_time: datetime
    vote_counts: TimeSlotVoteCounts


class SessionTimeslotsResponse(BaseModel):
    """Returned by GET /api/bot/sessions/{session_id}/timeslots (added in v0.2.0)."""

    session_id: uuid.UUID
    campaign_name: str
    game_system: str | None
    reminder_offsets_minutes: list[int] | None
    slots: list[TimeSlotItem]


class PlatformLinkResponse(BaseModel):
    """Returned by GET /api/bot/platform-links/{platform}/{id} (added in v0.2.0)."""

    user_id: uuid.UUID
    display_name: str


class LinkStatusResponse(BaseModel):
    """Returned by GET /api/bot/link-status/{token} (added in v0.2.0)."""

    linked: bool
    user_id: uuid.UUID | None = None


class BotSettingsResponse(BaseModel):
    """Returned by GET /api/bot/settings (added in v0.2.0).

    All fields are optional — only set fields are returned.
    """

    whisper_mode: str | None = None
    whisper_api_url: str | None = None
    openai_api_key: str | None = None
    summariser_mode: str | None = None
    ollama_url: str | None = None
    ollama_model: str | None = None
    anthropic_api_key: str | None = None


# ── Client ────────────────────────────────────────────────────────────────────


class QuestBoardClient:
    """Typed async client for all bot → Quest Board API calls.

    Usage:
        client = QuestBoardClient(settings.questboard_api_url, settings.bot_api_key)
        # … use client methods …
        await client.close()

    Or as an async context manager:
        async with QuestBoardClient(...) as client:
            sessions = await client.get_upcoming_sessions()
    """

    def __init__(self, base_url: str, api_key: str) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"X-Bot-Key": api_key},
            timeout=30.0,
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "QuestBoardClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    # ── Existing Quest Board endpoints (available from Quest Board v0.1.1) ─────

    async def get_upcoming_sessions(self) -> list[UpcomingSession]:
        """Return confirmed sessions in the next 7 days."""
        resp = await self._http.get("/api/bot/sessions/upcoming")
        resp.raise_for_status()
        return [UpcomingSession.model_validate(s) for s in resp.json()]

    async def get_linked_users(self, campaign_id: uuid.UUID) -> list[LinkedUser]:
        """Return all campaign members who have a Discord platform link."""
        resp = await self._http.get(
            f"/api/bot/campaigns/{campaign_id}/linked-users"
        )
        resp.raise_for_status()
        return [LinkedUser.model_validate(u) for u in resp.json()]

    async def put_vote(
        self,
        session_id: uuid.UUID,
        slot_id: uuid.UUID,
        discord_user_id: str,
        availability: Literal["yes", "maybe", "no"],
    ) -> dict:
        """Submit a vote on behalf of a Discord user."""
        resp = await self._http.put(
            f"/api/bot/sessions/{session_id}/timeslots/{slot_id}/vote",
            json={"discord_user_id": discord_user_id, "availability": availability},
        )
        resp.raise_for_status()
        return resp.json()

    async def put_attendance(
        self,
        session_id: uuid.UUID,
        discord_user_id: str,
        attended: bool,
    ) -> dict:
        """Mark a Discord user as attended/not attended for a session."""
        resp = await self._http.put(
            f"/api/bot/sessions/{session_id}/attendance/{discord_user_id}",
            json={"attended": attended},
        )
        resp.raise_for_status()
        return resp.json()

    async def post_transcript(
        self,
        session_id: uuid.UUID,
        transcript: str,
        summary: str,
        recording_url: str | None = None,
    ) -> dict:
        """Upload a transcript and summary for a completed session."""
        body: dict = {"transcript": transcript, "summary": summary}
        if recording_url is not None:
            body["recording_url"] = recording_url
        resp = await self._http.post(
            f"/api/bot/sessions/{session_id}/transcript",
            json=body,
        )
        resp.raise_for_status()
        return resp.json()

    # ── Endpoints added in Quest Board v0.2.0 ─────────────────────────────────

    async def get_session_timeslots(
        self, session_id: uuid.UUID
    ) -> SessionTimeslotsResponse:
        """Return time slots with vote counts, campaign metadata, and reminder schedule."""
        resp = await self._http.get(f"/api/bot/sessions/{session_id}/timeslots")
        resp.raise_for_status()
        return SessionTimeslotsResponse.model_validate(resp.json())

    async def get_platform_link(
        self, platform: str, platform_user_id: str
    ) -> PlatformLinkResponse | None:
        """Resolve a platform user ID to a Quest Board user.

        Returns None if not linked (404 response).
        """
        resp = await self._http.get(
            f"/api/bot/platform-links/{platform}/{platform_user_id}"
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return PlatformLinkResponse.model_validate(resp.json())

    async def get_link_status(self, token: str) -> LinkStatusResponse:
        """Check whether a linking token has been consumed.

        The token is deleted from Redis on first successful read (GETDEL).
        """
        resp = await self._http.get(f"/api/bot/link-status/{token}")
        resp.raise_for_status()
        return LinkStatusResponse.model_validate(resp.json())

    async def get_bot_settings(self) -> BotSettingsResponse:
        """Return admin-configured Whisper/LLM settings from Quest Board."""
        resp = await self._http.get("/api/bot/settings")
        resp.raise_for_status()
        return BotSettingsResponse.model_validate(resp.json())

    async def post_linking_token(
        self, token: str, discord_user_id: str
    ) -> dict:
        """Register a one-time linking token in Quest Board's Redis.

        The token is stored as discord_link:<token> with a 10-minute TTL.
        Quest Board's /auth/link endpoint consumes it when the user clicks the link.
        """
        resp = await self._http.post(
            "/api/bot/linking-tokens",
            json={"token": token, "discord_user_id": discord_user_id},
        )
        resp.raise_for_status()
        return resp.json()
