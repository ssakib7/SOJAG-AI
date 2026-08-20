"""Lead delivery sinks: Google Sheet (Apps Script web app) and Telegram.

Contract (same as the Node bot): return True = delivered, False = sink not configured
(skip permanently), raise = transient failure (the outbox keeps the item and retries).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)


async def send_to_sheet(lead: dict[str, Any]) -> bool:
    """Append a row via the Apps Script web app.

    The HTTP status alone does NOT mean the row was written: Apps Script answers 200 to
    everything it can run at all, including its own rejections and thrown exceptions.
    The response body's `ok` flag decides — anything else keeps the lead queued and
    surfaces the reason on /health.
    """
    s = get_settings()
    if not s.google_sheet_webapp_url:
        return False
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        res = await client.post(
            s.google_sheet_webapp_url,
            json={"token": s.sheet_shared_token, **lead},
        )
    # A dead/undeployed /exec URL is the one case Google answers with a real status (404).
    if res.status_code >= 400:
        raise RuntimeError(f"Sheet web app responded {res.status_code}: {res.text[:300]}")
    body = res.text.strip()
    try:
        ok = res.json().get("ok") is True
    except Exception:
        ok = body.lower().startswith("ok")  # older scripts reply the bare text "ok"
    if not ok:
        raise RuntimeError(f"Sheet web app rejected the lead: {body[:300]}")
    return True


async def send_telegram_text(text: str) -> bool:
    """Post plain text to the team's Telegram chat. False = not configured."""
    s = get_settings()
    if not (s.telegram_bot_token and s.telegram_chat_id):
        return False
    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.post(
            f"{s.telegram_api_base}/bot{s.telegram_bot_token}/sendMessage",
            json={"chat_id": s.telegram_chat_id, "text": text},
        )
    if res.status_code >= 400:
        # Includes Telegram 429 rate limits — the outbox backoff spaces retries out.
        raise RuntimeError(f"Telegram responded {res.status_code}: {res.text[:300]}")
    return True


async def send_lead_to_telegram(lead: dict[str, Any]) -> bool:
    text = (
        "🆕 New lead from Messenger\n"
        f"Name: {lead.get('name')}\n"
        f"Phone: {lead.get('phone')}\n"
        + (f"Interested in: {lead['interest']}\n" if lead.get("interest") else "")
        + (f"Remarks: {lead['remarks']}\n" if lead.get("remarks") else "")
        + f"Time: {lead.get('time')}"
        + (f"\nOpen chat: {lead['conversationUrl']}" if lead.get("conversationUrl") else "")
    )
    return await send_telegram_text(text)


async def send_payment_to_telegram(p: dict[str, Any]) -> bool:
    """A REQUEST TO VERIFY, never a confirmation — an agent checks the statement and
    confirms the student manually."""
    lines = [
        "💳 PAYMENT CLAIM — needs manual verification",
        f"Name: {p.get('name') or '(unknown)'}",
        f"Phone: {p.get('phone') or '(not given)'}",
    ]
    if p.get("method"):
        lines.append(f"Method: {p['method']}")
    if p.get("trxId"):
        lines.append(f"Trx ID: {p['trxId']}")
    if p.get("amount"):
        lines.append(f"Amount: {p['amount']}")
    if p.get("forCourse"):
        lines.append(f"For: {p['forCourse']}")
    if p.get("senderName"):
        lines.append(f"Sent from: {p['senderName']}")
    if p.get("note"):
        lines.append(f"Note: {p['note']}")
    if p.get("customerMessage"):
        lines.append(f'Customer wrote: "{p["customerMessage"]}"')
    if p.get("attachmentUrl"):
        lines.append(f"Screenshot: {p['attachmentUrl']}")
    lines.append(f"Time: {p.get('time')}")
    if p.get("conversationUrl"):
        lines.append(f"Open chat: {p['conversationUrl']}")
    lines.append("⚠ Verify before confirming — the bot has NOT confirmed anything to the customer.")
    return await send_telegram_text("\n".join(lines))
