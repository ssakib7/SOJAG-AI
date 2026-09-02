"""Meta "Data Deletion Request" callback support — signed_request verification and the
deletion log that answers status lookups. Port of deletion.js onto Postgres."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select

from app.db.engine import db_session
from app.db.models import (
    DeletionRow,
    LeadRow,
    NoticeRow,
    OutboxRow,
    PaymentClaimRow,
)

MAX_ENTRIES = 5000


def _b64url_decode(s: str) -> bytes:
    s = s.replace("-", "+").replace("_", "/")
    return base64.b64decode(s + "=" * (-len(s) % 4))


def parse_signed_request(signed_request: object, app_secret: str) -> dict[str, Any] | None:
    """Verify and decode Meta's signed_request. None = reject, never "empty request"."""
    if not isinstance(signed_request, str) or "." not in signed_request:
        return None
    encoded_sig, _, encoded_payload = signed_request.partition(".")
    if not encoded_sig or not encoded_payload:
        return None
    try:
        payload = json.loads(_b64url_decode(encoded_payload))
    except Exception:
        return None
    # Meta only ever signs these with HMAC-SHA256; anything else is a downgrade attempt.
    if str(payload.get("algorithm")).upper() != "HMAC-SHA256":
        return None
    expected = hmac.new(app_secret.encode(), encoded_payload.encode(), hashlib.sha256).digest()
    try:
        actual = _b64url_decode(encoded_sig)
    except Exception:
        return None
    if not hmac.compare_digest(expected, actual):
        return None
    return payload


async def record_deletion(user_id: object, purged: dict[str, Any]) -> str:
    """Log a completed deletion; returns the confirmation code Meta shows the user."""
    code = secrets.token_hex(8)
    async with db_session() as db:
        db.add(DeletionRow(
            code=code, user_id=str(user_id),
            purged={k: bool(v) for k, v in purged.items()},
            requested_at=datetime.now(timezone.utc).isoformat(),
        ))
        # Keep the log bounded — it only answers status lookups (30-day retention guidance).
        codes = (await db.execute(
            select(DeletionRow.code).order_by(DeletionRow.requested_at.desc()).offset(MAX_ENTRIES)
        )).scalars().all()
        if codes:
            await db.execute(delete(DeletionRow).where(DeletionRow.code.in_(codes)))
        await db.commit()
    return code


async def purge_business_records(sender_id: str) -> dict[str, int]:
    """Remove this person from the lead/payment/notice ledgers and the outbox.

    state.purge_user_data only clears the conversation stores; name and phone also live in
    the business ledgers, and leaving them there means a deletion request is not actually
    honoured. Delivered rows in the team's Google Sheet are outside our reach and still
    have to be cleared by hand — the callback logs a reminder for that.
    """
    from sqlalchemy import cast, String

    counts: dict[str, int] = {}
    async with db_session() as db:
        for label, model in (("leads", LeadRow), ("payments", PaymentClaimRow),
                             ("notices", NoticeRow), ("outbox", OutboxRow)):
            # The payload is JSON in both Postgres and SQLite; match on its text form so
            # this works without a dialect-specific JSON path operator.
            result = await db.execute(
                delete(model).where(cast(model.payload, String).contains(f'"{sender_id}"'))
            )
            counts[label] = result.rowcount or 0
        await db.commit()
    # Not a ledger, but still a row that names this person: the record of which
    # announcements they were sent. Owned by ops/announce because it also holds the
    # in-memory copy, which would otherwise keep the person "already announced to".
    from app.ops import announce

    counts["announcements"] = await announce.forget(sender_id)
    return counts


async def find_deletion(code: object) -> dict[str, Any] | None:
    if not code:
        return None
    async with db_session() as db:
        row = (await db.execute(select(DeletionRow).where(DeletionRow.code == str(code)))).scalar_one_or_none()
    if row is None:
        return None
    return {"code": row.code, "userId": row.user_id, "purged": row.purged, "requestedAt": row.requested_at}
