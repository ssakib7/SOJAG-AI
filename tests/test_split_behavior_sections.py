"""The behaviour-vs-knowledge split of the pulled custom sections.

The migration moves instruction prose out of the KB block (where STRICT_ADHERENCE frames it
as facts to recite) into === EXAMPLES & SCENARIOS ===. What these tests guard is the part
that could quietly break a live bot: the payment and escalation numbers must not leave the
knowledge base with the prose that surrounded them, and re-running must not duplicate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.split_behavior_sections import (
    BEHAVIOUR_TITLES,
    FACT_SECTIONS,
    normalize_address,
    residual_address_terms,
    split_sections,
)

SNAPSHOT = Path(__file__).resolve().parent.parent / "knowledge_base.json"


@pytest.fixture
def kb():
    return {
        "about": "De Jure Academy",
        "customSections": [
            {"title": "Enrollment Guideline:", "body": "ask the website question first", "adminOnly": True},
            {"title": "Payment Procedure:", "body": "ask the method, then bKash 01729290202", "adminOnly": True},
            {"title": "Conversation Continuity, Name Usage & Follow-Up Questions:",
             "body": "use the name sparingly", "adminOnly": False},
            {"title": "Qualification for attending BJS Exam", "body": "বয়স অনধিক ৩২", "adminOnly": False},
        ],
    }


def _titles(sections):
    return [s["title"] for s in sections]


def test_behaviour_moves_and_knowledge_stays(kb):
    new_kb, sections, summary = split_sections(kb, [])

    assert "Qualification for attending BJS Exam" in _titles(new_kb["customSections"])
    assert "Payment Procedure" in _titles(sections)
    assert "Conversation Continuity, Name Usage & Follow-Up Questions" in _titles(sections)
    # The eligibility rules are reference knowledge — they must NOT be treated as behaviour.
    assert "Qualification for attending BJS Exam" not in _titles(sections)
    assert summary["moved"]


def test_bodies_move_otherwise_verbatim(kb):
    # The address style is the ONLY edit to customer-facing Bengali; everything else moves as-is.
    _, sections, _ = split_sections(kb, [])
    moved = {s["title"]: s["body"] for s in sections}
    assert moved["Payment Procedure"] == "ask the method, then bKash 01729290202"


def test_trailing_colons_are_dropped_from_titles(kb):
    # prompt_sections render as "### {title}" — "### Payment Procedure:" reads as a typo.
    _, sections, _ = split_sections(kb, [])
    assert not any(s["title"].endswith(":") for s in sections)


def test_payment_facts_stay_inside_the_knowledge_base(kb):
    # The whole point of splitting rather than moving: STRICT_ADHERENCE only protects
    # contact details that are written in the KB block.
    new_kb, _, _ = split_sections(kb, [])
    kb_text = json.dumps(new_kb, ensure_ascii=False)
    for fact in ("01729290202", "01339622238", "1111164283300", "invoice.sslcommerz.com",
                 "+8801517824328"):
        assert fact in kb_text, f"{fact} left the knowledge base"


def test_payment_accounts_section_stays_admin_only(kb):
    # It replaces an adminOnly=True section; non-admin editors must not gain access to it.
    new_kb, _, _ = split_sections(kb, [])
    accounts = next(s for s in new_kb["customSections"] if s["title"] == "Payment accounts & links")
    assert accounts["adminOnly"] is True


def test_rerunning_is_a_no_op(kb):
    once_kb, once_sections, _ = split_sections(kb, [])
    twice_kb, twice_sections, summary = split_sections(once_kb, once_sections)

    assert twice_kb == once_kb
    assert twice_sections == once_sections
    assert summary["moved"] == []
    # And specifically: no duplicated facts section, no duplicated prompt section.
    assert len(_titles(twice_kb["customSections"])) == len(set(_titles(twice_kb["customSections"])))
    assert len(_titles(twice_sections)) == len(set(_titles(twice_sections)))


def test_existing_prompt_sections_are_preserved(kb):
    existing = [{"title": "Tone examples", "body": "..."}]
    _, sections, _ = split_sections(kb, existing)
    assert sections[0] == existing[0]


# --- address style -------------------------------------------------------------------


@pytest.mark.parametrize("before,after", [
    ('  "ভাইয়া, আপনি কি ক্লাস রুটিন জানতে চান?"', '  "স্যার, আপনি কি ক্লাস রুটিন জানতে চান?"'),
    ('  - "আপু, আপনি কি ভর্তি প্রক্রিয়া জানতে চান?"', '  - "ম্যাডাম, আপনি কি ভর্তি প্রক্রিয়া জানতে চান?"'),
    ('"জ্বি ভাইয়া, পেমেন্ট করে থাকলে..."', '"জ্বি স্যার, পেমেন্ট করে থাকলে..."'),
    ('"ধন্যবাদ ভাইয়া, আপনার তথ্যটি পেয়েছি।"', '"ধন্যবাদ স্যার, আপনার তথ্যটি পেয়েছি।"'),
])
def test_customer_address_becomes_sir_or_madam(before, after):
    assert normalize_address(before)[0] == after


def test_third_party_honorific_is_left_alone():
    # ভাইয়া for someone who is NOT the customer is correct Bengali. Rewriting the operations
    # manager to "তুহিন বাদশা স্যার" would be a different (wrong) sentence.
    line = "অপারেশনাল ম্যানেজার, মোঃ তুহিন বাদশা ভাইয়া'কে +8801517824328 এই নাম্বারে!"
    assert normalize_address(line) == (line, 0)
    assert residual_address_terms(line) == []


def test_unrecognised_kinship_terms_are_reported_not_guessed():
    # A phrasing the enumerated list does not cover must reach a human, not be rewritten.
    line = "ঠিক আছে ভাইয়া আমি দেখছি"
    assert normalize_address(line) == (line, 0)
    assert residual_address_terms(line) == ["ঠিক আছে ভাইয়া আমি দেখছি"]


def test_snapshot_ships_already_normalised():
    """The snapshot now carries স্যার/ম্যাডাম already, so the split has nothing to fix.

    The 8 fixes were applied to knowledge_base.json itself rather than left for the
    migration to make on the way in. Importing the KB and running the split are two separate
    manual steps on the server, and between them the bot answers customers — so the file
    must be safe to import on its own, without depending on a later step to bring its
    templates in line with the system prompt's ban on ভাইয়া/আপু.
    """
    kb = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    new_kb, sections, summary = split_sections(kb, [])

    assert summary["addresses_fixed"] == ["0"], "the snapshot regained a kinship term"
    assert summary["residual_address_terms"] == []
    # The third-party honorific is correct Bengali and must never be swept: the operations
    # manager is a person being referred to, not the customer being addressed.
    assert "তুহিন বাদশা ভাইয়া" in json.dumps({**new_kb, "s": sections}, ensure_ascii=False)


def test_snapshot_templates_address_the_customer_correctly():
    """Spot-check the customer-facing templates the fix rewrote."""
    raw = SNAPSHOT.read_text(encoding="utf-8")
    assert '"স্যার, আপনি কি' in raw or '"ম্যাডাম, আপনি কি' in raw
    assert '"ভাইয়া, আপনি কি' not in raw
    assert '"আপু, আপনি কি' not in raw


@pytest.mark.skipif(not SNAPSHOT.exists(), reason="pulled KB snapshot not in the tree")
def test_against_the_real_pulled_snapshot():
    """The migration is written for THIS data — assert against it, not just fixtures."""
    kb = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    new_kb, sections, summary = split_sections(kb, [])

    assert len(summary["moved"]) == len(BEHAVIOUR_TITLES), f"unmatched titles: {summary}"
    assert _titles(new_kb["customSections"]) == [
        "Qualification for attending BJS Exam",
        *[f["title"] for f in FACT_SECTIONS],
    ]
    # Nothing was lost: every original section is either still in the KB or now a prompt section.
    originals = {s["title"].rstrip(":").strip() for s in kb["customSections"]}
    landed = {*_titles(new_kb["customSections"]), *_titles(sections)}
    assert originals <= landed


def test_snapshot_carries_no_expired_free_class_material():
    """The 21-22 August 2026 masterclasses are over.

    That content was in the snapshot three times — its own custom section, three columns of
    the BJS Alpha course row, and a paragraph inside "Course in details" — and every copy
    told the bot to withhold the Zoom links until the customer surrendered a name and phone
    number, for a class that had already happened. Deleting one copy would have left the bot
    handing the links out from another, so this asserts on the whole file.
    """
    raw = SNAPSHOT.read_text(encoding="utf-8")
    assert "zoom.us" not in raw, "a dead masterclass Zoom link is back in the knowledge base"
    assert "Free Class Link" not in raw
    assert "Free Masterclass Opportunity" not in raw
