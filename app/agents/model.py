"""LLM model factory + the resilience guard around every model-bound run.

The guard ports the old llm.js envelope: a global concurrency semaphore, a hard
timeout, and ONE jittered retry on transient failures (429/5xx/timeouts). Agno's
provider adapters own the wire formats (including Gemini's thought_signature replay).
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Awaitable, Callable, TypeVar

from app.config import get_settings

log = logging.getLogger(__name__)

T = TypeVar("T")

_semaphore: asyncio.Semaphore | None = None


def _sem() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(get_settings().llm_max_concurrent)
    return _semaphore


def build_model(model_id: str | None = None, *, fast: bool = False) -> Any:
    """Build the configured Agno model (fresh instance per agent — they are not shared).

    fast=True is for calls where latency beats depth — the team leader's routing hop
    and other single-decision calls: a small output budget, and on Gemini a zero
    thinking budget (the 20-40s spike latencies were dominated by thinking tokens on
    the extra leader hop; routing needs none).
    """
    s = get_settings()
    mid = model_id or s.llm_model
    max_tokens = 512 if fast else s.llm_max_tokens
    if s.llm_provider == "openrouter":
        from agno.models.openrouter import OpenRouter

        kwargs = {"base_url": s.openrouter_api_base} if s.openrouter_api_base else {}
        return OpenRouter(id=mid, api_key=s.openrouter_api_key, max_tokens=max_tokens, **kwargs)
    from agno.models.google import Gemini

    kwargs = {"thinking_budget": 0} if fast else {}
    return Gemini(id=mid, api_key=s.gemini_api_key, max_output_tokens=max_tokens, **kwargs)


def _is_transient(err: BaseException) -> bool:
    text = str(err).lower()
    return isinstance(err, asyncio.TimeoutError) or any(
        marker in text for marker in ("429", "rate limit", "500", "502", "503", "504", "overloaded", "timeout", "timed out", "unavailable")
    )


async def guarded(run: Callable[[], Awaitable[T]], label: str = "llm") -> T:
    """Run a model-bound coroutine under the semaphore + timeout, with one jittered retry."""
    timeout = get_settings().llm_timeout_seconds
    async with _sem():
        try:
            return await asyncio.wait_for(run(), timeout)
        except BaseException as err:  # noqa: BLE001 — retry decision below, then re-raise
            if not _is_transient(err):
                raise
            delay = 0.5 + random.random()
            log.warning("%s transient failure (%s) — retrying in %.1fs", label, err, delay)
            await asyncio.sleep(delay)
            return await asyncio.wait_for(run(), timeout)
