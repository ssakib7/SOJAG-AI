"""The model-call guard: a slow answer is waited for, a timeout is retried, and a turn that
times out twice says so in words the team can act on.

Live on 2026-10-07 a plain "আসসালামু আলাইকুম" hit the old 60s ceiling and the team was
paged with a bare "TimeoutError:" — no duration, no hint that it had already been retried.
"""

from __future__ import annotations

import asyncio

import pytest

from app.agents import model
from app.config import get_settings


@pytest.fixture
def fast_guard(monkeypatch):
    """Shrink the timeout and the retry jitter so the test runs in milliseconds."""
    s = get_settings()
    monkeypatch.setattr(s, "llm_timeout_seconds", 0.05)
    monkeypatch.setattr(model.random, "random", lambda: -0.5)  # delay = 0.5 + (-0.5) = 0
    monkeypatch.setattr(model, "_semaphore", None)
    monkeypatch.setattr(model, "_notify", lambda text: None)
    monkeypatch.setitem(model.llm_stats, "consecutive_failures", 0)
    return s


def test_the_default_waits_two_minutes_per_attempt():
    from app.config import Settings

    assert Settings.model_fields["llm_timeout_seconds"].default == 120


def test_a_timeout_is_retried_and_the_retry_can_answer(fast_guard):
    calls = 0

    async def run():
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(1)  # outlives the timeout
        return "answer"

    assert asyncio.run(model.guarded(run, "test")) == "answer"
    assert calls == 2


def test_two_timeouts_raise_a_readable_error(fast_guard):
    calls = 0

    async def run():
        nonlocal calls
        calls += 1
        await asyncio.sleep(1)

    with pytest.raises(asyncio.TimeoutError) as exc:
        asyncio.run(model.guarded(run, "test"))
    assert calls == 2
    assert "twice" in str(exc.value) and "0.05s" in str(exc.value)
