"""Off-topic force-stop: one closing reply, then total silence.

The rule lives in STRICT_ADHERENCE (prompt side), but the silence is enforced
deterministically here — a prompt alone cannot make the bot say nothing, because an
empty model reply is replaced by FALLBACK_EMPTY before it is sent.

The trigger is the model's end_conversation tool call, never a match on the reply text:
the closing line is Bengali prose the model may legitimately reword, and a missed match
failed silently — the chat stayed open with nothing in the logs to say the rule had
fired at all.
"""

import pytest

from app.agents.turn_context import TurnContext, current_turn
from app.agents import tools
from app.db import state
from app.db.engine import create_tables
from app.ops import blocklist, followups
from app.pipeline import queues, turn


def _turn(sender_id="u1", *, ended=None):
    session = state.sessions.get(sender_id) or state.get_session(sender_id)
    ctx = TurnContext(sender_id=sender_id, session=session)
    ctx.end_conversation = ended
    ctx.extras = {"text": "…", "commit": True}
    return ctx


class TestEndConversationTool:
    def test_records_the_reason_on_the_turn(self):
        ctx = _turn()
        token = current_turn.set(ctx)
        try:
            result = tools.end_conversation("জমি সংক্রান্ত ব্যক্তিগত মামলার পরামর্শ চেয়েছেন")
        finally:
            current_turn.reset(token)
        assert result["status"] == "closed"
        assert ctx.end_conversation == "জমি সংক্রান্ত ব্যক্তিগত মামলার পরামর্শ চেয়েছেন"

    def test_a_blank_reason_still_closes(self):
        # The close must not hinge on the model filling in a field nicely.
        ctx = _turn()
        token = current_turn.set(ctx)
        try:
            tools.end_conversation("")
        finally:
            current_turn.reset(token)
        assert ctx.end_conversation  # falsy reason would mean no force-stop at all


class TestIsClosed:
    def test_open_session_is_not_closed(self):
        state.get_session("u1")
        assert not state.is_closed("u1")

    def test_unknown_sender_is_not_closed(self):
        assert not state.is_closed("nobody")

    def test_close_session_sets_the_flag(self):
        state.get_session("u1")
        state.close_session("u1")
        assert state.is_closed("u1")

    def test_is_closed_never_creates_or_refreshes_a_session(self):
        # It runs on every queued event; creating a session there would resurrect chats
        # and keep refreshing last_active, so a close could never expire.
        assert not state.is_closed("ghost")
        assert "ghost" not in state.sessions

        session = state.get_session("u1")
        state.close_session("u1")
        session["last_active"] -= 60_000
        stamp = session["last_active"]
        assert state.is_closed("u1")
        assert session["last_active"] == stamp

    def test_close_expires_with_the_idle_window(self):
        session = state.get_session("u1")
        state.close_session("u1")
        session["last_active"] -= state.SESSION_TTL_MS + 1
        assert not state.is_closed("u1")
        # ...and the next message starts a clean, un-closed conversation.
        assert not state.get_session("u1").get("closed")

    def test_close_on_a_missing_session_is_a_no_op(self):
        state.close_session("nobody")
        assert "nobody" not in state.sessions


class TestQueueDropsClosedConversations:
    @pytest.mark.asyncio
    async def test_pending_events_are_dropped_without_a_reply(self, monkeypatch):
        handled: list[str] = []
        monkeypatch.setattr(turn, "handle_text_batch",
                            lambda sid, inbox: handled.append(sid) or _noop())
        monkeypatch.setattr(turn, "handle_messaging_event", lambda event: _noop())

        state.get_session("u1")
        state.close_session("u1")
        inbox = {"queue": [{"sender": {"id": "u1"}, "message": {"mid": "m1", "text": "ei jomi ta kar?"}},
                           {"sender": {"id": "u1"}, "message": {"mid": "m2", "text": "bolen na keno"}}],
                 "running": False}
        await queues._run_sender_loop("u1", inbox)

        assert handled == []
        assert inbox["queue"] == []

    @pytest.mark.asyncio
    async def test_open_conversations_still_run(self, monkeypatch):
        handled: list[str] = []

        def _batch(sid, inbox):
            handled.append(sid)
            inbox["queue"].clear()
            return _noop()

        monkeypatch.setattr(turn, "handle_text_batch", _batch)
        state.get_session("u1")
        inbox = {"queue": [{"sender": {"id": "u1"}, "message": {"mid": "m1", "text": "course fee koto?"}}],
                 "running": False}
        await queues._run_sender_loop("u1", inbox)
        assert handled == ["u1"]


class TestFollowupsRespectTheClose:
    def test_close_cancels_a_pending_nudge(self, monkeypatch):
        state.get_session("u1")  # creating a session cancels its own stale timer — patch after
        cancelled: list[str] = []
        monkeypatch.setattr(followups, "cancel", lambda sid: cancelled.append(sid))
        state.close_session("u1")
        assert cancelled == ["u1"]

    def test_restore_does_not_rearm_a_closed_chat(self, monkeypatch):
        armed: list[str] = []
        monkeypatch.setattr(followups, "_arm", lambda sid, delay: armed.append(sid))
        monkeypatch.setattr(followups.get_settings(), "followup_delay_minutes", 30, raising=False)

        for sid in ("open", "closed"):
            state.get_session(sid)
        state.sessions["closed"]["closed"] = True
        followups.restore()
        assert "closed" not in armed


