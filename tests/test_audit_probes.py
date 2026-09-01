"""Launch-audit regression suite (findings from the 2026-08-29 audit).

Each test pins an invariant the bot must hold to run unattended: a reply the customer
never received is never treated as delivered, one customer is never answered by two
concurrent turns, an ad click is never met with silence, a phone number in hand is never
dropped, and the payment backstop pages the team for payments rather than for questions.

Every one of these failed when it was written; they are the proof the fixes hold. They
drive the real pipeline modules (queues, turn, graph) with the LLM and Graph API stubbed
at the seams — no network, no API keys.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.config import get_settings
from app.db import state


@pytest.fixture
def fast_replies(monkeypatch):
    """Zero humanized pause + fresh settings, restored afterwards."""
    monkeypatch.setenv("REPLY_MIN_SECONDS", "0")
    monkeypatch.setenv("REPLY_MAX_SECONDS", "0")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def leads_and_payments_on(monkeypatch, fast_replies):
    monkeypatch.setenv("GOOGLE_SHEET_WEBAPP_URL", "https://sheet.invalid/exec")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _graph_stub(monkeypatch, sent: list[str]):
    """Neutralize every outbound Graph call; record sent message texts."""
    from app.meta import graph

    async def fake_send(recipient_id, text):
        sent.append(text)

    async def fake_action(recipient_id, action):
        pass

    async def fake_profile(sender_id):
        return {"name": None, "gender": None, "link": None, "at": 0}

    async def fake_inbox_url(sender_id):
        return None

    monkeypatch.setattr(graph, "send_message", fake_send)
    monkeypatch.setattr(graph, "send_action", fake_action)
    monkeypatch.setattr(graph, "fetch_profile", fake_profile)
    monkeypatch.setattr(graph, "resolve_inbox_url", fake_inbox_url)


def _delivering_graph_stub(monkeypatch, sent: list[str]):
    """_graph_stub's send returns None, i.e. NOT delivered — fine for tests that only
    care what was written, wrong for tests about what enters history (nothing commits
    unless the customer actually received the message)."""
    from app.meta import graph

    _graph_stub(monkeypatch, sent)

    async def delivering_send(recipient_id, text):
        sent.append(text)
        return True

    monkeypatch.setattr(graph, "send_message", delivering_send)


def _text_event(sender: str, mid: str, text: str) -> dict:
    return {"sender": {"id": sender}, "message": {"mid": mid, "text": text}}


# ---------------------------------------------------------------------------
# PROBE 1 — per-sender serialization under Meta's batched webhook delivery.
# Two events for the same sender in ONE webhook POST are enqueued synchronously;
# the second enqueue sees running=False (the first loop task hasn't started yet)
# and spawns a SECOND sender loop. Invariant: one sender is never processed by
# two loops concurrently, and a message arriving mid-turn joins that turn.
# ---------------------------------------------------------------------------
def test_same_sender_never_processed_concurrently(monkeypatch):
    from app.pipeline import queues, turn

    record = {"active": 0, "max_active": 0, "batches": []}

    async def fake_batch(sender_id, inbox):
        record["active"] += 1
        record["max_active"] = max(record["max_active"], record["active"])
        texts = [e["message"]["text"] for e in inbox["queue"]]
        inbox["queue"].clear()
        record["batches"].append(texts)
        await asyncio.sleep(0.3)  # simulated model latency
        record["active"] -= 1

    monkeypatch.setattr(turn, "handle_text_batch", fake_batch)

    async def main():
        # One webhook POST delivering two bubbles for the same sender:
        queues.enqueue_event(_text_event("race-u", "r1", "bubble 1"))
        queues.enqueue_event(_text_event("race-u", "r2", "bubble 2"))
        await asyncio.sleep(0.1)  # loop 1 is mid-"generation"
        # Third bubble arrives while the first turn is still generating:
        queues.enqueue_event(_text_event("race-u", "r3", "bubble 3"))
        await asyncio.sleep(0.8)

    asyncio.run(main())
    assert record["max_active"] == 1, (
        f"two sender loops ran concurrently for one sender: {record}"
    )


# ---------------------------------------------------------------------------
# PROBE 2 — Facebook Send API failure. Invariant (from turn.py's own contract):
# "History commits only after the reply is actually sent." If the Send API is
# down (timeout/429/5xx), the customer got nothing — the turn must not enter
# history as if it were delivered, and the failure must not be silent.
# ---------------------------------------------------------------------------
def test_failed_send_does_not_commit_history(monkeypatch, fast_replies):
    from app.agents import factory
    from app.meta import graph
    from app.pipeline import turn

    async def fake_run_turn(t):
        return "আপনার প্রশ্নের উত্তর: কোর্স ফি ২২,০০০ টাকা।"

    monkeypatch.setattr(factory, "run_turn", fake_run_turn)

    class DownClient:
        async def post(self, *a, **k):
            raise httpx.ConnectError("facebook unreachable")

        async def get(self, *a, **k):
            raise httpx.ConnectError("facebook unreachable")

    monkeypatch.setattr(graph, "client", lambda: DownClient())
    monkeypatch.setattr(graph, "_profiles", {})

    inbox = {"queue": [_text_event("send-u", "ms1", "কোর্স ফি কত?")], "running": True}
    asyncio.run(turn.handle_text_batch("send-u", inbox))

    assert state.sessions["send-u"]["history"] == [], (
        "reply never reached the customer (Send API down), yet the turn was "
        "committed to history as delivered"
    )


# ---------------------------------------------------------------------------
# PROBE 3 — ad entry points. A user clicking a Click-to-Messenger ad or the
# Get Started button arrives as a postback/referral event (often with ad_id).
# Invariant: an ad-driven opener is answered, and never silently dropped.
# ---------------------------------------------------------------------------
def test_ad_postback_is_not_silently_dropped(monkeypatch):
    from app.meta import graph
    from app.pipeline import queues

    sent: list[str] = []
    _graph_stub(monkeypatch, sent)

    async def main():
        queues.enqueue_event({
            "sender": {"id": "ad-user"},
            "recipient": {"id": "page"},
            "timestamp": 1756400000000,
            "postback": {
                "title": "Get Started",
                "payload": "GET_STARTED",
                "referral": {"ref": "bjs-alpha-ad", "source": "ADS",
                             "type": "OPEN_THREAD", "ad_id": "120211234567890"},
            },
        })
        await asyncio.sleep(3.0)  # the opening greeting has its own short humanized pause

    asyncio.run(main())
    assert sent, "Get Started postback from an ad got no reply (event ignored)"


def test_ad_referral_event_is_not_silently_dropped(monkeypatch):
    from app.pipeline import queues

    sent: list[str] = []
    _graph_stub(monkeypatch, sent)

    async def main():
        queues.enqueue_event({
            "sender": {"id": "ad-user-2"},
            "referral": {"ref": "bar-course-ad", "source": "ADS",
                         "type": "OPEN_THREAD", "ad_id": "120219876543210"},
        })
        await asyncio.sleep(3.0)  # the opening greeting has its own short humanized pause

    asyncio.run(main())
    assert sent, "messaging_referrals event got no reply (event ignored)"


# ---------------------------------------------------------------------------
# PROBE 4 — duplicate escalation alerts. A customer repeating a payment claim
# in different words ("টাকা পাঠিয়েছি" ... "টাকা পাঠাইছি, পাইছেন?") with no trx id
# should not page the team once per rewording.
# ---------------------------------------------------------------------------
def test_repeated_claim_wording_does_not_realert(monkeypatch, leads_and_payments_on):
    from app.agents.turn_context import TurnContext
    from app.leads import outbox
    from app.pipeline import turn

    sent: list[str] = []
    _graph_stub(monkeypatch, sent)

    alerts: list[dict] = []

    async def fake_enqueue_payment(payment):
        alerts.append(payment)
        return "x"

    monkeypatch.setattr(outbox, "enqueue_payment", fake_enqueue_payment)

    session = state.get_session("pay-u")
    session["lead"]["captured"] = True  # keep the lead path out of the way

    async def main():
        # First two: they say they have paid but sent nothing to identify it. A
        # representative could not look these up, so they are a prompt to ask for the
        # proof — not something to page the team about.
        for text in ("টাকা পাঠিয়ে দিয়েছি বিকাশে",
                     "টাকা পাঠাইছি, পাইছেন কিনা জানাবেন?"):
            t = TurnContext(sender_id="pay-u", session=session, combined_text=text)
            await turn._apply_side_effects("pay-u", session, t, text, None)
        assert alerts == [], f"unidentified claim alerted {len(alerts)} time(s)"

        # Then the transaction id arrives, and the same claim reworded after it.
        for text in ("TRX BKX9912ZQ44 পাঠিয়েছি",
                     "BKX9912ZQ44 পাঠাইছি, পাইছেন কিনা জানাবেন?"):
            t = TurnContext(sender_id="pay-u", session=session, combined_text=text)
            await turn._apply_side_effects("pay-u", session, t, text, None)

    asyncio.run(main())
    assert len(alerts) == 1, (
        f"one payment, {len(alerts)} Telegram alerts (backstop re-alerted on reworded claim)"
    )
    assert alerts[0]["trxId"] == "BKX9912ZQ44"


# ---------------------------------------------------------------------------
# PROBE 5 — LLM total outage. The deterministic backstop must still capture a
# typed phone number, and the customer must get SILENCE, not an apology: the
# team is paged instead, because they are the only ones who can fix an outage.
# ---------------------------------------------------------------------------
def test_llm_outage_phone_backstop_still_captures(monkeypatch, leads_and_payments_on):
    from app.agents import factory
    from app.leads import outbox
    from app.ops import escalate
    from app.pipeline import turn

    sent: list[str] = []
    _graph_stub(monkeypatch, sent)

    async def broken_run_turn(t):
        raise RuntimeError("LLM 429 quota exhausted")

    monkeypatch.setattr(factory, "run_turn", broken_run_turn)

    leads: list[dict] = []

    async def fake_enqueue_lead(lead):
        leads.append(lead)
        return "x"

    monkeypatch.setattr(outbox, "enqueue_lead", fake_enqueue_lead)

    reasons: list[str] = []

    async def fake_notify(sender_id, session, category, reason, customer_message=""):
        reasons.append(reason)
        return True

    monkeypatch.setattr(escalate, "notify_team", fake_notify)

    inbox = {"queue": [_text_event("llm-u", "ml1", "Orko Rahman 01712345678")],
             "running": True}
    asyncio.run(turn.handle_text_batch("llm-u", inbox))

    assert sent == [], f"the customer was handed an apology for our outage: {sent}"
    assert len(leads) == 1 and leads[0]["phone"] == "01712345678", (
        "phone typed during LLM outage was not captured by the backstop"
    )
    assert reasons, "nobody was told the model is down"
    assert "API key" in reasons[0], (
        "the page must say this is an OUTAGE, not a content problem — the two need "
        f"different people: {reasons[0]}"
    )


# ---------------------------------------------------------------------------
# PROBE 6 — phone backstop false negatives: a phone directly preceded/followed
# by another digit token (amount, roll number, second phone) must still be found.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "22000 01712345678",                # amount then phone
    "Roll 123 01712345678",             # roll no then phone
    "01712345678 01912345678",          # two numbers ("call either")
])
def test_find_phone_survives_adjacent_digit_tokens(text):
    from app.utils.text import find_phone

    assert find_phone(text) == "01712345678", f"phone lost in {text!r}"


# ---------------------------------------------------------------------------
# PROBE 7 — payment-claim backstop false positives that page the team.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "ভর্তি ফি কত হয়েছে?",          # a fee QUESTION
    "iPhone14Pro theke likhtesi",   # alnum token looks like a trx id
    "fee completely bujhini",       # 'complete' matched inside 'completely'
])
def test_claim_backstop_ignores_non_claims(text):
    from app.utils.text import looks_like_payment_claim

    assert not looks_like_payment_claim(text), (
        f"non-claim {text!r} would page the team as a PAYMENT CLAIM"
    )


# ---------------------------------------------------------------------------
# PROBE 8 — an empty text bubble (no attachment) should be ignored, not
# answered with the "please write your question" nudge meant for voice/video.
# ---------------------------------------------------------------------------
def test_empty_text_bubble_stays_silent(monkeypatch):
    from app.pipeline import turn

    sent: list[str] = []
    _graph_stub(monkeypatch, sent)

    asyncio.run(turn.handle_messaging_event(
        {"sender": {"id": "empty-u"}, "message": {"mid": "m-empty", "text": ""}}
    ))
    assert not sent, f"empty text bubble was answered with: {sent}"


# ---------------------------------------------------------------------------
# The payment backstop must stay LOUD for real claims — the noise fixes above
# must not have bought quiet by going deaf.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "টাকা পাঠিয়েছি বিকাশে",
    "TrxID 8N7A2B3C4D",
    "bkash e 22000 pathaisi",
    "টাকা পাঠিয়েছি, পেয়েছেন কি?",   # a claim that also asks a question
])
def test_claim_backstop_still_catches_real_claims(text):
    from app.utils.text import looks_like_payment_claim

    assert looks_like_payment_claim(text), f"real payment claim {text!r} would NOT alert"


# ---------------------------------------------------------------------------
# Human takeover: when a colleague answers from the Page inbox, Meta echoes the
# message with NO app_id. The bot must fall silent for that customer instead of
# talking over the person handling them.
# ---------------------------------------------------------------------------
def test_human_reply_from_page_inbox_pauses_the_bot(monkeypatch):
    from app.pipeline import queues, turn

    handled: list[str] = []

    async def fake_batch(sender_id, inbox):
        handled.append(sender_id)
        inbox["queue"].clear()

    monkeypatch.setattr(turn, "handle_text_batch", fake_batch)

    async def main():
        # A colleague replies from the Page inbox: echo, no app_id, customer is recipient.
        queues.enqueue_event({
            "sender": {"id": "page-1"},
            "recipient": {"id": "cust-h"},
            "message": {"mid": "m-echo", "text": "আমি দেখছি স্যার", "is_echo": True},
        })
        assert state.is_human_handling("cust-h")
        queues.enqueue_event(_text_event("cust-h", "m-after", "ok thanks"))
        await asyncio.sleep(0.3)

    asyncio.run(main())
    assert handled == [], "bot answered a customer a human had taken over"


def test_bot_own_echo_does_not_pause_it(monkeypatch):
    """Our OWN sends echo back too — with an app_id. Those must not look like a takeover."""
    from app.pipeline import queues

    queues.enqueue_event({
        "sender": {"id": "page-1"},
        "recipient": {"id": "cust-e"},
        "message": {"mid": "m-echo-2", "text": "ধন্যবাদ", "is_echo": True, "app_id": 1234567890},
    })
    assert not state.is_human_handling("cust-e")


def test_our_own_echo_without_an_app_id_is_still_ours(monkeypatch):
    """The app_id is Meta's field to omit, and an echo of OUR message read as a colleague
    would silence the bot for eight hours. A mid we know we sent overrules the heuristic."""
    from app.meta import graph
    from app.pipeline import queues

    graph._record_our_mid('{"recipient_id": "cust-m", "message_id": "m-ours-1"}')
    assert graph.was_sent_by_us("m-ours-1")

    queues.enqueue_event({
        "sender": {"id": "page-1"},
        "recipient": {"id": "cust-m"},
        "message": {"mid": "m-ours-1", "text": "ধন্যবাদ", "is_echo": True},
    })
    assert not state.is_human_handling("cust-m"), (
        "the bot mistook its own message for a colleague's and stood down"
    )


# ---------------------------------------------------------------------------
# A turn takes 15-35s (reading beat + model + humanized pause). The queue's
# takeover checks all happen BEFORE a turn starts, so a colleague who replies
# while one is in flight used to be talked over by a reply already composed.
# The last moment it can be withdrawn is immediately before the send.
# ---------------------------------------------------------------------------
def test_reply_is_withheld_when_a_human_replies_mid_turn(monkeypatch, fast_replies):
    from app.agents.turn_context import TurnContext
    from app.pipeline import turn

    sent: list[str] = []

    async def _noop(*a, **kw):
        return None

    async def _model_turn(sender_id, session, text, *a, **kw):
        # The colleague answers from the Page inbox while the model is still working.
        state.pause_for_human(sender_id)
        ctx = TurnContext(sender_id=sender_id, session=session, combined_text=text)
        ctx.extras = {"text": "কোর্স ফি ২২,০০০ টাকা।", "commit": True}
        return ctx

    monkeypatch.setattr(turn.graph, "send_message", lambda sid, text: sent.append(text) or _noop())
    monkeypatch.setattr(turn.graph, "send_action", _noop)
    monkeypatch.setattr(turn, "_run_model_turn", _model_turn)
    monkeypatch.setattr(turn, "_profile_with_gender", lambda sid: _noop())
    monkeypatch.setattr(turn.followups, "schedule", lambda sid, session: None)
    monkeypatch.setattr(turn, "_rand_between", lambda lo, hi: 0)

    inbox = {"queue": [_text_event("mid-u", "m1", "কোর্স ফি কত?")], "running": True}
    asyncio.run(turn.handle_text_batch("mid-u", inbox))

    assert sent == [], "bot sent a reply it had already composed on top of a colleague's"
    assert state.sessions["mid-u"]["history"] == [], (
        "a withheld draft was committed to history as if the customer had seen it"
    )


def test_withheld_reply_still_captures_the_lead(monkeypatch, leads_and_payments_on):
    """Standing down is about not talking over a person — not about throwing away a phone
    number the customer already typed."""
    from app.agents.turn_context import TurnContext
    from app.pipeline import turn

    captured: list[dict] = []

    async def _noop(*a, **kw):
        return None

    async def _model_turn(sender_id, session, text, *a, **kw):
        state.pause_for_human(sender_id)
        ctx = TurnContext(sender_id=sender_id, session=session, combined_text=text)
        ctx.extras = {"text": "ধন্যবাদ স্যার।", "commit": True}
        return ctx

    monkeypatch.setattr(turn.graph, "send_message", _noop)
    monkeypatch.setattr(turn.graph, "send_action", _noop)
    monkeypatch.setattr(turn, "_run_model_turn", _model_turn)
    monkeypatch.setattr(turn, "_profile_with_gender", lambda sid: _noop())
    monkeypatch.setattr(turn, "_best_name", lambda sid, session: _noop())
    monkeypatch.setattr(turn, "capture_lead",
                        lambda sid, session, lead: captured.append(lead) or _noop())
    monkeypatch.setattr(turn.followups, "schedule", lambda sid, session: None)
    monkeypatch.setattr(turn, "_rand_between", lambda lo, hi: 0)

    inbox = {"queue": [_text_event("mid-l", "m1", "আমার নাম্বার ০১৭১২৩৪৫৬৭৮")], "running": True}
    asyncio.run(turn.handle_text_batch("mid-l", inbox))

    assert captured, "a phone number in hand was dropped because a colleague stepped in"
    assert captured[0]["phone"].endswith("1712345678")


def test_the_takeover_pause_never_outlives_the_conversation():
    """prune() refuses to drop a paused session, so a pause longer than the session TTL
    freezes a thread the bot has already forgotten. Keep the ordering pinned."""
    assert state.HUMAN_TAKEOVER_MS < state.SESSION_TTL_MS, (
        "the human-takeover pause outlasts the conversation it is protecting"
    )


def test_admin_can_hand_a_paused_conversation_back_to_the_bot():
    """The pause is persisted, so without a release there is no way to undo it at all —
    not even a restart."""
    from app.pipeline import queues

    queues.enqueue_event({
        "sender": {"id": "page-1"},
        "recipient": {"id": "cust-r"},
        "message": {"mid": "m-echo-r", "text": "আমি দেখছি", "is_echo": True},
    })
    assert state.is_human_handling("cust-r")

    assert state.resume_bot("cust-r") is True
    assert not state.is_human_handling("cust-r"), "the bot was not given the conversation back"
    assert state.resume_bot("cust-r") is False, "resuming an unpaused conversation reported success"


# ---------------------------------------------------------------------------
# Ad attribution: the ad that paid for a lead must reach the sheet row.
# ---------------------------------------------------------------------------
def test_ad_attribution_rides_along_to_the_lead(monkeypatch, leads_and_payments_on):
    from app.leads import outbox
    from app.pipeline import turn

    sent: list[str] = []
    _graph_stub(monkeypatch, sent)

    leads: list[dict] = []

    async def fake_enqueue_lead(lead):
        leads.append(lead)
        return "x"

    monkeypatch.setattr(outbox, "enqueue_lead", fake_enqueue_lead)

    async def main():
        await turn.handle_entry_event({
            "sender": {"id": "ad-lead"},
            "referral": {"ref": "bjs-alpha-ad", "source": "ADS", "ad_id": "120211234567890"},
        })
        session = state.get_session("ad-lead")
        await turn.capture_lead("ad-lead", session, {
            "name": "Orko", "phone": "01712345678", "interest": "Alpha", "remarks": "-",
        })

    asyncio.run(main())
    assert leads and leads[0]["adId"] == "120211234567890", (
        f"ad attribution lost on the way to the sheet: {leads}"
    )
    assert leads[0]["adRef"] == "bjs-alpha-ad"


# ---------------------------------------------------------------------------
# Escalation: a customer who needs a human must reach the team even when they
# never hand over a phone number.
# ---------------------------------------------------------------------------
def test_notify_team_reaches_the_outbox(monkeypatch, leads_and_payments_on):
    from app.leads import outbox
    from app.ops import escalate

    _graph_stub(monkeypatch, [])
    notices: list[dict] = []

    async def fake_enqueue_notice(notice):
        notices.append(notice)
        return "x"

    monkeypatch.setattr(outbox, "enqueue_notice", fake_enqueue_notice)

    session = state.get_session("angry-u")
    asyncio.run(escalate.notify_team(
        "angry-u", session, "angry", "গ্রাহক ক্ষুব্ধ, রিফান্ড চাইছেন", customer_message="রিফান্ড চাই!",
    ))
    assert len(notices) == 1
    assert notices[0]["category"] == "angry"
    assert "রিফান্ড" in notices[0]["reason"]


def test_repeat_escalation_of_the_same_kind_is_throttled(monkeypatch, leads_and_payments_on):
    from app.leads import outbox
    from app.ops import escalate

    _graph_stub(monkeypatch, [])
    notices: list[dict] = []

    async def fake_enqueue_notice(notice):
        notices.append(notice)
        return "x"

    monkeypatch.setattr(outbox, "enqueue_notice", fake_enqueue_notice)
    session = state.get_session("angry-u2")

    async def main():
        for _ in range(3):
            await escalate.notify_team("angry-u2", session, "angry", "still upset")
        # A DIFFERENT kind of problem in the same chat is still worth a ping.
        await escalate.notify_team("angry-u2", session, "refund", "now asking for a refund")

    asyncio.run(main())
    assert [n["category"] for n in notices] == ["angry", "refund"], (
        f"escalation throttling wrong: {[n['category'] for n in notices]}"
    )


def test_undelivered_reply_pages_the_team(monkeypatch, leads_and_payments_on):
    """A reply Facebook refused leaves the customer in silence — a human must be told."""
    from app.agents import factory
    from app.meta import graph
    from app.ops import escalate
    from app.pipeline import turn

    async def fake_run_turn(t):
        return "কোর্স ফি ২২,০০০ টাকা।"

    monkeypatch.setattr(factory, "run_turn", fake_run_turn)

    async def dead_send(recipient_id, text):
        return False

    async def fake_action(recipient_id, action):
        pass

    async def fake_profile(sender_id):
        return {"name": None, "gender": None, "link": None, "at": 0}

    monkeypatch.setattr(graph, "send_message", dead_send)
    monkeypatch.setattr(graph, "send_action", fake_action)
    monkeypatch.setattr(graph, "fetch_profile", fake_profile)

    escalations: list[tuple] = []

    async def fake_notify(sender_id, session, category, reason, customer_message=""):
        escalations.append((sender_id, category))
        return True

    monkeypatch.setattr(escalate, "notify_team", fake_notify)

    inbox = {"queue": [_text_event("dead-u", "md1", "কোর্স ফি কত?")], "running": True}
    asyncio.run(turn.handle_text_batch("dead-u", inbox))

    assert escalations, "reply was never delivered and nobody was told"
    assert state.sessions["dead-u"]["history"] == []


# ---------------------------------------------------------------------------
# An empty model reply is a failure the CUSTOMER sees. Observed live on
# "I don't want to give my number, just get me a human" — the one message you
# cannot afford to answer with an apology and no follow-up.
#
# The apology is now gone entirely: one retry, then silence plus a page to the
# team. Customers were getting "দুঃখিত, একটি সমস্যা হয়েছে" two and three times in a
# row under their own messages — a line that told them their message had failed
# while giving them nothing to do about it.
# ---------------------------------------------------------------------------
def test_empty_model_reply_retries_then_stays_silent_and_pages_the_team(
    monkeypatch, leads_and_payments_on
):
    from app.agents import factory
    from app.ops import escalate
    from app.pipeline import turn

    sent: list[str] = []
    _graph_stub(monkeypatch, sent)

    attempts: list[int] = []

    async def empty_run_turn(t):
        attempts.append(t.attempt)
        return ""  # model produced no text and called no tool

    monkeypatch.setattr(factory, "run_turn", empty_run_turn)

    escalations: list[str] = []

    async def fake_notify(sender_id, session, category, reason, customer_message=""):
        escalations.append(category)
        return True

    monkeypatch.setattr(escalate, "notify_team", fake_notify)

    inbox = {"queue": [_text_event("empty-r", "me1", "শুধু একজন মানুষ ধরিয়ে দিন")],
             "running": True}
    asyncio.run(turn.handle_text_batch("empty-r", inbox))

    assert sent == [], f"the customer must get nothing, not an apology — got {sent}"
    assert attempts == [0, 1], f"expected one attempt then one retry, got {attempts}"
    assert escalations, "the customer got nothing at all and nobody was told"
    # Their words are still ours: the next turn — and the colleague picking this up —
    # must be able to see what was asked.
    assert state.sessions["empty-r"]["history"] == [
        {"role": "user", "parts": [{"text": "শুধু একজন মানুষ ধরিয়ে দিন"}]}
    ], "the unanswered message was dropped — the bot will ask for it all over again"


def test_a_retry_that_succeeds_is_the_reply_the_customer_gets(monkeypatch, leads_and_payments_on):
    """The whole point of the retry: one empty response must not cost the customer their
    answer."""
    from app.agents import factory
    from app.pipeline import turn

    sent: list[str] = []
    _delivering_graph_stub(monkeypatch, sent)

    async def flaky_run_turn(t):
        return "ফি ৪,৫০০ টাকা।" if t.attempt else ""

    monkeypatch.setattr(factory, "run_turn", flaky_run_turn)

    inbox = {"queue": [_text_event("retry-r", "mr1", "ফি কত?")], "running": True}
    asyncio.run(turn.handle_text_batch("retry-r", inbox))

    assert sent == ["ফি ৪,৫০০ টাকা।"]
    assert len(state.sessions["retry-r"]["history"]) == 2, "a delivered retry is a normal turn"


def test_one_agent_answers_and_it_keeps_every_tool():
    """The team's three members collapsed into one agent. Every tool the members carried
    between them has to survive that: a lead or a payment claim rides on these, and the
    pipeline's regex backstops only cover the two of them that have a regex."""
    from app.agents import factory
    from app.agents.tools import end_conversation, notify_team, report_payment, save_lead

    agent = factory.build_agent()
    assert agent.tools is factory._tools, "tools must resolve per run, not be frozen at build"
    names = {t.__name__ for t in factory._tools()}
    assert names == {f.__name__ for f in (end_conversation, notify_team, save_lead, report_payment)}


