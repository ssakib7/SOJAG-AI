"""Durable notification outbox on Postgres — the port of lead_outbox.js.

Guarantee: a captured lead / payment claim can never be lost to a sink outage, a
Telegram rate limit, or a restart mid-send. The append-only ledger row (leads /
payment_claims) and the outbox row are written in ONE transaction BEFORE any network
delivery is attempted; delivery happens asynchronously with per-sink status and
exponential backoff (30s → 15min cap), retrying forever, resumed on boot. A sink that
already succeeded is never re-sent.

This deliberately ignores MEMORY_ENABLED — leads are business records.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import select, update

from app.db.engine import db_session
from app.db.models import LeadRow, NoticeRow, OutboxRow, PaymentClaimRow

log = logging.getLogger(__name__)

SCAN_INTERVAL_S = 30
BASE_RETRY_MS = 30 * 1000
MAX_RETRY_MS = 15 * 60 * 1000

Sink = Callable[[dict[str, Any]], Awaitable[bool]]


def _sinks() -> dict[str, dict[str, Sink]]:
    from app.leads import sinks

    # Payment claims and escalation notices go to Telegram only: the Sheet's columns are
    # lead-shaped, and the team needs these as an immediate ping, not a row to work
    # through later.
    return {
        "lead": {"sheet": sinks.send_to_sheet, "telegram": sinks.send_lead_to_telegram},
        "payment": {"telegram": sinks.send_payment_to_telegram},
        "notice": {"telegram": sinks.send_notice_to_telegram},
    }


_now_ms = lambda: int(time.time() * 1000)  # noqa: E731

# Since-boot counters + most recent delivery error, surfaced via /health.
# write_failures counts items that never made it INTO the outbox at all — a different and
# worse failure than a delivery that is merely retrying, because there is nothing left to
# retry. See enqueue().
stats: dict[str, Any] = {
    "enqueued": 0, "resolved": 0, "last_error": None,
    "write_failures": 0, "last_write_error": None,
}
_delivering = False
_wakeup: asyncio.Event | None = None


_LEDGERS = {"lead": LeadRow, "payment": PaymentClaimRow, "notice": NoticeRow}


def _ledger_row(kind: str, item_id: str, payload: dict[str, Any]):
    return _LEDGERS[kind](id=item_id, payload=payload)


async def enqueue(kind: str, payload: dict[str, Any]) -> str:
    """Persist (ledger + outbox, one transaction) and schedule delivery. Raises only if
    the database write itself fails — the caller logs the contact details as last resort."""
    item_id = f"{_now_ms()}-{random.randbytes(4).hex()}"
    payload = {**payload, "leadId": item_id}  # lets the Sheet dedupe retried deliveries
    try:
        async with db_session() as db:
            db.add(_ledger_row(kind, item_id, payload))
            db.add(OutboxRow(
                id=item_id, kind=kind, at=_now_ms(), payload=payload,
                sinks={name: "pending" for name in _sinks()[kind]},
                attempts=0, not_before=0, done=False,
            ))
            await db.commit()
    except Exception as err:
        # The ledger row and the outbox row share this transaction on purpose, so a failure
        # here loses BOTH: the item is not recorded and not queued, and no retry loop will
        # ever pick it up. The caller's log line and (for leads) its Telegram alert are the
        # only copies left, so this must be loud in /health rather than silent — a bot that
        # captures leads into nowhere looked perfectly healthy for a full day.
        stats["write_failures"] += 1
        stats["last_write_error"] = f"{kind}: {err}"
        raise
    stats["enqueued"] += 1
    if _wakeup is not None:
        _wakeup.set()  # fire-and-forget; the retry loop covers any failure
    return item_id


async def enqueue_lead(lead: dict[str, Any]) -> str:
    return await enqueue("lead", lead)


async def enqueue_payment(payment: dict[str, Any]) -> str:
    return await enqueue("payment", payment)


async def enqueue_notice(notice: dict[str, Any]) -> str:
    return await enqueue("notice", notice)


async def deliver_due() -> None:
    """One delivery sweep: try every due item's remaining sinks. Serialized so
    overlapping wakeups can't double-send."""
    global _delivering
    if _delivering:
        return
    _delivering = True
    try:
        async with db_session() as db:
            rows = (
                await db.execute(
                    select(OutboxRow).where(OutboxRow.done.is_(False), OutboxRow.not_before <= _now_ms())
                )
            ).scalars().all()

        from app.config import get_settings

        shadow = get_settings().shadow_mode
        registry = _sinks()
        for row in rows:
            failed = False
            sinks_state = dict(row.sinks)
            for name, send in registry.get(row.kind, registry["lead"]).items():
                if sinks_state.get(name) != "pending":
                    continue
                if shadow:
                    # Shadow mode: the ledger row is the record; never double-notify the
                    # team while the live bot is still handling the same customer.
                    sinks_state[name] = "skipped"
                    log.info("Outbox (%s): %s -> %s: skipped (shadow mode)", row.kind, row.id, name)
                    continue
                try:
                    sent = await send(row.payload)
                    sinks_state[name] = "sent" if sent else "skipped"
                    log.info(
                        "Outbox (%s): %s -> %s: %s", row.kind, row.id, name,
                        f"sent ({row.payload.get('name')} / {row.payload.get('phone')})" if sent
                        else "skipped (not configured)",
                    )
                except Exception as err:
                    failed = True
                    stats["last_error"] = {
                        "sink": name, "message": str(err)[:300],
                        "at": datetime.now(timezone.utc).isoformat(),
                    }
                    log.error("Outbox (%s): %s -> %s failed (attempt %d): %s",
                              row.kind, row.id, name, row.attempts + 1, err)

            done = "pending" not in sinks_state.values()
            attempts = row.attempts + (1 if failed else 0)
            not_before = _now_ms() + min(BASE_RETRY_MS * 2 ** (attempts - 1), MAX_RETRY_MS) if failed else 0
            async with db_session() as db:
                await db.execute(
                    update(OutboxRow).where(OutboxRow.id == row.id).values(
                        sinks=sinks_state, attempts=attempts, not_before=not_before, done=done,
                    )
                )
                await db.commit()
            if done:
                stats["resolved"] += 1
    finally:
        _delivering = False


