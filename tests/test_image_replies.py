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


class TestOneBurstOneReply:
    """A customer sending three photos, or a photo and then a question, has made ONE
    request. Each of those events used to open its own turn, and the customer got a stack
    of replies arguing with each other — including the bot answering a question it had
    only just asked, before they had a chance to."""

    @staticmethod
    def _inbox(*events):
        return {"queue": list(events), "running": True}

    @pytest.mark.asyncio
    async def test_a_burst_of_photos_is_one_turn_and_one_reply(self, rig, monkeypatch):
        _payments(monkeypatch, True)
        _downloads(monkeypatch, IMAGE)
        _classifies(monkeypatch, is_proof=False)

        first = _event(url=URL)
        inbox = self._inbox(_event(url=URL + "?2"), _event(url=URL + "?3"))
        await turn.handle_messaging_event(first, inbox)

        assert len(rig["runs"]) == 1
        assert len(rig["runs"][0]["images"]) == 3  # all three reached the same model call
        assert len(rig["sent"]) == 1
        assert inbox["queue"] == []  # the burst was consumed, not left to reply again

    @pytest.mark.asyncio
    async def test_the_burst_is_capped(self, rig, monkeypatch):
        _payments(monkeypatch, True)
        _downloads(monkeypatch, IMAGE)
        _classifies(monkeypatch, is_proof=False)

        extras = [_event(url=f"{URL}?{i}") for i in range(8)]
        inbox = self._inbox(*extras)
        await turn.handle_messaging_event(_event(), inbox)

        assert len(rig["runs"][0]["images"]) == turn.MAX_BURST_IMAGES
        assert len(rig["sent"]) == 1
        assert inbox["queue"]  # the rest stay queued rather than being silently dropped

    @pytest.mark.asyncio
    async def test_a_question_typed_while_we_read_the_photo_joins_the_same_turn(self, rig, monkeypatch):
        """The exact shape of the three-bubble failure: the reply is regenerated with the
        new message folded in, so the customer gets one answer, not two."""
        _payments(monkeypatch, True)
        _downloads(monkeypatch, IMAGE)
        _classifies(monkeypatch, is_proof=False)

        inbox = self._inbox()

        async def _model_turn(sender_id, session, text, *a, images=None, **kw):
            rig["runs"].append({"text": text, "images": list(images or [])})
            if len(rig["runs"]) == 1:  # they type while the first draft is being written
                inbox["queue"].append(
                    {"sender": {"id": "u1"}, "message": {"mid": "m2", "text": "এটার দাম কত?"}}
                )
            ctx = TurnContext(sender_id=sender_id, session=session, combined_text=text)
            ctx.extras = {"text": "জি স্যার।", "commit": True}
            return ctx

        monkeypatch.setattr(turn, "_run_model_turn", _model_turn)
        await turn.handle_messaging_event(_event(), inbox)

        assert len(rig["runs"]) == 2  # first draft discarded, not sent
        assert "এটার দাম কত?" in rig["runs"][1]["text"]  # answered together with the photo
        assert len(rig["sent"]) == 1

    @pytest.mark.asyncio
    async def test_several_shots_of_one_receipt_page_the_team_once(self, rig, monkeypatch):
        _payments(monkeypatch, True)
        _downloads(monkeypatch, IMAGE)
        _classifies(monkeypatch, is_proof=True, description="বিকাশ পেমেন্টের স্ক্রিনশট")

        inbox = self._inbox(_event(url=URL + "?2"))
        await turn.handle_messaging_event(_event(), inbox)

        assert len(rig["alerts"]) == 1
        assert rig["sent"] == [FALLBACK_ATTACHMENT]
        assert rig["runs"] == []

    @pytest.mark.asyncio
    async def test_a_sticker_after_a_photo_does_not_join_the_burst(self, rig, monkeypatch):
        # Only photos batch. A sticker still gets its own (silent) pass through the loop.
        _payments(monkeypatch, True)
        _downloads(monkeypatch, IMAGE)
        _classifies(monkeypatch, is_proof=False)

        inbox = self._inbox(_event(sticker=True))
        await turn.handle_messaging_event(_event(), inbox)

        assert len(rig["runs"][0]["images"]) == 1
        assert len(inbox["queue"]) == 1  # left for the loop, which answers it with nothing


