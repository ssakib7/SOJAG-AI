"""The Bot behaviour boxes ship pre-filled with the built-in defaults.

That only stays honest if saving an untouched box is a no-op: the config row must go
back empty, so a future release's improved default still reaches this panel. Browsers
submit textareas with CRLF line endings, which is what makes the comparison delicate.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from markupsafe import escape

from app.admin import auth
from app.kb import config as kb_config
from app.kb import defaults
from app import main


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        yield test_client


def _login(client) -> str:
    res = client.post("/login", data={"username": "admin", "password": "admin-pass"})
    assert res.status_code == 200, res.text
    return auth.csrf_token(client.cookies[auth.COOKIE_NAME])


def _stored(key: str) -> dict:
    return asyncio.run(kb_config.get_config_value(key)) or {}


def _as_browser_sends(text: str) -> str:
    """A textarea's submitted value: line endings normalised to CRLF."""
    return text.replace("\n", "\r\n")


def _save(client, csrf, **overrides):
    form = {
        "_csrf": csrf,
        "systemPrompt": _as_browser_sends(defaults.DEFAULT_SYSTEM_PROMPT),
        "answeringRules": _as_browser_sends(defaults.DEFAULT_ANSWERING_RULES),
        "leadInstruction": _as_browser_sends(defaults.DEFAULT_LEAD_INSTRUCTION),
        "leadAskAfterTurns": str(defaults.DEFAULT_ASK_AFTER_TURNS),
    }
    form.update(overrides)
    res = client.post("/system-prompt", data=form, follow_redirects=False)
    assert res.status_code == 302, res.text
    return res


def test_boxes_render_the_default_as_editable_text(client):
    _login(client)
    html = client.get("/system-prompt").text
    # The opening line of each default has to appear as textarea content, not only as a
    # placeholder attribute — a placeholder cannot be edited.
    for default in (defaults.DEFAULT_SYSTEM_PROMPT,
                    defaults.DEFAULT_ANSWERING_RULES,
                    defaults.DEFAULT_LEAD_INSTRUCTION):
        first_line = str(escape(default.splitlines()[0]))
        assert html.count(first_line) >= 2, f"missing editable copy of: {first_line[:40]}"


def test_saving_the_untouched_defaults_stores_blank(client):
    csrf = _login(client)
    _save(client, csrf)

    assert _stored("system_prompt").get("text") == ""
    assert _stored("answering_rules").get("text") == ""
    assert _stored("lead_capture").get("instruction") == ""


def test_saving_the_untouched_defaults_does_not_change_the_live_prompt(client):
    csrf = _login(client)
    before = kb_config.system_prompt_full()

    _save(client, csrf)

    assert kb_config.system_prompt_full() == before


def test_an_edited_box_is_stored_with_plain_newlines(client):
    csrf = _login(client)
    edited = defaults.DEFAULT_SYSTEM_PROMPT + "\nAlways mention the free trial class."

    _save(client, csrf, systemPrompt=_as_browser_sends(edited))

    stored = _stored("system_prompt")["text"]
    assert stored == edited, "an edit must survive verbatim"
    assert "\r" not in stored, "CRLF must not leak into the stored prompt"
    assert "Always mention the free trial class." in kb_config.system_prompt_full()

    _save(client, csrf)  # back to the default for the next test
    assert _stored("system_prompt").get("text") == ""
