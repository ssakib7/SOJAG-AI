"""Prompt caching + the fast-path reasoning cap on the OpenRouter model.

The rendered catalog makes the member system prompt ~22k tokens, whose stable half is
resent byte-identical every turn. Gemini's implicit caching does not engage through
OpenRouter, so the cache_control breakpoint below is what keeps the per-turn cost down
(measured 5x). Caching is prefix-matched, so these tests pin WHERE the breakpoint lands:
everything before CACHE_BREAK must be identical across members and across turns, or each
variation writes its own 22k-token cache entry. The routing hop caps reasoning instead:
it is a one-word decision that otherwise thinks.
"""

import pytest

from agno.models.message import Message

from app.agents import factory
from app.agents.model import (
    CACHE_BREAK,
    CACHE_MIN_CHARS,
    FAST_REASONING,
    GEMINI_PROVIDER_ROUTING,
    build_model,
)
from app.agents.turn_context import TurnContext, current_turn
from app.config import get_settings
from app.kb import config as kb_config


@pytest.fixture
def openrouter(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "llm_provider", "openrouter", raising=False)
    monkeypatch.setattr(s, "openrouter_api_key", "test-key", raising=False)
    return s


def _fmt(model, role, content):
    return model._format_message(Message(role=role, content=content))


def test_long_system_prompt_gets_a_cache_breakpoint(openrouter):
    model = build_model()
    out = _fmt(model, "system", "x" * (CACHE_MIN_CHARS + 1))
    assert out["content"] == [
        {"type": "text", "text": "x" * (CACHE_MIN_CHARS + 1), "cache_control": {"type": "ephemeral"}}
    ]


def test_short_system_prompt_is_left_alone(openrouter):
    # A cache write on the routing hop's few hundred chars costs more than it saves.
    out = _fmt(build_model(), "system", "x" * (CACHE_MIN_CHARS - 1))
    assert out["content"] == "x" * (CACHE_MIN_CHARS - 1)


def test_user_turn_is_never_cached(openrouter):
    # Only the system message is stable across turns; caching the customer's words
    # would write a fresh cache entry every single turn.
    out = _fmt(build_model(), "user", "x" * (CACHE_MIN_CHARS + 1))
    assert out["content"] == "x" * (CACHE_MIN_CHARS + 1)


def test_fast_path_caps_reasoning(openrouter):
    assert build_model(fast=True).extra_body["reasoning"] == FAST_REASONING


def test_normal_path_sends_no_reasoning_override(openrouter):
    assert "reasoning" not in (build_model().extra_body or {})


def test_gemini_is_pinned_to_googles_own_endpoints(openrouter):
    # OpenRouter would otherwise fan the call out to any host serving the model. Both the
    # normal and the fast path must carry the pin — the routing hop is a real customer turn.
    assert build_model().extra_body["provider"] == GEMINI_PROVIDER_ROUTING
    assert build_model(fast=True).extra_body["provider"] == GEMINI_PROVIDER_ROUTING


def test_non_gemini_models_are_left_to_openrouters_own_routing(openrouter):
    # The pin lists Google-only backends; sending it with anything else routes to nothing.
    assert build_model("openai/gpt-4o-mini").extra_body is None


@pytest.fixture
def gemini(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "llm_provider", "gemini", raising=False)
    monkeypatch.setattr(s, "gemini_api_key", "test-key", raising=False)
    return s


def test_gemini_path_is_unchanged(gemini):
    assert build_model(fast=True).thinking_budget == 0


def test_gemini_strips_the_sentinel_from_the_system_instruction(gemini):
    # gemini is the DEFAULT provider, and its API takes the system prompt as one opaque
    # string — there is no breakpoint to place, so the marker must simply not be sent.
    # Native Gemini's implicit caching still rewards the stable-prefix-first ordering.
    _, system = build_model()._format_messages([
        Message(role="system", content="persona + KB" + CACHE_BREAK + "\n\nvaries"),
        Message(role="user", content="দাম কত?"),
    ])
    assert system == "persona + KB\n\nvaries"


