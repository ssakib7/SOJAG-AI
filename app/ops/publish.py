"""Publish switch: the master on/off for the bot's customer-facing replies.

Unpublished, the bot goes completely silent — inbound events are dropped at the webhook
edge (no session, no profile lookup, no typing bubble, no LLM call), queued messages are
thrown away, and pending follow-up nudges never fire. Nothing else changes: the panel,
the leads already captured and the roster of who wrote in all keep working, so staff can
see the conversations arriving while a person answers them from the Page inbox.

Same shape as the blocklist — an in-memory flag makes the edge check synchronous, while
bot_config holds the durable copy, written only when an admin clicks. A deployment that
has never touched the switch is PUBLISHED: the row is absent and the default is on.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db.engine import db_session
from app.db.models import ConfigRow

log = logging.getLogger(__name__)

CONFIG_KEY = "bot_status"

_status: dict[str, Any] = {"published": True, "changed_at": "", "changed_by": ""}


async def load() -> None:
    """Read the durable flag at boot. A missing row means published."""
    async with db_session() as db:
        row = (await db.execute(select(ConfigRow).where(ConfigRow.key == CONFIG_KEY))).scalar_one_or_none()
    value = row.value if row else None
    if isinstance(value, dict):
        _status.update({
            "published": bool(value.get("published", True)),
            "changed_at": str(value.get("changed_at") or ""),
            "changed_by": str(value.get("changed_by") or ""),
        })
    if not _status["published"]:
        log.warning("⏸ Bot is UNPUBLISHED — no customer will get a reply until it is published again.")


def is_published() -> bool:
    return bool(_status["published"])


def status() -> dict[str, Any]:
    return dict(_status)


async def set_published(published: bool, by: str = "") -> None:
    """Flip the switch and persist it, so a restart or redeploy keeps the bot quiet."""
    _status.update({
        "published": bool(published),
        "changed_at": datetime.now(timezone.utc).isoformat(),
        "changed_by": str(by or ""),
    })
    async with db_session() as db:
        await db.merge(ConfigRow(key=CONFIG_KEY, value=dict(_status)))
        await db.commit()
    log.warning(
        "%s bot %s by %s",
        "▶" if published else "⏸",
        "PUBLISHED — replies resume" if published else "UNPUBLISHED — every reply is now suppressed",
        by or "admin",
    )
