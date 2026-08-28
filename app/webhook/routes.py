"""Public routes: Meta webhook (handshake + events), health, legal pages, data deletion.

The webhook ACKs 200 BEFORE any work so Meta doesn't retry; events fan out into the
per-sender queues. Signature verification is HMAC-SHA256 over the RAW body.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from app.config import get_settings
from app.db import state
from app.leads import outbox
from app.ops import deletion
from app.pipeline import queues

log = logging.getLogger(__name__)
router = APIRouter()

_boot = time.time()
OUTBOX_STUCK_MS = 30 * 60 * 1000
# Consecutive failures before /health reports degraded. Small, because both of these mean
# customers are actively getting nothing.
SEND_FAILURE_ALERT = 5
LLM_FAILURE_ALERT = 5

_PAGES_DIR = Path(__file__).resolve().parent.parent / "pages"


def _load_page(filename: str) -> str | None:
    try:
        return (_PAGES_DIR / filename).read_text(encoding="utf-8")
    except OSError as err:
        log.warning("%s not found — its route will 404. %s", filename, err)
        return None


PRIVACY_HTML = _load_page("privacy.html")
TERMS_HTML = _load_page("terms.html")
DELETION_HTML = _load_page("data_deletion.html")


def _page(content: str | None) -> Response:
    if not content:
        return Response(status_code=404)
    return HTMLResponse(content)


@router.get("/privacy")
async def privacy() -> Response:
    return _page(PRIVACY_HTML)


@router.get("/terms")
async def terms() -> Response:
    return _page(TERMS_HTML)


@router.get("/data-deletion")
async def data_deletion_page() -> Response:
    return _page(DELETION_HTML)


@router.get("/data-deletion/status")
async def deletion_status(request: Request) -> Response:
    if not DELETION_HTML:
        return Response(status_code=404)
    code = request.query_params.get("code")
    record = await deletion.find_deletion(code)
    if not code:
        banner = (
            '<div class="status warn"><h3>No confirmation code given</h3>'
            "<p>Add your code to the address, for example "
            "<code>/data-deletion/status?code=YOUR_CODE</code>.</p></div>"
        )
    elif not record:
        banner = (
            '<div class="status warn"><h3>We couldn\'t find that request</h3>'
            f'<p>No deletion request matches the code <span class="code">{html.escape(str(code))}</span>. '
            'Please check it for typos, or email <a href="mailto:support@dejureacademy.com">'
            "support@dejureacademy.com</a> and we'll look into it.</p></div>"
        )
    else:
        done = (
            "<p>The conversation history and assistant records for your account have been "
            "<strong>permanently deleted</strong>.</p>"
            if (record.get("purged") or {}).get("matched")
            else "<p>Your request has been received and logged. Our team is completing the removal of any "
            "remaining records and will finish within 30 days of the date above.</p>"
        )
        banner = (
            '<div class="status"><h3>Deletion request received</h3>'
            f'<p>Confirmation code: <span class="code">{html.escape(record["code"])}</span><br />'
            f'Received: {html.escape(record["requestedAt"])}</p>{done}'
            '<p class="muted">Any name and phone number held by our admissions team is removed within '
            "30 days of this date.</p></div>"
        )
    return HTMLResponse(DELETION_HTML.replace("<!--STATUS-->", banner))


@router.post("/data-deletion")
async def data_deletion_callback(request: Request) -> Response:
    """Meta posts a signed_request when someone removes the app from their account.
    The payload carries the app-scoped id while conversations key on the PSID; a miss
    is expected and logged for staff to finish by hand — a valid confirmation is always
    returned so the request is never dropped."""
    s = get_settings()
    form = await request.form()
    payload = deletion.parse_signed_request(form.get("signed_request"), s.app_secret)
    if not payload or not payload.get("user_id"):
        log.warning("Data deletion callback: bad or unsigned request — rejecting.")
        return Response(status_code=400)

    user_id = str(payload["user_id"])
    purged = state.purge_user_data(user_id)
    if purged["matched"]:
        await state.flush()
    # Conversation state is only half of it: the name and phone also sit in the lead,
    # payment, notice and outbox ledgers. Leaving those behind meant a deletion request
    # was acknowledged but not actually carried out.
    try:
        ledger_counts = await deletion.purge_business_records(user_id)
        if any(ledger_counts.values()):
            purged["matched"] = True
            purged["ledgers"] = True
            log.info("Data deletion: removed ledger rows for %s — %s", user_id, ledger_counts)
    except Exception as err:
        log.error("Data deletion: ledger purge FAILED for %s — complete this by hand: %s", user_id, err)
    code = await deletion.record_deletion(user_id, purged)
    if purged["matched"]:
        log.info("Data deletion [%s]: purged records for %s. Any row already delivered to the "
                 "Google Sheet must still be removed there by hand.", code, user_id)
    else:
        log.warning(
            "Data deletion [%s]: no local records matched %s — check the leads sheet and "
            "complete this by hand within 30 days.", code, user_id,
        )
    return JSONResponse({
        "url": f"{s.public_origin}/data-deletion/status?code={code}",
        "confirmation_code": code,
    })


@router.get("/webhook")
async def webhook_verify(request: Request) -> Response:
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == get_settings().verify_token:
        log.info("Webhook verified.")
        return PlainTextResponse(q.get("hub.challenge") or "")
    log.warning("Webhook verification failed (bad mode or verify token).")
    return Response(status_code=403)


def verify_signature(raw_body: bytes, signature: str | None) -> bool:
    if not signature or not raw_body:
        return False
    expected = "sha256=" + hmac.new(get_settings().app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature.encode(), expected.encode())


@router.post("/webhook")
async def webhook_events(request: Request) -> Response:
    raw = await request.body()
    if not verify_signature(raw, request.headers.get("x-hub-signature-256")):
        log.warning("Invalid X-Hub-Signature-256 — rejecting request.")
        return Response(status_code=403)
    try:
        body = await request.json()
    except Exception:
        return Response(status_code=400)
    if body.get("object") != "page":
        return Response(status_code=404)

    # Acknowledge fast so Meta doesn't retry; model + Send work happens in the queues.
    for entry in body.get("entry") or []:
        for event in entry.get("messaging") or []:
            queues.enqueue_event(event)
    return PlainTextResponse("EVENT_RECEIVED")


@router.get("/health")
async def health() -> Response:
    """Built for an EXTERNAL uptime monitor — the bot must not be the only thing that
    knows the bot is broken. 503 when lead delivery is stuck. Counts only, no PII."""
    from app.agents.model import llm_stats
    from app.meta.graph import send_stats
    from app.ops import publish

    s = get_settings()
    stats = await outbox.get_outbox_stats()

    # Three independent ways the bot can be useless while the process looks perfectly
    # healthy: leads not draining, Meta refusing our replies, or the model down. An
    # external uptime monitor watching only "does it return 200" must catch all three.
    problems = []
    if stats["oldest_pending_ms"] > OUTBOX_STUCK_MS:
        problems.append("lead delivery stuck")
    if send_stats["consecutive_failures"] >= SEND_FAILURE_ALERT:
        problems.append("replies not reaching Facebook")
    if llm_stats["consecutive_failures"] >= LLM_FAILURE_ALERT:
        problems.append("AI model failing")
    if not s.leads_on:
        problems.append("lead capture disabled by config")

    return JSONResponse(
        status_code=503 if problems else 200,
        content={
            "status": "degraded: " + "; ".join(problems) if problems else "ok",
            "uptimeSec": round(time.time() - _boot),
            "leadCapture": s.leads_on,
            "paymentAlerts": s.payments_on,
            "published": publish.is_published(),
            "outbox": {
                "pending": stats["pending"],
                "oldestPendingMin": round(stats["oldest_pending_ms"] / 60000),
                "enqueuedSinceBoot": stats["enqueued_since_boot"],
                "resolvedSinceBoot": stats["resolved_since_boot"],
                "lastError": stats["last_error"],
            },
            "send": {
                "failedSinceBoot": send_stats["failed_since_boot"],
                "consecutiveFailures": send_stats["consecutive_failures"],
                "lastFailure": send_stats["last_failure"],
            },
            "llm": {
                "failedSinceBoot": llm_stats["failed_since_boot"],
                "consecutiveFailures": llm_stats["consecutive_failures"],
                "lastError": llm_stats["last_error"],
            },
        },
    )
