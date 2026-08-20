"""Re-engagement follow-ups: ONE nudge after a stretch of silence, per conversation.

24-hour-window rule preserved from the Node bot: outside Meta's messaging window a
plain-text nudge is rejected, so a follow-up is never scheduled — or re-armed on boot —
once a conversation has been silent 24h. Timers are asyncio tasks (never persisted);
restore() recomputes remaining waits from each session's last_active.
"""

from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.db import state
from app.kb.defaults import FALLBACK_FOLLOWUP
from app.ops import blocklist

log = logging.getLogger(__name__)

FOLLOWUP_MAX_AGE_MS = 24 * 60 * 60 * 1000

_timers: dict[str, asyncio.Task] = {}


def is_pending(sender_id: str) -> bool:
    task = _timers.get(sender_id)
    return task is not None and not task.done()


def cancel(sender_id: str) -> None:
    task = _timers.pop(sender_id, None)
    if task is not None and not task.done():
        task.cancel()


async def _fire_after(sender_id: str, delay_s: float) -> None:
    try:
        await asyncio.sleep(delay_s)
    except asyncio.CancelledError:
        return
    _timers.pop(sender_id, None)
    session = state.sessions.get(sender_id)
    if session is None or session["lead"].get("captured"):
        return  # already have their details — no nudge needed
    if blocklist.is_blocked(sender_id):
        return  # blocked after the timer was armed — stay silent
    session["followup_sent"] = True
    # Refresh the idle window so a reply to the nudge keeps its conversation context.
    session["last_active"] = state.now_ms()
    state.mark_session_dirty(sender_id)
    try:
        from app.meta import graph

        await graph.send_message(sender_id, FALLBACK_FOLLOWUP)
        log.info("→ [%s] follow-up nudge sent", sender_id)
    except Exception as err:
        log.error("Follow-up send failed: %s", err)


def _arm(sender_id: str, delay_ms: float) -> None:
    cancel(sender_id)
    _timers[sender_id] = asyncio.get_running_loop().create_task(_fire_after(sender_id, delay_ms / 1000))


def schedule(sender_id: str, session: dict) -> None:
    """(Re)start the silence timer. Called on every incoming message, so the clock resets
    while the customer is active and only fires after real silence."""
    delay_ms = get_settings().followup_delay_ms
    if delay_ms <= 0:
        return
    cancel(sender_id)
    if session.get("followup_sent"):
        return  # at most one nudge per conversation
    _arm(sender_id, delay_ms)


def restore() -> None:
    """On boot, re-arm follow-ups for restored sessions and drop the rest of the expired
    ones. Overdue-while-down nudges fire shortly after boot."""
    delay_ms = get_settings().followup_delay_ms
    now = state.now_ms()
    for sid in list(state.sessions):
        s = state.sessions[sid]
        pending = (
            delay_ms > 0
            and not s.get("followup_sent")
            and not (s.get("lead") or {}).get("captured")
            and now - s["last_active"] < FOLLOWUP_MAX_AGE_MS
        )
        if now - s["last_active"] > state.SESSION_TTL_MS and not pending:
            state.sessions.pop(sid, None)
            continue
        if pending:
            remaining = delay_ms - (now - s["last_active"])
            _arm(sid, remaining if remaining > 0 else 1000)


def cancel_all() -> None:
    for sid in list(_timers):
        cancel(sid)
