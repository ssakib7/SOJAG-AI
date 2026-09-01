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


# Separators a customer may type INSIDE a number ("01712-345 678"). Matched between
# digits rather than stripped from the whole message: stripping globally merged separate
# numbers into one long digit run ("22000 01712345678" -> "2200001712345678"), and the
# no-digit-before guard then rejected the perfectly good number that followed. That cost
# real leads, because "amount then phone" is exactly how people write payment messages.
_SEP = r"[\s\-().]*"
# The boundary guards look at the RAW neighbouring character, not across separators: a
# digit immediately before or after means this run is longer than a mobile number and is
# something else (a roll, an amount, a 13-digit typo). A digit one space away is simply
# the next number in the message, and must not disqualify this one — otherwise
# "01712345678 01912345678" ("call either") answered with the wrong number, or neither.
_PHONE_SCAN = re.compile(r"(?<!\d)(?:\+?88{sep})?(0{sep}1(?:{sep}\d){{9}})(?!\d)".format(sep=_SEP))


def find_phone(raw: object) -> str | None:
    """Find a Bangladeshi mobile number inside free text, or None.

    Same rules as normalize_phone but scanning rather than parsing the whole string, so
    "James 01712345678", "আমার নম্বর ০১৭...।", "22000 taka, 01712-345678" and
    "01712345678 / 01912345678" all yield a number.
    """
    s = _to_ascii_digits(str(raw or ""))
    m = _PHONE_SCAN.search(s)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(1))
    return digits if re.fullmatch(r"01\d{9}", digits) else None


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
    # "complete" needs a word boundary: it was matching inside "completely", so
    # "fee completely bujhini" ("I didn't understand the fee at all") paged the team.
    r"hoye\s*ge?chh?e|hoye?chh?e|hoise|hoyese|(?<!be\s)\bpaid\b|\bsent\b|\bdone\b|complete(?:d)?\b)",
    re.IGNORECASE,
)
# Words that turn a completed-action verb back into a QUESTION. "ভর্তি ফি কত হয়েছে?"
# is context (ফি) + done (হয়েছে) and used to alert as a payment claim; it is someone
# asking a price. A question mark alone is not enough — "টাকা পাঠিয়েছি, পেয়েছেন?" is a
# real claim WITH a question — so this only fires on interrogatives that ask for a value.
ASKING = re.compile(r"(কত|কতো|koto|kbre|কীভাবে|কিভাবে|kivabe|kibhabe|\bhow\s+much\b|\bwhat\s+is\b)", re.IGNORECASE)
# bKash/Nagad/Rocket ids are ~8-12 chars of mixed letters+digits. Require both a letter
# and a digit so plain words and plain numbers (like phone numbers) don't match.
TRX_ID = re.compile(r"\b(?=[A-Z0-9]{8,14}\b)(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*\d)[A-Z0-9]{8,14}\b", re.IGNORECASE)
# Product names, batch codes and model numbers have exactly the shape of a transaction id
# ("Alpha20Batch", "iPhone14Pro"), and one of those alone used to page the team with a
# PAYMENT CLAIM. A real bKash/Nagad id is not a word with a number stuck on it: it has no
# recognisable word inside it. Anything whose letters spell one of ours is not an id.
TRX_WORDLIKE = re.compile(
    r"(alpha|batch|course|bjs|bar|mbg|iphone|samsung|redmi|realme|oppo|vivo|xiaomi|android|"
    r"class|exam|viva|preli|written|online|offline|road|house|block|sector|flat|room|"
    r"whatsapp|facebook|youtube|zoom|google|gmail)",
    re.IGNORECASE,
)
# Share-link short codes (vm.tiktok.com/ZSVhEN6k5, youtu.be/…) look exactly like trx ids,
# so URLs are removed before matching — a real trx id never arrives inside a link.
URL_IN_TEXT = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)


def find_trx_id(text: object) -> str | None:
    cleaned = URL_IN_TEXT.sub(" ", str(text or ""))
    for m in TRX_ID.finditer(cleaned):
        token = m.group(0)
        if TRX_WORDLIKE.search(token):
            continue  # a product/batch name, not a transaction id
        return token.upper()
    return None


