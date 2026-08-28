"""One-off migration: move behaviour rules out of the knowledge base into prompt sections.

Nine custom sections were pulled off the old VPS inside `knowledge_base.customSections`.
Seven of them are not knowledge at all — they are instructions ("ask which payment method
first", "never claim a payment is verified", "use the customer's name sparingly"). Sitting
inside the KB block they are framed by STRICT_ADHERENCE and KB_FOOTER as *facts the bot may
recite*, when what they describe is *how the bot should behave*. This moves them up to
=== EXAMPLES & SCENARIOS ===, which is where the assembled prompt puts behaviour (see
kb/config.py build_system_prompt).

This does NOT shrink the prompt — both blocks are in the same cached prefix. It changes what
the model is told the text IS.

Two of the seven carry hard facts mixed into the prose (the bKash/Nagad/bank/gateway details,
the operations manager's number). Those facts must stay under the KB's "never invent a contact
detail" guardrail, so this adds them back as compact facts-only KB sections. The payment
numbers therefore appear in both halves; the KB copy is the canonical one, and if an account
ever changes both must be updated.

The one edit this makes to customer-facing Bengali is the address style: the templates say
ভাইয়া/আপু, which the system prompt bans outright. See ADDRESS_FIXES — enumerated literals, so
that ভাইয়া used as a third-party honorific ("মোঃ তুহিন বাদশা ভাইয়া'কে") survives untouched.
Anything the sweep does not cover is printed for a human to read, never guessed at.

Left alone deliberately:
  - "Qualification for attending BJS Exam" — genuine reference knowledge (BJS eligibility).
  - "Free Class Link of 20th BJS Alpha Batch in 21 August, 2026" — EXPIRED (that date has
    passed) and still tells the bot to collect a phone number and hand out zoom links for a
    class that already ran. Deleting it is a content decision for the academy, not this
    migration's call. Flagged in the summary.

Usage (from the repo root, with DATABASE_URL set or the compose db running):
    uv run python scripts/split_behavior_sections.py            # dry run — writes proposals, touches nothing
    uv run python scripts/split_behavior_sections.py --apply    # write to bot_config + hot reload

Idempotent: re-running finds nothing left to move and is a no-op.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.kb.catalog import normalize_section_title  # noqa: E402

# Behaviour sections, in the order they should appear under === EXAMPLES & SCENARIOS ===:
# how to hold the conversation, then the enrolment -> payment -> post-payment path, then the
# specific situations. Matched on the normalised title, so a trailing colon does not matter.
BEHAVIOUR_TITLES = [
    "conversation continuity, name usage & follow-up questions",
    "enrollment guideline",
    "payment procedure",
    "post-payment & payment confirmation flow",
    "website related issue & enrollment problem questions",
    "upcoming batch related questions",
    "existing students and enrolled students",
]

# --- Address style -----------------------------------------------------------------
# The pulled templates address the customer as ভাইয়া/আপু, which the system prompt bans
# outright ("NEVER with casual terms like ভাই, ভাইয়া, or আপু") and the answering rules repeat.
# Three sources, three answers; স্যার/ম্যাডাম wins, so the templates are brought into line.
#
# These are ENUMERATED literals, not a regex sweep, because ভাইয়া is also a legitimate
# honorific for a THIRD PARTY: "মোঃ তুহিন বাদশা ভাইয়া'কে" (the operations manager) is correct
# Bengali and must survive untouched, as must "হাসান মাহমুদ স্যার" and other named mentors.
# Every pattern here anchors on the term being used to ADDRESS the customer — opening a quoted
# template, or following জ্বি/ধন্যবাদ.
#
# UNICODE TRAP: this data spells য় two ways. The six customer templates use the precomposed
# U+09DF (BENGALI LETTER YYA); "বাদশা ভাইয়া" uses the decomposed U+09AF U+09BC (য + nukta).
# They render identically and compare unequal, and NFC does NOT reconcile them — U+09DF is a
# composition exclusion, so normalising only ever decomposes. A literal written in one form
# silently matches none of the other, so every pattern is expanded into both spellings below.
# The replacements need no such care: স্যার and ম্যাডাম contain no nukta.
YYA_COMPOSED = "য়"
YYA_DECOMPOSED = "য়"


def _both_spellings(text: str) -> list[str]:
    """The pattern as written, in both য় encodings, deduplicated and order-stable."""
    decomposed = text.replace(YYA_COMPOSED, YYA_DECOMPOSED)
    return list(dict.fromkeys([decomposed, decomposed.replace(YYA_DECOMPOSED, YYA_COMPOSED)]))


ADDRESS_FIXES = [
    (pattern, replacement)
    for written, replacement in [
        ('"ভাইয়া, ', '"স্যার, '),
        ('"আপু, ', '"ম্যাডাম, '),
        ("জ্বি ভাইয়া, ", "জ্বি স্যার, "),
        ("ধন্যবাদ ভাইয়া, ", "ধন্যবাদ স্যার, "),
    ]
    for pattern in _both_spellings(written)
]

# What a residual scan reports but must NOT rewrite: kinship terms about someone else.
THIRD_PARTY_HONORIFICS = tuple(_both_spellings("বাদশা ভাইয়া"))


def normalize_address(text: str) -> tuple[str, int]:
    """Apply the enumerated address fixes. Returns (new text, replacements made)."""
    replaced = 0
    for old, new in ADDRESS_FIXES:
        replaced += text.count(old)
        text = text.replace(old, new)
    return text, replaced


def residual_address_terms(text: str) -> list[str]:
    """Remaining ভাইয়া/আপু, with context, minus the third-party honorifics. Anything this
    returns is for a human to read — the migration never guesses at it."""
    import re

    terms = "|".join(t for written in ("ভাইয়া", "আপু") for t in _both_spellings(written))
    out = []
    for match in re.finditer(rf".{{0,30}}(?:{terms}).{{0,25}}", text):
        snippet = match.group(0).replace("\n", " ").strip()
        if not any(allowed in snippet for allowed in THIRD_PARTY_HONORIFICS):
            out.append(snippet)
    return out


# --- Facts rescued from the moved sections -------------------------------------------
# What would otherwise leave the knowledge base with the prose around it. Transcribed from
# the sections being moved — no new information.
FACT_SECTIONS: list[dict[str, Any]] = [
    {
        "title": "Payment accounts & links",
        "adminOnly": True,
        "body": """These are the academy's only payment destinations. Never invent, modify, guess, or
