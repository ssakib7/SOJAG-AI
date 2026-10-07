"""Broadcast audience report: who could actually receive a Messenger message.

Answers three separate questions for a date range, because they have three different
answers and only the last one is sendable:

  1. leads      — captured leads (name+phone) in the range. Kept FOREVER, and the
                  payload carries senderId, so these PSIDs survive even when the
                  7-day recents roster has already pruned the person.
  2. known      — distinct senders still present in the recents roster for the range.
                  Bounded by RECENTS_TTL_MS (7 days), so an older range reads low not
                  because nobody wrote but because prune() already deleted them.
  3. in_window  — of everyone we know, how many messaged within the last 24h. This is
                  the ONLY set Meta's Send API will deliver a broadcast to; outside the
                  standard messaging window the call fails rather than being queued.

Usage (run where DATABASE_URL points at the real database, i.e. on the VPS):
    uv run python scripts/audience_report.py 2026-09-01 2026-09-10
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db.engine import db_session, dispose_engine
from app.db.models import CustomerRow, LeadRow, RecentRow

WINDOW_MS = 24 * 60 * 60 * 1000


def _parse(day: str, end: bool = False) -> datetime:
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return d + timedelta(days=1) if end else d


async def main(start_day: str, end_day: str) -> None:
    start, end = _parse(start_day), _parse(end_day, end=True)
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    cutoff_ms = now_ms - WINDOW_MS

    async with db_session() as db:
        lead_rows = (await db.execute(
            select(LeadRow).where(LeadRow.at >= start, LeadRow.at < end)
        )).scalars().all()
        # One conversation can re-capture after the 4h session TTL, so dedupe by PSID.
        lead_psids = {p.get("senderId") for row in lead_rows for p in [row.payload or {}] if p.get("senderId")}

        recent_rows = (await db.execute(
            select(RecentRow).where(RecentRow.last_seen >= start_ms, RecentRow.last_seen < end_ms)
        )).scalars().all()
        recent_psids = {r.sender_id for r in recent_rows}

        # In-window is deliberately NOT restricted to the range: the range decides who
        # you want to reach, the window decides who Meta will let you reach.
        in_window = (await db.execute(
            select(func.count()).select_from(RecentRow).where(RecentRow.last_seen >= cutoff_ms)
        )).scalar_one()
        in_window_in_range = len(
            [r for r in recent_rows if r.last_seen >= cutoff_ms]
        )

        roster_total = (await db.execute(select(func.count()).select_from(RecentRow))).scalar_one()
        oldest = (await db.execute(select(func.min(RecentRow.last_seen)))).scalar_one() or 0
        customers_total = (await db.execute(select(func.count()).select_from(CustomerRow))).scalar_one()
        newest_lead = (await db.execute(select(func.max(LeadRow.at)))).scalar_one()

    def when(ms: int) -> str:
        return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if ms else "—"

    print(f"\nRange {start_day} .. {end_day}  (now {when(now_ms)})\n")
    print(f"  leads captured in range      {len(lead_rows):>6}  ({len(lead_psids)} distinct PSIDs, kept forever)")
    print(f"  senders still in recents     {len(recent_psids):>6}  (7-day TTL — older ranges read low because pruned)")
    print(f"  of those, inside 24h window  {in_window_in_range:>6}  <- deliverable from this range")
    print(f"\n  in 24h window, whole roster  {in_window:>6}  <- deliverable right now, any date")
    print("\nContext:")
    print(f"  recents roster size          {roster_total:>6}  (cap RECENTS_MAX=1000)")
    print(f"  oldest roster entry          {when(oldest)}")
    print(f"  customers table              {customers_total:>6}")
    print(f"  newest lead ever written     {newest_lead}")
    print()
    await dispose_engine()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: audience_report.py <start YYYY-MM-DD> <end YYYY-MM-DD>")
    asyncio.run(main(sys.argv[1], sys.argv[2]))
