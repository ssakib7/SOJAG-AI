"""Webhook endpoint: handshake, HMAC verification, ACK-before-work, event fan-out."""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.pipeline import queues


@pytest.fixture
def client(monkeypatch):
    # Capture enqueued events instead of running the real pipeline.
    events = []
    monkeypatch.setattr(queues, "enqueue_event", events.append)
    with TestClient(app) as test_client:
        test_client.captured_events = events
        yield test_client


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(get_settings().app_secret.encode(), body, hashlib.sha256).hexdigest()


class TestHandshake:
    def test_valid(self, client):
        res = client.get("/webhook", params={
            "hub.mode": "subscribe", "hub.verify_token": "test-verify-token", "hub.challenge": "12345",
        })
        assert res.status_code == 200
        assert res.text == "12345"

    def test_bad_token(self, client):
        res = client.get("/webhook", params={
            "hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "12345",
        })
        assert res.status_code == 403


class TestEvents:
    def _payload(self):
        return {"object": "page", "entry": [{"messaging": [
            {"sender": {"id": "u1"}, "message": {"mid": "m1", "text": "dam koto"}},
            {"sender": {"id": "u2"}, "message": {"mid": "m2", "text": "hello"}},
        ]}]}

    def test_valid_signature_fans_out(self, client):
        body = json.dumps(self._payload()).encode()
        res = client.post("/webhook", content=body,
                          headers={"content-type": "application/json", "x-hub-signature-256": _sign(body)})
        assert res.status_code == 200
        assert res.text == "EVENT_RECEIVED"
        assert len(client.captured_events) == 2

    def test_bad_signature_rejected(self, client):
        body = json.dumps(self._payload()).encode()
        res = client.post("/webhook", content=body,
                          headers={"content-type": "application/json", "x-hub-signature-256": "sha256=bad"})
        assert res.status_code == 403
        assert not client.captured_events

    def test_missing_signature_rejected(self, client):
        res = client.post("/webhook", json=self._payload())
        assert res.status_code == 403

    def test_non_page_object_404(self, client):
        body = json.dumps({"object": "instagram", "entry": []}).encode()
        res = client.post("/webhook", content=body,
                          headers={"content-type": "application/json", "x-hub-signature-256": _sign(body)})
        assert res.status_code == 404


class TestHealth:
    """The test database carries no knowledge base, so /health is legitimately degraded
    here — that is the check working, not a broken fixture. See test_health_grading.py for
    the graded conditions and the liveness split."""

    def test_reports_empty_knowledge_base(self, client):
        res = client.get("/health")
        assert res.status_code == 503
        data = res.json()
        assert "knowledge base is EMPTY" in data["status"]
        assert "outbox" in data
        assert data["knowledgeBase"]["courses"] == 0

    def test_liveness_is_independent_of_grading(self, client):
        """Autoheal must NOT restart-loop over an empty knowledge base."""
        res = client.get("/health/live")
        assert res.status_code == 200
        assert res.json()["status"] == "alive"


class TestLegalPages:
    def test_privacy(self, client):
        assert client.get("/privacy").status_code == 200

    def test_terms(self, client):
        assert client.get("/terms").status_code == 200

    def test_deletion_page(self, client):
        assert client.get("/data-deletion").status_code == 200


class TestAdmin:
    def test_root_redirects_to_login(self, client):
        res = client.get("/", follow_redirects=False)
        assert res.status_code == 302
        assert res.headers["location"] == "/login"

    def test_login_flow(self, client):
        res = client.post("/login", data={"username": "admin", "password": "admin-pass"},
                          follow_redirects=False)
        assert res.status_code == 302
        assert "dj_admin" in res.cookies
        page = client.get("/", cookies={"dj_admin": res.cookies["dj_admin"]})
        assert page.status_code == 200
        assert "Knowledge base" in page.text

    def test_bad_login(self, client):
        res = client.post("/login", data={"username": "admin", "password": "nope"})
        assert "সঠিক নয়" in res.text  # Bengali error banner, as in the original panel
        assert "dj_admin" not in res.cookies