async def get_outbox_stats() -> dict[str, Any]:
    """Health snapshot. oldest_pending_ms is the single best "is delivery stuck?" signal
    (max backoff is 15min, so much older means a sink is hard-down)."""
    async with db_session() as db:
        rows = (await db.execute(select(OutboxRow.at).where(OutboxRow.done.is_(False)))).scalars().all()
    oldest = min(rows) if rows else None
    return {
        "pending": len(rows),
        "oldest_pending_ms": _now_ms() - oldest if oldest else 0,
        "enqueued_since_boot": stats["enqueued"],
        "resolved_since_boot": stats["resolved"],
        "last_error": stats["last_error"],
        "write_failures": stats["write_failures"],
        "last_write_error": stats["last_write_error"],
    }


async def pending_count() -> int:
    return (await get_outbox_stats())["pending"]


async def count_leads_on(date_str: str, tz) -> int:
    """Count leads captured on a calendar day (for the daily digest)."""
    async with db_session() as db:
        ats = (await db.execute(select(LeadRow.at))).scalars().all()
    count = 0
    for at in ats:
        if at is None:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if at.astimezone(tz).strftime("%Y-%m-%d") == date_str:
            count += 1
    return count


async def run_delivery_loop(stop: asyncio.Event) -> None:
    """Boot: reset stale backoffs, then sweep every 30s (or immediately on enqueue)."""
    global _wakeup
    _wakeup = asyncio.Event()
    # Guarded: this ran outside any try/except, so a Postgres blip at exactly this moment
    # killed the delivery task for the whole process lifetime — leads then queued forever
    # and the only symptom was a /health 503 nobody was watching. A failure here is
    # survivable: the sweep below picks the same rows up once their backoff expires.
    try:
        async with db_session() as db:
            result = await db.execute(update(OutboxRow).where(OutboxRow.done.is_(False)).values(not_before=0))
            await db.commit()
        if result.rowcount:
            log.info("Lead outbox: %d undelivered item(s) from before restart — retrying.", result.rowcount)
    except Exception as err:
        log.error("Outbox: could not reset stale backoffs at boot (delivery continues): %s", err)

    while not stop.is_set():
        try:
            await deliver_due()
        except Exception as err:
            log.error("Outbox delivery sweep failed: %s", err)
        _wakeup.clear()
        try:
            # Woken early by an enqueue; shutdown sets stop and then _wakeup too.
            await asyncio.wait_for(_wakeup.wait(), SCAN_INTERVAL_S)
        except asyncio.TimeoutError:
            pass


def wake_for_shutdown() -> None:
    if _wakeup is not None:
        _wakeup.set()
