"""Webhook dedupe + text-event classification (port-parity with server.js)."""

import pytest

from app.pipeline import queues


@pytest.fixture(autouse=True)
def clear_dedupe_cache():
    queues._seen_event_keys.clear()


def _text_event(mid: str, text: str = "hi"):
    return {"sender": {"id": "u1"}, "message": {"mid": mid, "text": text}}


def _edit_event(mid: str, num_edit: int, text: str):
    return {"sender": {"id": "u1"}, "message_edit": {"mid": mid, "num_edit": num_edit, "text": text}}


class TestDedupe:
    def test_redelivered_message_dedupes(self):
        assert not queues.is_duplicate_event(_text_event("m1"))
        assert queues.is_duplicate_event(_text_event("m1"))

    def test_redelivered_edit_dedupes_but_new_edit_passes(self):
        # Edits reuse the original mid, so they key on mid + edit content.
        assert not queues.is_duplicate_event(_text_event("m1"))
        assert not queues.is_duplicate_event(_edit_event("m1", 1, "fixed"))
        assert queues.is_duplicate_event(_edit_event("m1", 1, "fixed"))       # redelivery
        assert not queues.is_duplicate_event(_edit_event("m1", 2, "fixed2"))  # genuine 2nd edit

    def test_no_mid_never_dedupes(self):
        event = {"sender": {"id": "u1"}, "delivery": {}}
        assert not queues.is_duplicate_event(event)
        assert not queues.is_duplicate_event(event)

    def test_cache_bounded(self):
        for i in range(queues.SEEN_EVENT_KEYS_MAX + 50):
            queues.is_duplicate_event(_text_event(f"m{i}"))
        assert len(queues._seen_event_keys) == queues.SEEN_EVENT_KEYS_MAX
        # Oldest evicted -> no longer considered duplicate.
        assert not queues.is_duplicate_event(_text_event("m0"))


class TestTextEvent:
    def test_plain_text(self):
        assert queues.is_text_event(_text_event("m1"))

    def test_edit_counts_as_text(self):
        assert queues.is_text_event(_edit_event("m1", 1, "01712345678"))

    def test_echo_is_not_text(self):
        assert not queues.is_text_event({"message": {"mid": "m", "text": "x", "is_echo": True}})

    def test_attachment_is_not_text(self):
        assert not queues.is_text_event({"message": {"mid": "m", "attachments": [{"type": "image"}]}})
