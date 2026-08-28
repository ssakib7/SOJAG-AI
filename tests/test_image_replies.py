"""Images the customer sends reach the model that answers them — and only a receipt
interrupts the team.

Two separate ideas live here:

1. The vision classifier (specialists.classify_image) only decides "is this a payment
   receipt?" — it is not the agent that replies. Everything it clears has to reach the
   conversational team as a real image, or the bot is answering a 200-character caption
   of a course poster instead of the poster itself.
2. A photo is not by itself news for the team. The MODEL decides whether something needs
   their attention, the same way it does for text; the only guess left is when nothing
   looked at the file at all.
"""

import pytest

from app.agents.specialists import AttachmentVerdict
from app.agents.turn_context import TurnContext
from app.config import get_settings
from app.db import state
from app.kb.defaults import FALLBACK_ATTACHMENT, FALLBACK_NON_TEXT
from app.pipeline import turn

IMAGE = ("image/jpeg", b"\xff\xd8\xff-not-really-a-jpeg")
URL = "https://scontent.example/poster.jpg"


def _event(kind="image", url=URL, sticker=False):
    message = {"mid": "m1", "attachments": [{"type": kind, "payload": {"url": url}}]}
    if sticker:
        message["sticker_id"] = 369239263222822
    return {"sender": {"id": "u1"}, "message": message}


@pytest.fixture
def rig(monkeypatch):
    """Stub every edge: Graph, download, classifier, profile lookup, model turn."""
    sent: list[str] = []
    alerts: list[dict] = []
    runs: list[dict] = []

    async def _noop(*a, **kw):
        return None

    async def _model_turn(sender_id, session, text, ask_contact, offered, known, profile, images=None):
        runs.append({"text": text, "images": list(images or [])})
        ctx = TurnContext(sender_id=sender_id, session=session, combined_text=text)
        ctx.extras = {"text": "জি স্যার, এটি আমাদের কোর্স পোস্টার।", "commit": True}
        return ctx

    monkeypatch.setattr(turn.graph, "send_message", lambda sid, text: sent.append(text) or _noop())
    monkeypatch.setattr(turn.graph, "send_action", _noop)
    monkeypatch.setattr(turn, "_run_model_turn", _model_turn)
    monkeypatch.setattr(turn, "_profile_with_gender", lambda sid: _noop())
    monkeypatch.setattr(turn, "report_payment_claim",
                        lambda sid, session, data: alerts.append(data) or _noop())
    monkeypatch.setattr(turn.followups, "schedule", lambda sid, session: None)
    monkeypatch.setattr(turn, "_rand_between", lambda lo, hi: 0)
    return {"sent": sent, "alerts": alerts, "runs": runs}


def _payments(monkeypatch, on: bool):
    # payments_on is a computed property, so patch it on the Settings class.
    monkeypatch.setattr(type(get_settings()), "payments_on", property(lambda self: on))


def _downloads(monkeypatch, result):
    async def _fetch(url):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(turn, "fetch_attachment_image", _fetch)


def _classifies(monkeypatch, is_proof, description="কোর্স পোস্টার"):
    async def _classify(mime, data):
        assert (mime, data) == IMAGE  # the classifier reuses the ONE download
        return AttachmentVerdict(is_payment_proof=is_proof, description=description)

    monkeypatch.setattr(turn, "classify_image", _classify)


def _model_fails(monkeypatch):
    async def _model_turn(sender_id, session, text, *a, images=None, **kw):
        ctx = TurnContext(sender_id=sender_id, session=session, combined_text=text)
        ctx.extras = {"text": "দুঃখিত...", "commit": False}  # LLM error, nothing committed
        return ctx

    monkeypatch.setattr(turn, "_run_model_turn", _model_turn)


class TestModelSeesTheImage:
    @pytest.mark.asyncio
    async def test_cleared_image_reaches_the_model_as_an_image(self, rig, monkeypatch):
        _payments(monkeypatch, True)
        _downloads(monkeypatch, IMAGE)
        _classifies(monkeypatch, is_proof=False)

        await turn.handle_messaging_event(_event())

        assert len(rig["runs"]) == 1
        images = rig["runs"][0]["images"]
        assert len(images) == 1
        assert images[0].content == IMAGE[1]  # the real bytes, not a caption of them
        assert images[0].mime_type == IMAGE[0]
        assert "কোর্স পোস্টার" in rig["runs"][0]["text"]  # the caption still frames it
        assert rig["alerts"] == []

    @pytest.mark.asyncio
    async def test_seen_even_when_payment_alerts_are_off(self, rig, monkeypatch):
        # Seeing must not depend on Telegram being configured.
        _payments(monkeypatch, False)
        _downloads(monkeypatch, IMAGE)

        await turn.handle_messaging_event(_event())

        assert len(rig["runs"]) == 1
        assert rig["runs"][0]["images"][0].content == IMAGE[1]
        assert rig["alerts"] == []  # nothing to alert to

    @pytest.mark.asyncio
    async def test_a_classifier_outage_still_lets_the_model_look(self, rig, monkeypatch):
        # We have the bytes; only the receipt-detector failed. The model can see it itself.
        _payments(monkeypatch, True)
        _downloads(monkeypatch, IMAGE)

        async def _boom(mime, data):
            raise RuntimeError("vision model unreachable")

        monkeypatch.setattr(turn, "classify_image", _boom)
        await turn.handle_messaging_event(_event())

        assert rig["alerts"] == []
        assert rig["runs"][0]["images"][0].content == IMAGE[1]


