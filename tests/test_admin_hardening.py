"""Admin panel hardening: login throttling, Secure cookie, and the KB save guards.

The panel is on a public domain and one save replaces everything the bot tells customers,
so these are the two ways it can go badly wrong: someone grinds the password, or a bad
save empties the knowledge base with no way back.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.admin import auth


@pytest.fixture
def client():
    from app import main

    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clear_throttle():
    auth._login_failures.clear()
    yield
    auth._login_failures.clear()


class TestLoginThrottle:
    def test_locks_out_after_repeated_failures(self):
        for _ in range(auth.MAX_LOGIN_FAILURES - 1):
            auth.record_login_failure("1.2.3.4")
        assert auth.lockout_remaining_s("1.2.3.4") == 0, "locked out too early"
        auth.record_login_failure("1.2.3.4")
        assert auth.lockout_remaining_s("1.2.3.4") > 0, "unlimited password guessing allowed"

    def test_lockout_is_per_ip(self):
        for _ in range(auth.MAX_LOGIN_FAILURES):
            auth.record_login_failure("1.2.3.4")
        assert auth.lockout_remaining_s("5.6.7.8") == 0

    def test_success_clears_the_counter(self):
        for _ in range(auth.MAX_LOGIN_FAILURES - 1):
            auth.record_login_failure("1.2.3.4")
        auth.clear_login_failures("1.2.3.4")
        assert auth.lockout_remaining_s("1.2.3.4") == 0

    def test_failures_expire(self, monkeypatch):
        import time as time_module

        now = time_module.time()
        monkeypatch.setattr(auth.time, "time", lambda: now)
        for _ in range(auth.MAX_LOGIN_FAILURES):
            auth.record_login_failure("1.2.3.4")
        assert auth.lockout_remaining_s("1.2.3.4") > 0
        monkeypatch.setattr(auth.time, "time", lambda: now + auth.LOCKOUT_S + 1)
        assert auth.lockout_remaining_s("1.2.3.4") == 0


class TestLoginRoute:
    def test_wrong_password_is_rejected_and_counted(self, client):
        res = client.post("/login", data={"username": "admin", "password": "wrong"},
                          follow_redirects=False)
        assert res.status_code == 200  # re-rendered form, not a session
        assert auth._login_failures, "a failed login was not counted toward the lockout"

    def test_lockout_blocks_even_the_correct_password(self, client):
        for _ in range(auth.MAX_LOGIN_FAILURES):
            auth.record_login_failure("testclient")
        res = client.post("/login", data={"username": "admin", "password": "admin-pass"},
                          follow_redirects=False)
        assert res.status_code == 200
        assert "dj_admin" not in res.cookies

    def test_cookie_is_secure_on_an_https_origin(self, monkeypatch):
        """In production PUBLIC_URL is https, so the session cookie must be Secure."""
        from fastapi.testclient import TestClient

        from app.config import get_settings

        monkeypatch.setenv("PUBLIC_URL", "https://bot.dejureacademy.net")
        get_settings.cache_clear()
        try:
            from app.main import app

            with TestClient(app, base_url="https://testserver") as https_client:
                res = https_client.post(
                    "/login", data={"username": "admin", "password": "admin-pass"},
                    follow_redirects=False,
                )
                cookie_header = res.headers.get("set-cookie", "")
            assert "secure" in cookie_header.lower(), (
                f"admin session cookie is not Secure: {cookie_header}"
            )
            assert "httponly" in cookie_header.lower()
        finally:
            get_settings.cache_clear()


class TestKbSaveGuards:
    @pytest.mark.asyncio
    async def test_undo_restores_the_previous_value(self):
        """Uses a throwaway key: set_config_value/restore_previous are key-generic, and
        writing the real knowledge_base here would leak into every other test's app boot."""
        from app.db.engine import create_tables
        from app.kb import config as kb_config

        await create_tables()
        key = "test_undo_scratch"
        await kb_config.set_config_value(key, {"courseCategories": [{"name": "v1"}]})
        await kb_config.set_config_value(key, {"courseCategories": [{"name": "v2"}]})

        assert await kb_config.has_previous(key)
        assert await kb_config.restore_previous(key)
        value = await kb_config.get_config_value(key)
        assert value["courseCategories"][0]["name"] == "v1"

        # ...and the restore itself is undoable, so a mis-click on Undo is recoverable.
        assert await kb_config.restore_previous(key)
        value = await kb_config.get_config_value(key)
        assert value["courseCategories"][0]["name"] == "v2"

    @pytest.mark.asyncio
    async def test_undo_with_no_history_is_a_no_op(self):
        from app.db.engine import create_tables
        from app.kb import config as kb_config

        await create_tables()
        assert not await kb_config.restore_previous("a_key_never_saved")
