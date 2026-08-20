"""Blocklist: PSIDs the bot must never respond to.

Blocked senders are dropped at the webhook edge — before any session, profile lookup,
typing indicator, or LLM call — so they cost one log line. (Meta's own "Move to spam"
still fires the webhook.) The in-memory set makes the edge check synchronous; Postgres
holds the durable copy, written only on admin clicks.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select

from app.db.engine import db_session
from app.db.models import BlockRow

log = logging.getLogger(__name__)

_blocked: dict[str, dict[str, Any]] = {}  # senderId -> {name, note, blocked_at}


async def load() -> None:
    async with db_session() as db:
        for row in (await db.execute(select(BlockRow))).scalars():
            _blocked[row.sender_id] = {"name": row.name, "note": row.note, "blocked_at": row.blocked_at}


def is_blocked(sender_id: object) -> bool:
    return str(sender_id or "").strip() in _blocked


async def block_sender(sender_id: object, name: str = "", note: str = "") -> bool:
    """Returns False when the id is blank. Also forgets the live session so no pending
    follow-up nudge can fire and no stale history lingers."""
    sid = str(sender_id or "").strip()
    if not sid:
        return False
    entry = {
        "name": str(name or "").strip(),
        "note": str(note or "").strip(),
        "blocked_at": _blocked.get(sid, {}).get("blocked_at") or datetime.now(timezone.utc).isoformat(),
    }
    _blocked[sid] = entry
    async with db_session() as db:
        await db.merge(BlockRow(sender_id=sid, name=entry["name"], note=entry["note"],
                                blocked_at=entry["blocked_at"]))
        await db.commit()
    from app.db import state

    state.drop_session(sid)
    return True


async def unblock_sender(sender_id: object) -> bool:
    sid = str(sender_id or "").strip()
    removed = _blocked.pop(sid, None) is not None
    if removed:
        async with db_session() as db:
            await db.execute(delete(BlockRow).where(BlockRow.sender_id == sid))
            await db.commit()
    return removed


def list_blocked() -> list[dict[str, Any]]:
    """Newest block first, for the admin page."""
    return sorted(
        [{"senderId": sid, **info} for sid, info in _blocked.items()],
        key=lambda b: str(b.get("blocked_at") or ""),
        reverse=True,
    )