def test_llm_error_keeps_the_customers_message(monkeypatch, leads_and_payments_on):
    """A turn the model crashed on sends the customer nothing at all — but their own words
    must survive it, or the next turn re-asks for details they already gave. This is what
    the live chat showed: name, number and batch typed one after another, each answered
    with a fallback, and every one of them forgotten."""
    from app.agents import factory
    from app.pipeline import turn

    sent: list[str] = []
    _delivering_graph_stub(monkeypatch, sent)

    async def boom(t):
        raise RuntimeError("provider down")

    monkeypatch.setattr(factory, "run_turn", boom)

    inbox = {"queue": [_text_event("err-r", "er1", "batch 19th bjs Alpha")],
             "running": True}
    asyncio.run(turn.handle_text_batch("err-r", inbox))

    assert sent == []
    assert state.sessions["err-r"]["history"] == [
        {"role": "user", "parts": [{"text": "batch 19th bjs Alpha"}]}
    ]


def test_notice_without_text_gets_the_escalation_fallback(monkeypatch, leads_and_payments_on):
    """When the model raises the team but writes nothing, the customer must be told a
    person is coming — not handed the generic 'something went wrong' line."""
    from app.agents import factory
    from app.agents.turn_context import TurnContext
    from app.kb.defaults import FALLBACK_ESCALATED
    from app.ops import escalate
    from app.pipeline import turn

    sent: list[str] = []
    _graph_stub(monkeypatch, sent)

    async def notice_run_turn(t: TurnContext):
        t.notice = {"category": "human_request", "reason": "মানুষের সাথে কথা বলতে চান"}
        return ""

    monkeypatch.setattr(factory, "run_turn", notice_run_turn)

    async def fake_notify(sender_id, session, category, reason, customer_message=""):
        return True

    monkeypatch.setattr(escalate, "notify_team", fake_notify)

    inbox = {"queue": [_text_event("esc-r", "me2", "মানুষের সাথে কথা বলতে চাই")], "running": True}
    asyncio.run(turn.handle_text_batch("esc-r", inbox))

    assert sent == [FALLBACK_ESCALATED]


# ---------------------------------------------------------------------------
# A session restored from the database predates the newer keys (human_until,
# notices, entry, greeted). Boot must not crash on the customers who were
# mid-conversation during the deploy — they are the ones already engaged.
# ---------------------------------------------------------------------------
def test_pre_upgrade_sessions_survive_a_deploy(monkeypatch, leads_and_payments_on):
    from app.ops import followups
    from app.pipeline import queues

    legacy = {
        "history": [], "last_active": state.now_ms(),
        "lead": {"captured": False, "name": None, "asked": False, "msg_count": 1},
        "followup_sent": False, "returning": False, "customer_name": None,
        "recent_mids": [], "payments": [], "non_text_prompted_at": 0, "closed": False,
    }
    state.sessions["legacy-u"] = legacy

    # Each of these reads a key the old shape does not have.
    assert state.is_human_handling("legacy-u") is False
    assert queues.is_human_takeover({"message": {"is_echo": True}}) is True
    state.prune()
    followups.restore()
    state.pause_for_human("legacy-u")
    assert state.is_human_handling("legacy-u") is True
    assert state.resume_bot("legacy-u") is True
    assert state.is_human_handling("legacy-u") is False
