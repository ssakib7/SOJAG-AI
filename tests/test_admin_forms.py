"""Bracket-notation form parsing — the contract between the ported admin panel's
HTML (categories[0][courses][1][online][offer]) and the stored knowledge base."""

import pytest

from app.admin.forms import form_to_kb, merge_editor_kb, parse_bracket_form, prompt_sections_from_form


def test_parse_nests_brackets():
    parsed = parse_bracket_form([
        ("about", "hi"),
        ("contact[phone]", "01712345678"),
        ("categories[0][name]", "BJS"),
        ("categories[0][courses][1][online][offer]", "20000"),
    ])
    assert parsed["about"] == "hi"
    assert parsed["contact"]["phone"] == "01712345678"
    assert parsed["categories"]["0"]["courses"]["1"]["online"]["offer"] == "20000"


def _kb_form(extra=()):
    return parse_bracket_form([
        ("about", "About us"),
        ("contact[phone]", "01712345678"),
        ("contact[email]", "info@dejure.com"),
        ("categories[0][name]", "BJS"),
        ("categories[0][extraColumns][0]", "Class days"),
        ("categories[0][courses][0][name]", "Signature Batch"),
        ("categories[0][courses][0][online][offer]", "22000"),
        ("categories[0][courses][0][online][regular]", "25000"),
        ("categories[0][courses][0][duration]", "6 mo"),
        ("categories[0][courses][0][extra][0]", "Sat, Mon"),
        ("categories[0][courses][1][name]", ""),  # blank row the admin never filled in
        ("books", "Book set — 4500"),
        ("enroll", "Call us"),
        *extra,
    ])


def test_form_to_kb_shapes_courses():
    kb = form_to_kb(_kb_form())
    cat = kb["courseCategories"][0]
    assert cat["name"] == "BJS" and cat["extraColumns"] == ["Class days"]
    assert len(cat["courses"]) == 1, "blank rows must be dropped"
    course = cat["courses"][0]
    assert course["online"] == {"offer": "22000", "regular": "25000"}
    assert course["offline"] == {"offer": "", "regular": ""}
    assert course["extra"] == ["Sat, Mon"]
    assert kb["contact"]["email"] == "info@dejure.com"


def test_sparse_columns_stay_aligned():
    """A column removed in the browser leaves a gap; values must follow their header key."""
    kb = form_to_kb(parse_bracket_form([
        ("categories[0][name]", "BJS"),
        ("categories[0][extraColumns][0]", "Days"),
        ("categories[0][extraColumns][2]", "Teacher"),   # index 1 was removed
        ("categories[0][courses][0][name]", "Batch A"),
        ("categories[0][courses][0][extra][0]", "Sat"),
        ("categories[0][courses][0][extra][2]", "Rahim Sir"),
    ]))
    cat = kb["courseCategories"][0]
    assert cat["extraColumns"] == ["Days", "Teacher"]
    assert cat["courses"][0]["extra"] == ["Sat", "Rahim Sir"]


def test_blank_column_header_drops_column():
    kb = form_to_kb(parse_bracket_form([
        ("categories[0][name]", "BJS"),
        ("categories[0][extraColumns][0]", "   "),
        ("categories[0][courses][0][name]", "Batch A"),
        ("categories[0][courses][0][extra][0]", "orphaned"),
    ]))
    assert kb["courseCategories"][0]["extraColumns"] == []
    assert kb["courseCategories"][0]["courses"][0]["extra"] == []


def test_custom_sections_and_admin_only_checkbox():
    kb = form_to_kb(parse_bracket_form([
        ("customSections[0][title]", "FAQ"),
        ("customSections[0][body]", "Q&A"),
        ("customSections[1][title]", "Payment procedure"),
        ("customSections[1][body]", "internal"),
        ("customSections[1][adminOnly]", "1"),
        ("customSections[2][title]", ""),   # blank -> dropped
        ("customSections[2][body]", "  "),
    ]))
    assert [s["title"] for s in kb["customSections"]] == ["FAQ", "Payment procedure"]
    assert kb["customSections"][0]["adminOnly"] is False
    assert kb["customSections"][1]["adminOnly"] is True


class TestEditorRole:
    """The server-side guarantee: an editor's POST can only change courses, books and
    their own custom sections — even a hand-crafted one with extra fields."""

    def _current(self):
        return {
            "about": "ORIGINAL about", "enroll": "ORIGINAL enroll",
            "contact": {"phone": "01700000000", "email": "", "address": "", "officeHours": ""},
            "courseCategories": [{"name": "Old", "extraColumns": [], "courses": []}],
            "books": "old books",
            "customSections": [
                {"title": "Payment procedure", "body": "internal", "adminOnly": True},
                {"title": "FAQ", "body": "public", "adminOnly": False},
            ],
        }

    def test_editor_cannot_touch_admin_fields(self):
        merged = merge_editor_kb(self._current(), _kb_form())
        assert merged["about"] == "ORIGINAL about"
        assert merged["enroll"] == "ORIGINAL enroll"
        assert merged["contact"]["phone"] == "01700000000"
        assert merged["courseCategories"][0]["name"] == "BJS"  # what they may change
        assert merged["books"] == "Book set — 4500"

    def test_admin_only_sections_survive_and_cannot_be_forged(self):
        merged = merge_editor_kb(self._current(), parse_bracket_form([
            ("customSections[0][title]", "FAQ"),
            ("customSections[0][body]", "edited"),
            ("customSections[0][adminOnly]", "1"),  # forged: must not stick
        ]))
        titles = [(s["title"], s["adminOnly"]) for s in merged["customSections"]]
        assert ("Payment procedure", True) in titles, "admin-only section was dropped"
        assert ("FAQ", False) in titles, "editor's section must stay visible to admins"

    def test_admin_save_replaces_everything(self):
        kb = form_to_kb(_kb_form())
        assert kb["about"] == "About us" and kb["enroll"] == "Call us"


def test_prompt_sections_from_form():
    sections = prompt_sections_from_form(parse_bracket_form([
        ("promptSections[0][title]", "Example lead"),
        ("promptSections[0][body]", "When a customer…"),
        ("promptSections[1][title]", ""),
        ("promptSections[1][body]", ""),
    ]))
    assert sections == [{"title": "Example lead", "body": "When a customer…"}]


@pytest.mark.parametrize("payload", [{}, {"categories": "x"}, {"customSections": 5}])
def test_malformed_bodies_do_not_crash(payload):
    kb = form_to_kb(payload)
    assert kb["courseCategories"] == [] and kb["customSections"] == []
