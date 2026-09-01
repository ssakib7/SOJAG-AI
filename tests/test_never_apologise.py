"""The bot never apologises to a customer for its own machinery.

"দুঃখিত, একটি সমস্যা হয়েছে। অনুগ্রহ করে আবার চেষ্টা করুন।" was a canned fallback, and
customers were getting it two and three times in a row under their own messages — a line
that told them their message had failed while giving them nothing to do about it. The
constant is deleted, but that alone would only stop OUR code from writing it: the model
can produce the same sentence unprompted, out of the knowledge base, out of an
admin-edited prompt, or out of nothing. So the rule is enforced on the way OUT, at the one
function every customer-facing message passes through.

The customer gets silence instead, and the team gets paged. That is the trade this whole
file exists to lock in.
"""

from __future__ import annotations

import asyncio

import pytest

from app.utils.text import apologises_for_a_failure

BANNED = "দুঃখিত, একটি সমস্যা হয়েছে। অনুগ্রহ করে আবার চেষ্টা করুন।"


@pytest.fixture
def sending(monkeypatch):
    """The real send_message, with only the HTTP hop stubbed — so the guard runs."""
    from app.meta import graph

    delivered: list[str] = []

    async def fake_send_one(recipient_id, chunk):
        delivered.append(chunk)
        return True

    monkeypatch.setattr(graph, "_send_one", fake_send_one)
    return delivered


def test_the_exact_sentence_never_reaches_the_customer(sending):
    from app.meta import graph

    assert asyncio.run(graph.send_message("cust", BANNED)) is False
    assert sending == [], f"the banned apology went out anyway: {sending}"


@pytest.mark.parametrize("text", [
    BANNED,
    "দুঃখিত, একটি সমস্যা হয়েছে।",
    "সরি স্যার, কিছু একটা ভুল হয়েছে। আবার চেষ্টা করুন।",
    "দুঃখিত, প্রযুক্তিগত ত্রুটির কারণে উত্তর দিতে পারছি না।",
    "Sorry, something went wrong. Please try again.",
    "দুঃখিত স্যার, সিস্টেমে একটু সমস্যা হচ্ছে।",
])
def test_every_way_of_writing_it_is_caught(sending, text):
    from app.meta import graph

    assert apologises_for_a_failure(text)
    assert asyncio.run(graph.send_message("cust", text)) is False
    assert sending == []


@pytest.mark.parametrize("text", [
    # Ordinary courtesy. An apology is not the problem — blaming our software is.
    "দুঃখিত স্যার, এই তথ্যটি আমাদের কাছে নেই। একজন প্রতিনিধি আপনাকে জানাবেন।",
    "দুঃখিত, ১৯তম বিজেএস ব্যাচের ভর্তি ইতিমধ্যে শেষ হয়ে গেছে।",
    # A failure word with no apology: the bot asking ABOUT the customer's problem.
    "আপনার পেমেন্টে কোনো সমস্যা হয়েছে কিনা আমাদের টিম দেখে জানাবে।",
    "কোর্সে ভর্তি হতে কোনো সমস্যা হচ্ছে কি স্যার?",
    # The ordinary answer, which must obviously survive.
    "১৯তম BJS Preli MBG Crash কোর্সের ফি ৳৮,০০০ স্যার।",
    "Course er fee 8,000 taka sir. Class shuru hobe 15 September theke.",
])
def test_normal_replies_still_go_out(sending, text):
    from app.meta import graph

    assert not apologises_for_a_failure(text)
    assert asyncio.run(graph.send_message("cust", text)) is True
    assert sending == [text]


def test_a_crashed_batch_tells_the_team_and_not_the_customer(sending, monkeypatch):
    """The last-resort path, for a turn that blew up after the customer's messages were
    already consumed. It used to apologise; the apology made the chat look answered to
    anyone scrolling the Page inbox, so the one person who could help scrolled past."""
    from app.ops import escalate
    from app.pipeline import queues

    reasons: list[str] = []

    async def fake_notify(sender_id, session, category, reason, customer_message=""):
        reasons.append(reason)
        return True

    monkeypatch.setattr(escalate, "notify_team", fake_notify)
    asyncio.run(queues._rescue_failed_batch("crash-1", RuntimeError("kaboom")))

    assert sending == [], "the customer was told our software failed"
    assert reasons and "RuntimeError" in reasons[0], "the team was not told what broke"


def test_a_blocked_reply_is_never_recorded_as_answered(sending, monkeypatch):
    """Blocking must leave the same trace as any undelivered reply: nothing in history,
    and the team told. A bot that believed it had answered would argue on the next turn
    with a message the customer never saw."""
    from app.agents import factory
    from app.db import state
    from app.meta import graph
    from app.ops import escalate
    from app.pipeline import turn

    async def apologising_run_turn(t):
        return BANNED  # the model wrote it itself; no constant involved

    async def fake_action(recipient_id, action):
        pass

    async def fake_profile(sender_id):
        return {"name": None, "gender": None, "link": None, "at": 0}

    async def fake_inbox_url(sender_id):
        return None

    escalations: list[str] = []

    async def fake_notify(sender_id, session, category, reason, customer_message=""):
        escalations.append(category)
        return True

    monkeypatch.setattr(factory, "run_turn", apologising_run_turn)
    monkeypatch.setattr(graph, "send_action", fake_action)
    monkeypatch.setattr(graph, "fetch_profile", fake_profile)
    monkeypatch.setattr(graph, "resolve_inbox_url", fake_inbox_url)
    monkeypatch.setattr(escalate, "notify_team", fake_notify)
    monkeypatch.setenv("REPLY_MIN_SECONDS", "0")
    monkeypatch.setenv("REPLY_MAX_SECONDS", "0")

    from app.config import get_settings

    get_settings.cache_clear()
    try:
        inbox = {"queue": [{"sender": {"id": "apo-1"},
                            "message": {"mid": "ma1", "text": "ফি কত?"}}], "running": True}
        asyncio.run(turn.handle_text_batch("apo-1", inbox))
    finally:
        get_settings.cache_clear()

    assert sending == [], "the customer was handed an apology for our own failure"
    assert state.sessions["apo-1"]["history"] == [], "a reply nobody saw must not enter history"
    assert escalations, "the customer got nothing and nobody was told"
