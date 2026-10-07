"""Course-catalog rendering for the system prompt.

The catalog (knowledge_base JSON) stays rendered into the system prompt: it is small,
changes only on admin edits, and price questions must never miss retrieval. Long-tail
content (books detail, FAQ growth) can graduate to the pgvector Knowledge store later.

TWO renderers live here, and keeping both is deliberate:

- render_markdown() is the byte-exact port of the old kb.js renderMarkdown(). Nothing in
  the running bot calls it any more; test_catalog.py pins it against fixtures generated
  from the retired Node implementation, and that test is the only surviving evidence that
  the Python port never quietly changed what customers are told. It is kept for that.
- render_labeled() is what build_system_prompt actually assembles into the prompt today.
  Its docstring carries the measurements that put it there.
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


def render_labeled(kb: dict[str, Any]) -> str:
    """The catalog as labelled lines, one fact per line, instead of one wide table per category.

    render_markdown lays each category out as a markdown table whose columns are Course,
    Online, Offline, Online+Offline, Duration, Starts and then every extraColumn the admin
    has added — nineteen columns wide for BJS. Reading a fee out of that means counting
    pipes across a row, and the whole reason a course carries three prices is that they are
    NOT interchangeable: quoting the online+offline regular fee at someone asking about
    offline is a wrong number in taka, sent to a customer, in our voice.

    Measured on a 10-question price eval (5 reps each, live API):

        gemini-3.7-flash      + table     50/50   $0.000962/call
        gemini-3.7-flash      + labeled   50/50   $0.000720/call   (-25%)
        gemini-3.5-flash-lite + table     45/50   $0.000352/call
        gemini-3.5-flash-lite + labeled   48/50   $0.000362/call

    So on the model we run today this is not an accuracy fix — 3.7-flash was already perfect
    on both renderings — it is a COST fix: the same answers come out 25% cheaper, because the
    model stops restating the table context it had to walk in order to find the number. The
    accuracy headroom is what would make a cheaper model survivable later: on the single
    question that separates the two models ("is the mock viva batch offline?", where the KB
    says that mode is undecided rather than absent) flash-lite goes from 0/10 to 9/10 here.

    Every line repeats the course name. That is the point, not an oversight: it is what stops
    a fee being read against the wrong course when several are on screen at once. Costs +4%
    characters against the table, which sits inside the cached prefix.

    Tried and rejected: marking non-numeric price cells as "[STATUS — not a fee]". It reads
    as an invitation to improvise and made flash-lite WORSE on the very question it was meant
    to fix (9/10 -> 4/10, newly claiming the batch runs both online and offline).
    """
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
    lines.append(
        "Every line below names the course it belongs to. A fee written under one course NEVER "
        "applies to another course, and a fee written under one mode NEVER applies to another mode."
    )
    lines.append("")
    for cat in kb.get("courseCategories") or []:
        lines.append(f"### {cat.get('name')}")
        lines.append("")
        extra_cols = cat.get("extraColumns") or []
        for course in cat.get("courses") or []:
            name = str(course.get("name") or "").strip()
            if not name:
                continue
            lines.append(f"#### {name}")
            for label, key in (
                ("Online fee", "online"),
                ("Offline fee", "offline"),
                ("Online+Offline fee", "onlineOffline"),
            ):
                value = _format_price(course.get(key))
                # "—" is _format_price's empty marker: the admin left that mode blank. A blank
                # is not "we do not offer it" (STRICT_ADHERENCE: silence is not a no), so the
                # line is dropped rather than rendered as an absence the model can quote back.
                if value and value != "—":
                    lines.append(f"- {name} — {label}: {value}")
            for label, key in (("Duration", "duration"), ("Starts", "starts")):
                value = str(course.get(key) or "").strip()
                if value:
                    lines.append(f"- {name} — {label}: {value}")
            extra = course.get("extra") or []
            for i, col in enumerate(extra_cols):
                value = (str(extra[i]) if i < len(extra) else "").strip()
                if value and value != "—":
                    lines.append(f"- {name} — {col}: {value}")
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