class TestEscalationWindow:
    def test_each_visit_adds_one_close(self):
        for expected in (1, 2, 3):
            state.sessions.pop("u1", None)  # a new session = a customer who came back
            state.get_session("u1")
            assert state.close_session("u1") == expected

    def test_closes_outside_the_window_no_longer_count(self):
        # Three misfires spread over months must not block a real customer.
        stale = state.now_ms() - state.OFF_TOPIC_CLOSE_WINDOW_MS - 1
        state.off_topic_closes["u1"] = [stale, stale]
        state.get_session("u1")
        assert state.close_session("u1") == 1
        assert state.off_topic_closes["u1"] == [pytest.approx(state.now_ms(), abs=5_000)]

    def test_closes_inside_the_window_do_count(self):
        recent = state.now_ms() - 60_000
        state.off_topic_closes["u1"] = [recent, recent]
        state.get_session("u1")
        assert state.close_session("u1") == 3

    def test_close_on_a_missing_session_reports_without_bumping(self):
        state.off_topic_closes["u1"] = [state.now_ms(), state.now_ms()]
        assert state.close_session("u1") == 2
        assert len(state.off_topic_closes["u1"]) == 2


class TestForceStopTail:
    """The image path answers through the same team, so it must force-stop the same way —
    an image the vision classifier clears as "not a receipt" is an ordinary model turn."""

    @pytest.mark.asyncio
    async def test_closes_when_the_model_called_the_tool(self):
        ctx = _turn(ended="চাকরির আবেদন")
        await turn._force_stop_if_off_topic("u1", ctx.session, ctx)
        assert state.is_closed("u1")

    @pytest.mark.asyncio
    async def test_leaves_an_ordinary_turn_alone(self):
        ctx = _turn(ended=None)
        await turn._force_stop_if_off_topic("u1", ctx.session, ctx)
        assert not state.is_closed("u1")
        assert "u1" not in state.off_topic_closes

    @pytest.mark.asyncio
    async def test_the_closing_text_alone_does_not_close(self, monkeypatch):
        # The old string-matching trigger: a reply that merely reads like the closing line,
        # with no tool call behind it, must NOT force-stop anyone.
        from app.kb import defaults

        ctx = _turn(ended=None)
        ctx.extras["text"] = defaults.OFF_TOPIC_CLOSING
        await turn._force_stop_if_off_topic("u1", ctx.session, ctx)
        assert not state.is_closed("u1")

    @pytest.mark.asyncio
    async def test_blocks_and_announces_a_repeat_offender(self, monkeypatch):
        await create_tables()
        notices: list[str] = []
        monkeypatch.setattr(turn.sinks, "send_telegram_text",
                            lambda text, html=False: notices.append(text) or _noop())
        monkeypatch.setattr(turn.graph, "resolve_inbox_url", lambda sid: _text("https://inbox/u1"))

        recent = state.now_ms() - 60_000
        state.off_topic_closes["u1"] = [recent] * (turn.OFF_TOPIC_CLOSES_BEFORE_BLOCK - 1)
        ctx = _turn(ended="বিজ্ঞাপনের লিংক পাঠিয়েছেন")
        try:
            await turn._force_stop_if_off_topic("u1", ctx.session, ctx)
            assert blocklist.is_blocked("u1")
            assert len(notices) == 1  # a silent block is a customer lost without a trace
            assert "বিজ্ঞাপনের লিংক পাঠিয়েছেন" in notices[0]
            assert "u1" in notices[0]  # the sender id, so an admin can Unblock
        finally:
            await blocklist.unblock_sender("u1")

    @pytest.mark.asyncio
    async def test_a_telegram_outage_does_not_break_the_block(self, monkeypatch):
        await create_tables()

        async def _boom(text, html=False):
            raise RuntimeError("telegram unreachable")

        monkeypatch.setattr(turn.sinks, "send_telegram_text", _boom)
        monkeypatch.setattr(turn.graph, "resolve_inbox_url", lambda sid: _text(""))
        recent = state.now_ms() - 60_000
        state.off_topic_closes["u1"] = [recent] * (turn.OFF_TOPIC_CLOSES_BEFORE_BLOCK - 1)
        ctx = _turn(ended="স্প্যাম")
        try:
            await turn._force_stop_if_off_topic("u1", ctx.session, ctx)
            assert blocklist.is_blocked("u1")
        finally:
            await blocklist.unblock_sender("u1")

    @pytest.mark.asyncio
    async def test_does_not_block_below_the_threshold(self, monkeypatch):
        notices: list[str] = []
        monkeypatch.setattr(turn.sinks, "send_telegram_text",
                            lambda text, html=False: notices.append(text) or _noop())
        state.off_topic_closes["u1"] = [state.now_ms()] * (turn.OFF_TOPIC_CLOSES_BEFORE_BLOCK - 2)
        ctx = _turn(ended="হোমওয়ার্ক")
        await turn._force_stop_if_off_topic("u1", ctx.session, ctx)
        assert not blocklist.is_blocked("u1")
        assert notices == []
        assert state.is_closed("u1")

    @pytest.mark.asyncio
    async def test_blocking_clears_the_tally_so_unblock_is_a_clean_slate(self):
        await create_tables()
        state.get_session("u1")
        state.close_session("u1")
        assert len(state.off_topic_closes["u1"]) == 1
        try:
            assert await blocklist.block_sender("u1", note="auto: repeated off-topic/spam")
            assert blocklist.is_blocked("u1")
            assert "u1" not in state.off_topic_closes  # a later Unblock starts from zero
            assert "u1" not in state.sessions          # and the closed session is gone
        finally:
            await blocklist.unblock_sender("u1")


async def _noop():
    return None


async def _text(value):
    return value
