"""Team escalation: the path for everything that is neither a lead nor a payment.

Before this existed the bot could only tell the team two things — "here is a phone
number" and "someone says they paid". Everything else that genuinely needs a human
(an angry customer, a question the knowledge base cannot answer, someone asking for a
person and declining to leave a number, an account/website problem) reached nobody: the
model apologised, promised a callback, and the conversation ended there.

Notices ride the SAME durable outbox as leads and payments, so a Telegram outage delays
them instead of dropping them, and they resume after a restart.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.db import state
from app.leads import outbox
from app.meta import graph
from app.utils.text import NAME_UNKNOWN, real_name, short_note

log = logging.getLogger(__name__)

# One conversation should not page the team once per message. A second notice about the
# same conversation is only worth sending if it says something new (different category)
# or enough time has passed that the first is stale.
NOTICE_COOLDOWN_MS = 30 * 60 * 1000

CATEGORY_LABELS = {
    "human_request": "🙋 গ্রাহক একজন মানুষের সাথে কথা বলতে চান",
    "angry": "😠 গ্রাহক অসন্তুষ্ট / অভিযোগ করছেন",
    "unknown_answer": "❓ বটের কাছে উত্তর নেই",
    "account_issue": "🔐 অ্যাকাউন্ট / ওয়েবসাইট সমস্যা",
    "refund": "💸 রিফান্ড / ফেরত সংক্রান্ত",
    "high_value": "⭐ গুরুত্বপূর্ণ সম্ভাব্য গ্রাহক",
    "other": "📣 মানুষের হস্তক্ষেপ প্রয়োজন",
}


def _should_send(session: dict[str, Any] | None, category: str) -> bool:
    """Per-conversation throttle, so a long unhappy chat produces a handful of useful
    pings rather than one per message."""
    if session is None:
        return True
    sent = session.setdefault("notices", {})
    now = state.now_ms()
    last = sent.get(category)
    if last is not None and now - last < NOTICE_COOLDOWN_MS:
        return False
    sent[category] = now
    return True


async def notify_team(
    sender_id: str,
    session: dict[str, Any] | None,
    category: str,
    reason: str,
    customer_message: str = "",
) -> bool:
    """Queue a human-attention notice. Returns False when it was throttled or the
    Telegram sink is not configured."""
    s = get_settings()
    if not (s.telegram_bot_token and s.telegram_chat_id):
        log.warning("escalation [%s] %s: %s — Telegram not configured, nobody was told",
                    sender_id, category, reason)
        return False
    if not _should_send(session, category):
        log.info("escalation [%s] %s suppressed (already notified recently)", sender_id, category)
        return False

    known = state.customers.get(sender_id) or {}
    # Best-effort enrichment: this is the path that rescues a conversation, so a Graph
    # hiccup must degrade the alert's detail, never cost the alert itself.
    profile_name = None
    conversation_url = None
    try:
        profile_name = await graph.fetch_profile_name(sender_id)
        conversation_url = await graph.resolve_inbox_url(sender_id)
    except Exception as err:
        log.warning("escalation [%s]: profile/link lookup failed, alerting anyway: %s", sender_id, err)
    name = (
        real_name(profile_name)
        or real_name((session or {}).get("customer_name"))
        or real_name(known.get("name"))
        or NAME_UNKNOWN
    )
    notice = {
        "category": category,
        "label": CATEGORY_LABELS.get(category, CATEGORY_LABELS["other"]),
        "name": name,
        "phone": known.get("phone", ""),
        "reason": short_note(reason, 300),
        "customerMessage": short_note(customer_message, 400),
        "senderId": sender_id,
        "time": datetime.now(timezone.utc).isoformat(),
        "conversationUrl": conversation_url,
    }
    log.info('escalation [%s] %s: "%s"', sender_id, category, notice["reason"])
    try:
        await outbox.enqueue_notice(notice)
    except Exception as err:
        # The database is the only thing that can fail here; the log line is the last
        # resort so the conversation is at least recoverable by hand.
        log.error("⚠ ESCALATION NOT PERSISTED [%s] %s / %s: %s", sender_id, category, reason, err)
        return False
    if session is not None:
        state.mark_session_dirty(sender_id)
    return True
