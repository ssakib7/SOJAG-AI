"""Course-catalog markdown rendering — the port of kb.js renderMarkdown().

The catalog (knowledge_base JSON) stays rendered into the system prompt: it is small,
changes only on admin edits, and price questions must never miss retrieval. Long-tail
content (books detail, FAQ growth) can graduate to the pgvector Knowledge store later.
"""

from __future__ import annotations

import re
from typing import Any

from app.kb.defaults import LEGACY_ADMIN_ONLY_TITLES


def normalize_section_title(title: object) -> str:
    return re.sub(r":+$", "", str(title or "").strip()).strip().lower()


def backfill_admin_only(kb: dict[str, Any]) -> dict[str, Any]:
    """Custom sections saved before the adminOnly flag existed get it backfilled."""
    sections = []
    for s in kb.get("customSections") or []:
        if s and s.get("adminOnly") is None:
            s = {**s, "adminOnly": normalize_section_title(s.get("title")) in LEGACY_ADMIN_ONLY_TITLES}
        sections.append(s)
    return {**kb, "customSections": sections}


def _group_digits(value: object) -> str:
    s = str(value or "").strip()
    if not s or not s.isdigit():
        return s
    return f"{int(s):,}"


def _format_price(price: dict[str, Any] | None) -> str:
    offer = str((price or {}).get("offer") or "").strip()
    if not offer:
        return "—"
    regular = str((price or {}).get("regular") or "").strip()
    base = f"৳{_group_digits(offer)}"
    return f"{base} (reg {_group_digits(regular)})" if regular else base


def render_markdown(kb: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# De Jure Academy — Knowledge Base")
    lines.append("")
    lines.append("> Source of truth for the Facebook Messenger AI auto-reply bot.")
    lines.append("> Generated from the admin panel's knowledge base. Edit there, not here.")
    lines.append("")

    lines.append("## About")
    lines.append(str(kb.get("about") or "").strip())
    lines.append("")

    c = kb.get("contact") or {}
    lines.append("## Contact")
    lines.append(f"- Phone / WhatsApp: {str(c.get('phone') or '').strip() or 'TODO'}")
    lines.append(f"- Email: {str(c.get('email') or '').strip() or 'TODO'}")
    lines.append(f"- Address / Campus: {str(c.get('address') or '').strip() or 'TODO'}")
    lines.append(f"- Office hours: {str(c.get('officeHours') or '').strip() or 'TODO'}")
    lines.append("")

    lines.append("## Courses")
    lines.append('Prices are in BDT (৳). "Offer" = current sale price; "regular" shown when different.')
    lines.append("Modes: **online** = live online classes · **offline** = campus classes · **online+offline** = both.")
    lines.append("Seats are limited per batch.")
    lines.append("")
    for cat in kb.get("courseCategories") or []:
        lines.append(f"### {cat.get('name')}")
        extra_cols = cat.get("extraColumns") or []
        headers = ["Course", "Online", "Offline", "Online+Offline", "Duration", "Starts", *extra_cols]
        lines.append(f"| {' | '.join(headers)} |")
        lines.append(f"|{'|'.join('---' for _ in headers)}|")
        for course in cat.get("courses") or []:
            extra = course.get("extra") or []
            cells = [
                course.get("name") or "",
                _format_price(course.get("online")),
                _format_price(course.get("offline")),
                _format_price(course.get("onlineOffline")),
                str(course.get("duration") or "").strip() or "—",
                str(course.get("starts") or "").strip() or "—",
                *[(str(extra[k]) if k < len(extra) else "").strip() or "—" for k in range(len(extra_cols))],
            ]
            lines.append(f"| {' | '.join(cells)} |")
        lines.append("")

    lines.append("## Books / Products (BDT ৳)")
    lines.append(str(kb.get("books") or "").strip())
    lines.append("")
    lines.append("## How to enroll / pay")
    lines.append(str(kb.get("enroll") or "").strip())
    lines.append("")

    for section in kb.get("customSections") or []:
        title = str((section or {}).get("title") or "").strip()
        body = str((section or {}).get("body") or "").strip()
        if not title and not body:
            continue
        lines.append(f"## {title or 'Additional information'}")
        lines.append(body)
        lines.append("")

    return "\n".join(lines)
