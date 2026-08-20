"""Text utilities ported 1:1 from the Node bot (server.js).

These are the deterministic backstops that sit UNDER the model's judgment —
phone validation/scanning, payment-claim detection, and the Messenger 2000-char
split. Behaviour must match the old bot exactly; the unit tests pin them.
"""

from __future__ import annotations

import re

BN_DIGITS = "০১২৩৪৫৬৭৮৯"
_BN_TRANS = {ord(d): str(i) for i, d in enumerate(BN_DIGITS)}


def _to_ascii_digits(s: str) -> str:
    return s.translate(_BN_TRANS)


def normalize_phone(raw: object) -> str | None:
    """Normalise a Bangladeshi mobile number to 11-digit 01XXXXXXXXX, or None if invalid.

    Accepts Bengali digits, +880/880 country codes, and spaces/dashes.
    """
    s = _to_ascii_digits(str(raw or ""))
    s = re.sub(r"[^\d+]", "", s)
    s = s.lstrip("+")
    if s.startswith("880"):
        s = "0" + s[3:]
    return s if re.fullmatch(r"01\d{9}", s) else None


_PHONE_SCAN = re.compile(r"(?<!\d)(?:\+?88)?(01\d{9})(?!\d)")


def find_phone(raw: object) -> str | None:
    """Find a Bangladeshi mobile number inside free text, or None.

    Same rules as normalize_phone but scanning rather than parsing the whole
    string, so "James 01712345678" or "আমার নম্বর ০১৭...।" are both found.
    """
    s = _to_ascii_digits(str(raw or ""))
    s = re.sub(r"[\s\-().]", "", s)
    m = _PHONE_SCAN.search(s)
    return m.group(1) if m else None


# Payment-claim detection: claim-shaped, not topic-shaped. "course fee ki eksathe
# payment korte hoi" is a QUESTION and must NOT alert the team — only "I have paid /
# টাকা পাঠিয়েছি / <trx id>" should. Trigger requires BOTH a payment-context word AND a
# completed-action verb, or a transaction id on its own.
PAY_CONTEXT = re.compile(
    r"(টাকা|পেমেন্ট|পরিশোধ|ফি|বিকাশ|নগদ|রকেট|taka|toka|payment|\bpaid\b|fee|bkash|bikash|nagad|nogod|rocket|upay|"
    r"send\s*money|porishodh|ট্রানজেকশন|লেনদেন|transaction|trx|txn)",
    re.IGNORECASE,
)
PAY_DONE = re.compile(
    r"(পাঠিয়েছি|পাঠাইছি|পাঠাইসি|পাঠায়েছি|পাঠালাম|পাঠাইলাম|দিয়েছি|দিলাম|দিসি|দিছি|করেছি|করে\s*(?:ফেলেছি|দিয়েছি)|করলাম|করসি|"
    r"হয়ে\s*গেছে|হয়েছে|হইছে|জমা\s*দিয়েছি|pathi?ye?chh?i|patha(?:i|ie)?s(?:i|hi)|pathaici|pathalam|pathailam|"
    r"diye?chh?i|diye\s*disi|dilam|dis[i]?si|disi|dichi|kore?chh?i|kor(?:si|ci|lam)|kore\s*(?:felsi|felechi|disi|dilam)|"
    r"hoye\s*ge?chh?e|hoye?chh?e|hoise|hoyese|(?<!be\s)\bpaid\b|\bsent\b|\bdone\b|complete(?:d)?)",
    re.IGNORECASE,
)
# bKash/Nagad/Rocket ids are ~8-12 chars of mixed letters+digits. Require both a letter
# and a digit so plain words and plain numbers (like phone numbers) don't match.
TRX_ID = re.compile(r"\b(?=[A-Z0-9]{8,14}\b)(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*\d)[A-Z0-9]{8,14}\b", re.IGNORECASE)
# Share-link short codes (vm.tiktok.com/ZSVhEN6k5, youtu.be/…) look exactly like trx ids,
# so URLs are removed before matching — a real trx id never arrives inside a link.
URL_IN_TEXT = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)


def find_trx_id(text: object) -> str | None:
    m = TRX_ID.search(URL_IN_TEXT.sub(" ", str(text or "")))
    return m.group(0).upper() if m else None


def looks_like_payment_claim(text: object) -> bool:
    """True only for actual claims: a transaction id (customers often paste just the id),
    or payment context + a completed-action verb together in one message."""
    s = str(text or "")
    if not s.strip():
        return False
    if find_trx_id(s):
        return True
    return bool(PAY_CONTEXT.search(s) and PAY_DONE.search(s))


def short_note(raw: object, max_len: int = 300) -> str:
    """Tidy a model-written note: collapse whitespace to one line, cap length."""
    s = re.sub(r"\s+", " ", str(raw or "")).strip()
    return s[: max_len - 1].rstrip() + "…" if len(s) > max_len else s


# The placeholder shown on alerts when no name is known. Old backstop captures stored
# this literal string AS the customer's name, so name handling must treat it — not just
# empty — as "we don't know".
NAME_UNKNOWN = "(নাম জানা যায়নি)"


def real_name(value: object) -> str | None:
    s = str(value or "").strip()
    return s if s and "নাম জানা যায়নি" not in s else None


# Messenger rejects a text message over 2000 characters outright (error #100). Split on
# the largest natural boundary that fits — blank line, then Bangla/Latin sentence end,
# then any space — so a long answer arrives as readable bubbles, never a mid-word cut.
SEND_TEXT_LIMIT = 2000


def split_for_messenger(text: object, limit: int = SEND_TEXT_LIMIT) -> list[str]:
    chunks: list[str] = []
    rest = str(text or "")
    while len(rest) > limit:
        window = rest[:limit]
        cut = window.rfind("\n\n")
        if cut < limit * 0.5:
            cut = max(window.rfind("। "), window.rfind(". "), window.rfind("\n"))
        if cut < limit * 0.5:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit  # no boundary at all (one huge unbroken token)
        chunks.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        chunks.append(rest)
    return chunks
