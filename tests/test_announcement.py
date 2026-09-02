"""The announcement: one extra message the bot rides out on the back of a normal reply.

Covers what makes it safe to hand to a non-technical editor — it goes out once per person
and not once per session, the dates actually bound it, a changed wording is a new
announcement, a failed send is retried rather than written off, and the restricted editor
account can reach the page at all (the whole point of adding it).
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import main
from app.admin import auth
from app.db import state
from app.ops import announce


@pytest.fixture(autouse=True)
def clean_campaign():
    announce._campaign.update({
        "enabled": False, "text": "", "link": "", "starts": "", "ends": "",
        "id": "", "saved_at": "", "saved_by": "",
    })
    announce._sent.clear()
    yield
    announce._campaign.update({
        "enabled": False, "text": "", "link": "", "starts": "", "ends": "",
        "id": "", "saved_at": "", "saved_by": "",
    })
    announce._sent.clear()


def _live(text="ফ্রী ক্লাস আগামী শুক্রবার", link="https://dejure.example/free", **over):
    """Put a live campaign in memory without touching the database."""
    announce._campaign.update({
        "enabled": True, "text": text, "link": link, "starts": "", "ends": "",
        "id": announce._campaign_id(text, link), **over,
    })


def _dhaka_day(offset_days: int) -> str:
    return (datetime.now(announce.TZ) + timedelta(days=offset_days)).strftime("%Y-%m-%d")


class TestWindow:
    def test_live_only_when_enabled_and_written(self):
        _live()
        assert announce.is_live()
        announce._campaign["enabled"] = False
        assert not announce.is_live(), "the switch must actually stop it"
        announce._campaign.update({"enabled": True, "text": ""})
        assert not announce.is_live(), "an empty announcement is not an announcement"

    def test_dates_bound_the_run_inclusively(self):
        _live(starts=_dhaka_day(0), ends=_dhaka_day(9))
        assert announce.is_live(), "today is inside a run that starts today"

        _live(starts=_dhaka_day(1), ends=_dhaka_day(10))
        assert not announce.is_live(), "a run starting tomorrow must not send today"

        _live(starts=_dhaka_day(-10), ends=_dhaka_day(-1))
        assert not announce.is_live(), "a finished run must stop on its own"

    def test_last_day_counts_to_the_end_of_the_evening(self):
        """The end date is a Dhaka day, not a UTC instant. Read as UTC midnight it would
        cut the final evening — the busiest hours — off every campaign."""
        _live(starts=_dhaka_day(-3), ends=_dhaka_day(0))
        last_evening = datetime.now(announce.TZ).replace(hour=22, minute=30)
        assert announce.is_live(last_evening.astimezone(timezone.utc))

    def test_a_half_typed_date_is_ignored_not_obeyed(self):
        saved = announce._clean({"enabled": True, "text": "hi", "ends": "2026-13"})
        assert saved["ends"] == "", "a broken date must not become a window that never opens"


class TestMessage:
    def test_link_goes_on_its_own_line(self):
        _live(text="ফ্রী ক্লাস", link="https://x.example/y")
        assert announce.message() == "ফ্রী ক্লাস\n\nhttps://x.example/y"

    def test_a_link_already_in_the_text_is_not_repeated(self):
        _live(text="যোগ দিন: https://x.example/y", link="https://x.example/y")
        assert announce.message().count("https://x.example/y") == 1


class TestOncePerPerson:
    def test_due_until_sent_then_never_again(self):
        _live()
        assert announce.is_due("cust-1")
        announce._sent.add("cust-1")
        assert not announce.is_due("cust-1")
        assert announce.is_due("cust-2"), "one person's copy must not silence everyone else's"

    def test_a_new_session_does_not_re_announce(self):
        """The regression this table exists to prevent: sessions expire after four hours,
        so a flag living on the session would re-advertise to the same person daily."""
        _live()
        state.sessions.clear()
        announce._sent.add("cust-1")
        session = state.get_session("cust-1")
        session["last_active"] = state.now_ms() - (state.SESSION_TTL_MS + 60_000)
        state.get_session("cust-1")  # forces a brand-new session
        assert not announce.is_due("cust-1")

    def test_nothing_is_due_when_the_campaign_is_not_live(self):
        _live(enabled=False)
        assert not announce.is_due("cust-1")


class TestDelivery:
    def _run(self, coro):
        return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)

    def test_sends_a_second_message_and_records_it(self, monkeypatch):
        from app.pipeline import turn

        _live()
        sent = []
        monkeypatch.setattr(turn.graph, "send_message", lambda sid, text: _ok(sent, sid, text))
        monkeypatch.setattr(turn, "_rand_between", lambda a, b: 0)
        recorded = []
        monkeypatch.setattr(announce, "mark_sent", lambda sid: _note(recorded, sid))

        self._run(turn._send_announcement("cust-1"))

        assert sent == [("cust-1", announce.message())]
        assert recorded == ["cust-1"]

    def test_a_failed_send_is_not_recorded(self, monkeypatch):
        """Meta refusing the message must leave the person still due, so their next turn
        tries again — writing them off is how a campaign quietly reaches half its list."""
        from app.pipeline import turn

        _live()
        monkeypatch.setattr(turn.graph, "send_message", lambda sid, text: _fail())
        monkeypatch.setattr(turn, "_rand_between", lambda a, b: 0)
        recorded = []
        monkeypatch.setattr(announce, "mark_sent", lambda sid: _note(recorded, sid))

        self._run(turn._send_announcement("cust-1"))

        assert recorded == []
        assert announce.is_due("cust-1")

    def test_withheld_when_a_colleague_took_the_conversation_over(self, monkeypatch):
        from app.pipeline import turn

        _live()
        sent = []
        monkeypatch.setattr(turn.graph, "send_message", lambda sid, text: _ok(sent, sid, text))
        monkeypatch.setattr(turn, "_rand_between", lambda a, b: 0)
        monkeypatch.setattr(turn.state, "is_human_handling", lambda sid: True)

        self._run(turn._send_announcement("cust-1"))

        assert sent == [], "a person is answering this thread — the bot must not talk over them"

    def test_a_broken_announcement_never_breaks_the_turn(self, monkeypatch):
        from app.pipeline import turn

        _live()
        monkeypatch.setattr(turn, "_rand_between", lambda a, b: 0)
        monkeypatch.setattr(turn.graph, "send_message", lambda sid, text: _boom())

        self._run(turn._send_announcement("cust-1"))  # must not raise


async def _ok(log, sender_id, text):
    log.append((sender_id, text))
    return True


async def _fail():
    return False


async def _boom():
    raise RuntimeError("Graph API is down")


async def _note(log, sender_id):
    log.append(sender_id)


class TestCampaignIdentity:
    def test_new_wording_is_a_new_announcement(self):
        first = announce._campaign_id("free class friday", "https://a")
        assert announce._campaign_id("free class friday", "https://a") == first
        assert announce._campaign_id("free class saturday", "https://a") != first, (
            "next month's message must reach everyone, not silently nobody")
        assert announce._campaign_id("free class friday", "https://b") != first

    def test_an_empty_announcement_has_no_id(self):
        assert announce._campaign_id("   ", "https://a") == ""


class TestPanelAccess:
    @pytest.fixture
    def client(self):
        with TestClient(main.app) as test_client:
            yield test_client

    def _login(self, client, username="admin", password="admin-pass"):
        res = client.post("/login", data={"username": username, "password": password})
        assert res.status_code == 200, res.text
        return auth.csrf_token(client.cookies[auth.COOKIE_NAME])

    def test_logged_out_visitors_are_sent_to_the_login_page(self, client):
        res = client.get("/announcement", follow_redirects=False)
        assert res.status_code == 302 and res.headers["location"] == "/login"

    def test_a_save_without_a_csrf_token_is_refused(self, client):
        self._login(client)
        res = client.post("/announcement", data={"text": "hi", "enabled": "1"})
        assert res.status_code == 403

    def test_saving_makes_it_live_immediately(self, client):
        csrf = self._login(client)
        res = client.post("/announcement", data={
            "_csrf": csrf, "enabled": "1", "text": "ফ্রী ক্লাস শুক্রবার",
            "link": "https://dejure.example/free",
            "starts": _dhaka_day(0), "ends": _dhaka_day(9),
        }, follow_redirects=False)
        assert res.status_code == 302
        assert announce.is_live()
        assert announce.is_due("someone-new")
        assert "https://dejure.example/free" in announce.message()

    def test_an_unticked_switch_turns_it_off(self, client):
        csrf = self._login(client)
        client.post("/announcement", data={"_csrf": csrf, "enabled": "1", "text": "x"})
        assert announce.is_live()
        client.post("/announcement", data={"_csrf": csrf, "text": "x"})  # checkbox posts nothing
        assert not announce.is_live()

    def test_the_editor_account_can_open_and_save_it(self, client, monkeypatch):
        """The restricted account exists so the team can run day-to-day changes without
        the owner. An announcement is exactly that, so it must not be admin-only."""
        from app.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "editor_username", "tuhin", raising=False)
        monkeypatch.setattr(settings, "editor_password", "editor-pass", raising=False)

        csrf = self._login(client, "tuhin", "editor-pass")
        page = client.get("/announcement")
        assert page.status_code == 200 and "Announcement" in page.text

        client.post("/announcement", data={"_csrf": csrf, "enabled": "1", "text": "ফ্রী ক্লাস"})
        assert announce.is_live()
