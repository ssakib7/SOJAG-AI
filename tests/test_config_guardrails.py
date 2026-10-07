"""Boot-time guardrails on the model we are configured to run.

A model that answers fluently but cannot call tools is the worst failure this bot has,
because nothing about it looks like a failure: the Bengali is good, /health is green, the
restart ping is cheerful, and every lead, payment claim and escalation is dropped on the
floor. gemini-3.1-flash-lite scores 0/8 on our tool suite and was the default in
config.py, one unset LLM_MODEL away from being what production ran.

startup_warnings() is the right home for it — it already reaches both the boot log and
the Telegram restart ping (main.py), which is where the team would actually see it.
"""

from app.config import Settings, is_tool_blind


def _settings(**kw) -> Settings:
    # Lead capture on, so the leads/payments warnings stay quiet and the assertions below
    # are about the model and nothing else.
    base = dict(
        llm_model="gemini-3.7-flash",
        telegram_bot_token="t",
        telegram_chat_id="c",
        google_sheet_webapp_url="https://example.invalid/sheet",
        fb_page_id="123",
        messenger_page_token="tok",
    )
    base.update(kw)
    return Settings(**base)


def test_the_tool_blind_model_is_detected_under_either_provider_spelling():
    # Native path sends the bare id; OpenRouter sends it namespaced. Same broken model.
    assert is_tool_blind("gemini-3.1-flash-lite")
    assert is_tool_blind("google/gemini-3.1-flash-lite")
    assert is_tool_blind("  GOOGLE/Gemini-3.1-Flash-Lite  ")


def test_models_verified_on_the_tool_suite_are_not_flagged():
    assert not is_tool_blind("gemini-3.7-flash")
    assert not is_tool_blind("google/gemini-3.7-flash")
    assert not is_tool_blind("google/gemini-3.5-flash-lite")
    assert not is_tool_blind("")


def test_booting_on_a_tool_blind_model_warns_loudly():
    warnings = _settings(llm_model="google/gemini-3.1-flash-lite").startup_warnings()
    hit = [w for w in warnings if "CANNOT RELIABLY CALL TOOLS" in w]
    assert hit, f"no tool-blind warning in {warnings}"
    # The warning has to name the consequences, not just the model: the team reading it on
    # Telegram needs to know what stopped working, since nothing else will tell them.
    assert "save_lead" in hit[0] and "report_payment" in hit[0]


def test_a_tool_capable_model_produces_no_such_warning():
    warnings = _settings().startup_warnings()
    assert not [w for w in warnings if "CANNOT RELIABLY CALL TOOLS" in w], warnings


def test_the_shipped_default_is_itself_tool_capable():
    """The default is what an unset LLM_MODEL falls back to — it must be safe on its own."""
    assert not is_tool_blind(Settings.model_fields["llm_model"].default)
