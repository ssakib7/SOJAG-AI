"""Runtime conversation state: the port of store.js + the session logic in server.js.

At runtime everything lives in memory (dicts below); Postgres holds snapshots so a
restart/redeploy resumes active chats, pending follow-up nudges, and the roster of
captured customers. The flush loop writes only dirty entries every 10s; a final flush
runs on shutdown. When MEMORY_ENABLED=false nothing is persisted and no customer is
recalled — every chat starts fresh.

Session shape (a plain dict, JSON-serializable — the live follow-up task is never
serialized; ops/followups re-arms from last_active on boot):
  { history: [{role: "user"|"model", parts: [{text}]}],   # legacy Gemini shape, kept for
    last_active: ms,                                       # painless migration from bot.db.json
    lead: {captured, name, asked, msg_count},
    followup_sent: bool, returning: bool, customer_name: str|None,
    recent_mids: [{mid, text}], payments: [key], non_text_prompted_at: ms,
    closed: bool }                                         # force-stopped by the off-topic rule
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import delete, select

from app.config import get_settings
from app.db.engine import db_session
from app.db.models import CustomerRow, RecentRow, SessionRow

# Forget a conversation after this much silence. Ad traffic is full of customers who ask
# a price, go and think about it, and come back after dinner — 30 minutes threw away the
# context (and the contact-ask counter) for exactly the leads worth keeping.
SESSION_TTL_MS = 4 * 60 * 60 * 1000

# How long the bot stays quiet after a colleague replies from the Page inbox. Long enough
# to cover a real back-and-forth, short enough that a customer who returns tomorrow is
# served instead of ignored. Refreshed on every further human reply.
HUMAN_TAKEOVER_MS = 8 * 60 * 60 * 1000

# How long a force-stop counts toward the auto-block. Without a window, three misfires
# spread over three months would block a real customer as surely as three in ten minutes.
OFF_TOPIC_CLOSE_WINDOW_MS = 24 * 60 * 60 * 1000

# senderId -> timestamps of recent force-stops. Each close expires with its session, so a
# repeat offender is someone who came back and did it again. Deliberately in-memory and
# never persisted: a restart forgives, which is the right bias for a heuristic that can
# misfire on a real customer. Cleared once they are blocked.
off_topic_closes: dict[str, list[int]] = {}


def _recent_closes(sender_id: str) -> list[int]:
    now = now_ms()
    return [t for t in off_topic_closes.get(sender_id, []) if now - t < OFF_TOPIC_CLOSE_WINDOW_MS]
MAX_HISTORY = 10  # last 10 messages (~5 exchanges) per customer
RECENTS_TTL_MS = 7 * 24 * 60 * 60 * 1000
RECENTS_MAX = 1000
RECENT_MIDS_MAX = 20

now_ms = lambda: int(time.time() * 1000)  # noqa: E731

# senderId -> session dict / customer dict / recent dict
sessions: dict[str, dict[str, Any]] = {}
customers: dict[str, dict[str, Any]] = {}
recents: dict[str, dict[str, Any]] = {}

# Dirty tracking per store so the flush loop writes only what changed.
_dirty_sessions: set[str] = set()
_dirty_customers: set[str] = set()
_dirty_recents: set[str] = set()
_deleted_sessions: set[str] = set()
_deleted_recents: set[str] = set()
_deleted_customers: set[str] = set()


def fresh_lead() -> dict[str, Any]:
    return {"captured": False, "name": None, "asked": False, "msg_count": 0}


def mark_session_dirty(sender_id: str) -> None:
    _dirty_sessions.add(sender_id)


def get_session(sender_id: str) -> dict[str, Any]:
    """Get-or-create the session, starting a fresh conversation after the idle window.

    A returning captured customer is recalled (greet by name, never re-ask contact) —
    unless memory is paused, in which case every chat starts fresh.
    """
    now = now_ms()
    existing = sessions.get(sender_id)
    if existing and now - existing["last_active"] <= SESSION_TTL_MS:
        existing["last_active"] = now
        return existing

    # New conversation; ops/followups cancels any timer still pending from the old one.
    from app.ops import followups  # local import to avoid a cycle

    followups.cancel(sender_id)

    lead = fresh_lead()
    known = customers.get(sender_id) if get_settings().memory_on else None
    if known:
        lead["name"] = known.get("name") or None
        lead["captured"] = True
        lead["asked"] = True

    fresh = {
        "history": [],
        "last_active": now,
        "lead": lead,
        "followup_sent": False,
        "returning": bool(known),
        "customer_name": (known or {}).get("name") or None,
        "recent_mids": [],
        "payments": [],
        "non_text_prompted_at": 0,
        "closed": False,
        "human_until": 0,
        "notices": {},
    }
    sessions[sender_id] = fresh
    mark_session_dirty(sender_id)
    return fresh


def drop_session(sender_id: str) -> None:
    if sessions.pop(sender_id, None) is not None:
        _deleted_sessions.add(sender_id)
        _dirty_sessions.discard(sender_id)
    from app.ops import followups

    followups.cancel(sender_id)


def pause_for_human(sender_id: str) -> None:
    """A colleague replied from the Page inbox — hand the conversation to them.

    Deliberately stamps the session directly rather than going through get_session, so a
    human answering a customer the bot has never spoken to still silences the bot for the
    rest of that exchange. Also cancels the follow-up nudge: nothing is worse than the bot
    chirping "any other questions?" into a live human conversation.
    """
    session = sessions.get(sender_id)
    if session is None:
        session = get_session(sender_id)
    previously = session.get("human_until") or 0
    session["human_until"] = now_ms() + HUMAN_TAKEOVER_MS
    session["last_active"] = now_ms()
    mark_session_dirty(sender_id)
    from app.ops import followups

    followups.cancel(sender_id)
    if not previously or previously < now_ms():
        import logging

        logging.getLogger(__name__).info(
            "🧑 [%s] human replied from the Page inbox — bot paused for %dh",
            sender_id, HUMAN_TAKEOVER_MS // 3_600_000,
        )


def is_human_handling(sender_id: str) -> bool:
    session = sessions.get(sender_id)
    if session is None:
        return False
    return now_ms() < (session.get("human_until") or 0)


def resume_bot(sender_id: str) -> bool:
    """Admin override: give the conversation back to the bot before the pause expires."""
    session = sessions.get(sender_id)
    if session is None or not session.get("human_until"):
        return False
    session["human_until"] = 0
    mark_session_dirty(sender_id)
    return True


def is_closed(sender_id: str) -> bool:
    """True while this conversation is force-stopped by the off-topic rule.

    Read-only on purpose: it must not create a session or refresh last_active, so the
    close expires with the session's normal 30-minute idle window. A customer who keeps
    typing stays silenced; one who comes back later with a real course question gets a
    fresh session and a real answer.
    """
    session = sessions.get(sender_id)
    if session is None or not session.get("closed"):
        return False
    return now_ms() - session["last_active"] <= SESSION_TTL_MS


def close_session(sender_id: str) -> int:
    """Force-stop: every later message from this sender is dropped until the session
    idles out. Also kills the pending follow-up nudge — we just told them goodbye.

    Returns how many times this sender has been force-stopped inside the rolling
    OFF_TOPIC_CLOSE_WINDOW_MS, so the caller can escalate a repeat offender to the blocklist.
    """
    session = sessions.get(sender_id)
    if session is None:
        return len(_recent_closes(sender_id))
    session["closed"] = True
    mark_session_dirty(sender_id)
    from app.ops import followups

    followups.cancel(sender_id)
    off_topic_closes[sender_id] = [*_recent_closes(sender_id), now_ms()]
    return len(off_topic_closes[sender_id])


def remember_turn(session: dict[str, Any], user_text: str, reply_text: str) -> None:
    """Commit one turn to history, trimming to the cap. Only called after the reply
    is actually sent — a discarded draft leaves no trace."""
    session["history"].append({"role": "user", "parts": [{"text": user_text}]})
    session["history"].append({"role": "model", "parts": [{"text": reply_text}]})
    if len(session["history"]) > MAX_HISTORY:
        del session["history"][: len(session["history"]) - MAX_HISTORY]


def record_recent(sender_id: str, last_message: object) -> None:
    """Roster update, called on every inbound message/attachment exactly once."""
    prev = recents.get(sender_id)
    recents[sender_id] = {
        "name": (prev or {}).get("name", ""),
        "last_message": str(last_message or "")[:200] or (prev or {}).get("last_message", ""),
        "last_seen": now_ms(),
        "msg_count": ((prev or {}).get("msg_count", 0)) + 1,
    }
    _dirty_recents.add(sender_id)
    if len(recents) > RECENTS_MAX:
        oldest_id = min(recents, key=lambda k: recents[k].get("last_seen", 0))
        recents.pop(oldest_id, None)
        _deleted_recents.add(oldest_id)


def record_recent_name(sender_id: str, name: str) -> None:
    rec = recents.get(sender_id)
    if rec is not None and name and rec.get("name") != name:
        rec["name"] = name
        _dirty_recents.add(sender_id)


def upsert_customer(sender_id: str, data: dict[str, Any]) -> None:
    customers[sender_id] = data
    _dirty_customers.add(sender_id)


def mark_customer_dirty(sender_id: str) -> None:
    if sender_id in customers:
        _dirty_customers.add(sender_id)


def purge_user_data(sender_id: str) -> dict[str, bool]:
    """Remove everything held for one person (data-deletion callback). Returns what was
    actually found so the status page can be specific."""
    conversation = sessions.pop(sender_id, None) is not None
    if conversation:
        _deleted_sessions.add(sender_id)
    customer = customers.pop(sender_id, None) is not None
    if customer:
        _deleted_customers.add(sender_id)
    recent = recents.pop(sender_id, None) is not None
    if recent:
        _deleted_recents.add(sender_id)
    from app.ops import followups

    followups.cancel(sender_id)
    return {"conversation": conversation, "customer": customer, "matched": conversation or customer or recent}


def prune() -> None:
    """Drop idle sessions (keeping those with a pending follow-up) and expired roster rows."""
    from app.ops import followups

    now = now_ms()
    for sid in [s for s, v in sessions.items() if now - v["last_active"] > SESSION_TTL_MS]:
        # Keep a session alive while a nudge is pending, or while a colleague still owns
        # the conversation — dropping the latter would silently un-pause the bot.
        if not followups.is_pending(sid) and now >= (sessions[sid].get("human_until") or 0):
            sessions.pop(sid, None)
            _deleted_sessions.add(sid)
            _dirty_sessions.discard(sid)
    for sid in [s for s, v in recents.items() if now - v.get("last_seen", 0) > RECENTS_TTL_MS]:
        recents.pop(sid, None)
        _deleted_recents.add(sid)
        _dirty_recents.discard(sid)


async def load() -> None:
    """Load persisted state at boot. Skipped when memory is paused."""
    if not get_settings().memory_on:
        return
    async with db_session() as db:
        for row in (await db.execute(select(SessionRow))).scalars():
            sessions[row.sender_id] = dict(row.data)
        for row in (await db.execute(select(CustomerRow))).scalars():
            customers[row.sender_id] = {
                "name": row.name,
                "phone": row.phone,
                "interest": row.interest,
                "remarks": row.remarks,
                "captured": row.captured,
                "first_seen": row.first_seen,
                "last_seen": row.last_seen,
            }
        for row in (await db.execute(select(RecentRow))).scalars():
            recents[row.sender_id] = {
                "name": row.name,
                "last_message": row.last_message,
                "last_seen": row.last_seen,
                "msg_count": row.msg_count,
            }


async def flush() -> None:
    """Write dirty entries to Postgres. No-op when nothing changed; paused memory writes nothing."""
    if not get_settings().memory_on:
        return
    if not (
        _dirty_sessions or _dirty_customers or _dirty_recents
        or _deleted_sessions or _deleted_customers or _deleted_recents
    ):
        return

    dirty_s, dirty_c, dirty_r = set(_dirty_sessions), set(_dirty_customers), set(_dirty_recents)
    del_s, del_c, del_r = set(_deleted_sessions), set(_deleted_customers), set(_deleted_recents)
    _dirty_sessions.clear(); _dirty_customers.clear(); _dirty_recents.clear()
    _deleted_sessions.clear(); _deleted_customers.clear(); _deleted_recents.clear()

    try:
        async with db_session() as db:
            if del_s:
                await db.execute(delete(SessionRow).where(SessionRow.sender_id.in_(del_s)))
            if del_c:
                await db.execute(delete(CustomerRow).where(CustomerRow.sender_id.in_(del_c)))
            if del_r:
                await db.execute(delete(RecentRow).where(RecentRow.sender_id.in_(del_r)))
            for sid in dirty_s:
                s = sessions.get(sid)
                if s is not None:
                    await db.merge(SessionRow(sender_id=sid, data=s, last_active=s["last_active"]))
            for sid in dirty_c:
                c = customers.get(sid)
                if c is not None:
                    await db.merge(CustomerRow(
                        sender_id=sid, name=c.get("name", ""), phone=c.get("phone", ""),
                        interest=c.get("interest", ""), remarks=c.get("remarks", ""),
                        captured=bool(c.get("captured", True)),
                        first_seen=c.get("first_seen", 0), last_seen=c.get("last_seen", 0),
                    ))
            for sid in dirty_r:
                r = recents.get(sid)
                if r is not None:
                    await db.merge(RecentRow(
                        sender_id=sid, name=r.get("name", ""), last_message=r.get("last_message", ""),
                        last_seen=r.get("last_seen", 0), msg_count=r.get("msg_count", 0),
                    ))
            await db.commit()
    except Exception:
        # Snapshot never landed — re-dirty so the next tick retries.
        _dirty_sessions.update(dirty_s); _dirty_customers.update(dirty_c); _dirty_recents.update(dirty_r)
        _deleted_sessions.update(del_s); _deleted_customers.update(del_c); _deleted_recents.update(del_r)
        raise
