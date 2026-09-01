"""Turn handling: batching, regeneration, pacing, lead/payment side effects.

The heart of the parity port. Key invariants (see AGNO_REBUILD_PLAN.md §6):
- One burst = ONE turn (one msg_count increment, one reply). Bubbles and PICTURES alike:
  a multi-photo send, or a question with a photo under it, is a single request.
- New bubbles or photos arriving mid-generation or mid-pause discard the draft and
  regenerate (cap 3); a discarded draft leaves zero trace — no history, no side effects.
- History commits only after the reply is actually sent.
- Deterministic backstops run UNDER the model: a valid phone number in hand is never
  dropped; an unmistakable payment claim never goes unreported.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agno.media import Image

from app.agents import factory
from app.agents.specialists import classify_image, classify_name_gender
from app.agents.turn_context import TurnContext
from app.config import get_settings
from app.db import state
from app.kb import config as kb_config
from app.kb.defaults import (
    FALLBACK_ATTACHMENT,
    FALLBACK_ESCALATED,
    FALLBACK_GREETING,
    FALLBACK_LEAD_RETRY,
    FALLBACK_LEAD_THANKS,
    FALLBACK_NON_TEXT,
    FALLBACK_PAYMENT_ACK,
)
from app.leads import outbox, sinks
from app.meta import graph
from app.ops import blocklist, escalate, followups
from app.ops.attachments import fetch_attachment_image
from app.utils.text import (
    NAME_UNKNOWN,
    claims_payment_verified,
    find_phone,
    find_trx_id,
    looks_like_payment_claim,
    real_name,
    short_note,
)

log = logging.getLogger(__name__)

# How many off-topic force-stops a sender gets before the blocklist takes over for good.
# Three means: one polite decline, then two more chances after the session cools off — a
# real customer caught by a misfire always gets back in, a spammer runs out.
OFF_TOPIC_CLOSES_BEFORE_BLOCK = 3

NON_TEXT_PROMPT_COOLDOWN_MS = 10 * 60 * 1000
MAX_REGENERATIONS = 3


def _rand_between(min_ms: int, max_ms: int) -> int:
    return random.randint(min_ms, max_ms)


def _reply_window_ms() -> tuple[int, int]:
    s = get_settings()
    lo = max(s.reply_min_seconds, 0) * 1000
    hi = max(max(s.reply_max_seconds, 0) * 1000, lo)
    return lo, hi


def _human_took_over(sender_id: str, what: str) -> bool:
    """A colleague answered from the Page inbox while this turn was still being composed.

    The queue checks this before a turn starts, but a turn runs 15-35s (the reading beat,
    then the model, then the humanized pause, times any regeneration). A colleague who
    jumped in during that window was talked over by a reply that was already in flight —
    the pause had been set, the draft just never looked at it again. Checked HERE too,
    immediately before every outbound message, because that is the last moment the answer
    can still be withdrawn.
    """
    if not state.is_human_handling(sender_id):
        return False
    log.info("🧑 [%s] human took over mid-turn — %s withheld", sender_id, what)
    return True


async def _human_send(sender_id: str, text: str, head_start_ms: int = 0) -> bool:
    """Typing bubble, randomized human pause (model latency discounted), then send.
    Returns whether the customer actually received it."""
    await graph.send_action(sender_id, "typing_on")
    lo, hi = _reply_window_ms()
    wait = _rand_between(lo, hi) - head_start_ms
    if wait > 0:
        await asyncio.sleep(wait / 1000)
    return await graph.send_message(sender_id, text)


# --- Model-facing text for edits / replies (port of editedText / inboundText) ---
def _edited_text(session: dict[str, Any], edit: dict[str, Any]) -> str:
    rec = next((r for r in session.get("recent_mids", []) if r["mid"] == edit.get("mid")), None)
    before = rec["text"] if rec else None
    if rec:
        rec["text"] = edit.get("text", "")  # later reply-quotes show the edited version
    if before:
        return f'[গ্রাহক তাদের আগের বার্তা "{before}" সম্পাদনা করে নতুন করে লিখেছেন:]\n{edit.get("text", "")}'
    return f'[গ্রাহক তাদের আগের একটি বার্তা সম্পাদনা করে নতুন করে লিখেছেন:]\n{edit.get("text", "")}'


def _inbound_text(session: dict[str, Any], message: dict[str, Any]) -> str:
    """When a customer REPLIES to an earlier message, the webhook carries only the new
    text plus the quoted mid — re-inject the quoted content so "এইতো দিলাম" doesn't read
    like the customer gave nothing."""
    text = message.get("text", "")
    ref = (message.get("reply_to") or {}).get("mid")
    if ref:
        quoted = next((r["text"] for r in session.get("recent_mids", []) if r["mid"] == ref), None)
        if quoted:
            text = f'[গ্রাহক তাদের আগের একটি বার্তার উত্তরে লিখছেন। উদ্ধৃত বার্তাটি: "{quoted}"]\n{text}'
        else:
            text = f"[গ্রাহক আগের একটি বার্তার উত্তরে লিখছেন; উদ্ধৃত বার্তাটির বিষয়বস্তু আমাদের কাছে দৃশ্যমান নয়।]\n{text}"
    if message.get("mid"):
        mids = session.setdefault("recent_mids", [])
        mids.append({"mid": message["mid"], "text": message.get("text", "")})
        if len(mids) > state.RECENT_MIDS_MAX:
            del mids[: len(mids) - state.RECENT_MIDS_MAX]
    return text


def _drain_texts(inbox: dict[str, Any], sender_id: str, session: dict[str, Any]) -> list[str]:
    """Pull every text event waiting at the FRONT of the queue (stopping at the first
    non-text event so arrival order is preserved). Roster update lives HERE — the one
    place every text event passes exactly once — so bubbles that join an in-progress
    turn still update the Blocked-users page's last message and count."""
    from app.pipeline.queues import is_text_event

    texts: list[str] = []
    while inbox["queue"] and is_text_event(inbox["queue"][0]):
        event = inbox["queue"].pop(0)
        edit = event.get("message_edit")
        if edit:
            state.record_recent(sender_id, edit.get("text"))
            log.info("← [%s] (edited) %s", sender_id, edit.get("text"))
            texts.append(_edited_text(session, edit))
        else:
            message = event["message"]
            state.record_recent(sender_id, message.get("text"))
            log.info("← [%s] %s", sender_id, message.get("text"))
            texts.append(_inbound_text(session, message))
    return texts


