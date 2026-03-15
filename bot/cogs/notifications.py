"""Notifications cog — receives POST /notify events from Quest Board.

The aiohttp /notify endpoint in main.py validates the X-Bot-Key header and
dispatches on_bot_notify with the parsed payload dict. This cog handles that
event, formats a rich Discord embed, and posts it to the campaign's channel.

For session_proposed events, seed emoji reactions (🇦–🇪) are added immediately
after posting, and the message_id → session mapping is stored in Redis (with an
in-memory dict as fallback) so the voting cog can resolve reactions back to
time slots without a round-trip to Quest Board.

Payload shape from Quest Board (all events):
    {
        "event_type": "session_proposed" | "session_confirmed"
                     | "session_reminder" | "session_cancelled" | "vote_update",
        "session_id": "<UUID>",
        "campaign_id": "<UUID>",
        "guild_id": "<snowflake string>",
        "channel_id": "<snowflake string>",
        "extra": { ... event-specific fields ... }
    }

extra fields by event_type:
    session_proposed  — slot_ids: list[str], title: str, campaign_name: str
    session_confirmed — confirmed_time: ISO-8601 str
    session_reminder  — confirmed_time: ISO-8601 str, hours_until: float
    session_cancelled — title: str, campaign_name: str
    vote_update       — (no extra fields required; bot fetches from API)
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

import discord
from discord.ext import commands

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# Regional indicator letters A–E, one per time slot.
_SLOT_EMOJIS = ["🇦", "🇧", "🇨", "🇩", "🇪"]

# Attendance RSVP reactions seeded on session_confirmed embeds.
_ATTENDANCE_EMOJIS = ["✅", "❌"]

_MSG_MAP_PREFIX = "qb_msg:"
_MSG_MAP_TTL = 30 * 24 * 3600  # 30 days — long enough to cover any voting window

# Embed accent colours.
_COLOR_PROPOSED = 0x5865F2   # Discord blurple
_COLOR_CONFIRMED = 0x57F287  # Green
_COLOR_REMINDER = 0xF0A500   # Amber
_COLOR_CANCELLED = 0xED4245  # Red


# ── Cog ───────────────────────────────────────────────────────────────────────


class NotificationsCog(commands.Cog, name="Notifications"):
    """Posts rich Discord embeds for Quest Board session events.

    Also exposes get_message_mapping() for the voting cog to resolve
    reaction events back to session IDs and time slot IDs.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # In-memory fallback when Redis is not configured or unavailable.
        self._message_map: dict[int, dict] = {}
        self._redis = None  # lazily initialised on first use

    # ── Redis helpers ──────────────────────────────────────────────────────────

    async def _get_redis(self):
        """Return a connected Redis client, or None if unavailable."""
        if self._redis is not None:
            return self._redis
        redis_url = self.bot.settings.redis_url
        if not redis_url:
            return None
        try:
            import redis.asyncio as aioredis
            r = aioredis.from_url(redis_url, decode_responses=True)
            await r.ping()
            self._redis = r
            log.info("Notifications cog connected to Redis for message mapping.")
            return r
        except Exception as exc:
            log.warning(
                "Redis unavailable for message mapping, using in-memory fallback: %s", exc
            )
            return None

    async def _store_message_map(
        self,
        message_id: int,
        session_id: str,
        slot_order: list[str],
        map_type: str = "voting",
    ) -> None:
        """Persist message_id → {session_id, slot_order, type} mapping.

        map_type is "voting" for session_proposed messages and "attendance"
        for session_confirmed messages.  The voting cog reads this field to
        decide how to handle reactions on each message.
        """
        payload = json.dumps(
            {"session_id": session_id, "slot_order": slot_order, "type": map_type}
        )
        r = await self._get_redis()
        if r:
            try:
                await r.setex(f"{_MSG_MAP_PREFIX}{message_id}", _MSG_MAP_TTL, payload)
                return
            except Exception as exc:
                log.warning("Redis write failed, storing in memory instead: %s", exc)
        self._message_map[message_id] = {
            "session_id": session_id, "slot_order": slot_order, "type": map_type
        }

    async def get_message_mapping(self, message_id: int) -> dict | None:
        """Return {session_id, slot_order} for a message_id, or None if unknown.

        Called by the voting cog on every reaction event.
        """
        r = await self._get_redis()
        if r:
            try:
                raw = await r.get(f"{_MSG_MAP_PREFIX}{message_id}")
                if raw:
                    return json.loads(raw)
            except Exception as exc:
                log.warning("Redis read failed, checking in-memory fallback: %s", exc)
        return self._message_map.get(message_id)

    # ── Main dispatcher ────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_bot_notify(self, payload: dict) -> None:
        """Route an incoming notify payload to the appropriate embed handler."""
        event_type = payload.get("event_type", "")
        channel_id_str = payload.get("channel_id", "")
        session_id = payload.get("session_id", "")
        extra = payload.get("extra") or {}

        if not channel_id_str:
            log.warning("Notify payload missing channel_id (event_type=%s)", event_type)
            return

        _handlers = {
            "session_proposed": self._handle_proposed,
            "session_confirmed": self._handle_confirmed,
            "session_reminder": self._handle_reminder,
            "session_cancelled": self._handle_cancelled,
            "vote_update": self._handle_vote_update,
        }
        handler = _handlers.get(event_type)
        if handler is None:
            log.warning("Unknown event_type received: %r", event_type)
            return

        try:
            channel = await self._resolve_channel(channel_id_str)
            if channel is None:
                return
            await handler(channel, session_id, extra)
        except Exception:
            log.exception(
                "Unhandled error in %s handler (session_id=%s)", event_type, session_id
            )

    # ── Channel resolution ─────────────────────────────────────────────────────

    async def _resolve_channel(self, channel_id_str: str) -> discord.TextChannel | None:
        """Fetch the TextChannel by ID, logging clearly if it cannot be reached."""
        try:
            channel_id = int(channel_id_str)
        except ValueError:
            log.warning("channel_id is not a valid integer: %r", channel_id_str)
            return None

        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.NotFound:
                log.warning("Channel %s not found — bot may not be in this server.", channel_id)
                return None
            except discord.Forbidden:
                log.warning("No access to channel %s.", channel_id)
                return None

        if not isinstance(channel, discord.TextChannel):
            log.warning("Channel %s is not a text channel (type=%s).", channel_id, type(channel))
            return None

        return channel

    # ── Event handlers ─────────────────────────────────────────────────────────

    async def _handle_proposed(
        self, channel: discord.TextChannel, session_id: str, extra: dict
    ) -> None:
        """Post a session_proposed embed with time-slot fields and seed reactions."""
        slot_ids: list[str] = extra.get("slot_ids") or []
        title = extra.get("title") or "Untitled Session"
        campaign_name = extra.get("campaign_name") or "Quest Board"

        if len(slot_ids) > len(_SLOT_EMOJIS):
            log.warning(
                "Session %s has %d slots but only %d emoji reactions will be seeded "
                "(slots beyond index %d cannot receive votes).",
                session_id, len(slot_ids), len(_SLOT_EMOJIS), len(_SLOT_EMOJIS) - 1,
            )

        embed = discord.Embed(title=f"📋 New Session Proposed — {title}", color=_COLOR_PROPOSED)
        embed.set_footer(text=campaign_name)

        # Fetch slot details so we can show proposed times in the embed.
        ordered_slot_ids = slot_ids
        try:
            timeslots = await self.bot.api.get_session_timeslots(uuid.UUID(session_id))
            campaign_name = timeslots.campaign_name or campaign_name
            embed.set_footer(text=campaign_name)

            slot_map = {str(s.slot_id): s for s in timeslots.slots}
            ordered_slots = [slot_map[sid] for sid in slot_ids if sid in slot_map]

            for i, slot in enumerate(ordered_slots):
                emoji = _SLOT_EMOJIS[i]
                ts = int(slot.proposed_time.replace(tzinfo=timezone.utc).timestamp()
                         if slot.proposed_time.tzinfo is None
                         else slot.proposed_time.timestamp())
                embed.add_field(
                    name=f"{emoji} Option {chr(ord('A') + i)}",
                    value=f"<t:{ts}:F>\n<t:{ts}:R>",
                    inline=True,
                )
        except Exception as exc:
            log.warning(
                "Could not fetch timeslots for session_proposed (session_id=%s): %s",
                session_id, exc,
            )
            embed.description = (
                f"{len(slot_ids)} time slot(s) available — react to vote!"
                if slot_ids
                else "React to vote on the proposed times."
            )

        embed.add_field(
            name="How to vote",
            value="React with the emoji for your preferred time. You may vote for multiple slots.",
            inline=False,
        )

        message = await channel.send(embed=embed)

        # Seed one reaction per slot, in order.
        for i in range(min(len(ordered_slot_ids), len(_SLOT_EMOJIS))):
            await message.add_reaction(_SLOT_EMOJIS[i])
            await asyncio.sleep(0.5)  # stay comfortably inside Discord rate limits

        await self._store_message_map(message.id, session_id, ordered_slot_ids)
        log.info(
            "Posted session_proposed (session_id=%s message_id=%d slots=%d)",
            session_id, message.id, len(ordered_slot_ids),
        )

    async def _handle_confirmed(
        self, channel: discord.TextChannel, session_id: str, extra: dict
    ) -> None:
        """Post a session_confirmed embed with attendance RSVP reactions."""
        embed = discord.Embed(title="✅ Session Confirmed", color=_COLOR_CONFIRMED)
        _add_time_field(embed, extra.get("confirmed_time"), "When")
        embed.add_field(
            name="Attendance",
            value="React ✅ if you'll be there, ❌ if you can't make it.",
            inline=False,
        )

        # Enrich with campaign name from the API.
        try:
            timeslots = await self.bot.api.get_session_timeslots(uuid.UUID(session_id))
            embed.set_footer(text=timeslots.campaign_name)
        except Exception:
            embed.set_footer(text="Quest Board")

        message = await channel.send(embed=embed)

        # Seed RSVP reactions and store the message → session mapping so the
        # voting cog can call put_attendance when players react.
        for emoji in _ATTENDANCE_EMOJIS:
            await message.add_reaction(emoji)
            await asyncio.sleep(0.5)

        await self._store_message_map(message.id, session_id, [], map_type="attendance")
        log.info("Posted session_confirmed (session_id=%s message_id=%d)", session_id, message.id)

    async def _handle_reminder(
        self, channel: discord.TextChannel, session_id: str, extra: dict
    ) -> None:
        """Post a session_reminder embed."""
        hours_until = extra.get("hours_until") or 0
        label = _reminder_label(hours_until)

        embed = discord.Embed(
            title=f"⏰ Reminder — {label} until the session!",
            color=_COLOR_REMINDER,
        )
        _add_time_field(embed, extra.get("confirmed_time"), "When")

        try:
            timeslots = await self.bot.api.get_session_timeslots(uuid.UUID(session_id))
            embed.set_footer(text=timeslots.campaign_name)
        except Exception:
            embed.set_footer(text="Quest Board")

        await channel.send(embed=embed)
        log.info(
            "Posted session_reminder (session_id=%s hours_until=%s)", session_id, hours_until
        )

    async def _handle_cancelled(
        self, channel: discord.TextChannel, session_id: str, extra: dict
    ) -> None:
        """Post a session_cancelled embed."""
        title = extra.get("title") or "Untitled Session"
        campaign_name = extra.get("campaign_name") or "Quest Board"

        embed = discord.Embed(
            title=f"❌ Session Cancelled — {title}",
            color=_COLOR_CANCELLED,
        )
        embed.set_footer(text=campaign_name)

        await channel.send(embed=embed)
        log.info("Posted session_cancelled (session_id=%s)", session_id)

    async def _handle_vote_update(
        self, channel: discord.TextChannel, session_id: str, extra: dict
    ) -> None:
        """Post a vote_update embed showing current counts for all slots."""
        try:
            timeslots = await self.bot.api.get_session_timeslots(uuid.UUID(session_id))
        except Exception as exc:
            log.warning(
                "Could not fetch timeslots for vote_update (session_id=%s): %s",
                session_id, exc,
            )
            return

        embed = discord.Embed(title="🗳️ Vote Update", color=_COLOR_PROPOSED)
        embed.set_footer(text=timeslots.campaign_name)

        for i, slot in enumerate(timeslots.slots):
            emoji = _SLOT_EMOJIS[i] if i < len(_SLOT_EMOJIS) else f"Option {i + 1}"
            ts = int(slot.proposed_time.replace(tzinfo=timezone.utc).timestamp()
                     if slot.proposed_time.tzinfo is None
                     else slot.proposed_time.timestamp())
            vc = slot.vote_counts
            embed.add_field(
                name=f"{emoji}  <t:{ts}:F>",
                value=f"✅ {vc.yes}  🤔 {vc.maybe}  ❌ {vc.no}",
                inline=True,
            )

        await channel.send(embed=embed)
        log.info("Posted vote_update (session_id=%s slots=%d)", session_id, len(timeslots.slots))


# ── Module-level helpers ───────────────────────────────────────────────────────


def _reminder_label(hours_until: float) -> str:
    """Return a human-readable label for a reminder offset, e.g. '24 hours', '7 days'."""
    if hours_until >= 48:
        days = round(hours_until / 24)
        return f"{days} day{'s' if days != 1 else ''}"
    if hours_until >= 1:
        h = round(hours_until)
        return f"{h} hour{'s' if h != 1 else ''}"
    minutes = round(hours_until * 60)
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


def _add_time_field(embed: discord.Embed, iso_str: str | None, name: str) -> None:
    """Add a Discord-timestamp field to an embed, or skip if the time is missing/invalid."""
    if not iso_str:
        return
    try:
        dt = datetime.fromisoformat(iso_str)
        ts = int(dt.timestamp())
        embed.add_field(name=name, value=f"<t:{ts}:F>\n<t:{ts}:R>", inline=False)
    except (ValueError, OSError):
        embed.add_field(name=name, value=iso_str, inline=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(NotificationsCog(bot))