# --- where the breakpoint lands -----------------------------------------------


def test_breakpoint_caches_only_the_stable_head(openrouter):
    head, tail = "h" * CACHE_MIN_CHARS, "\n\nvaries every turn"
    out = _fmt(build_model(), "system", head + CACHE_BREAK + tail)
    assert out["content"] == [
        {"type": "text", "text": head, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": tail},
    ]


def test_sentinel_never_reaches_the_model(openrouter):
    # Both sides of the CACHE_MIN_CHARS branch must strip it — it is our marker, not prompt text.
    long_out = _fmt(build_model(), "system", "h" * CACHE_MIN_CHARS + CACHE_BREAK + "tail")
    short_out = _fmt(build_model(), "system", "head" + CACHE_BREAK + "tail")
    assert not any(CACHE_BREAK in part["text"] for part in long_out["content"])
    assert short_out["content"] == "headtail"


def test_head_below_the_minimum_is_not_cached(openrouter):
    # A short head means the tail carries the bulk — caching the head would cost more
    # than it saves, and the tail is by definition the part that varies.
    out = _fmt(build_model(), "system", "head" + CACHE_BREAK + "t" * CACHE_MIN_CHARS)
    assert out["content"] == "head" + "t" * CACHE_MIN_CHARS


# --- what the team members actually put on each side of it ---------------------


@pytest.fixture
def assembled_prompt(monkeypatch):
    prompt = "PERSONA + KNOWLEDGE BASE " * 200
    monkeypatch.setitem(kb_config._cache, "system_prompt_full", prompt)
    return prompt


def _prompt(turn=None) -> str:
    token = current_turn.set(turn)
    try:
        return factory._system_message()()
    finally:
        current_turn.reset(token)


def test_the_knowledge_base_is_in_the_cached_half(assembled_prompt):
    # The whole point: the persona + knowledge base is one cache entry, byte-identical on
    # every call. Agno's default assembly emits its own scaffolding BEFORE the
    # instructions, which is why the agent takes system_message= instead.
    assert _prompt().split(CACHE_BREAK)[0] == assembled_prompt + factory._static_policy_blocks()


def test_the_unchanging_policy_blocks_are_cached_not_paid_for_every_turn(assembled_prompt):
    """The honorific and payment policies read like per-turn instructions but are the same
    bytes for every customer on every turn. Behind the breakpoint they cost full input rate
    on every member call, and twice on any turn with a tool call."""
    head, tail = _prompt().split(CACHE_BREAK)
    assert "=== HONORIFICS ===" in head and "=== HONORIFICS ===" not in tail
    assert "=== PAYMENTS ===" in head and "=== PAYMENTS ===" not in tail


def test_dynamic_blocks_land_after_the_breakpoint(assembled_prompt):
    # The per-turn blocks are why the breakpoint moved: appended before it, every distinct
    # customer state would write its own copy of the knowledge base.
    turn = TurnContext(
        sender_id="1",
        session={"returning": True, "customer_name": "Rahim Uddin"},
        profile={"name": "Rahim Uddin", "gender_guess": "male"},
    )
    head, tail = _prompt(turn).split(CACHE_BREAK)
    assert head == assembled_prompt + factory._static_policy_blocks()
    assert "=== RETURNING CUSTOMER ===" in tail
    # Only the one line that actually differs per customer stays behind the breakpoint.
    assert "=== THIS CUSTOMER'S HONORIFIC ===" in tail
    assert len(tail) < 1500, f"the full-rate tail is paid on every call; it is {len(tail)} chars"


def test_prefix_is_identical_across_turns_with_different_state(assembled_prompt):
    quiet = _prompt()
    asking = _prompt(TurnContext(sender_id="1", session={}, ask_contact=True))
    assert quiet != asking
    assert quiet.split(CACHE_BREAK)[0] == asking.split(CACHE_BREAK)[0]
