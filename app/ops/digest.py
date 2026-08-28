"""Daily lead digest — a dead-man's switch for the whole pipeline.

Every day at LEAD_DIGEST_TIME (Asia/Dhaka) the bot posts a one-line report to the
team's Telegram chat. Its job is as much the ABSENCE signal as the numbers: if the
report doesn't arrive on time, the team knows the bot (or its Telegram path) is down
without any customer having to complain first.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.leads import outbox
from app.leads.sinks import send_telegram_text

log = logging.getLogger(__name__)

DIGEST_TZ = ZoneInfo("Asia/Dhaka")
_boot_time = time.time()
_last_digest_date: str | None = None  # in-memory: a restart may repeat one digest; harmless
_digest_failures: dict[str, int] = {}
MAX_DIGEST_ATTEMPTS = 10


def _dhaka_clock() -> tuple[str, str]:
    now = datetime.now(DIGEST_TZ)
    return now.strftime("%Y-%m-%d"), now.strftime("%H:%M")


async def send_daily_digest() -> None:
    date, _ = _dhaka_clock()
    stats = await outbox.get_outbox_stats()
    text = (
        f"📊 Daily lead report — {date}\n"
        f"Leads captured today: {await outbox.count_leads_on(date, DIGEST_TZ)}\n"
        f"Awaiting delivery: {stats['pending']}"
    )
    if stats["pending"]:
        text += f"\n⚠ Oldest has waited {round(stats['oldest_pending_ms'] / 60000)} min"
        if stats["last_error"]:
            text += f" — last error ({stats['last_error']['sink']}): {stats['last_error']['message']}"
    text += f"\nBot uptime: {round((time.time() - _boot_time) / 3600)}h"
    await send_telegram_text(text)
    log.info("Daily digest sent for %s", date)


async def run_digest_loop(stop: asyncio.Event) -> None:
    global _last_digest_date
    digest_time = get_settings().lead_digest_time.strip()
    if not re.fullmatch(r"\d{1,2}:\d{2}", digest_time):
        return  # disabled (e.g. LEAD_DIGEST_TIME=off)
    while not stop.is_set():
        date, now = _dhaka_clock()
        # ">=" rather than "==" so a busy minute (or a restart shortly after digest time)
        # still produces that day's report, just late.
        if now >= digest_time and _last_digest_date != date:
            try:
                await send_daily_digest()
                _last_digest_date = date  # only once it has actually been delivered
            except Exception as err:
                # Marking the day done before sending meant one Telegram blip at 21:00
                # skipped the report entirely — and a missing digest is exactly how the
                # team is supposed to notice the bot is dead. Retry on the next tick, but
                # give up after a few so a hard outage doesn't retry all night.
                _digest_failures[date] = _digest_failures.get(date, 0) + 1
                if _digest_failures[date] >= MAX_DIGEST_ATTEMPTS:
                    _last_digest_date = date
                    log.error("Daily digest failed %d times — giving up for %s: %s",
                              _digest_failures[date], date, err)
                else:
                    log.error("Daily digest failed (attempt %d), retrying next minute: %s",
                              _digest_failures[date], err)
        try:
            await asyncio.wait_for(stop.wait(), 60)
        except asyncio.TimeoutError:
            pass
