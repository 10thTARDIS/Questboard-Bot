# Quest Board Improvement Backlog

Improvements to the Quest Board backend that would make the bot richer or more
resilient. Items here are not blocking — the bot works without them — but each
one is worth doing at some point.

---

## How to add an entry

Copy the template below, fill it in, and insert it in priority order (lowest
number first). Keep descriptions factual and brief. If an item is implemented,
delete it from this document rather than marking it done.

```markdown
## <Short title>

**Description:** What is missing or suboptimal, and what effect does it have
on the bot right now.

**How to add:** Which file(s) to change and roughly what the change is.
Keep this to 2–4 sentences — enough to act on without needing to re-research.

**Priority:** N / 5
```

---

## Session title missing from confirmed and reminder payloads

**Description:** The `send_session_confirmed` and `send_session_reminder` Celery
tasks do not include the session title or campaign name in the `extra` field of
the bot notify payload. The confirmed and reminder embeds therefore display a
generic "✅ Session Confirmed" / "⏰ Reminder" title with no session name, which
is less useful when a campaign has several sessions in flight.

**How to add:** In `backend/app/tasks/reminder_tasks.py`, add
`"title": session_title` and `"campaign_name": campaign_name` to the `extra`
dict in both the `send_session_confirmed` and `send_session_reminder` bot notify
calls. Both values are already present as task arguments — they just need
forwarding into `extra`. Update `_handle_confirmed` and `_handle_reminder` in
`bot/cogs/notifications.py` to read them from `extra` and include them in the
embed title.

**Priority:** 2 / 5

---

## Session title missing from timeslots API response

**Description:** `GET /api/bot/sessions/{session_id}/timeslots` returns
`campaign_name` and `game_system` but not the session `title` or `description`.
The notifications cog falls back to fetching this endpoint when session metadata
is not in the payload, but can still never display the session title this way.
The recording cog (v0.6.0) will also want the title when announcing that
recording has started.

**How to add:** In `backend/app/routers/bot.py`, add `title: str | None` and
`description: str | None` fields to `SessionTimeslotsResponse` and populate
them from `session.title` and `session.description` in the
`bot_session_timeslots` endpoint. Update `SessionTimeslotsResponse` in
`bot/api_client.py` to match.

**Priority:** 2 / 5

---

## Webhook fallback when bot is unreachable

**Description:** When a campaign has `guild_id` configured and the Celery task
successfully calls the bot, it returns early and skips the webhook. If the bot
HTTP server is down or the call fails with a non-retried exception, the
notification is silently dropped — no Discord message is sent at all.

**How to add:** In the `send_session_confirmed` and `send_session_reminder`
tasks in `backend/app/tasks/reminder_tasks.py`, wrap the bot HTTP call in a
try/except. On failure, log a warning and fall through to the existing webhook
code path (remove the early `return`). This makes the webhook a true fallback
rather than dead code once a campaign has the bot configured.

**Priority:** 2 / 5

---

## vote_update event never fired by Quest Board

**Description:** The bot has a `vote_update` embed handler, but Quest Board
never calls the bot's `/notify` endpoint with `event_type: "vote_update"`. The
existing `send_vote_notification` Celery task posts a plain text message to the
campaign webhook instead. As a result, the rich vote-count embed with per-slot
yes/maybe/no tallies is never displayed.

**How to add:** In `backend/app/tasks/reminder_tasks.py`, update
`send_vote_notification` with the same bot-routing pattern used by the other
tasks: if `guild_id` and `QUESTBOARD_BOT_URL` are set, call
`POST {bot_url}/notify` with `event_type: "vote_update"`. The `session_id`,
`campaign_id`, `guild_id`, and `channel_id` will need to be added as task
arguments; update the call site in `backend/app/services/vote_service.py`
(or wherever `send_vote_notification.delay(...)` is called) to pass them.

**Priority:** 3 / 5

---

## Bot-facing endpoint to remove a platform link

**Description:** The `/unlink` slash command stub (v0.4.0) currently redirects
users to their Quest Board profile page because there is no bot-facing API
endpoint to delete a platform link directly. A dedicated endpoint would let the
bot handle the full unlink flow in Discord without requiring a browser visit.

