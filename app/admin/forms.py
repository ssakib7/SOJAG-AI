"""Form parsing for the admin panel — the port of admin.js formToKb/mergeEditorKb.

The panel posts bracket-notation fields exactly as the old Express/qs setup did, e.g.

    categories[0][courses][1][online][offer] = 20000
    customSections[2][adminOnly]             = 1

Express's `qs` produced nested objects/arrays from those keys; FastAPI hands us a flat
multidict, so `parse_bracket_form` rebuilds the same nesting. Indices can be sparse (a
column removed in the browser leaves a gap), which is why values are keyed by their
original index and course cells are read from the SAME key as their column header.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

_KEY_PART = re.compile(r"\[([^\]]*)\]")


def parse_bracket_form(items: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    """Flat form pairs -> nested dicts. Numeric keys stay strings (sparse-safe)."""
    out: dict[str, Any] = {}
    for key, value in items:
        head, _, rest = key.partition("[")
        parts = [head] + _KEY_PART.findall("[" + rest if rest else "")
        node: dict[str, Any] = out
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = value
    return out


def _ordered_entries(x: Any) -> list[tuple[str, Any]]:
    """Items ordered by numeric key, keeping the ORIGINAL key (may be sparse)."""
    if isinstance(x, dict):
        def sort_key(k: str) -> tuple[int, str]:
            return (int(k), "") if k.isdigit() else (10**9, k)

        return [(k, x[k]) for k in sorted(x, key=sort_key)]
    if isinstance(x, list):
        return [(str(i), v) for i, v in enumerate(x)]
    return []


def _to_list(x: Any) -> list[Any]:
    return [v for _, v in _ordered_entries(x) if v is not None]


def _price(p: Any) -> dict[str, str]:
    p = p if isinstance(p, dict) else {}
    return {"offer": str(p.get("offer") or "").strip(), "regular": str(p.get("regular") or "").strip()}


def _read_extra(extra: Any, key: str) -> str:
    if isinstance(extra, dict):
        return str(extra.get(key) or "")
    if isinstance(extra, list) and key.isdigit() and int(key) < len(extra):
        return str(extra[int(key)] or "")
    return ""


def form_to_kb(body: dict[str, Any]) -> dict[str, Any]:
    """Parsed form body -> knowledge-base dict (same shape the bot renders from)."""
    categories = []
    for cat in _to_list(body.get("categories")):
        cat = cat if isinstance(cat, dict) else {}
        # Keep only columns with a non-empty header; remember their keys so each course's
        # value is read from the same (possibly sparse) key as its header.
        col_entries = [(k, str(name or "").strip()) for k, name in _ordered_entries(cat.get("extraColumns"))]
        col_entries = [(k, name) for k, name in col_entries if name]
        col_keys = [k for k, _ in col_entries]

        courses = []
        for course in _to_list(cat.get("courses")):
            course = course if isinstance(course, dict) else {}
            name = str(course.get("name") or "").strip()
            if not name:
                continue  # drop fully-empty rows the admin added but left blank
            courses.append({
                "name": name,
                "duration": str(course.get("duration") or "").strip(),
                "starts": str(course.get("starts") or "").strip(),
                "online": _price(course.get("online")),
                "offline": _price(course.get("offline")),
                "onlineOffline": _price(course.get("onlineOffline")),
                "extra": [_read_extra(course.get("extra"), k).strip() for k in col_keys],
            })
        categories.append({
            "name": str(cat.get("name") or "").strip(),
            "extraColumns": [name for _, name in col_entries],
            "courses": courses,
        })

    custom_sections = []
    for s in _to_list(body.get("customSections")):
        s = s if isinstance(s, dict) else {}
        title = str(s.get("title") or "").strip()
        text = str(s.get("body") or "")
        if title or text.strip():
            # Unchecked boxes send nothing -> False.
            custom_sections.append({"title": title, "body": text, "adminOnly": s.get("adminOnly") == "1"})

    contact = body.get("contact") if isinstance(body.get("contact"), dict) else {}
    return {
        "about": str(body.get("about") or ""),
        "contact": {
            "phone": str(contact.get("phone") or ""),
            "email": str(contact.get("email") or ""),
            "address": str(contact.get("address") or ""),
            "officeHours": str(contact.get("officeHours") or ""),
        },
        "courseCategories": categories,
        "books": str(body.get("books") or ""),
        "enroll": str(body.get("enroll") or ""),
        "customSections": custom_sections,
    }


def merge_editor_kb(current: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """Apply an EDITOR's submission: courses, books and (non-admin-only) custom sections
    come from the form; everything else is copied from the stored KB. This is the
    server-side guarantee behind the editor role — a hand-crafted POST with extra fields
    cannot change anything but these three. adminOnly is forced False on submitted
    sections so a crafted POST can't hide a section from the admin's own view.
    """
    submitted = form_to_kb(body)
    admin_only = [s for s in (current.get("customSections") or []) if s.get("adminOnly")]
    return {
        **current,
        "courseCategories": submitted["courseCategories"],
        "books": submitted["books"],
        "customSections": [*admin_only, *[{**s, "adminOnly": False} for s in submitted["customSections"]]],
    }


def prompt_sections_from_form(body: dict[str, Any]) -> list[dict[str, str]]:
    """promptSections[i][title|body] -> a clean list, blanks dropped."""
    out = []
    for s in _to_list(body.get("promptSections")):
        s = s if isinstance(s, dict) else {}
        title = str(s.get("title") or "").strip()
        text = str(s.get("body") or "")
        if title or text.strip():
            out.append({"title": title, "body": text})
    return out
