"""A lead's phone is only ever a number the customer typed.

The model used to be trusted when the customer had sent none: it called save_lead on the
first message of a chat with placeholders like 01700000000, which pass the format check.
The fake went to the Sheet, the lead counted as captured, and the real number the customer
sent next was then ignored.
"""

from __future__ import annotations

import asyncio

import pytest

from app.db import state
from app.utils.text import looks_like_phone_attempt
from tests.test_audit_probes import _graph_stub, fast_replies, leads_and_payments_on  # noqa: F401


def _call_save_lead(phone="01700000000", **context):
    from app.agents.tools import save_lead
    from app.agents.turn_context import TurnContext, current_turn

    turn = TurnContext(sender_id="sl", session=state.get_session("sl"), **context)
    token = current_turn.set(turn)
    try:
        result = save_lead(name="Unknown", phone=phone, interest="Run For free class, online", remarks="-")
    finally:
        current_turn.reset(token)
    return turn, result


@pytest.mark.parametrize("message", [".", "কি করতে হবে", "Apndr ekhane ki bjs er general subject korano hoy?",
                                     "20th BJS 2026 fee 25000"])
@pytest.mark.parametrize("invented", ["01700000000", "01000000000", "01712345678"])
def test_a_number_the_customer_never_sent_is_not_saved(message, invented):
    turn, result = _call_save_lead(phone=invented, combined_text=message)
    assert result["status"] == "no_phone_from_customer"
    assert turn.lead is None
    # lead_invalid would send "we could not read your number" to someone who sent none.
    assert turn.lead_invalid is False


def test_the_typed_number_is_saved_whatever_the_model_passes():
    turn, result = _call_save_lead(phone="01700000000", combined_text="01310044531",
                                   offered_phone="01310044531")
    assert result["status"] == "ok"
    assert turn.lead["phone"] == "01310044531"


def test_the_number_on_file_still_works_for_a_late_name():
    turn, result = _call_save_lead(phone="", combined_text="আমার নাম রহিম", known_phone="01310044531")
    assert result["status"] == "ok"
    assert turn.lead["phone"] == "01310044531"


def test_a_mistyped_number_is_still_asked_for_again():
    turn, result = _call_save_lead(phone="0131004453", combined_text="0131004453")
    assert result["status"] == "invalid_phone"
    assert turn.lead is None
    assert turn.lead_invalid is True


@pytest.mark.parametrize("text, expected", [
    ("0131004453", True), ("০১৩১০০৪৪৫৩", True), ("013-100 445", True),
    (".", False), ("20th BJS", False), ("25,000", False), ("7 November 2026", False), ("", False),
])
def test_what_counts_as_trying_to_send_a_number(text, expected):
    assert looks_like_phone_attempt(text) is expected


def test_the_real_number_is_captured_after_an_invented_one_was_refused(monkeypatch, leads_and_payments_on):
    """The SA MI RA chat: the model tries to save on the question, the number comes next."""
    from app.agents.turn_context import TurnContext
    from app.leads import outbox
    from app.pipeline import turn as pipeline

    _graph_stub(monkeypatch, [])
    rows: list[dict] = []

    async def fake_enqueue_lead(lead):
        rows.append(lead)
        return "x"

    monkeypatch.setattr(outbox, "enqueue_lead", fake_enqueue_lead)
    session = state.get_session("sl-real")

    refused, _ = _call_save_lead(combined_text="offline coaching korano hoy?")
    asyncio.run(pipeline._apply_side_effects("sl-real", session, refused, refused.combined_text, None))
    assert rows == []
    assert session["lead"]["captured"] is False

    # The model makes no save_lead call at all on this turn; the backstop still has it.
    answered = TurnContext(sender_id="sl-real", session=session, combined_text="01310044531",
                           offered_phone="01310044531")
    asyncio.run(pipeline._apply_side_effects("sl-real", session, answered, answered.combined_text,
                                             answered.offered_phone))
    assert [r["phone"] for r in rows] == ["01310044531"]