**How to add:** Add `DELETE /api/bot/platform-links/discord/{discord_user_id}`
to `backend/app/routers/bot.py` using `require_bot_auth`. The endpoint should
look up the `PlatformLink` by `(platform=discord, platform_user_id)`, verify it
belongs to a real user, delete it, and return `{"detail": "unlinked"}`. Update
the `/unlink` command in `bot/cogs/linking.py` to call this endpoint and confirm
success in a DM rather than redirecting to the web UI.

**Priority:** 3 / 5

---

## Notification when a session auto-completes

**Description:** Quest Board's Celery Beat task silently transitions sessions
from `confirmed` to `completed` once `confirmed_time` passes. The bot is never
told a session has ended, so it cannot prompt the GM to start a recording, post
a "session complete" message, or take any other post-session action automatically.

**How to add:** In `backend/app/tasks/reminder_tasks.py`, update
`_auto_complete_sessions_async` to call `send_session_completed.delay(...)` for
each session it transitions. Add a new `send_session_completed` task that calls
`POST {bot_url}/notify` with `event_type: "session_completed"`. Add a handler
for this event in `bot/cogs/notifications.py` — at minimum a brief embed noting
the session is over; in v0.6.0+ this could also prompt the GM to run `/record`
if a recording was not already started.

**Priority:** 4 / 5

---

## Guild-scoped next-session endpoint (required for bot v0.9.0)

**Description:** The bot's `/next` slash command needs to find the next confirmed
session for the Discord server it is invoked from. There is no bot-facing endpoint
that accepts a `guild_id` and returns the associated campaign's next session.

**How to add:** In `backend/app/routers/bot.py`, add
`GET /api/bot/guilds/{guild_id}/next-session` with `require_bot_auth`. Query
`Campaign` by `guild_id`, then find the next confirmed session where
`confirmed_time > now()` ordered ascending, limit 1. Return
`{ session_id, campaign_id, campaign_name, game_system, title, confirmed_time }`;
return 404 if no campaign has that `guild_id` or no upcoming session exists.

**Priority:** 2 / 5

---

## Session summary endpoint (required for bot v0.9.0)

**Description:** The bot's `/recap` command needs to fetch a specific session's
stored summary and GM notes. No bot-facing endpoint returns these fields today.

**How to add:** In `backend/app/routers/bot.py`, add
`GET /api/bot/sessions/{session_id}/summary` with `require_bot_auth`. Join
`Session → Campaign` and return
`{ session_id, campaign_name, title, confirmed_time, summary, session_notes }`.
Both `summary` and `session_notes` may be null.

**Priority:** 2 / 5

---

## Bot session note creation endpoint (required for bot v0.9.0)

**Description:** The bot's `/note` command needs to create a `SessionNote` on
behalf of a linked Discord user. There is no bot-facing endpoint for this today;
the existing `PUT /sessions/{id}/my-note` requires a user-scoped auth token.

**How to add:** In `backend/app/routers/bot.py`, add
`POST /api/bot/sessions/{session_id}/notes` with `require_bot_auth`.
Accept `{ discord_user_id: str, note: str }`. Resolve `discord_user_id` →
`user_id` via `PlatformLink` (return 404 if not linked). Upsert a `SessionNote`
with `visibility=private` for that user and session — if a note already exists,
append the new text on a new line rather than overwriting.

**Priority:** 2 / 5

---

## Session history endpoint for campaign Q&A (required for bot v0.10.0)

**Description:** The bot's `/ask` command builds a context window from past
session summaries to answer natural-language questions about campaign history.
No bot-facing endpoint returns a list of completed sessions with their summaries.

**How to add:** In `backend/app/routers/bot.py`, add
`GET /api/bot/guilds/{guild_id}/sessions/history?limit=N` with `require_bot_auth`.
Look up the campaign by `guild_id`, then return the most recent N sessions where
`summary IS NOT NULL` and `status = 'completed'`, ordered `confirmed_time DESC`,
as `[{ session_id, title, confirmed_time, summary }]`. Default `limit=10`, max 20.

**Priority:** 3 / 5