class TestOnlyReceiptsInterruptTheTeam:
    @pytest.mark.asyncio
    async def test_a_receipt_alerts_and_never_reaches_the_model(self, rig, monkeypatch):
        _payments(monkeypatch, True)
        _downloads(monkeypatch, IMAGE)
        _classifies(monkeypatch, is_proof=True, description="বিকাশ পেমেন্টের স্ক্রিনশট")

        await turn.handle_messaging_event(_event())

        assert len(rig["alerts"]) == 1
        assert rig["alerts"][0]["dedupeKey"] == URL
        assert rig["sent"] == [FALLBACK_ATTACHMENT]
        assert rig["runs"] == []

    @pytest.mark.asyncio
    async def test_unreadable_file_asks_the_model_instead_of_paging_the_team(self, rig, monkeypatch):
        # A PDF or an oversized photo is not news by itself. The model has the history and
        # the report_payment tool — let it decide, the same as it does for text.
        _payments(monkeypatch, True)
        _downloads(monkeypatch, None)

        await turn.handle_messaging_event(_event())

        assert rig["alerts"] == []
        assert len(rig["runs"]) == 1
        assert rig["runs"][0]["images"] == []  # nothing to show it
        assert "খুলে দেখতে পারিনি" in rig["runs"][0]["text"]

    @pytest.mark.asyncio
    async def test_a_failed_download_also_goes_to_the_model(self, rig, monkeypatch):
        _payments(monkeypatch, True)
        _downloads(monkeypatch, RuntimeError("attachment fetch responded 403"))

        await turn.handle_messaging_event(_event())

        assert rig["alerts"] == []
        assert len(rig["runs"]) == 1

    @pytest.mark.asyncio
    async def test_the_model_can_still_call_for_the_team(self, rig, monkeypatch):
        # It looked at the image, read the conversation, and decided this needs verifying.
        _payments(monkeypatch, True)
        _downloads(monkeypatch, IMAGE)
        _classifies(monkeypatch, is_proof=False)

        async def _model_turn(sender_id, session, text, *a, images=None, **kw):
            ctx = TurnContext(sender_id=sender_id, session=session, combined_text=text)
            ctx.payment = {"trxId": "BKX9912ZQ", "note": "গ্রাহক রসিদ পাঠিয়েছেন"}
            ctx.extras = {"text": "ধন্যবাদ স্যার।", "commit": True}
            return ctx

        monkeypatch.setattr(turn, "_run_model_turn", _model_turn)
        await turn.handle_messaging_event(_event())

        assert len(rig["alerts"]) == 1
        assert rig["alerts"][0]["trxId"] == "BKX9912ZQ"


class TestFailOpenIsNarrow:
    @pytest.mark.asyncio
    async def test_alerts_when_nothing_looked_at_it_at_all(self, rig, monkeypatch):
        # Unreadable file AND the model call failed: no judgement was formed anywhere, and
        # it might be a receipt. This is the one case left where guessing beats silence.
        _payments(monkeypatch, True)
        _downloads(monkeypatch, None)
        _model_fails(monkeypatch)

        await turn.handle_messaging_event(_event())

        assert len(rig["alerts"]) == 1
        assert "সতর্কতাবশত" in rig["alerts"][0]["note"]

    @pytest.mark.asyncio
    async def test_a_readable_image_the_classifier_cleared_never_fails_open(self, rig, monkeypatch):
        # The classifier DID look and said "not a receipt" — a later model outage is not a
        # reason to page the team about a course poster.
        _payments(monkeypatch, True)
        _downloads(monkeypatch, IMAGE)
        _classifies(monkeypatch, is_proof=False)
        _model_fails(monkeypatch)

        await turn.handle_messaging_event(_event())

        assert rig["alerts"] == []

    @pytest.mark.asyncio
    async def test_never_alerts_with_payment_alerts_off(self, rig, monkeypatch):
        _payments(monkeypatch, False)
        _downloads(monkeypatch, None)
        _model_fails(monkeypatch)

        await turn.handle_messaging_event(_event())

        assert rig["alerts"] == []


class TestNonImages:
    @pytest.mark.asyncio
    async def test_unreadable_file_with_payments_off_still_gets_an_answer(self, rig, monkeypatch):
        _payments(monkeypatch, False)
        _downloads(monkeypatch, None)

        await turn.handle_messaging_event(_event())

        assert rig["alerts"] == []
        assert len(rig["runs"]) == 1
        assert rig["runs"][0]["images"] == []

    @pytest.mark.asyncio
    async def test_video_gets_the_write_it_down_nudge(self, rig, monkeypatch):
        _payments(monkeypatch, True)
        await turn.handle_messaging_event(_event(kind="video"))
        assert rig["sent"] == [FALLBACK_NON_TEXT]
        assert rig["runs"] == []

    @pytest.mark.asyncio
    async def test_the_nudge_respects_its_cooldown(self, rig, monkeypatch):
        _payments(monkeypatch, True)
        await turn.handle_messaging_event(_event(kind="video"))
        await turn.handle_messaging_event(_event(kind="video"))
        assert rig["sent"] == [FALLBACK_NON_TEXT]  # once, not twice

    @pytest.mark.asyncio
    async def test_sticker_gets_nothing_at_all(self, rig, monkeypatch):
        _payments(monkeypatch, True)
        await turn.handle_messaging_event(_event(sticker=True))
        assert rig["sent"] == []
        assert rig["runs"] == []
        assert "u1" not in state.sessions  # not even a session
