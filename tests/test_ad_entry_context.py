"""Does the bot see the question the customer is answering?

A Click-to-Messenger ad opens the chat with its own question ("আপনি কি ২০তম বিজেএস
কোর্সের রুটিন ও সফলদের ভিডিও গাইডলাইনটি এখন দেখতে চান?"). The webhook delivers only
the customer's "হ্যাঁ"; the question lives in the Facebook thread. Before this, the model
answered "হ্যাঁ" with nothing in front of it and sent the demo link instead of the ad's
flow. And the question's echo carries no app_id, so it read as a colleague taking over.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.db import state
from app.kb import config as kb_config

WOMEN_AD = "আপনি কি ২০তম বিজেএস কোর্সের রুটিন ও সফলদের ভিডিও গাইডলাইনটি এখন দেখতে চান?"
ORIENTATION_AD = (
    "২০তম বিজেএস প্রস্তুতির ওরিয়েন্টেশনে আপনি কোন মাধ্যমে অংশ নিতে আগ্রহী?\n"
    "অনলাইন (Zoom) নাকি অফলাইন (ক্যাম্পাস) ?"
)
AD_SECTION = {
    "title": "Ad entry points (Run For campaign)",
    "body": (
        "These are the automatic messages our Facebook ads send.\n"
        f'1. Message starting "{WOMEN_AD}" (women students ad): Funnel B.\n'
        '2. Message starting "টানা চারবার দেশসেরা মেন্টর প্যানেলের..." (mentor ad): demo.\n'
        '3. Message starting "২০তম বিজেএস প্রস্তুতির ওরিয়েন্টেশনে আপনি কোন মাধ্যমে অংশ নিতে আগ্রহী?" '
        "(free class ads): Funnel A.\n"
    ),
}


@pytest.fixture
def ad_kb():
    before = kb_config._cache["kb"]
    kb_config._cache["kb"] = {"customSections": [{"title": "Fees", "body": "x"}, AD_SECTION]}
    yield
    kb_config._cache["kb"] = before


def _thread(*messages):
    """(from_page, text) pairs, oldest first, shaped like graph.fetch_thread_messages."""
    now = state.now_ms()
    return [
        {"id": f"m_{i}", "text": text, "at": now - (len(messages) - i) * 1000, "from_page": from_page}
        for i, (from_page, text) in enumerate(messages)
    ]


def _serve_thread(monkeypatch, thread):
    from app.meta import graph

    async def fake(sender_id):
        return thread

    monkeypatch.setattr(graph, "fetch_thread_messages", fake)


# --- The ad list comes from the knowledge base -----------------------------------------
def test_ad_messages_are_recognised_from_the_kb_section(ad_kb):
    from app.kb import ad_entries

    assert ad_entries.is_ad_message(WOMEN_AD)
    # Line breaks, spacing and punctuation are Ads Manager's, not ours to match.
    assert ad_entries.is_ad_message(ORIENTATION_AD)
    assert ad_entries.is_ad_message("টানা চারবার দেশসেরা মেন্টর প্যানেলের বিজেএস স্ট্যান্ডার্ড ডেমো মাস্টারক্লাসগুলোর এক্সেস লিংক পাঠাবো?")
    assert not ad_entries.is_ad_message("স্যার, আপনার ভর্তি নিশ্চিত করা হয়েছে।")
    assert not ad_entries.is_ad_message("")


def test_one_letter_off_from_the_kb_still_matches():
    """The live KB says প্রস্তুতি where the ad says প্রস্তুতির."""
    from app.kb import ad_entries

    before = kb_config._cache["kb"]
    kb_config._cache["kb"] = {"customSections": [{
        "title": "Ad entry points (Run For campaign)",
        "body": '3. Message starting "২০তম বিজেএস প্রস্তুতি ওরিয়েন্টেশনে আপনি কোন মাধ্যমে অংশ নিতে আগ্রহী?" : Funnel A.',
    }]}
    try:
        assert ad_entries.is_ad_message(ORIENTATION_AD)
        # A colleague's reply about the same class is still a colleague.
        assert not ad_entries.is_ad_message("২০তম বিজেএস ওরিয়েন্টেশন ক্লাস ৫ই নভেম্বর সন্ধ্যা ৭টায়।")
    finally:
        kb_config._cache["kb"] = before


def test_no_ad_section_means_no_ad_messages():
    from app.kb import ad_entries

    before = kb_config._cache["kb"]
    kb_config._cache["kb"] = {"customSections": [{"title": "Fees", "body": f'Message starting "{WOMEN_AD}"'}]}
    try:
        assert not ad_entries.is_ad_message(WOMEN_AD)
    finally:
        kb_config._cache["kb"] = before


# --- An ad's echo is not a colleague ----------------------------------------------------
def test_ad_message_echo_does_not_pause_the_bot(ad_kb):
    from app.pipeline import queues

    ad_echo = {"sender": {"id": "page"}, "recipient": {"id": "ad-cust"},
               "message": {"is_echo": True, "mid": "m_ad", "text": WOMEN_AD}}
    assert queues.is_human_takeover(ad_echo) is False
    queues.enqueue_event(ad_echo)
    assert state.is_human_handling("ad-cust") is False


def test_colleague_echo_still_pauses_the_bot(ad_kb):
    from app.pipeline import queues

    staff = {"sender": {"id": "page"}, "recipient": {"id": "staff-cust"},
             "message": {"is_echo": True, "mid": "m_staff", "text": "স্যার, আমি দেখছি।"}}
    assert queues.is_human_takeover(staff) is True


# --- The ad's question reaches the model ------------------------------------------------
def test_the_question_before_the_customer_reply_enters_history(monkeypatch, ad_kb):
    from app.pipeline import turn

    _serve_thread(monkeypatch, _thread(
        (True, "Walid replied to an ad."),
        (True, WOMEN_AD),
        (False, "হ্যাঁ"),
    ))
    session = state.get_session("yes-cust")
    assert asyncio.run(turn._absorb_page_messages("yes-cust", session)) == 1
    assert session["history"] == [{"role": "page", "parts": [{"text": WOMEN_AD}]}]

    # The next turn reads the same thread: nothing is added twice.
    assert asyncio.run(turn._absorb_page_messages("yes-cust", session)) == 0
    assert len(session["history"]) == 1


def test_our_own_replies_and_older_page_messages_are_not_absorbed(monkeypatch):
    from app.pipeline import turn

    session = state.get_session("own-cust")
    state.remember_turn(session, "ফি কত?", "অফলাইনে ২৫,০০০ টাকা।")
    _serve_thread(monkeypatch, _thread(
        (True, "পুরোনো একটি বার্তা"),        # before an earlier customer message: not this reply's question
        (False, "ফি কত?"),
        (True, "অফলাইনে ২৫,০০০ টাকা।"),    # our reply, already in history
        (False, "ধন্যবাদ"),
    ))
    assert asyncio.run(turn._absorb_page_messages("own-cust", session)) == 0
    assert [h["role"] for h in session["history"]] == ["user", "model"]


def test_a_failed_thread_lookup_changes_nothing(monkeypatch):
    from app.meta import graph
    from app.pipeline import turn

    class Boom:
        async def get(self, *a, **kw):
            raise httpx.ConnectError("down")

    monkeypatch.undo()  # drop the conftest stub: exercise the real lookup
    monkeypatch.setattr(graph, "client", lambda: Boom())
    session = state.get_session("down-cust")
    assert asyncio.run(graph.fetch_thread_messages("down-cust")) == []
    assert asyncio.run(turn._absorb_page_messages("down-cust", session)) == 0
    assert session["history"] == []


def test_thread_lookup_reads_meta_newest_first_and_drops_old_messages(monkeypatch):
    from app.config import get_settings
    from app.meta import graph

    page = get_settings().fb_page_id

    class Res:
        status_code = 200

        def json(self):
            return {"data": [{"messages": {"data": [
                {"id": "m_3", "message": "হ্যাঁ", "from": {"id": "cust"}, "created_time": "2099-01-01T10:00:05+0000"},
                {"id": "m_2", "message": WOMEN_AD, "from": {"id": page}, "created_time": "2099-01-01T10:00:00+0000"},
                {"id": "m_1", "message": "গত মাসের বার্তা", "from": {"id": page}, "created_time": "2000-01-01T10:00:00+0000"},
            ]}}]}

    class Client:
        async def get(self, *a, **kw):
            return Res()

    monkeypatch.undo()
    monkeypatch.setattr(graph, "client", lambda: Client())
    thread = asyncio.run(graph.fetch_thread_messages("cust"))
    assert [(m["id"], m["from_page"]) for m in thread] == [("m_2", True), ("m_3", False)]


def test_the_model_is_told_the_customer_is_answering_the_page(ad_kb):
    from app.agents import factory
    from app.agents.turn_context import TurnContext

    session = state.get_session("prompt-cust")
    state.remember_page_message(session, "m_ad", WOMEN_AD)
    turn = TurnContext(sender_id="prompt-cust", session=session, combined_text="হ্যাঁ")
    history = factory._history_messages(session)
    turn.transcript = factory._render_transcript(history, "হ্যাঁ")
    turn.history_turns = len(history)
    turn.replied_turns = 0
    turn.page_turns = 1

    blocks = factory._dynamic_blocks(turn)
    assert f"Page (sent from our Page, not by you): {WOMEN_AD}" in blocks
    assert "Ad entry points" in blocks
    assert "technical failure" not in blocks  # this is not a reply we failed to send
    assert "=== CURRENT DATE ===" in blocks and "(Bangladesh time)" in blocks


# --- An ad click whose question is already in the thread is not greeted over ------------
def test_ad_click_with_its_own_question_gets_no_generic_welcome(monkeypatch, ad_kb):
    from app.kb.defaults import FALLBACK_GREETING
    from app.meta import graph
    from app.pipeline import turn

    sent: list[str] = []

    async def fake_send(recipient_id, text):
        sent.append(text)
        return True

    async def fake_action(recipient_id, action):
        pass

    monkeypatch.setattr(graph, "send_message", fake_send)
    monkeypatch.setattr(graph, "send_action", fake_action)
    _serve_thread(monkeypatch, _thread((True, WOMEN_AD)))

    asyncio.run(turn.handle_entry_event({
        "sender": {"id": "click-cust"},
        "referral": {"ref": "", "source": "ADS", "type": "OPEN_THREAD", "ad_id": "120211234567890"},
    }))
    assert FALLBACK_GREETING not in sent
    assert state.sessions["click-cust"]["history"][0]["role"] == "page"
