"""LLM model factory + the resilience guard around every model-bound run.

The guard ports the old llm.js envelope: a global concurrency semaphore, a hard
timeout, and ONE jittered retry on transient failures (429/5xx/timeouts). Agno's
provider adapters own the wire formats (including Gemini's thought_signature replay).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from functools import lru_cache
from typing import Any, Awaitable, Callable, TypeVar

from app.config import get_settings

log = logging.getLogger(__name__)


T = TypeVar("T")

_semaphore: asyncio.Semaphore | None = None

# Prompt caching. The rendered catalog makes the member system prompt ~22k tokens and its
# stable half is byte-identical every turn, so without a cache breakpoint we pay full input
# rate to resend it. Gemini's implicit caching does NOT engage through OpenRouter (measured:
# cached_tokens=0 on repeat identical calls) — an explicit cache_control breakpoint is
# required, and it takes the per-turn cost from $0.0051 to $0.0010. Only the system message is
# marked: it is the one part of the request that is stable across turns.
#
# Caching is PREFIX-matched, so WHERE the breakpoint sits decides the hit rate. A system
# message may carry CACHE_BREAK to mean "cache everything up to here": the team members put
# the persona + knowledge base before it and everything that varies (their role, the per-turn
# dynamic blocks) after, so all three members and every customer share ONE cache entry instead
# of one per member per dynamic-block combination. A system message without the sentinel is
# cached whole, which is what the single-instruction specialists want.
CACHE_BREAK = "<<<CACHE_BREAKPOINT>>>"
CACHE_MIN_CHARS = 2000  # below this a cache write costs more than it saves (the routing hop)

# Thinking is off on EVERY call, not just the single-decision ones. Reasoning tokens are
# billed at the output rate, are invisible in the reply, and share llm_max_tokens with the
# Bengali answer — so a run that thinks its way to the cap returns no text at all and the
# customer sees nothing. The answers here come from a knowledge base already in the prompt;
# they did not measurably improve for the thinking spent.
#
# On OpenRouter "minimal" is the floor the model accepts — reasoning {"enabled": false} and
# {"max_tokens": 0} are both rejected with a 400. The native Gemini path takes the real zero
# (thinking_budget=0).
NO_REASONING = {"effort": "minimal"}

# OpenRouter load-balances a model across every provider that hosts it, so a Gemini call can
# land on a third-party host we have never tested — different quantisation, different tool-call
# behaviour, different latency tail. Pin Gemini traffic to Google's own endpoints. Order is the
# preference order OpenRouter walks: the flex lanes first (cheaper), each falling through to
# the standard lane on the same backend.
GEMINI_PROVIDER_ROUTING: dict[str, Any] = {
    "only": [
        "google-ai-studio/flex",
        "google-ai-studio",
        "google-vertex/global",
        "google-vertex/global/flex",
    ]
}


def _sem() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(get_settings().llm_max_concurrent)
    return _semaphore


@lru_cache
def _caching_openrouter_cls() -> Any:
    """OpenRouter subclass that marks the system prompt with a cache_control breakpoint.

    Agno formats message content as a plain string; OpenRouter needs the content-parts
    shape to carry a breakpoint, so we widen just the system message on the way out.
    Built lazily so the gemini path never imports the OpenRouter adapter.
    """
    from agno.models.openrouter import OpenRouter

    class CachingOpenRouter(OpenRouter):
        def _format_message(self, message, compress_tool_results: bool = False) -> dict[str, Any]:  # noqa: ANN001
            out = super()._format_message(message, compress_tool_results)
            content = out.get("content")
            if message.role != "system" or not isinstance(content, str):
                return out

            head, marked, tail = content.partition(CACHE_BREAK)
            if len(head) < CACHE_MIN_CHARS:
                # Not worth a cache write. Drop the sentinel so it never reaches the model.
                out["content"] = head + tail if marked else content
                return out

            parts: list[dict[str, Any]] = [
                {"type": "text", "text": head, "cache_control": {"type": "ephemeral"}}
            ]
            if tail:
                parts.append({"type": "text", "text": tail})
            out["content"] = parts
            return out

    return CachingOpenRouter


@lru_cache
def _sentinel_stripping_gemini_cls() -> Any:
    """Gemini adapter that drops CACHE_BREAK from the system instruction.

    The native Gemini API takes the system prompt as one opaque string — there is no
    per-part breakpoint to place — and its IMPLICIT caching does engage here (unlike
    through OpenRouter), for which a stable prefix is exactly the right shape. So this
    path keeps the ordering and only removes our marker, which would otherwise be sent
    to the model as literal prompt text.
    """
    from agno.models.google import Gemini

    class SentinelStrippingGemini(Gemini):
        def _format_messages(self, messages, compress_tool_results: bool = False):  # noqa: ANN001
            contents, system_instruction = super()._format_messages(messages, compress_tool_results)
            if isinstance(system_instruction, str):
                system_instruction = system_instruction.replace(CACHE_BREAK, "")
            return contents, system_instruction

    return SentinelStrippingGemini


def build_model(model_id: str | None = None, *, fast: bool = False) -> Any:
    """Build the configured Agno model (fresh instance per agent — they are not shared).

    Every model this returns has thinking disabled (see NO_REASONING).

    fast=True now only shrinks the output budget, for single-decision calls like the
    name-gender classifier that answer in one word.
    """
    s = get_settings()
    mid = model_id or s.llm_model
    max_tokens = 512 if fast else s.llm_max_tokens
    if s.llm_provider == "openrouter":
        kwargs: dict[str, Any] = {"base_url": s.openrouter_api_base} if s.openrouter_api_base else {}
        extra: dict[str, Any] = {"reasoning": NO_REASONING}
        if "gemini" in mid.lower():
            extra["provider"] = GEMINI_PROVIDER_ROUTING
        kwargs["extra_body"] = extra
        return _caching_openrouter_cls()(
            id=mid, api_key=s.openrouter_api_key, max_tokens=max_tokens, **kwargs
        )
    return _sentinel_stripping_gemini_cls()(
        id=mid, api_key=s.gemini_api_key, max_output_tokens=max_tokens, thinking_budget=0
    )


def _is_transient(err: BaseException) -> bool:
    text = str(err).lower()
    return isinstance(err, asyncio.TimeoutError) or any(
        marker in text for marker in ("429", "rate limit", "500", "502", "503", "504", "overloaded", "timeout", "timed out", "unavailable")
    )


def _log_usage(label: str, result: Any) -> None:
    """Log the token split for one run. cache_read is the only honest proof the breakpoint
    above is landing — after the first turn it should be nearly the whole input. INFO so it
    shows up in the VPS logs without a debug build.

    A turn with a tool call reports twice, once per model round-trip; both should show the
    same near-total cache read, since the system prompt is identical across them."""
    metrics = getattr(result, "metrics", None)
    if metrics is None:
        return
    read = getattr(metrics, "cache_read_tokens", 0) or 0
    inp = getattr(metrics, "input_tokens", 0) or 0
    out = getattr(metrics, "output_tokens", 0) or 0
    # Thinking is disabled in build_model, so this should read 0 on every line. It stays
    # broken out precisely because it is supposed to be zero: reasoning tokens are billed at
    # the OUTPUT rate, are invisible in the reply, and share the cap with the answer — a
    # non-zero here means a provider ignored the override and is worth catching early.
    think = getattr(metrics, "reasoning_tokens", 0) or 0
    log.info(
        "%s tokens: input=%d cached=%d (%.0f%%) output=%d (thinking=%d, cap=%d)",
        label,
        inp,
        read,
        100.0 * read / inp if inp else 0.0,
        out,
        think,
        get_settings().llm_max_tokens,
    )


# LLM health, surfaced on /health and alerted on. A quota exhaustion or a revoked key
# looks exactly like a healthy bot from the outside: every customer simply gets the "can't
# answer right now" line, forever, and nobody is told.
llm_stats: dict[str, Any] = {"failed_since_boot": 0, "consecutive_failures": 0, "last_error": None}
ALERT_AFTER_CONSECUTIVE_FAILURES = 5
_alerted_down = False


def _record_llm_result(ok: bool, err: BaseException | None = None) -> None:
    global _alerted_down
    if ok:
        if _alerted_down:
            log.warning("LLM recovered after %d consecutive failures", llm_stats["consecutive_failures"])
            _notify("✅ De Jure bot: the AI model is responding again. Replies are back to normal.")
        _alerted_down = False
        llm_stats["consecutive_failures"] = 0
        return
    llm_stats["failed_since_boot"] += 1
    llm_stats["consecutive_failures"] += 1
    llm_stats["last_error"] = str(err)[:300] if err else None
    if llm_stats["consecutive_failures"] >= ALERT_AFTER_CONSECUTIVE_FAILURES and not _alerted_down:
        _alerted_down = True
        log.error("⚠ LLM DOWN — %d consecutive failures. Customers are getting the fallback line.",
                  llm_stats["consecutive_failures"])
        _notify(
            "🚨 De Jure bot: the AI model has failed "
            f"{llm_stats['consecutive_failures']} times in a row.\n"
            "Every customer is now getting “দুঃখিত, এই মুহূর্তে উত্তর দিতে পারছি না”.\n"
            "Please answer from the Page inbox and check the model provider "
            f"(quota/billing/API key).\nLast error: {llm_stats['last_error']}"
        )


def _notify(text: str) -> None:
    """Fire-and-forget team ping. Never allowed to interfere with the turn in flight."""
    from app.leads import sinks

    async def _send() -> None:
        with contextlib.suppress(Exception):
            await sinks.send_telegram_text(text)

    with contextlib.suppress(RuntimeError):  # no running loop (tests/CLI)
        asyncio.get_running_loop().create_task(_send())


async def guarded(run: Callable[[], Awaitable[T]], label: str = "llm") -> T:
    """Run a model-bound coroutine under the semaphore + timeout, with one jittered retry."""
    timeout = get_settings().llm_timeout_seconds
    async with _sem():
        try:
            try:
                result = await asyncio.wait_for(run(), timeout)
            except BaseException as err:  # noqa: BLE001 — retry decision below, then re-raise
                if not _is_transient(err):
                    raise
                delay = 0.5 + random.random()
                log.warning("%s transient failure (%s) — retrying in %.1fs", label, err, delay)
                await asyncio.sleep(delay)
                result = await asyncio.wait_for(run(), timeout)
        except BaseException as err:
            _record_llm_result(False, err)
            raise
        _record_llm_result(True)
        _log_usage(label, result)
        return result
