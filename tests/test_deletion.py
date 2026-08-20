"""signed_request verification (Meta data-deletion callback)."""

import base64
import hashlib
import hmac
import json

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