def looks_like_payment_claim(text: object) -> bool:
    """True only for actual claims: a transaction id (customers often paste just the id),
    or payment context + a completed-action verb together in one message.

    This is the net UNDER the model, so it stays biased toward alerting — but every false
    positive trains the team to skim past payment alerts, which is how a real one gets
    missed. Questions about price are therefore excluded explicitly.
    """
    s = str(text or "")
    if not s.strip():
        return False
    if find_trx_id(s):
        return True
    if not (PAY_CONTEXT.search(s) and PAY_DONE.search(s)):
        return False
    return not ASKING.search(s)


# The one sentence the bot must never write. Only a human can confirm money arrived, so
# when a receipt is on the table the OUTGOING reply is checked too, not just the incoming
# message — the prompt already forbids this, and this is the net under the prompt.
#
# Deliberately broad on the verb side: a false positive costs the customer a warm sentence
# and hands them the safe canned acknowledgement instead, which is exactly what they used
# to get. A false negative tells someone their money is in when nobody has looked.
VERIFY_SUBJECT = re.compile(
    r"(পেমেন্ট|পেমেন্টটি|পেমেন্টের|টাকা|টাকাটা|ট্রানজেকশন|লেনদেন|ভর্তি|payment|transaction)",
    re.IGNORECASE,
)
VERIFY_CLAIM = re.compile(
    r"(যাচাই|নিশ্চিত|কনফার্ম|সফল|গৃহীত|অনুমোদ|সম্পন্ন|গ্রহণ|পৌঁছে|verified|confirmed|"
    r"successful|approved|received)",
    re.IGNORECASE,
)
# "…হয়েছে" is what turns a word like সম্পন্ন into a claim. "পেমেন্ট সম্পন্ন করুন" is an
# instruction and must not trip this; "পেমেন্ট সম্পন্ন হয়েছে" must.
VERIFY_DONE = re.compile(
    r"(হয়েছে|হয়ে\s*গেছে|হয়ে\s*গেল|হয়েছেন|করা\s*হয়েছে|পেয়ে\s*গেছি|verified|confirmed|"
    r"successful|approved)",
    re.IGNORECASE,
)


# The other sentence the bot must never write: an apology for its own machinery.
# "দুঃখিত, একটি সমস্যা হয়েছে। অনুগ্রহ করে আবার চেষ্টা করুন।" was a canned fallback until it
# was deleted — but deleting a constant only stops OUR code from sending it. The model can
# write the same sentence itself at any time, from the KB, from an admin-edited prompt, or
# from nothing at all, so the ban is enforced on the way OUT (see graph.send_message).
#
# It is banned because it is not an answer: it tells a customer their message failed while
# giving them nothing to do about it, and when the cause persists it lands two and three
# times in a row under their own messages. Silence plus a page to the team beats it — the
# customer is no less informed, and a human arrives.
#
# BOTH halves are required, so ordinary courtesy still gets through: "দুঃখিত স্যার, এই
# তথ্যটি আমাদের কাছে নেই" is an apology with no failure claim, and "পেমেন্টে সমস্যা হয়েছে
# কিনা দেখছি" is a failure word with no apology. Only the two together make the sentence
# that says "our software broke".
APOLOGY = re.compile(r"(দুঃখিত|দু:খিত|দুখিত|সরি|sorry|apolog)", re.IGNORECASE)
TECH_FAILURE = re.compile(
    r"(সমস্যা\s*হয়েছে|সমস্যা\s*হচ্ছে|ভুল\s*হয়েছে|ত্রুটি|গণ্ডগোল|"
    r"went\s*wrong|error|technical\s*(problem|issue|difficult))",
    re.IGNORECASE,
)


def apologises_for_a_failure(text: object) -> bool:
    """True when a reply blames our own software for not answering."""
    s = str(text or "")
    return bool(APOLOGY.search(s) and TECH_FAILURE.search(s))


def claims_payment_verified(text: object) -> bool:
    """True when a reply tells the customer their payment has landed, cleared, or been
    accepted. Used only on turns where a receipt was actually seen."""
    s = str(text or "")
    if not s.strip():
        return False
    return bool(VERIFY_SUBJECT.search(s) and VERIFY_CLAIM.search(s) and VERIFY_DONE.search(s))


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
