"""The model's deliberation must never reach a customer.

Live 2026-10-29: asked "ফ্রি ওরিয়েন্টেশন ক্লাসের জন্য কীভাবে বুক করব?", the bot sent back
its own reasoning — our prompt rules quoted in English, "Wait, look at:" — as the reply.
Thinking runs at OpenRouter's "minimal" floor, and on a turn the model finds confusing it
thinks out loud into the answer. Such a draft is now discarded like an empty one.
"""

from __future__ import annotations

import asyncio

import pytest

from app.db import state
from app.utils.text import leaks_reasoning
from tests.test_audit_probes import (  # noqa: F401
    _delivering_graph_stub,
    _text_event,
    fast_replies,
    leads_and_payments_on,
)

LIVE_LEAK = (
    'াডাম" (in a Banglish reply: "Sir" or "Madam"; default to স্যার or Sir when their gender is '
    'not clear)."\n"GREETING: If there are ANY earlier messages in the history, do not greet, do '
    'not welcome, do not say "স্বাগতম"." (There is Page message in history, so NO greeting).\n'
    'Wait, look at:\n"WHICH COURSE. If the customer says only "20th BJS" or "BJS course" and the '
    "knowledge base has more"
)
GOOD_REPLY = "অনলাইন ফ্রি ক্লাসের Zoom লিংক পেতে অনুগ্রহ করে এক লাইনে লিখে পাঠান: ১. আপনার পূর্ণ নাম"


@pytest.mark.parametrize("reply", [
    LIVE_LEAK,
    "The customer wants the free class. I should ask for their name first.",
    "Let me check the funnel. Step 1 says to send the video.",
    "=== CURRENT DATE ===\nToday is Thursday.",
])
def test_deliberation_is_recognised(reply):
    assert leaks_reasoning(reply)


@pytest.mark.parametrize("reply", [
    GOOD_REPLY,
    "অভিনন্দন স্যার! আমাদের অফলাইন ফ্রি ক্লাসের জন্য আপনার নামে ১টি আসন চূড়ান্তভাবে বুক (Seat Confirmed) করা হয়েছে।",
    "Run For 20th BJS Course এর ফি অফলাইনে ২৫,০০০ টাকা এবং অনলাইনে ২০,০০০ টাকা।",
    "Demo class dekhte ei link-e jan: https://dejureacademy.com/timeline Class gulo dekhe janaben, Sir.",
    "Ji Sir, ektu wait korun, ami details pathacchi.",
])
def test_real_replies_pass(reply):
    assert not leaks_reasoning(reply)


def _pages(monkeypatch) -> list[str]:
    from app.ops import escalate

    reasons: list[str] = []

    async def fake_notify(sender_id, session, category, reason, customer_message=""):
        reasons.append(reason)
        return True

    monkeypatch.setattr(escalate, "notify_team", fake_notify)
    return reasons


def test_a_leaked_draft_is_retried_and_the_clean_retry_is_sent(monkeypatch, leads_and_payments_on):
    from app.agents import factory
    from app.pipeline import turn

    sent: list[str] = []
    _delivering_graph_stub(monkeypatch, sent)
    pages = _pages(monkeypatch)

    async def run_turn(t):
        return GOOD_REPLY if t.attempt else LIVE_LEAK

    monkeypatch.setattr(factory, "run_turn", run_turn)
    inbox = {"queue": [_text_event("leak-1", "ml1", "ফ্রি ওরিয়েন্টেশন ক্লাসের জন্য কীভাবে বুক করব?")],
             "running": True}
    asyncio.run(turn.handle_text_batch("leak-1", inbox))

    assert sent == [GOOD_REPLY]
    assert pages == []
    assert state.sessions["leak-1"]["history"][-1]["parts"][0]["text"] == GOOD_REPLY


def test_a_leak_twice_sends_nothing_and_pages_the_team(monkeypatch, leads_and_payments_on):
    from app.agents import factory
    from app.pipeline import turn

    sent: list[str] = []
    _delivering_graph_stub(monkeypatch, sent)
    pages = _pages(monkeypatch)

    async def run_turn(t):
        return LIVE_LEAK

    monkeypatch.setattr(factory, "run_turn", run_turn)
    inbox = {"queue": [_text_event("leak-2", "ml2", "ফ্রি ওরিয়েন্টেশন ক্লাসের জন্য কীভাবে বুক করব?")],
             "running": True}
    asyncio.run(turn.handle_text_batch("leak-2", inbox))

    assert sent == [], f"the customer must never see the model's reasoning — got {sent}"
    assert pages and "চিন্তা/নির্দেশনা" in pages[0], "nobody was told the customer got nothing"
    # The leak is not history: only the customer's words survive.
    assert all(h["role"] == "user" for h in state.sessions["leak-2"]["history"])
