"""The release valve on the human-takeover pause.

A colleague answering one question in the Meta inbox silences the bot for that customer
for HUMAN_TAKEOVER_MS. That default is right, but the pause is persisted — before this
there was no way to end it early, not even by restarting the container. These cover the
panel showing who owns a conversation and handing it back.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app import main
from app.admin import auth
from app.db import state


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        yield test_client


def _login(client) -> str:
    res = client.post("/login", data={"username": "admin", "password": "admin-pass"})
    assert res.status_code == 200, res.text
    return auth.csrf_token(client.cookies[auth.COOKIE_NAME])


def _row_for(page: str, sender_id: str) -> str:
    """That customer's own table row. The test database is file-backed and the app
    reloads sessions at startup, so other tests' paused conversations are visible on the
    same page — a page-wide assertion would be testing them, not this one."""
    match = re.search(r"<tr>(?:(?!</tr>).)*" + re.escape(sender_id) + r"(?:(?!</tr>).)*</tr>",
                      page, re.S)
    assert match, f"{sender_id} is not listed on the page"
    return match.group(0)


def _paused_customer(sender_id="cust-paused"):
    state.record_recent(sender_id, "ভর্তি কবে শুরু?")
    state.pause_for_human(sender_id)
    assert state.is_human_handling(sender_id)
    return sender_id


class TestPanel:
    def test_a_paused_conversation_is_visible_with_a_way_out(self, client):
        csrf = _login(client)
        sender_id = _paused_customer()

        page = client.get("/blocked").text
        assert "Answered by" in page
        row = _row_for(page, sender_id)
        assert "Give back to bot" in row, "no way to end the pause from the panel"
        assert "/handover/resume" in row
        assert csrf in row

    def test_an_unpaused_conversation_shows_the_bot_owns_it(self, client):
        _login(client)
        state.record_recent("cust-normal", "কোর্স ফি কত?")

        row = _row_for(client.get("/blocked").text, "cust-normal")
        # The help text above the table names the button, so look for the form that posts.
        assert "/handover/resume" not in row, "an unpaused chat offered a release button"
        assert "🤖 Bot" in row


class TestResume:
    def test_the_button_hands_the_conversation_back(self, client):
        csrf = _login(client)
        sender_id = _paused_customer()

        res = client.post("/handover/resume",
                          data={"_csrf": csrf, "senderId": sender_id, "next": "/blocked"},
                          follow_redirects=False)
        assert res.status_code == 302
        assert "done=resumed" in res.headers["location"]
        assert not state.is_human_handling(sender_id), "the bot was not given the chat back"

    def test_resuming_a_conversation_nobody_took_over_says_so(self, client):
        csrf = _login(client)
        state.record_recent("cust-quiet", "hello")

        res = client.post("/handover/resume",
                          data={"_csrf": csrf, "senderId": "cust-quiet"},
                          follow_redirects=False)
        assert "done=not-paused" in res.headers["location"]

    def test_a_forged_request_cannot_release_a_conversation(self, client):
        _login(client)
        sender_id = _paused_customer()

        res = client.post("/handover/resume",
                          data={"_csrf": "forged", "senderId": sender_id},
                          follow_redirects=False)
        assert res.status_code == 403
        assert state.is_human_handling(sender_id), "CSRF check did not protect the release"

    def test_a_logged_out_visitor_cannot_release_a_conversation(self, client):
        sender_id = _paused_customer()

        res = client.post("/handover/resume",
                          data={"_csrf": "x", "senderId": sender_id}, follow_redirects=False)
        assert res.headers["location"] == "/login"
        assert state.is_human_handling(sender_id)
