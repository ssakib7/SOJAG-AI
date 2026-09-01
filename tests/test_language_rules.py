"""The bot writes back in the script the customer used — Bangla or Banglish.

Exactly ONE instruction in the assembled prompt is allowed to decide the reply language.
This is not pedantry: the off-topic closing line used to be English while a rule two
paragraphs above said "always Bengali, no exceptions", and the model resolving that
contradiction its own way was the whole failure mode. The prompt is assembled from four
separate places (the system prompt, the answering rules, STRICT_ADHERENCE and the
knowledge base), so a second language rule is easy to reintroduce by accident and
invisible until customers see it.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from app.kb import defaults
from app.kb.catalog import backfill_admin_only
from app.kb.config import build_system_prompt


@pytest.fixture(scope="module")
def assembled() -> str:
    kb_path = pathlib.Path(__file__).resolve().parent.parent / "knowledge_base.json"
    kb = backfill_admin_only(json.loads(kb_path.read_text(encoding="utf-8")))
    return build_system_prompt(kb, "", [], defaults.DEFAULT_ANSWERING_RULES)


# Wordings that pin the bot to one output language regardless of the customer. Each of
# these was in the prompt before Banglish support and would now contradict it.
ALWAYS_ONE_LANGUAGE = [
    "ALWAYS reply in Bengali",
    "Always reply in Bengali",
    "always reply in Bengali",
    "regardless of the language the customer writes in",
    "even if the customer writes in English",
]


@pytest.mark.parametrize("phrase", ALWAYS_ONE_LANGUAGE)
def test_no_rule_forces_a_single_reply_language(assembled, phrase):
    assert phrase not in assembled, (
        f"{phrase!r} is still in the prompt and contradicts the script-matching rule — "
        "the model will pick one of the two at random, per customer"
    )


def test_the_script_matching_rule_is_actually_there(assembled):
    assert "WRITE BACK IN THE SCRIPT THEY WROTE IN" in assembled
    assert "Banglish" in assembled


def test_both_scripts_of_the_honorific_are_given(assembled):
    """A Banglish reply cannot carry স্যার in Bengali script without looking machine-made,
    and the honorific rule is absolute — so the prompt has to name the Latin form too."""
    assert "স্যার" in assembled and "Sir" in assembled


def test_kinship_terms_stay_banned_in_both_scripts(assembled):
    for term in ("ভাইয়া", "আপু"):
        assert term in assembled, f"the ban on {term} was lost in the rewrite"
