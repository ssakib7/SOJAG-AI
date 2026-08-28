"""signed_request verification (Meta data-deletion callback)."""

import base64
import hashlib
import hmac
import json

import pytest

from app.ops.deletion import parse_signed_request

SECRET = "test-secret"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def make_signed_request(payload: dict, secret: str = SECRET) -> str:
    encoded = _b64url(json.dumps(payload).encode())
    sig = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest()
    return f"{_b64url(sig)}.{encoded}"


def test_valid_roundtrip():
    payload = {"algorithm": "HMAC-SHA256", "user_id": "12345", "issued_at": 1700000000}
    assert parse_signed_request(make_signed_request(payload), SECRET) == payload


def test_wrong_secret_rejected():
    payload = {"algorithm": "HMAC-SHA256", "user_id": "12345"}
    assert parse_signed_request(make_signed_request(payload, "other"), SECRET) is None


def test_algorithm_downgrade_rejected():
    payload = {"algorithm": "none", "user_id": "12345"}
    assert parse_signed_request(make_signed_request(payload), SECRET) is None


def test_malformed_rejected():
    assert parse_signed_request(None, SECRET) is None
    assert parse_signed_request("", SECRET) is None
    assert parse_signed_request("nodot", SECRET) is None
    assert parse_signed_request("a.b", SECRET) is None


class TestBusinessRecordPurge:
    """A deletion request must also clear the lead/payment ledgers, not just the chat
    state — name and phone live there too, and leaving them behind means the request was
    acknowledged but never actually carried out."""

    @pytest.mark.asyncio
    async def test_purges_only_the_requesting_user(self):
        from app.db.engine import create_tables, db_session
        from app.db.models import LeadRow
        from app.leads import outbox
        from app.ops.deletion import purge_business_records
        from sqlalchemy import select

        await create_tables()
        await outbox.enqueue_lead({"name": "Gone", "phone": "01712345678", "senderId": "del-user"})
        await outbox.enqueue_lead({"name": "Stays", "phone": "01812345678", "senderId": "keep-user"})

        counts = await purge_business_records("del-user")
        assert counts["leads"] >= 1
        assert counts["outbox"] >= 1

        async with db_session() as db:
            remaining = [r.payload.get("senderId") for r in (await db.execute(select(LeadRow))).scalars()]
        assert "del-user" not in remaining
        assert "keep-user" in remaining, "purge removed an unrelated customer's lead"

        # Leave no pending outbox row behind: the tests share one SQLite file, and a
        # later test that boots the app would have this lead delivered to its sink stub
        # by the resuming delivery loop.
        await purge_business_records("keep-user")
