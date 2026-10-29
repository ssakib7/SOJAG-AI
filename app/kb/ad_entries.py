"""The automatic messages our Facebook ads send, as the admin lists them in the knowledge base.

The "Ad entry points" custom section tells the model which flow follows which ad message.
The same list tells the echo handler that one of those messages is Meta's ad automation
and not a colleague typing in the Page inbox — without that, every ad lead would pause the
bot for this customer (queues.is_human_takeover). One list, kept in the admin panel.

The section's convention: each entry quotes the message's opening after `Message starting`,
e.g.  1. Message starting "আপনি কি ২০তম বিজেএস কোর্সের ...?" (women students ad): ...
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from app.kb import config as kb_config

_OPENING = re.compile(r'Message starting\s*"([^"]+)"', re.IGNORECASE)
# Shorter than this and an opening could match an ordinary sentence a colleague typed.
MIN_OPENING_CHARS = 12
# How alike the message's start and a listed opening must be, when not exactly equal.
MATCH_RATIO = 0.9


def normalize(text: str) -> str:
    """Letters, marks and digits only, lowercased. Bengali vowel signs are marks (Mn/Mc),
    so they stay; spaces, punctuation, line breaks, emoji and a trailing "..." go."""
    return "".join(
        ch for ch in (text or "").lower() if unicodedata.category(ch)[0] in ("L", "M", "N")
    )


def ad_openings() -> list[str]:
    """Normalized openings from every custom section whose title mentions ad entry points."""
    out: list[str] = []
    for section in kb_config.current_kb().get("customSections") or []:
        title = str((section or {}).get("title") or "").lower()
        if "ad entry" not in title:
            continue
        for quoted in _OPENING.findall(str((section or {}).get("body") or "")):
            opening = normalize(quoted)
            if len(opening) >= MIN_OPENING_CHARS:
                out.append(opening)
    return out


def is_ad_message(text: str | None) -> bool:
    """True when this text carries one of the listed ad messages.

    Near enough counts: the admin types the opening by hand, and one letter off from
    what Ads Manager sends (প্রস্তুতি / প্রস্তুতির) must not turn every lead from that
    ad into a paused conversation. A colleague's own sentence never comes this close."""
    body = normalize(text or "")
    if not body:
        return False
    for opening in ad_openings():
        if opening in body:
            return True
        if SequenceMatcher(None, opening, body[: len(opening)]).ratio() >= MATCH_RATIO:
            return True
    return False