substitute a number, account detail, or link.

**bKash — 01729290202 (Ehsan Enterprize)**
This is a Merchant Payment: the customer must use the "Payment" option (bKash App or *247#),
NOT Send Money and NOT Cash Out. Reference: the customer's name or mobile number.

**Nagad — 01339622238**
Merchant Pay option (Nagad App or *167#). Reference: the customer's name or mobile number.

**Bank transfer**
- Name: MD.WALID RAHMAN SWAPNIL
- A/C No: 1111164283300
- Bank: AB Bank PLC
- Branch: Dhanmondi
- Routing No: 020261182
- Swift Code: ABBLBDDH
Use NPSB for instant confirmation. BEFTN and RTGS must not be used.

**Credit / debit card — secure gateway**
https://invoice.sslcommerz.com/invoice-form?refer=68C167630ABF0
The customer enters their name, mobile number and the amount on that page.

A transaction id, screenshot or deposit slip is payment information SUBMITTED for
verification. It is never proof that a payment has been verified by the academy.""",
    },
    {
        "title": "Support escalation contact",
        "adminOnly": False,
        "body": """Operations Manager: মোঃ তুহিন বাদশা — WhatsApp +8801517824328.

For account-lock and enrolment problems that cannot be resolved in chat. The number must
always be given exactly as +8801517824328.""",
    },
]


def split_sections(
    kb: dict[str, Any], prompt_sections: list[dict[str, str]]
) -> tuple[dict[str, Any], list[dict[str, str]], dict[str, list[str]]]:
    """Pure transform. Returns (new kb, new prompt sections, summary of what changed)."""
    # Address style is normalised across EVERY section, moved or not — a section that stays
    # in the KB is just as capable of contradicting the system prompt.
    custom = []
    addresses_fixed = 0
    for section in kb.get("customSections") or []:
        if not section:
            continue
        body, n = normalize_address(str(section.get("body") or ""))
        addresses_fixed += n
        custom.append({**section, "body": body})

    by_title = {normalize_section_title(s.get("title")): s for s in custom}

    moved: list[dict[str, str]] = []
    for key in BEHAVIOUR_TITLES:
        section = by_title.get(key)
        if section is None:
            continue  # already moved on an earlier run, or renamed in the panel
        moved.append({
            # The panel renders these as "### {title}"; a trailing colon reads as a typo there.
            "title": str(section.get("title") or "").strip().rstrip(":").strip(),
            "body": str(section.get("body") or ""),
        })

    moved_keys = set(BEHAVIOUR_TITLES)
    kept = [s for s in custom if normalize_section_title((s or {}).get("title")) not in moved_keys]

    present = {normalize_section_title(s.get("title")) for s in kept if s}
    added = [f for f in FACT_SECTIONS if normalize_section_title(f["title"]) not in present]
    new_kb = {**kb, "customSections": [*kept, *added]}

    # Re-running must not duplicate: a title already in prompt_sections is replaced, not appended.
    incoming = {normalize_section_title(s["title"]) for s in moved}
    survivors = [
        s for s in prompt_sections
        if normalize_section_title((s or {}).get("title")) not in incoming
    ]
    new_sections = [*survivors, *moved]

    residual = residual_address_terms(
        "\n".join([*(s.get("body", "") for s in new_kb["customSections"]),
                   *(s["body"] for s in new_sections)])
    )

    return new_kb, new_sections, {
        "moved": [s["title"] for s in moved],
        "kept_in_kb": [str((s or {}).get("title") or "") for s in kept],
        "facts_added": [f["title"] for f in added],
        "addresses_fixed": [str(addresses_fixed)],
        "residual_address_terms": residual,
    }


def _report(summary: dict[str, list[str]]) -> None:
    for title in summary["moved"]:
        print(f"  KB -> prompt sections : {title}")
    for title in summary["facts_added"]:
        print(f"  new KB facts section  : {title}")
    for title in summary["kept_in_kb"]:
        print(f"  stays in the KB       : {title}")
    if not summary["moved"]:
        print("  nothing left to move — already migrated.")

    print(f"\n  ভাইয়া/আপু -> স্যার/ম্যাডাম : {summary['addresses_fixed'][0]} replacements")
    if summary["residual_address_terms"]:
        print("  REVIEW — kinship terms this migration would not touch:")
        for snippet in summary["residual_address_terms"]:
            print(f"    {snippet}")


async def main(apply: bool, out_dir: Path) -> None:
    from app.kb.config import get_config_value, reload, set_config_value

    kb = (await get_config_value("knowledge_base")) or {}
    if not kb.get("customSections"):
        print("No knowledge_base.customSections in bot_config — is DATABASE_URL pointing at the bot's db?")
        return
    sections = ((await get_config_value("prompt_sections")) or {}).get("sections") or []

    new_kb, new_sections, summary = split_sections(kb, sections)
    _report(summary)

    if not apply:
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, value in (("knowledge_base", new_kb), ("prompt_sections", {"sections": new_sections})):
            path = out_dir / f"{name}.proposed.json"
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\nwrote {path}")
        print("\nDry run — bot_config untouched. Re-run with --apply to write.")
        return

    await set_config_value("knowledge_base", new_kb)
    await set_config_value("prompt_sections", {"sections": new_sections})
    await reload()  # this process only — see the note below
    print(
        "\nApplied to bot_config."
        "\n\n⚠ The RUNNING bot has not picked this up yet: it caches the assembled prompt in"
        "\n  its own memory and only rebuilds it on boot or when someone saves in the admin"
        "\n  panel. Restart the bot (docker compose restart bot) — or open the admin panel and"
        "\n  press Save once — before assuming replies use the new layout."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write to bot_config (default: dry run)")
    parser.add_argument("--out", default="build/kb-split", help="where a dry run writes its proposals")
    args = parser.parse_args()

    from app.db.engine import dispose_engine

    async def _run() -> None:
        try:
            await main(args.apply, Path(args.out))
        finally:
            await dispose_engine()

    asyncio.run(_run())
