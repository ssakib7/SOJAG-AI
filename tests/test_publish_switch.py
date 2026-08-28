"""Publish/unpublish: the admin panel's master off switch for replies.

Covers the three things that make it a real kill switch rather than a hidden UI toggle:
events are dropped at the webhook edge, the flag survives a restart, and only an admin
may flip it.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.admin import auth
from app.db import state
from app import main
from app.ops import publish
from app.pipeline import queues


@pytest.fixture(autouse=True)
def published_again():
    """Every test starts from a published bot and leaves one behind."""
    publish._status.update({"published": True, "changed_at": "", "changed_by": ""})
    yield
    publish._status.update({"published": True, "changed_at": "", "changed_by": ""})


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        yield test_client


def _login(client, username="admin", password="admin-pass") -> str:
    """Log in and return the CSRF token that goes with the session cookie."""
    res = client.post("/login", data={"username": username, "password": password})
    assert res.status_code == 200, res.text
    return auth.csrf_token(client.cookies[auth.COOKIE_NAME])


def _text_event(sender="cust-pub", mid="m-pub-1", text="দাম কত?"):
    return {"sender": {"id": sender}, "message": {"mid": mid, "text": text}}


class TestEdgeGate:
    def test_unpublished_drops_the_event_without_queueing_it(self, monkeypatch):
        queues._seen_event_keys.clear()
        publish._status["published"] = False
        spawned = []
        monkeypatch.setattr(queues, "_spawn", spawned.append)

        queues.enqueue_event(_text_event())

        assert spawned == [], "an unpublished bot must not start a turn"
        assert "cust-pub" not in queues._inboxes

    def test_unpublished_still_records_the_roster(self, monkeypatch):
        queues._seen_event_keys.clear()
        publish._status["published"] = False
        monkeypatch.setattr(queues, "_spawn", lambda coro: coro.close())

        queues.enqueue_event(_text_event(text="ভর্তি কবে?"))

        # Staff must be able to see who wrote in while the bot was off.
        assert state.recents["cust-pub"]["last_message"] == "ভর্তি কবে?"

    def test_published_queues_normally(self, monkeypatch):
        queues._seen_event_keys.clear()
        spawned = []
        monkeypatch.setattr(queues, "_spawn", lambda coro: (coro.close(), spawned.append(1)))

        queues.enqueue_event(_text_event(mid="m-pub-2"))

        assert spawned, "a published bot still answers"


class TestPersistence:
    def test_flag_survives_a_restart(self, tmp_path, monkeypatch):
        """An unpublished bot must stay unpublished across a redeploy — otherwise the
        next deploy quietly turns it back on."""
        from app.config import get_settings
        from app.db.engine import create_tables, dispose_engine, reset_engine_for_tests

        # Own database + engine: asyncio.run below opens its own event loop, and the
        # session-wide engine belongs to whichever loop first touched it.
        monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'pub.db').as_posix()}")
        get_settings.cache_clear()
        reset_engine_for_tests()

        async def flip_and_reload():
            await create_tables()
            try:
                await publish.set_published(False, by="admin")
                publish._status["published"] = True  # pretend the process restarted
                await publish.load()
            finally:
                await dispose_engine()

        try:
            asyncio.run(flip_and_reload())
            assert publish.is_published() is False
        finally:
            get_settings.cache_clear()
            reset_engine_for_tests()

    def test_missing_row_means_published(self):
        # Fresh deployments have never written bot_status; they must answer customers.
        assert publish.is_published() is True


class TestAdminRoute:
    def test_admin_can_unpublish_and_publish(self, client):
        csrf = _login(client)

        res = client.post("/publish", data={"_csrf": csrf, "published": "0", "next": "/leads"})
        assert res.status_code == 200
        assert res.url.path == "/leads"
        assert publish.is_published() is False
        assert "unpublished" in res.text.lower()

        res = client.post("/publish", data={"_csrf": csrf, "published": "1", "next": "/blocked"})
        assert publish.is_published() is True
        assert res.url.path == "/blocked"

    def test_bad_csrf_is_rejected(self, client):
        _login(client)
        res = client.post("/publish", data={"_csrf": "nope", "published": "0"})
        assert res.status_code == 403
        assert publish.is_published() is True

    def test_editor_may_not_flip_the_switch(self, client, monkeypatch):
        """An editor manages courses and blocked users; silencing the whole bot is not
        theirs to do."""
        from app.config import get_settings

        monkeypatch.setenv("EDITOR_USERNAME", "editor")
        monkeypatch.setenv("EDITOR_PASSWORD", "editor-pass")
        get_settings.cache_clear()
        try:
            csrf = _login(client, "editor", "editor-pass")
            res = client.post("/publish", data={"_csrf": csrf, "published": "0"})
            assert res.status_code == 403
            assert publish.is_published() is True
        finally:
            get_settings.cache_clear()

    def test_logged_out_is_bounced_to_login(self, client):
        res = client.post("/publish", data={"published": "0"})
        assert res.url.path == "/login"
        assert publish.is_published() is True

    def test_offsite_redirect_is_refused(self, client):
        csrf = _login(client)
        res = client.post(
            "/publish", data={"_csrf": csrf, "published": "0", "next": "//evil.example.com/"},
            follow_redirects=False,
        )
        assert res.headers["location"].startswith("/?bot=")

    def test_switch_is_rendered_on_the_panel(self, client):
        _login(client)
        assert "Unpublish" in client.get("/blocked").text
        publish._status["published"] = False
        page = client.get("/blocked").text
        assert "The bot is unpublished." in page
        assert ">\n        Publish</button>" in page or "Publish</button>" in page
