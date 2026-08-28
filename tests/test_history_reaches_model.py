"""Does the routed member actually SEE the conversation so far?

The system prompt now tells the model to read the history and to greet only in the first
reply of a conversation. That instruction is worthless if the history never reaches the
member: the bot would re-greet and re-introduce itself on every turn, which is exactly what
customers were seeing.

The plumbing is not obvious. `run_turn` builds `[*history, user]` and hands it to a Team in
route mode with `add_history_to_context=False`; the leader then delegates to one member.
Whether the member receives the full list or just the delegated task is Agno's business,
not ours — so this asserts it on the wire, against the same stub the e2e test uses.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

from tests.test_e2e import StubHandler, e2e_client, stub_server  # noqa: F401


def _sign(body: bytes) -> str:
    from app.config import get_settings

    return "sha256=" + hmac.new(get_settings().app_secret.encode(), body, hashlib.sha256).hexdigest()


PRIOR_CUSTOMER = "১৯তম বিজেএস কোর্সের ফি কত?"
PRIOR_REPLY = "১৯তম BJS Preli MBG Crash কোর্সের ফি ৳৮,০০০।"


@pytest.fixture
def seeded_session():
    """A customer we have already exchanged one full turn with."""
    from app.db import state

    sender = "cust-history"
    session = state.get_session(sender) if hasattr(state, "get_session") else None
    if session is None:
        state.sessions[sender] = {
            "history": [], "last_active": state.now_ms(),
            "lead": {"captured": False, "name": None, "asked": False, "msg_count": 0},
            "followup_sent": False, "returning": False, "customer_name": None,
            "recent_mids": [], "payments": [], "non_text_prompted_at": 0,
        }
        session = state.sessions[sender]
    session["history"] = [
        {"role": "user", "parts": [{"text": PRIOR_CUSTOMER}]},
        {"role": "model", "parts": [{"text": PRIOR_REPLY}]},
    ]
    yield sender
    state.sessions.pop(sender, None)


def test_member_receives_the_conversation_so_far(e2e_client, seeded_session):  # noqa: F811
    payload = {"object": "page", "entry": [{"messaging": [{
        "sender": {"id": seeded_session},
        "message": {"mid": "m-hist-1", "text": "আর অফলাইনে?"},
    }]}]}
    body = json.dumps(payload).encode()
    assert e2e_client.post("/webhook", content=body, headers={
        "content-type": "application/json", "x-hub-signature-256": _sign(body),
    }).status_code == 200

    deadline = time.time() + 60
    member_calls = []
    while time.time() < deadline:
        member_calls = [e for e in StubHandler.log
                        if e["kind"] == "llm" and e.get("role") == "member-final" and e.get("messages")]
        if member_calls:
            break
        time.sleep(0.25)

    assert member_calls, f"no member call reached the stub; log={StubHandler.log}"
    # The whole request, system message included: route mode collapses the input list into
    # one user turn and drops assistant replies, so the history reaches the member through
    # the === CONVERSATION SO FAR === block in its system prompt instead.
    flat = json.dumps(member_calls[-1]["messages"], ensure_ascii=False)

    assert PRIOR_CUSTOMER in flat, (
        "the customer's earlier message never reached the member — it cannot know this is "
        "not the first turn, so it will greet and re-introduce every time"
    )
    assert PRIOR_REPLY in flat, (
        "our own earlier reply never reached the member — it cannot avoid repeating itself "
        "or re-asking for something already given"
    )
    assert "আর অফলাইনে?" in flat, "the current message must be there too"
    assert "=== CONVERSATION SO FAR ===" in flat, "the history block is missing entirely"
    assert "do NOT greet" in flat


def test_first_reply_carries_no_history_block(e2e_client):  # noqa: F811
    """A genuinely first message must NOT be told the conversation is already under way —
    that block is what suppresses the greeting, and suppressing it on turn one would make
    the bot answer a cold 'হ্যালো' with no welcome at all."""
    payload = {"object": "page", "entry": [{"messaging": [{
        "sender": {"id": "cust-fresh"},
        "message": {"mid": "m-fresh-1", "text": "কোর্স সম্পর্কে জানতে চাই"},
    }]}]}
    body = json.dumps(payload).encode()
    assert e2e_client.post("/webhook", content=body, headers={
        "content-type": "application/json", "x-hub-signature-256": _sign(body),
    }).status_code == 200

    deadline = time.time() + 60
    calls = []
    while time.time() < deadline:
        calls = [e for e in StubHandler.log
                 if e["kind"] == "llm" and e.get("role") == "member-final" and e.get("messages")]
        if calls:
            break
        time.sleep(0.25)

    assert calls, "no member call reached the stub"
    assert "=== CONVERSATION SO FAR ===" not in json.dumps(calls[-1]["messages"], ensure_ascii=False)