async def _profile_with_gender(sender_id: str) -> dict[str, Any]:
    """Profile + one-time strict gender classification of the name (cached), so the model
    stops guessing ভাইয়া/আপু blind. The customer's own words always outrank it."""
    entry = await graph.fetch_profile(sender_id)
    if entry.get("gender") or not entry.get("name"):
        return entry  # real Meta field, or nothing to classify
    if "gender_guess" not in entry:
        try:
            entry["gender_guess"] = await classify_name_gender(entry["name"])
            log.info('profile gender: "%s" → %s', entry["name"], entry["gender_guess"])
        except Exception as err:
            # Leave unset so the next message retries; this turn just goes neutral.
            log.warning("profile gender classification failed: %s", err)
    return entry


async def _best_name(sender_id: str, session: dict[str, Any] | None) -> str:
    """Profile name first (stable identity, immune to model mangling); then what the
    conversation gave us. Placeholder values from old builds are skipped everywhere."""
    return (
        real_name(await graph.fetch_profile_name(sender_id))
        or real_name((session or {}).get("lead", {}).get("name"))
        or real_name((session or {}).get("customer_name"))
        or real_name(state.customers.get(sender_id, {}).get("name"))
        or ""
    )


async def _profile_first_lead(sender_id: str, lead: dict[str, Any]) -> dict[str, Any]:
    """The Facebook profile name leads; a differing chat-typed name goes into remarks."""
    prof = real_name(await graph.fetch_profile_name(sender_id))
    if not prof:
        return lead
    typed = real_name(lead.get("name"))
    differs = typed and typed.lower() != prof.lower()
    remarks = lead.get("remarks", "")
    if differs:
        remarks = " | ".join(x for x in [remarks, f"গ্রাহক চ্যাটে নাম দিয়েছেন: {typed}"] if x)
    return {**lead, "name": prof, "remarks": remarks}


async def capture_lead(sender_id: str, session: dict[str, Any], lead: dict[str, Any]) -> None:
    """Persist a captured lead and hand it to the durable outbox. Runs at most once per
    conversation; the customer-facing confirmation is the model's own reply."""
    name = lead.get("name")
    phone = lead.get("phone")
    interest = lead.get("interest", "")
    remarks = lead.get("remarks", "")
    # The alert may carry the placeholder, but it must never be STORED as the name.
    known_name = real_name(name)
    session["lead"]["captured"] = True
    session["lead"]["name"] = known_name

    now = state.now_ms()
    prev = state.customers.get(sender_id)
    state.upsert_customer(sender_id, {
        "name": known_name or "",
        "phone": phone,
        "interest": interest,
        "remarks": remarks,
        "captured": True,
        "first_seen": (prev or {}).get("first_seen", now),
        "last_seen": now,
    })
    session["returning"] = True
    session["customer_name"] = known_name
    state.mark_session_dirty(sender_id)

    conversation_url = await graph.resolve_inbox_url(sender_id)
    entry = session.get("entry") or {}
    try:
        await outbox.enqueue_lead({
            "name": name,
            "phone": phone,
            "interest": interest,
            "remarks": remarks,
            "senderId": sender_id,
            "time": datetime.now(timezone.utc).isoformat(),
            "source": "messenger",
            # Which ad paid for this lead. Empty for organic chats.
            "adId": entry.get("adId", ""),
            "adRef": entry.get("ref", ""),
            "entrySource": entry.get("source", ""),
            "conversationUrl": conversation_url,
        })
    except Exception as err:
        # The database write itself failed — the one path where the durable outbox cannot
        # help. Log the details, then page the team directly so the lead is worked by hand
        # instead of living only in a log file nobody reads.
        log.error("⚠ LEAD NOT PERSISTED [%s] %s / %s: %s", sender_id, name, phone, err)
        with contextlib.suppress(Exception):
            await sinks.send_telegram_text(
                "⚠ LEAD AT RISK — could not be saved to the database\n"
                f"Name: {name}\nPhone: {phone}\n"
                f"Interested in: {interest or '(not stated)'}\n"
                f"Remarks: {remarks or '-'}\n"
                f"Sender ID: {sender_id}\n"
                f"{conversation_url or ''}\n"
                "Please add this lead to the sheet by hand."
            )


async def report_payment_claim(sender_id: str, session: dict[str, Any] | None, details: dict[str, Any]) -> None:
    """Push a payment claim to the team via the durable outbox, deduped per conversation
    on trx id / screenshot URL so a repeated "did you get it?" doesn't re-alert."""
    # A trx id or screenshot identifies a specific payment. Without either, the customer is
    # just saying "I paid" — and saying it again in different words ("টাকা পাঠিয়েছি" then
    # "পাঠাইছি, পাইছেন?") is the SAME payment, not a second one. Keying such claims on the
    # message text alerted once per rewording; they collapse onto one per conversation.
    identified = details.get("dedupeKey") or details.get("trxId") or find_trx_id(details.get("customerMessage"))
    key = str(identified or "UNIDENTIFIED-CLAIM").strip().upper()[:80]
    if session is not None:
        payments = session.setdefault("payments", [])
        if key in payments:
            log.info('payment claim: duplicate of "%s" — already reported, not re-alerting', key)
            return
        # An identified payment supersedes an earlier vague one: the team gets the version
        # with the transaction id, and the vague key stays recorded so it cannot re-fire.
        payments.append(key)
        state.mark_session_dirty(sender_id)

    known = state.customers.get(sender_id) or {}
    payment = {
        "name": (await _best_name(sender_id, session)) or NAME_UNKNOWN,
        "phone": known.get("phone", ""),
        "trxId": short_note(details.get("trxId"), 40),
        "method": short_note(details.get("method"), 40),
        "amount": short_note(details.get("amount"), 20),
        "forCourse": short_note(details.get("forCourse"), 120),
        "senderName": short_note(details.get("senderName"), 60),
        "note": short_note(details.get("note")),
        "customerMessage": short_note(details.get("customerMessage"), 400),
        "attachmentUrl": details.get("attachmentUrl", ""),
        "senderId": sender_id,
        "time": datetime.now(timezone.utc).isoformat(),
        "conversationUrl": await graph.resolve_inbox_url(sender_id),
    }
    log.info('payment claim: name="%s" trx="%s" amount="%s" method="%s"%s',
             payment["name"], payment["trxId"], payment["amount"], payment["method"],
             " +screenshot" if payment["attachmentUrl"] else "")
    try:
        await outbox.enqueue_payment(payment)
    except Exception as err:
        log.error("⚠ PAYMENT CLAIM NOT PERSISTED [%s] %s %s: %s", sender_id, payment["name"], payment["trxId"], err)