class TestWordsAndPicturesAnswerTogether:
    """A question with a photo under it is ONE request. The text turn used to commit
    before the picture was even looked at, so the customer got two replies — the second
    one answering something the first had already covered."""

    @staticmethod
    def _text(mid, body):
        return {"sender": {"id": "u1"}, "message": {"mid": mid, "text": body}}

    @staticmethod
    def _inbox(*events):
        return {"queue": list(events), "running": True}

    @pytest.mark.asyncio
    async def test_a_photo_sent_with_a_question_is_one_turn(self, rig, monkeypatch):
        _payments(monkeypatch, True)
        _downloads(monkeypatch, IMAGE)
        _classifies(monkeypatch, is_proof=False)

        inbox = self._inbox(self._text("m1", "এই কোর্সটার কি অবস্থা?"), _event())
        await turn.handle_text_batch("u1", inbox)

        assert len(rig["runs"]) == 1
        assert len(rig["sent"]) == 1  # not one reply for the words and another for the photo
        assert "এই কোর্সটার কি অবস্থা?" in rig["runs"][0]["text"]
        assert "কোর্স পোস্টার" in rig["runs"][0]["text"]  # the picture is framed in the same turn
        assert rig["runs"][0]["images"][0].content == IMAGE[1]  # and actually shown to the model
        assert inbox["queue"] == []

    @pytest.mark.asyncio
    async def test_a_photo_arriving_mid_reply_joins_the_turn(self, rig, monkeypatch):
        _payments(monkeypatch, True)
        _downloads(monkeypatch, IMAGE)
        _classifies(monkeypatch, is_proof=False)

        inbox = self._inbox(self._text("m1", "একটা জিনিস দেখান"))

        async def _model_turn(sender_id, session, text, *a, images=None, **kw):
            rig["runs"].append({"text": text, "images": list(images or [])})
            if len(rig["runs"]) == 1:  # the photo lands while the first draft is written
                inbox["queue"].append(_event())
            ctx = TurnContext(sender_id=sender_id, session=session, combined_text=text)
            ctx.extras = {"text": "জি স্যার।", "commit": True}
            return ctx

        monkeypatch.setattr(turn, "_run_model_turn", _model_turn)
        await turn.handle_text_batch("u1", inbox)

        assert len(rig["runs"]) == 2  # first draft discarded, never sent
        assert rig["runs"][0]["images"] == []
        assert len(rig["runs"][1]["images"]) == 1
        assert len(rig["sent"]) == 1

    @pytest.mark.asyncio
    async def test_a_receipt_with_a_question_gets_the_fixed_acknowledgement(self, rig, monkeypatch):
        """Whatever else was typed, a receipt is answered by the fixed line and nothing
        else. Only a person can say money arrived, so the model is not asked to try."""
        _payments(monkeypatch, True)
        _downloads(monkeypatch, IMAGE)
        _classifies(monkeypatch, is_proof=True, description="বিকাশ পেমেন্টের স্ক্রিনশট")

        inbox = self._inbox(self._text("m1", "পেমেন্ট করেছি, ক্লাস কবে শুরু?"), _event())
        await turn.handle_text_batch("u1", inbox)

        assert len(rig["alerts"]) == 1  # exactly one, not one per backstop
        assert rig["alerts"][0]["dedupeKey"] == URL
        assert rig["runs"] == []  # the model never gets to phrase this one
        assert rig["sent"] == [FALLBACK_ATTACHMENT]

    @pytest.mark.asyncio
    async def test_a_receipt_landing_mid_reply_is_still_reported(self, rig, monkeypatch):
        """Folding a late photo into the running turn must not swallow the alert with it."""
        _payments(monkeypatch, True)
        _downloads(monkeypatch, IMAGE)
        _classifies(monkeypatch, is_proof=True, description="নগদ পেমেন্টের স্ক্রিনশট")

        inbox = self._inbox(self._text("m1", "ভর্তি হতে চাই"))

        async def _model_turn(sender_id, session, text, *a, images=None, **kw):
            rig["runs"].append({"text": text, "images": list(images or [])})
            if len(rig["runs"]) == 1:
                inbox["queue"].append(_event(url=URL + "?late"))
            ctx = TurnContext(sender_id=sender_id, session=session, combined_text=text)
            ctx.extras = {"text": "জি স্যার।", "commit": True}
            return ctx

        monkeypatch.setattr(turn, "_run_model_turn", _model_turn)
        await turn.handle_text_batch("u1", inbox)

        assert len(rig["alerts"]) == 1
        assert rig["alerts"][0]["dedupeKey"] == URL + "?late"
        assert len(rig["sent"]) == 1


class TestOnlyIdentifiedPaymentsPageTheTeam:
    """A representative can act on a transaction id or a screenshot. They can do nothing
    with "আমি পেমেন্ট করেছি" — so that one is answered by asking for the proof, and the
    alert waits until the proof actually arrives."""

    @staticmethod
    def _turn(session, text, payment=None):
        ctx = TurnContext(sender_id="u1", session=session, combined_text=text)
        ctx.payment = payment
        ctx.extras = {"text": "জি স্যার।", "commit": True}
        return ctx

    @pytest.mark.asyncio
    async def test_a_bare_claim_does_not_alert(self, rig, monkeypatch):
        _payments(monkeypatch, True)
        session = state.get_session("u1")
        session["lead"]["captured"] = True

        text = "আমি পেমেন্ট করেছি"
        await turn._apply_side_effects("u1", session, self._turn(session, text), text, None)

        assert rig["alerts"] == []

    @pytest.mark.asyncio
    async def test_a_bare_claim_the_model_reported_still_does_not_alert(self, rig, monkeypatch):
        # Even the model calling report_payment cannot page the team over nothing.
        _payments(monkeypatch, True)
        session = state.get_session("u1")
        session["lead"]["captured"] = True

        text = "টাকা পাঠিয়েছি"
        payment = {"trxId": "", "note": "গ্রাহক বলছেন পেমেন্ট করেছেন"}
        await turn._apply_side_effects("u1", session, self._turn(session, text, payment), text, None)

        assert rig["alerts"] == []

    @pytest.mark.asyncio
    async def test_a_transaction_id_alerts(self, rig, monkeypatch):
        _payments(monkeypatch, True)
        session = state.get_session("u1")
        session["lead"]["captured"] = True

        text = "BKX9912ZQ44 পাঠিয়েছি"
        await turn._apply_side_effects("u1", session, self._turn(session, text), text, None)

        assert len(rig["alerts"]) == 1
        assert rig["alerts"][0]["trxId"] == "BKX9912ZQ44"

    @pytest.mark.asyncio
    async def test_a_screenshot_alerts_even_with_no_id(self, rig, monkeypatch):
        _payments(monkeypatch, True)
        _downloads(monkeypatch, IMAGE)
        _classifies(monkeypatch, is_proof=True, description="বিকাশ পেমেন্টের স্ক্রিনশট")

        inbox = {"queue": [_event()], "running": True}
        await turn.handle_messaging_event(inbox["queue"].pop(0), inbox)

        assert len(rig["alerts"]) == 1
        assert rig["alerts"][0]["attachmentUrl"] == URL
