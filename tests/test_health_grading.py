"""What /health must refuse to call healthy, and what /health/live must ignore.

Written after an outage where the bot returned a clean 200 for a full day while it held no
knowledge base, answered every customer with "a representative will contact you", and threw
away every lead, payment claim and escalation on a failing database write. Nothing in the
response said so: `pending: 0` reads as calm when the true state is "nothing ever arrived".
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.kb import config as kb_config
from app.leads import outbox
from app.main import create_app


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def stocked_kb(monkeypatch):
    """A knowledge base with actual content in it."""
    kb = {"courseCategories": [{"name": "BJS", "courses": [{"name": "BJS Alpha"}]}], "books": ""}
    monkeypatch.setitem(kb_config._cache, "kb", kb)
    monkeypatch.setitem(kb_config._cache, "system_prompt_full", "x" * 5000)
    return kb


@pytest.fixture(autouse=True)
def clean_stats(monkeypatch):
    monkeypatch.setitem(outbox.stats, "write_failures", 0)
    monkeypatch.setitem(outbox.stats, "last_write_error", None)


class TestEmptyKnowledgeBase:
    def test_empty_kb_is_not_healthy(self, client):
        res = client.get("/health")
        assert res.status_code == 503
        assert "knowledge base is EMPTY" in res.json()["status"]

    def test_stocked_kb_is_healthy(self, client, stocked_kb):
        res = client.get("/health")
        assert res.status_code == 200, res.json()["status"]
        assert res.json()["status"] == "ok"

    def test_summary_counts_are_reported(self, client, stocked_kb):
        kb = client.get("/health").json()["knowledgeBase"]
        assert kb["courses"] == 1
        assert kb["categories"] == 1
        assert kb["promptChars"] == 5000

    def test_about_text_alone_is_not_content(self, client, monkeypatch):
        """Knowing who we are is not knowing anything a customer asks about."""
        monkeypatch.setitem(kb_config._cache, "kb", {"about": "De Jure Academy, founded 2021."})
        assert client.get("/health").status_code == 503

    def test_book_text_counts_as_content(self, client, monkeypatch):
        """`books` is free text in this schema, not a list — a non-empty string is content."""
        monkeypatch.setitem(kb_config._cache, "kb", {"books": "BJS Preli Digest — 850 taka"})
        assert client.get("/health").status_code == 200

    def test_custom_sections_alone_count_as_content(self, client, monkeypatch):
        """A knowledge base can be legitimately course-free while still holding the
        enrolment, payment and eligibility sections the bot answers from."""
        monkeypatch.setitem(kb_config._cache, "kb", {"customSections": [{"title": "Payment Procedure"}]})
        assert client.get("/health").status_code == 200


class TestLostWrites:
    def test_a_single_failed_write_is_never_healthy(self, client, stocked_kb):
        outbox.stats["write_failures"] = 1
        res = client.get("/health")
        assert res.status_code == 503
        assert "FAILED" in res.json()["status"]

    def test_failure_details_are_reported(self, client, stocked_kb):
        outbox.stats["write_failures"] = 3
        outbox.stats["last_write_error"] = "lead: can't subtract offset-naive and offset-aware datetimes"
        body = client.get("/health").json()
        assert body["outbox"]["writeFailures"] == 3
        assert "offset-naive" in body["outbox"]["lastWriteError"]

    def test_does_not_reset_on_a_later_success(self, client, stocked_kb):
        """A lost lead stays lost. The counter is a ledger of damage, not a live gauge —
        going quiet again must not make it look like nothing happened."""
        outbox.stats["write_failures"] = 1
        outbox.stats["enqueued"] = 50
        assert client.get("/health").status_code == 503


class TestLivenessSplit:
    """Autoheal restarts containers whose healthcheck fails. It must therefore never see a
    condition a restart cannot fix, or it restart-loops forever — dropping the in-memory
    sessions, per-sender queues and follow-up timers on every cycle."""

    def test_live_ignores_an_empty_knowledge_base(self, client):
        assert client.get("/health").status_code == 503
        assert client.get("/health/live").status_code == 200

    def test_live_ignores_lost_writes(self, client, stocked_kb):
        outbox.stats["write_failures"] = 2
        assert client.get("/health").status_code == 503
        assert client.get("/health/live").status_code == 200

    def test_live_fails_when_the_database_is_unreachable(self, client, monkeypatch):
        """The failure autoheal DOES exist for: alive process, dead dependency."""
        async def boom():
            raise RuntimeError("connection pool exhausted")

        monkeypatch.setattr(outbox, "pending_count", boom)
        res = client.get("/health/live")
        assert res.status_code == 503
        assert "database unreachable" in res.json()["status"]