async def _force_stop_if_off_topic(sender_id: str, session: dict[str, Any], turn: TurnContext) -> None:
    """Shared tail for every path that sends a model-written reply — the text batch and the
    image path alike, since an image the vision classifier cleared as "not a receipt" is
    answered by the same team, under the same off-topic rule.

    Driven by the model's end_conversation tool call, never by matching the reply text: the
    closing line is Bengali prose the model may legitimately reword, and a missed match used
    to fail silently, leaving the chat open with nothing in the logs to say so.

    Deliberately runs AFTER side effects: the payment/phone backstops are safety nets worth
    more than a stray off-topic lead, and block_sender drops the session they still read.
    """
    if not turn.end_conversation:
        return
    closes = state.close_session(sender_id)
    log.info('🚫 [%s] end_conversation("%s") — force-stopped (close %d of %d in %dh)',
             sender_id, turn.end_conversation, closes, OFF_TOPIC_CLOSES_BEFORE_BLOCK,
             state.OFF_TOPIC_CLOSE_WINDOW_MS // 3_600_000)
    if closes < OFF_TOPIC_CLOSES_BEFORE_BLOCK:
        return
    # Repeat offender: the polite decline isn't landing, so stop paying for them. Blocking
    # drops their events at the webhook edge — no session, no model call, no reply.
    name = real_name(session.get("customer_name")) or ""
    await blocklist.block_sender(sender_id, name=name, note="auto: repeated off-topic/spam")
    log.warning("⛔ [%s] auto-blocked after %d off-topic closings", sender_id, closes)
    await _announce_auto_block(sender_id, name, turn.end_conversation, closes)


async def _alert_undelivered(sender_id: str, session: dict[str, Any], combined: str) -> None:
    """Meta refused our reply after every retry. The customer is sitting in silence and
    only a human can rescue the conversation, so this is exactly what escalation is for."""
    await escalate.notify_team(
        sender_id, session, "other",
        "বটের উত্তর Facebook গ্রহণ করেনি — গ্রাহক কোনো উত্তর পাননি। অনুগ্রহ করে ইনবক্স থেকে সরাসরি উত্তর দিন।",
        customer_message=combined,
    )


async def _announce_auto_block(sender_id: str, name: str, reason: str, closes: int) -> None:
    """Tell the team we stopped answering someone. A wrong block is a customer lost in
    silence, so it gets the same courtesy as a payment claim: a ping, not just a log line.
    Best-effort — a Telegram outage must never break the reply that already went out."""
    try:
        await sinks.send_telegram_text(
            "⛔ <b>স্বয়ংক্রিয়ভাবে ব্লক করা হয়েছে</b>\n"
            f"নাম: {html.escape(name or NAME_UNKNOWN)}\n"
            f"কারণ: {html.escape(reason)}\n"
            f"{closes} বার অপ্রাসঙ্গিক বার্তার পর বন্ধ করা হয়েছে।\n"
            f"ভুল হলে অ্যাডমিন প্যানেল থেকে Unblock করুন — Sender ID: <code>{html.escape(sender_id)}</code>\n"
            f"{await graph.resolve_inbox_url(sender_id)}",
            html=True,
        )
    except Exception as err:
        log.error("⚠ [%s] auto-block notice not delivered: %s", sender_id, err)


def _reply_fallback(turn: TurnContext) -> str:
    """The right fallback when the model produced no text after a tool call.

    Returns "" when there is nothing to go on — the model wrote no text AND called no
    tool. That empty string is a real answer to the caller's question, not a bug: there
    is no honest sentence to put in its place, so nothing is sent at all.
    """
    if turn.payment:
        return FALLBACK_PAYMENT_ACK
    if turn.notice:
        return FALLBACK_ESCALATED
    if turn.lead:
        return FALLBACK_LEAD_THANKS
    if turn.lead_invalid:
        return FALLBACK_LEAD_RETRY
    # Nothing to go on. A person should look — observed live on "I don't want to give my
    # number, just get me a human", which is exactly the message you cannot afford to
    # fumble; _apply_side_effects pages the team off this flag.
    turn.extras["empty_reply"] = True
    return ""


# One retry, then silence. Sampling alone makes the second attempt a genuinely different
# call, and an empty reply is cheap to retry and expensive to ship. A third would only add
# latency for a customer already waiting.
EMPTY_REPLY_RETRIES = 1


def _no_reply_reason(turn: TurnContext) -> str:
    """What to tell the team when the customer got nothing. The two causes need different
    words because they need different people: a model that answered with nothing is a
    prompt/content problem, a model that did not answer at all is an outage — an API key,
    a quota, a billing card. Saying which saves the first ten minutes."""
    error = turn.extras.get("llm_error")
    if error:
        return (
            "AI মডেল সাড়া দেয়নি — গ্রাহক কোনো উত্তর পাননি। অনুগ্রহ করে ইনবক্স থেকে সরাসরি উত্তর "
            f"দিন এবং মডেলের কোটা/বিলিং/API key দেখে নিন। কারিগরি বিবরণ: {error}"
        )
    return (
        "বট এই বার্তার কোনো উত্তর তৈরি করতে পারেনি — গ্রাহক কোনো উত্তর পাননি। "
        "অনুগ্রহ করে দেখে নিন।"
    )


async def _model_attempt(sender_id: str, session: dict[str, Any], combined: str,
                         ask_contact: bool, offered_phone: str | None, known_phone: str | None,
                         profile: dict[str, Any], images: list[Image] | None = None,
                         attempt: int = 0) -> TurnContext:
    """One model call. Returns the TurnContext with reply text in extras["text"] —
    "" when the model produced nothing usable, INCLUDING when the call failed outright.

    A model failure sends the customer nothing at all. It used to send "দুঃখিত, এই মুহূর্তে
    উত্তর দিতে পারছি না। একটু পরে আবার চেষ্টা করুন।", and that line was doing no work: the
    customer cannot fix our model, "try again later" is an instruction to leave, and the
    bubble made the chat look answered to anyone scrolling the inbox. The team is paged
    instead (extras["llm_error"], read in _apply_side_effects), which is the only response
    that actually ends with the customer getting an answer.
    """
    turn = TurnContext(
        sender_id=sender_id, session=session, combined_text=combined, profile=profile,
        ask_contact=ask_contact, offered_phone=offered_phone, known_phone=known_phone,
        images=images or [], attempt=attempt,
    )
    try:
        text = await factory.run_turn(turn)
        turn.extras["text"] = text or _reply_fallback(turn)
        turn.extras["commit"] = True
    except Exception as err:
        log.error("LLM error: %s", err)
        # Don't store failed turns, so a transient error doesn't pollute history —
        # and drop any scratch a partial run may have left.
        turn.lead = None
        turn.payment = None
        turn.extras["text"] = ""
        turn.extras["commit"] = False
        turn.extras["llm_error"] = f"{type(err).__name__}: {err}"[:200]
    return turn


async def _run_model_turn(sender_id: str, session: dict[str, Any], combined: str,
                          ask_contact: bool, offered_phone: str | None, known_phone: str | None,
                          profile: dict[str, Any], images: list[Image] | None = None) -> TurnContext:
    """One turn's worth of model attempts: the call, then — only if it produced nothing at
    all — one retry.

    A turn that yields nothing twice comes back with extras["text"] == "", and the caller
    sends NOTHING. That is deliberate. The apology this used to send ("দুঃখিত, একটি সমস্যা
    হয়েছে। অনুগ্রহ করে আবার চেষ্টা করুন।") was the worst of both worlds: it told the
    customer their message had failed while giving them nothing to act on, it landed
    repeatedly in a row when the cause persisted, and it buried the real answer a
    colleague was about to type under an apology from the bot. Silence costs the customer
    the same amount of information and costs us none of their trust — and it is never
    silence to US, because empty_reply pages the team on the same turn.
    """
    for attempt in range(EMPTY_REPLY_RETRIES + 1):
        turn = await _model_attempt(
            sender_id, session, combined, ask_contact, offered_phone, known_phone, profile,
            images=images, attempt=attempt,
        )
        if not turn.extras.get("empty_reply"):
            return turn
        if attempt < EMPTY_REPLY_RETRIES:
            log.warning("⟳ [%s] the model produced no reply — retrying", sender_id)
    return turn


async def handle_text_batch(sender_id: str, inbox: dict[str, Any]) -> None:
    """Answer everything a customer has sent, as one turn (see module docstring).

    "Everything" includes pictures. A question with a photo under it is ONE request, and
    answering the words and the picture in two separate turns produced two replies that
    talked over each other — the second one arguing with a message the first had already
    answered."""
    s = get_settings()
    session = state.get_session(sender_id)
    texts = _drain_texts(inbox, sender_id, session)
    if not texts:
        return

    followups.schedule(sender_id, session)

    # A brief "reading" beat before the read receipt, so we don't mark seen the instant
    # the message lands (which feels robotic).
    await asyncio.sleep(_rand_between(800, 2500) / 1000)
    await graph.send_action(sender_id, "mark_seen")
    texts.extend(_drain_texts(inbox, sender_id, session))  # bubbles from the reading beat

    # Pictures sent with the message join this turn rather than opening their own.
    photos = await _collect_photos(sender_id, _drain_images(inbox, sender_id, MAX_BURST_IMAGES))
    if photos.proof is not None:
        return await _receipt_reply(sender_id, session, photos)

    await graph.send_action(sender_id, "typing_on")

    # Deterministic contact-ask backstop: a whole burst counts as ONE turn.
    session["lead"]["msg_count"] += 1
    ask_contact = (
        s.leads_on
        and not session["lead"]["captured"]
        and not session["lead"]["asked"]
        and session["lead"]["msg_count"] >= kb_config.lead_ask_after_turns()
    )
    if ask_contact:
        session["lead"]["asked"] = True
    state.mark_session_dirty(sender_id)

    known_phone = state.customers.get(sender_id, {}).get("phone") or None
    profile = await _profile_with_gender(sender_id)

    lo, hi = _reply_window_ms()
    attempt = 0
    while True:
        combined = "\n".join([*texts, photos.caption] if photos else texts)
        # A valid BD number anywhere in the burst — recomputed per attempt, a late bubble
        # may add it. Prefers the number the customer TYPED over the model's copy.
        offered_phone = find_phone(combined) if s.leads_on and not session["lead"]["captured"] else None

        start = state.now_ms()
        turn = await _run_model_turn(sender_id, session, combined, ask_contact, offered_phone,
                                     known_phone, profile, images=photos.media)

        # Hold the reply for the rest of the humanized pause.
        wait = _rand_between(lo, hi) - (state.now_ms() - start)
        if wait > 0:
            await asyncio.sleep(wait / 1000)

        # The wait-then-check: anything new arrive while we were generating/pausing?
        fresh = _drain_texts(inbox, sender_id, session)
        more = await _collect_photos(
            sender_id, _drain_images(inbox, sender_id, MAX_BURST_IMAGES - len(photos.urls))
        )
        if (fresh or more) and attempt < MAX_REGENERATIONS:
            log.info("↺ [%s] %d new message(s) and %d photo(s) arrived mid-reply — regenerating",
                     sender_id, len(fresh), len(more.urls))
            if more.proof is not None:
                # A receipt landed mid-reply. The draft is discarded exactly as it would be
                # for any late message, and the receipt gets the one answer it may have.
                return await _receipt_reply(sender_id, session, more)
            texts.extend(fresh)
            photos.extend(more)
            attempt += 1
            await graph.send_action(sender_id, "typing_on")  # keep the bubble alive
            continue  # draft discarded: nothing sent, nothing committed, no side effects
        break

    text = _guard_payment_claim(sender_id, turn.extras["text"])
    if _human_took_over(sender_id, "reply"):
        # The draft is dropped exactly as a regenerated one is — nothing sent, nothing
        # committed to history. The ledger still runs: a phone number or a payment claim
        # already in hand is ours whether or not we get to answer.
        await _apply_side_effects(sender_id, session, turn, combined, offered_phone, photos)
        state.mark_session_dirty(sender_id)
        return
    if not text:
        # Nothing to say — the model answered with nothing twice, or it did not answer at
        # all. Either way the customer gets silence and the team gets paged, because
        # neither apology this used to send was worth the bubble it took up. The customer's
        # own words still enter history, so the next turn (or the colleague reading the
        # thread) still has the name, the number, the batch they just gave.
        log.error("🤐 [%s] no reply (%s) — staying silent and paging the team", sender_id,
                  turn.extras.get("llm_error") or f"empty after {EMPTY_REPLY_RETRIES + 1} attempts")
        state.remember_unanswered(session, combined)
        await _apply_side_effects(sender_id, session, turn, combined, offered_phone, photos)
        state.mark_session_dirty(sender_id)
        return
    delivered = await graph.send_message(sender_id, text)
    if delivered and turn.extras["commit"]:  # a failed turn has no text, so this is belt-and-braces
        state.remember_turn(session, combined, text)  # only now does this turn enter history
    log.info("→ [%s] %s%s", sender_id, text, "" if delivered else "  [NOT DELIVERED]")

    # Side effects run even on a failed send: the customer's phone number and payment
    # claim are ours whether or not our reply reached them — losing the lead because
    # Meta rejected one message would be the worse failure by far.
    await _apply_side_effects(sender_id, session, turn, combined, offered_phone, photos)
    state.mark_session_dirty(sender_id)
    if delivered:
        await _force_stop_if_off_topic(sender_id, session, turn)
    else:
        # The closing line never landed, so don't start dropping their messages.
        await _alert_undelivered(sender_id, session, combined)


async def _apply_side_effects(sender_id: str, session: dict[str, Any], turn: TurnContext,
                              combined: str, offered_phone: str | None,
                              photos: _Photos | None = None) -> None:
    s = get_settings()
    lead = turn.lead

    if s.leads_on and lead and not session["lead"]["captured"]:
        await capture_lead(sender_id, session, await _profile_first_lead(sender_id, lead))
    elif s.leads_on and lead and session["lead"]["captured"] and not real_name(session["lead"].get("name")):
        # Lead already out via the phone-only backstop; NOW we learned the name. Update
        # records so later alerts/greetings use it — but don't push a second lead.
        log.info('lead name learned after capture: [%s] now "%s"', sender_id, lead["name"])
        session["lead"]["name"] = lead["name"]
        session["customer_name"] = lead["name"]
        rec = state.customers.get(sender_id)
        if rec is not None:
            rec["name"] = lead["name"]
            rec["interest"] = rec.get("interest") or lead.get("interest", "")
            rec["remarks"] = rec.get("remarks") or lead.get("remarks", "")
            rec["last_seen"] = state.now_ms()
            state.mark_customer_dirty(sender_id)
    elif offered_phone and not session["lead"]["captured"]:
        # Deterministic backstop: a valid number was sent but the model produced no
        # usable save_lead. A phone number in hand must NEVER be dropped.
        log.error("⚠ [%s] phone %s seen but model produced no lead — capturing via backstop", sender_id, offered_phone)
        await capture_lead(sender_id, session, {
            "name": (await _best_name(sender_id, session)) or NAME_UNKNOWN,
            "phone": offered_phone,
            "interest": "",
            "remarks": "স্বয়ংক্রিয়ভাবে সংরক্ষিত — বিস্তারিত জানতে Messenger কথোপকথনটি দেখুন।",
        })

    if turn.notice:
        await escalate.notify_team(sender_id, session, turn.notice["category"],
                                   turn.notice["reason"], customer_message=combined)
    elif turn.extras.get("empty_reply") or turn.extras.get("llm_error"):
        # The customer got NOTHING — no answer and, deliberately, no apology either. This
        # ping is the only thing standing between them and silence, so it is not optional.
        await escalate.notify_team(
            sender_id, session, "other", _no_reply_reason(turn), customer_message=combined,
        )

    # Only an IDENTIFIED payment reaches the team: a transaction id, or a screenshot. A bare
    # "আমি পেমেন্ট করেছি" gives a representative nothing to look up — it is a prompt to ask
    # for the proof, not a claim to chase — and alerting on it buried the real ones. The
    # alert fires the moment that proof arrives, on this turn or a later one.
    screenshot = photos.alert_key if photos else ""
    trx = short_note((turn.payment or {}).get("trxId"), 40) or find_trx_id(combined) or ""

    if turn.payment and (trx or screenshot):
        # A screenshot in hand keys the alert, so the model's report and the deterministic
        # one collapse onto a single entry instead of paging the team twice.
        keyed = {"attachmentUrl": screenshot, "dedupeKey": screenshot} if screenshot else {}
        await report_payment_claim(sender_id, session,
                                   {**turn.payment, "trxId": trx, "customerMessage": combined, **keyed})
    elif s.payments_on and photos is not None and photos.unchecked and not turn.extras["commit"]:
        # A file we could not read AND a model call that failed: nothing looked at this at
        # all, and it might be a receipt. The one case left where guessing beats silence.
        log.error("⚠ [%s] attachment unread and model unavailable — alerting to be safe", sender_id)
        await report_payment_claim(sender_id, session, {
            "attachmentUrl": photos.alert_key,
            "note": "গ্রাহক একটি ফাইল পাঠিয়েছেন — স্বয়ংক্রিয়ভাবে পরীক্ষা করা যায়নি, তাই সতর্কতাবশত পাঠানো হলো। যাচাই করুন।",
            "dedupeKey": photos.alert_key,
            "customerMessage": combined,
        })
    elif s.payments_on and trx:
        # Regex net UNDER the model's judgment: a transaction id sitting in the message
        # with no report — model outage, skipped call, or the verifier overruled a real
        # one. An id is actionable, so it never goes unreported.
        log.error("⚠ [%s] transaction id in the message but no report from the model — using the backstop",
                  sender_id)
        await report_payment_claim(sender_id, session, {
            "trxId": trx,
            "note": "স্বয়ংক্রিয়ভাবে শনাক্ত — গ্রাহকের বার্তা দেখে যাচাই করুন।",
            "customerMessage": combined,
        })
    elif s.payments_on and looks_like_payment_claim(combined):
        # They say they have paid but sent nothing to identify it. Not an alert — the reply
        # asks for the transaction id or screenshot, and that is what the team acts on.
        log.info("[%s] payment claim with no trx id or screenshot — asking for proof, not alerting", sender_id)


async def _prompt_for_text(sender_id: str, session: dict[str, Any]) -> None:
    """Ask (once per cooldown) for the question in writing — for anything we cannot read:
    a voice note, a video, or a file the image path could not download."""
    if _human_took_over(sender_id, "the write-it-in-text nudge"):
        return  # the cooldown is deliberately NOT burned: they never got the nudge
    now = state.now_ms()
    if now - (session.get("non_text_prompted_at") or 0) < NON_TEXT_PROMPT_COOLDOWN_MS:
        log.info("non-text fallback suppressed (already prompted recently)")
        return
    session["non_text_prompted_at"] = now
    state.mark_session_dirty(sender_id)
    await graph.send_message(sender_id, FALLBACK_NON_TEXT)
    log.info("→ [%s] %s", sender_id, FALLBACK_NON_TEXT)


def _entry_ref(event: dict[str, Any]) -> dict[str, Any]:
    """The ad/entry attribution Meta attaches when someone arrives from a Click-to-
    Messenger ad, an m.me link, or the Get Started button."""
    referral = event.get("referral") or (event.get("postback") or {}).get("referral") or {}
    return {
        "ref": str(referral.get("ref") or "")[:120],
        "adId": str(referral.get("ad_id") or "")[:40],
        "source": str(referral.get("source") or "")[:40],
        "postback": str((event.get("postback") or {}).get("payload") or "")[:120],
    }


async def handle_entry_event(event: dict[str, Any]) -> None:
    """A customer arrived from an ad or tapped Get Started but has not typed yet.

    These carry no message text, so they used to fall straight through and the customer
    got silence — a paid-for click that never became a conversation. They also carry the
    only ad attribution we will ever see (ref / ad_id), so it is stamped on the session
    and rides along to the lead row.
    """
    sender_id = (event.get("sender") or {}).get("id")
    if not sender_id:
        return
    session = state.get_session(sender_id)
    entry = _entry_ref(event)
    if any(entry.values()):
        # First touch wins: a customer who returns via a second ad is still the lead the
        # first ad paid for, and overwriting would quietly rewrite attribution.
        session.setdefault("entry", entry)
        state.mark_session_dirty(sender_id)
    log.info("← [%s] (entry: ref=%s ad_id=%s postback=%s)",
             sender_id, entry["ref"] or "-", entry["adId"] or "-", entry["postback"] or "-")
    state.record_recent(sender_id, "(এড থেকে চ্যাট শুরু)" if entry["adId"] else "(চ্যাট শুরু)")

    # Only greet on the OPENING event. Meta also sends postbacks for buttons inside a
    # running conversation; answering those with a greeting would talk over the thread.
    if session.get("greeted") or session.get("history"):
        return
    session["greeted"] = True
    followups.schedule(sender_id, session)
    await graph.send_action(sender_id, "mark_seen")
    await graph.send_action(sender_id, "typing_on")
    await asyncio.sleep(_rand_between(600, 1800) / 1000)
    if _human_took_over(sender_id, "the opening greeting"):
        return
    delivered = await graph.send_message(sender_id, FALLBACK_GREETING)
    log.info("→ [%s] %s%s", sender_id, FALLBACK_GREETING, "" if delivered else "  [NOT DELIVERED]")
    state.mark_session_dirty(sender_id)


# A customer who fires off five photos is making ONE request, not five. Cap what one turn
# carries anyway: past a handful of pictures the model gains nothing and the turn gets slow.
MAX_BURST_IMAGES = 4

_BN_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")


def _image_url(event: dict[str, Any]) -> str | None:
    """The scontent URL when this event is a plain photo, else None (stickers, videos,
    files, text, echoes)."""
    message = event.get("message") or {}
    if message.get("text") or message.get("is_echo") or not message.get("attachments"):
        return None
    attachment = (message.get("attachments") or [{}])[0]
    if message.get("sticker_id") or (attachment.get("payload") or {}).get("sticker_id"):
        return None
    if attachment.get("type") != "image":
        return None
    return (attachment.get("payload") or {}).get("url") or None


def _drain_images(inbox: dict[str, Any] | None, sender_id: str, limit: int) -> list[str]:
    """Pull further PHOTOS waiting at the front of the queue into the turn already running,
    exactly as _drain_texts does for bubbles. Meta delivers a multi-photo send as one event
    per picture, and answering each on its own turned one action into a wall of replies."""
    urls: list[str] = []
    while inbox and inbox["queue"] and len(urls) < limit:
        url = _image_url(inbox["queue"][0])
        if not url:
            break
        inbox["queue"].pop(0)
        state.record_recent(sender_id, "(image)")
        log.info("← [%s] (non-text: image — joins the same turn)", sender_id)
        urls.append(url)
    return urls


async def _load_images(sender_id: str, urls: list[str]) -> tuple[list[tuple[str, tuple[str, bytes]]], bool]:
    """Download every picture in the burst, each kept paired with the URL it came from so
    an alert can point at the picture that caused it. The bool is "something here went
    unread" — a PDF, an oversized file, a download that failed — which the caller treats
    as "we don't know" exactly as it always did."""
    loaded: list[tuple[str, tuple[str, bytes]]] = []
    unchecked = False
    for url in urls:
        try:
            image = await fetch_attachment_image(url)
        except Exception as err:
            log.error("⚠ [%s] attachment download failed: %s", sender_id, err)
            unchecked = True
            continue
        if image is None:
            unchecked = True  # a PDF, or bigger than we will read
        else:
            loaded.append((url, image))
    return loaded, unchecked


def _image_caption(count: int, descriptions: list[str]) -> str:
    """How the burst is framed for the model. The pictures themselves go along with it —
    this only says what arrived."""
    if not count:
        return "[গ্রাহক এমন একটি ফাইল পাঠিয়েছেন যা আমরা খুলে দেখতে পারিনি।]"
    detail = "; ".join(d for d in descriptions if d)
    if count == 1:
        head = "[গ্রাহক একটি ছবি পাঠিয়েছেন।"
        return f"{head} ছবিতে রয়েছে: {detail}]" if detail else f"{head}]"
    n = str(count).translate(_BN_DIGITS)
    head = f"[গ্রাহক একসাথে {n}টি ছবি পাঠিয়েছেন — এগুলো একটি অনুরোধ, একটিই উত্তর দিন।"
    return f"{head} ছবিগুলোতে রয়েছে: {detail}]" if detail else f"{head}]"


@dataclass
class _Photos:
    """The pictures a customer sent in one breath: downloaded once, vetted once, and
    carried through whichever turn answers them — the photo-only turn, or the text turn
    they arrived alongside."""

    urls: list[str] = field(default_factory=list)
    images: list[tuple[str, bytes]] = field(default_factory=list)
    descriptions: list[str] = field(default_factory=list)
    proof: Any = None  # the AttachmentVerdict, when one of them is a payment receipt
    proof_url: str = ""  # the picture that verdict was about
    unchecked: bool = False  # something here went unread — the caller treats it as "don't know"

    def __bool__(self) -> bool:
        return bool(self.urls)

    @property
    def media(self) -> list[Image] | None:
        return [Image(content=data, mime_type=mime) for mime, data in self.images] or None

    @property
    def caption(self) -> str:
        """How the pictures are framed for the model, alongside the pictures themselves.
        A receipt never reaches the model at all — see _receipt_reply."""
        return _image_caption(len(self.images), self.descriptions)

    def extend(self, other: "_Photos") -> None:
        self.urls += other.urls
        self.images += other.images
        self.descriptions += other.descriptions
        if self.proof is None and other.proof is not None:
            self.proof, self.proof_url = other.proof, other.proof_url
        self.unchecked = self.unchecked or other.unchecked

    @property
    def alert_key(self) -> str:
        """What a payment alert about this burst is keyed and linked on."""
        return self.proof_url or (self.urls[0] if self.urls else "")


async def _collect_photos(sender_id: str, urls: list[str]) -> _Photos:
    """Download the burst and ask the classifier the one question it answers: is any of
    this a payment receipt? payments_on gates the CLASSIFIER only — the model gets to look
    at a course poster either way."""
    photos = _Photos(urls=list(urls))
    if not urls:
        return photos
    pairs, photos.unchecked = await _load_images(sender_id, urls)
    photos.images = [image for _, image in pairs]
    if not get_settings().payments_on:
        return photos
    for source, image in pairs:
        try:
            verdict = await classify_image(*image)
        except Exception as err:
            log.error("⚠ [%s] attachment classification failed, alerting to be safe: %s", sender_id, err)
            photos.unchecked = True
            continue
        photos.descriptions.append(verdict.description)
        if verdict.is_payment_proof:
            photos.proof, photos.proof_url = verdict, source
            break
    return photos


async def _receipt_reply(sender_id: str, session: dict[str, Any], photos: _Photos) -> None:
    """The whole reply to a receipt: tell the team, then tell the customer we have passed
    it on. Fixed wording, never the model's — only a person can confirm that money arrived,
    and a warm sentence that oversteps that is the single most expensive thing the bot
    could say. Their other questions are answered by the representative who follows up."""
    await _report_receipt(sender_id, session, photos)
    # The team is told either way — that alert is the point. Only the customer-facing
    # acknowledgement is withheld, because a colleague is already writing one.
    if _human_took_over(sender_id, "the receipt acknowledgement"):
        return
    await graph.send_message(sender_id, FALLBACK_ATTACHMENT)
    log.info("→ [%s] %s", sender_id, FALLBACK_ATTACHMENT)
    state.mark_session_dirty(sender_id)


def _guard_payment_claim(sender_id: str, text: str) -> str:
    """Last line of defence on the way out. The prompt forbids telling a customer their
    payment has been verified, received or approved; this makes it impossible. A reply that
    oversteps is replaced by the acknowledgement it should have been."""
    if not claims_payment_verified(text):
        return text
    log.error('⚠ [%s] model claimed a payment was settled — reply replaced. It wrote: "%s"',
              sender_id, short_note(text, 300))
    return FALLBACK_ATTACHMENT


async def _report_receipt(sender_id: str, session: dict[str, Any], photos: _Photos) -> None:
    """A receipt is news for the team whatever else the customer typed alongside it.
    Reported HERE, deterministically, so it never depends on the model remembering to call
    the tool while it is busy answering the question underneath the screenshot."""
    if photos.proof is None:
        return
    log.info("attachment: payment proof — %s", photos.proof.description)
    await report_payment_claim(sender_id, session, {
        "attachmentUrl": photos.alert_key,
        "note": f"গ্রাহক পেমেন্টের প্রমাণ পাঠিয়েছেন: {photos.proof.description} — যাচাই করুন।",
        "dedupeKey": photos.alert_key,
    })

async def handle_messaging_event(event: dict[str, Any], inbox: dict[str, Any] | None = None) -> None:
    """Non-text events: stickers (silent), images (downloaded once, then answered by the
    model that can actually see them), everything else (one nudge per 10-min cooldown).

    ONE burst = ONE reply, the same invariant the text path has always held. A multi-photo
    send arrives as one event per picture, and bubbles typed straight after a photo arrive
    as their own events; each used to get its own full turn, so a single customer action
    came back as a stack of replies talking over each other.

    payments_on gates only the payment-proof CLASSIFIER and its alert — not whether the
    model gets to look. A course poster or a book page is worth answering either way."""
    s = get_settings()
    sender_id = (event.get("sender") or {}).get("id")
    message = event.get("message")
    if not sender_id:
        return
    if message and message.get("is_echo"):
        return

    # Ad clicks / Get Started / m.me links arrive with no message at all.
    if not message:
        if event.get("postback") or event.get("referral"):
            return await handle_entry_event(event)
        return

    if message.get("text"):
        return  # text never reaches here — the sender loop routes it to handle_text_batch

    if not message.get("attachments"):
        # An empty bubble with nothing in it at all. Answering it with the "please write
        # your question" nudge meant for voice notes just confuses people who fat-fingered
        # send; there is genuinely nothing to respond to.
        log.info("← [%s] (empty message — ignored)", sender_id)
        return

    attachment = (message.get("attachments") or [{}])[0]
    url = (attachment.get("payload") or {}).get("url", "")
    is_sticker = bool(message.get("sticker_id") or (attachment.get("payload") or {}).get("sticker_id"))
    is_image = not is_sticker and attachment.get("type") == "image" and bool(url)

    log.info("← [%s] (non-text: %s)", sender_id, "sticker" if is_sticker else attachment.get("type") or "unknown")
    state.record_recent(sender_id, "(স্টিকার)" if is_sticker else f"({attachment.get('type') or 'attachment'})")

    # Stickers/likes get NO reply at all — answering each one turned a row of 👍 into a
    # wall of identical bot bubbles.
    if is_sticker:
        return

    if not is_image:
        # Voice notes, videos, shares, GIFs: ask (once) for the question in writing.
        return await _prompt_for_text(sender_id, state.get_session(sender_id))

    session = state.get_session(sender_id)
    followups.schedule(sender_id, session)
    await graph.send_action(sender_id, "mark_seen")

    # Everything the customer sent in this breath, answered together.
    # One download per picture, two consumers: the payment classifier and — when it says
    # none of these is a receipt — the reply itself, which answers the actual pictures.
    # Anything we could not check (a PDF, a failed download, an unreachable classifier)
    # counts as "don't know", and don't-know ALERTS further down: an unnoticed payment is
    # far worse than a redundant alert.
    photos = await _collect_photos(sender_id, [url] + _drain_images(inbox, sender_id, MAX_BURST_IMAGES - 1))

    # A receipt is the one thing worth interrupting the team for, so it short-circuits here.
    # One alert and one acknowledgement for the whole burst, however many shots of the same
    # transaction the customer sent.
    if photos.proof is not None:
        return await _receipt_reply(sender_id, session, photos)

    # Everything else — including a file we could not open — goes through the normal
    # conversation path. A photo is not by itself news for the team: the MODEL decides
    # whether this needs their attention, exactly as it does for text, because it has the
    # history and the report_payment tool. Guessing from "we couldn't read it" alone was
    # paging the team for every oversized selfie and every PDF.
    log.info("attachment: not payment proof — %s",
             "; ".join(d for d in photos.descriptions if d) or "(not classified)")
    # The pictures themselves go to the model, so it can answer a course poster, a book
    # page or a screenshot on its own terms. The caption only frames them — it is no longer
    # the only thing the model gets to see.
    as_text = photos.caption
    await graph.send_action(sender_id, "typing_on")
    profile = await _profile_with_gender(sender_id)

    # Wait-then-check, exactly as handle_text_batch does it: a bubble the customer types
    # while we are looking at their photo joins THIS turn instead of earning a second
    # reply. A discarded draft leaves no trace — no history, no side effects.
    lo, hi = _reply_window_ms()
    attempt = 0
    while True:
        start = state.now_ms()
        turn = await _run_model_turn(
            sender_id, session, as_text, False, None,
            state.customers.get(sender_id, {}).get("phone") or None, profile,
            images=photos.media,
        )
        wait = _rand_between(lo, hi) - (state.now_ms() - start)
        if wait > 0:
            await asyncio.sleep(wait / 1000)
        fresh = _drain_texts(inbox, sender_id, session) if inbox else []
        if fresh and attempt < MAX_REGENERATIONS:
            log.info("↺ [%s] %d message(s) arrived while we read the photo — regenerating",
                     sender_id, len(fresh))
            as_text = "\n".join([as_text, *fresh])
            attempt += 1
            await graph.send_action(sender_id, "typing_on")  # keep the bubble alive
            continue  # draft discarded: nothing sent, nothing committed, no side effects
        break

    text = _guard_payment_claim(sender_id, turn.extras["text"])
    # Withdrawn, not failed: a stood-down draft must not page the team as an undelivered
    # reply, and must not enter history as one the customer has seen.
    stood_down = _human_took_over(sender_id, "reply")
    # Nothing usable — no text after the retry, or no model call at all. The photo path
    # stays silent for the same reason the text path does, and pages the team below.
    withheld = not text and not stood_down
    if withheld:
        log.error("🤐 [%s] no reply to the photo (%s) — staying silent and paging the team", sender_id,
                  turn.extras.get("llm_error") or f"empty after {EMPTY_REPLY_RETRIES + 1} attempts")
    delivered = False if (stood_down or withheld) else await graph.send_message(sender_id, text)
    if turn.extras["commit"] and delivered:
        state.remember_turn(session, as_text, text)
    elif withheld:
        # The customer's half of the turn is the only part worth keeping, and losing it is
        # how the context of a photo conversation disappears.
        state.remember_unanswered(session, as_text)
    if not stood_down and not withheld:
        log.info("→ [%s] %s%s", sender_id, text, "" if delivered else "  [NOT DELIVERED]")
    if turn.notice:
        await escalate.notify_team(sender_id, session, turn.notice["category"],
                                   turn.notice["reason"], customer_message=as_text)
    elif turn.extras.get("empty_reply") or turn.extras.get("llm_error"):
        await escalate.notify_team(sender_id, session, "other", _no_reply_reason(turn),
                                   customer_message=as_text)
    if not delivered and not stood_down and not withheld:
        await _alert_undelivered(sender_id, session, as_text)

    # The picture wasn't proof, but the model may still act on the conversation around it
    # (e.g. they told us they'd paid a moment ago) — honour those like the text path does.
    if get_settings().leads_on and turn.lead and not session["lead"]["captured"]:
        await capture_lead(sender_id, session, await _profile_first_lead(sender_id, turn.lead))
    if turn.payment:
        await report_payment_claim(sender_id, session,
                                   {**turn.payment, "attachmentUrl": photos.alert_key,
                                    "dedupeKey": photos.alert_key})
    elif s.payments_on and photos.unchecked and not turn.extras["commit"]:
        # We could not read the file AND the model call failed: nothing looked at this at
        # all, and it might be a receipt. This is the only case left worth guessing on.
        log.error("⚠ [%s] attachment unread and model unavailable — alerting to be safe", sender_id)
        await report_payment_claim(sender_id, session, {
            "attachmentUrl": photos.alert_key,
            "note": "গ্রাহক একটি ফাইল পাঠিয়েছেন — স্বয়ংক্রিয়ভাবে পরীক্ষা করা যায়নি, তাই সতর্কতাবশত পাঠানো হলো। যাচাই করুন।",
            "dedupeKey": photos.alert_key,
        })
    state.mark_session_dirty(sender_id)
    if delivered:
        await _force_stop_if_off_topic(sender_id, session, turn)
