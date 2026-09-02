"""Announcement: one extra message the bot delivers alongside its normal reply, for a
fixed window — a free class, an admission deadline, an offer.

What this is NOT, deliberately: a broadcast. Meta removed the Broadcast API, and a
promotional message to someone who has not written in within 24 hours is outside the
standard messaging window and covered by no message tag — sending it risks the Page's
messaging permission. So the announcement never initiates a conversation. It rides on the
back of a reply the customer's own message just triggered, which is inside the window and
needs no tag. Over a ten-day run that reaches essentially everyone who is actually active.

It is sent as its OWN message rather than appended to the reply, and never handed to the
model, because a link is the whole point of it: the model paraphrases, shortens and
occasionally drops a URL, and one mangled link is the entire campaign lost.

Shape mirrors ops/publish: an in-memory copy so the hot path stays synchronous, with
bot_config holding the durable version, written only when an admin saves.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, time as dtime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select

from app.config import get_settings
from app.db.engine import db_session
from app.db.models import AnnouncementSendRow, ConfigRow

log = logging.getLogger(__name__)

CONFIG_KEY = "announcement"

# The team, the students and the dates on the form are all in Bangladesh. A campaign that
# "ends on the 12th" has to mean midnight in Dhaka, not in UTC — six hours early would cut
# the last evening off every run.
TZ = ZoneInfo("Asia/Dhaka")

MAX_TEXT = 1500  # comfortably inside one Messenger message, so it is never split
MAX_LINK = 400

_campaign: dict[str, Any] = {
    "enabled": False,
    "text": "",
    "link": "",
    "starts": "",   # "YYYY-MM-DD", inclusive, Dhaka time. Blank = start now.
    "ends": "",     # "YYYY-MM-DD", inclusive to the end of that day. Blank = no end.
    "id": "",       # content hash; a new wording is a new campaign (see _campaign_id)
    "saved_at": "",
    "saved_by": "",
}

# Sender ids that already received the CURRENT campaign. Loaded for that campaign id only,
# so the set stays the size of one run rather than of every announcement ever sent.
_sent: set[str] = set()


def _campaign_id(text: str, link: str) -> str:
    """Identity of an announcement is its content.

    The alternative — a fixed id, or one the admin bumps by hand — fails the way that
    costs money: next month's announcement is typed over this month's, the id never
    changes, and it silently reaches nobody who was here for the first one. Tying it to
    the wording means a genuinely new message always goes out, at the price of a typo fix
    mid-run resending to the handful already reached. The panel says so in as many words.
    """
    if not text.strip():
        return ""
    return hashlib.sha256(f"{text.strip()}\n{link.strip()}".encode()).hexdigest()[:16]


def _clean(value: dict[str, Any] | None) -> dict[str, Any]:
    v = value if isinstance(value, dict) else {}
    text = str(v.get("text") or "").replace("\r\n", "\n").replace("\r", "\n").strip()[:MAX_TEXT]
    link = str(v.get("link") or "").strip()[:MAX_LINK]
    return {
        "enabled": bool(v.get("enabled")),
        "text": text,
        "link": link,
        "starts": _clean_date(v.get("starts")),
        "ends": _clean_date(v.get("ends")),
        "id": _campaign_id(text, link),
        "saved_at": str(v.get("saved_at") or ""),
        "saved_by": str(v.get("saved_by") or ""),
    }


def _clean_date(value: object) -> str:
    """Keep only a real YYYY-MM-DD. A half-typed date must not silently become a window
    that never opens — it is dropped, and the field simply stops bounding the run."""
    text = str(value or "").strip()
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return ""
    return text


def _day_bounds(day: str, end_of_day: bool) -> datetime | None:
    if not day:
        return None
    d = datetime.strptime(day, "%Y-%m-%d").date()
    moment = dtime(23, 59, 59) if end_of_day else dtime(0, 0, 0)
    return datetime.combine(d, moment, tzinfo=TZ)


async def load() -> None:
    """Read the campaign at boot, then the roster of who has already had it."""
    async with db_session() as db:
        row = (await db.execute(select(ConfigRow).where(ConfigRow.key == CONFIG_KEY))).scalar_one_or_none()
    _campaign.update(_clean(row.value if row else None))
    await _load_sent()
    if is_live():
        log.info("📣 Announcement is LIVE (%s) — every customer the bot answers gets it once.",
                 _window_label())


async def _load_sent() -> None:
    _sent.clear()
    if not _campaign["id"]:
        return
    async with db_session() as db:
        rows = (await db.execute(
            select(AnnouncementSendRow.sender_id).where(AnnouncementSendRow.campaign_id == _campaign["id"])
        )).scalars().all()
    _sent.update(rows)


def _window_label() -> str:
    starts, ends = _campaign["starts"], _campaign["ends"]
    if starts and ends:
        return f"{starts} → {ends}"
    if ends:
        return f"until {ends}"
    if starts:
        return f"from {starts}"
    return "no end date"


def current() -> dict[str, Any]:
    """The campaign as the admin panel shows it, plus how far it has got."""
    return {**_campaign, "sent_count": len(_sent), "live": is_live()}


def is_live(now: datetime | None = None) -> bool:
    """Switched on, has something to say, and inside its dates."""
    if not (_campaign["enabled"] and _campaign["text"]):
        return False
    moment = now or datetime.now(timezone.utc)
    starts = _day_bounds(_campaign["starts"], end_of_day=False)
    ends = _day_bounds(_campaign["ends"], end_of_day=True)
    if starts and moment < starts:
        return False
    if ends and moment > ends:
        return False
    return True


def message() -> str:
    """Text and link as one message. The link goes on its own line so Messenger renders a
    preview for it and a customer can tap it without catching the sentence around it."""
    text, link = _campaign["text"], _campaign["link"]
    return f"{text}\n\n{link}" if link and link not in text else text


def is_due(sender_id: object) -> bool:
    """Synchronous on purpose: this is checked after every single reply, and the answer is
    no almost every time — it must not cost a database round trip to say so."""
    sid = str(sender_id or "").strip()
    return bool(sid) and is_live() and sid not in _sent


async def mark_sent(sender_id: str) -> None:
    """Recorded only after the message was actually accepted by Meta, so a send that
    failed is retried on the customer's next turn instead of being written off.

    MEMORY_ENABLED=false is honoured here: it promises that nothing about a person is
    written down, and this row names one. The in-memory set still stops a repeat while the
    process lives, so the cost is one duplicate after a restart — with memory paused the
    bot has already forgotten every conversation anyway, and quietly persisting through a
    switch that says it does not is the worse of the two.
    """
    _sent.add(sender_id)
    if not get_settings().memory_on:
        return
    campaign_id = _campaign["id"]
    try:
        async with db_session() as db:
            await db.merge(AnnouncementSendRow(
                key=f"{campaign_id}:{sender_id}", campaign_id=campaign_id,
                sender_id=sender_id, at=int(datetime.now(timezone.utc).timestamp() * 1000),
            ))
            await db.commit()
    except Exception as err:
        # The in-memory set still holds, so this process will not repeat itself. Only a
        # restart could, and a duplicate announcement is a far smaller failure than a
        # turn that raises on the way out of a customer's reply.
        log.error("Announcement: could not record the send for %s: %s", sender_id, err)


async def save(values: dict[str, Any], by: str = "") -> dict[str, Any]:
    """Persist an edited campaign. Reloads the already-sent roster, because a changed
    wording is a new campaign and has reached nobody yet."""
    previous_id = _campaign["id"]
    cleaned = _clean({**values, "saved_at": datetime.now(timezone.utc).isoformat(), "saved_by": by})
    async with db_session() as db:
        await db.merge(ConfigRow(key=CONFIG_KEY, value=cleaned))
        await db.commit()
    _campaign.update(cleaned)
    if cleaned["id"] != previous_id:
        await _load_sent()
    log.info("📣 Announcement saved by %s: %s, %s, %d char(s)%s",
             by or "admin",
             "ON" if cleaned["enabled"] else "off",
             _window_label(), len(cleaned["text"]),
             " — NEW wording, so everyone gets it again" if cleaned["id"] != previous_id else "")
    return current()


async def forget(sender_id: str) -> int:
    """Drop this person from every campaign's roster (data-deletion callback)."""
    _sent.discard(sender_id)
    async with db_session() as db:
        result = await db.execute(
            delete(AnnouncementSendRow).where(AnnouncementSendRow.sender_id == sender_id))
        await db.commit()
    return result.rowcount or 0
