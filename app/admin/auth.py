"""Admin panel auth: HMAC-signed httpOnly cookie (12h TTL, role in payload), CSRF token
derived from the cookie value, constant-time comparisons. Two roles: "admin" (full
access) and "editor" (courses + books + custom sections + blocklist only) — enforced
server-side; hiding UI is not security.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import Request

from app.config import get_settings

COOKIE_NAME = "dj_admin"
SESSION_TTL_S = 12 * 60 * 60


def _sign(secret: str, payload: bytes) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def make_session(username: str, role: str) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"u": username, "r": role, "t": int(time.time())}).encode()
    ).decode().rstrip("=")
    return f"{payload}.{_sign(get_settings().session_secret, payload.encode())}"


def verify_session(value: str | None) -> dict[str, Any] | None:
    if not value or "." not in value:
        return None
    payload, _, sig = value.rpartition(".")
    if not hmac.compare_digest(sig, _sign(get_settings().session_secret, payload.encode())):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except Exception:
        return None
    if int(time.time()) - int(data.get("t", 0)) > SESSION_TTL_S:
        return None
    return data


def csrf_token(session_value: str) -> str:
    return _sign(get_settings().session_secret, f"csrf:{session_value}".encode())


def accounts() -> list[dict[str, str]]:
    s = get_settings()
    users = [{"username": s.admin_username, "password": s.admin_password, "role": "admin"}]
    if s.editor_username and s.editor_password:
        users.append({"username": s.editor_username, "password": s.editor_password, "role": "editor"})
    return users


def check_login(username: str, password: str) -> dict[str, str] | None:
    """Constant-time credential check. Values are compared as UTF-8 bytes:
    hmac.compare_digest rejects non-ASCII str outright (TypeError), which would turn a
    password containing any non-ASCII character into a 500 instead of a failed login."""
    supplied_user = username.strip().encode("utf-8")
    supplied_pass = password.encode("utf-8")
    match = None
    for account in accounts():
        # No early exit: every account is compared so timing does not reveal which
        # username exists.
        user_ok = hmac.compare_digest(supplied_user, account["username"].strip().encode("utf-8"))
        pass_ok = hmac.compare_digest(supplied_pass, account["password"].encode("utf-8"))
        if user_ok and pass_ok:
            match = account
    return match


def current_session(request: Request) -> tuple[dict[str, Any] | None, str | None]:
    value = request.cookies.get(COOKIE_NAME)
    return verify_session(value), value
