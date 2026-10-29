"""A free class registration reaches the Sheet with its own column.

The customer picks online (Zoom) or offline (campus, 60 seats). That choice used to land
only in the model's free-text interest/remarks, so nobody could filter the two lists or
count the offline seats. It now travels as classMode ("Online" / "Offline" / "").
"""

from __future__ import annotations

import asyncio

import pytest

from app.db import state
from tests.test_audit_probes import _graph_stub, fast_replies, leads_and_payments_on  # noqa: F401


def _call_save_lead(**kwargs):
    from app.agents.tools import save_lead
    from app.agents.turn_context import TurnContext, current_turn

    turn = TurnContext(sender_id="fc", session=state.get_session("fc"))
    token = current_turn.set(turn)
    try:
        result = save_lead(**kwargs)
    finally:
        current_turn.reset(token)
    return turn, result


@pytest.mark.parametrize("given, expected", [
    ("online", "Online"), ("Zoom", "Online"), ("অনলাইন", "Online"),
    ("offline", "Offline"), ("ক্যাম্পাস", "Offline"), ("Offline (campus)", "Offline"),
    ("", ""), ("maybe", ""),
])
def test_the_choice_is_written_the_way_the_sheet_filters_it(given, expected):
    turn, result = _call_save_lead(name="Test Sakib", phone="01712345678", interest="ফ্রি ক্লাস",
                                   remarks="-", class_mode=given)
    assert result["status"] == "ok"
    assert turn.lead["classMode"] == expected


def test_an_ordinary_lead_leaves_it_empty():
    turn, _ = _call_save_lead(name="Test Sakib", phone="01712345678", interest="ফি", remarks="-")
    assert turn.lead["classMode"] == ""


def test_it_reaches_the_sheet_row(monkeypatch, leads_and_payments_on):
    from app.leads import outbox
    from app.pipeline import turn

    _graph_stub(monkeypatch, [])
    rows: list[dict] = []

    async def fake_enqueue_lead(lead):
        rows.append(lead)
        return "x"

    monkeypatch.setattr(outbox, "enqueue_lead", fake_enqueue_lead)
    asyncio.run(turn.capture_lead("fc-row", state.get_session("fc-row"), {
        "name": "Test Sakib", "phone": "01712345678", "interest": "ফ্রি ক্লাস", "remarks": "-",
        "classMode": "Offline",
    }))
    assert rows[0]["classMode"] == "Offline"
    assert "university" not in rows[0]


def test_the_telegram_alert_shows_it(monkeypatch):
    from app.leads import sinks

    texts: list[str] = []

    async def fake_text(text, html=False):
        texts.append(text)
        return True

    monkeypatch.setattr(sinks, "send_telegram_text", fake_text)
    asyncio.run(sinks.send_lead_to_telegram({
        "name": "Test Sakib", "phone": "01712345678", "time": "now",
        "classMode": "Online",
    }))
    assert "Free class: Online" in texts[0]
