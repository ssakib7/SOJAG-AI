"""Per-sender inbox queues + webhook dedupe (port of server.js enqueueForSender et al).

Different customers process fully in parallel, but each customer's own messages run
strictly in order — and consecutive TEXT messages are answered as ONE batch rather than
one reply per bubble. Customers routinely split a thought across quick messages
("Orko" / "01745..." / "Elephant Road"); answering each separately produced replies
answering stale snapshots. Photos batch the same way: a multi-photo send arrives as one
event per picture, and answering each on its own turned one action into a wall of replies.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import OrderedDict
from typing import Any

from app.db import state
from app.ops import blocklist, publish

log = logging.getLogger(__name__)

# senderId -> {"queue": [event], "running": bool}
_inboxes: dict[str, dict[str, Any]] = {}
_tasks: set[asyncio.Task] = set()

# Webhook redelivery guard. Meta may deliver the same event more than once — without
# this, one 👍 or one message becomes N identical replies. Keyed by message mid; edits
# reuse the original mid, so they key on mid + edit content instead (a redelivered edit
# dedupes, a genuinely new edit passes).
_seen_event_keys: OrderedDict[str, bool] = OrderedDict()
SEEN_EVENT_KEYS_MAX = 1000


def is_duplicate_event(event: dict[str, Any]) -> bool:
    edit = event.get("message_edit") or {}
    message = event.get("message") or {}
    if edit.get("mid"):
        key = f"{edit['mid']}#edit:{edit.get('num_edit', '')}:{edit.get('text', '')}"
    else:
        key = message.get("mid")
    if not key:
        return False  # delivery/read/etc. carry no mid — let them pass
    if key in _seen_event_keys:
        return True
    _seen_event_keys[key] = True
    while len(_seen_event_keys) > SEEN_EVENT_KEYS_MAX:
        _seen_event_keys.popitem(last=False)
    return False


def is_text_event(event: dict[str, Any]) -> bool:
    """An edited message counts as a text event too: a customer fixing a typo'd phone
    number arrives ONLY through message_edit, and dropping it once cost a lead."""
    message = event.get("message") or {}
    return bool(message.get("text") and not message.get("is_echo")) or bool(
        (event.get("message_edit") or {}).get("text")
    )


def is_human_takeover(event: dict[str, Any]) -> bool:
    """True when a COLLEAGUE just replied to this customer from the Page inbox.

    Meta echoes every outbound message back to us. Our own sends carry the app_id of the
    app that sent them; a message a human typed in the Page inbox / Business Suite has no
    app_id. That difference is the only signal we get that a person has taken the
    conversation over — without it the bot cheerfully talks over its own colleagues.
    """
    message = event.get("message") or {}
    return bool(message.get("is_echo")) and not message.get("app_id")


def enqueue_event(event: dict[str, Any]) -> None:
    sender_id = (event.get("sender") or {}).get("id")
    # Blocked senders are dropped here, at the very edge — no session, no profile
    # lookup, no typing indicator, no model call.
    if sender_id and blocklist.is_blocked(sender_id):
        log.info("⛔ [%s] sender is blocked — event dropped", sender_id)
        return
    if is_duplicate_event(event):
        mid = (event.get("message") or {}).get("mid") or (event.get("message_edit") or {}).get("mid")
        log.info("duplicate webhook delivery ignored (%s)", mid)
        return

    # Echoes: on an echo the "sender" is the PAGE and the customer is the recipient.
    if (event.get("message") or {}).get("is_echo"):
        if is_human_takeover(event):
            customer_id = (event.get("recipient") or {}).get("id")
            if customer_id:
                state.pause_for_human(customer_id)
        return

    # The switch is off in the admin panel: the bot answers nobody. Dropped here, at the
    # same edge as a blocked sender, so an unpublished bot costs one log line per event.
    # The roster is still updated — staff need to see who wrote in while it was off.
    if not publish.is_published():
        if sender_id:
            state.record_recent(sender_id, _preview_text(event))
        log.info("⏸ [%s] bot is unpublished — event dropped", sender_id or "unknown sender")
        return

    if not sender_id:
        _spawn(_handle_direct(event))
        return

    # A colleague is handling this customer by hand — stay out of the way, but keep the
    # roster current so the admin panel still shows the conversation moving.
    if state.is_human_handling(sender_id):
        log.info("🧑 [%s] a human is handling this conversation — bot staying silent", sender_id)
        state.record_recent(sender_id, _preview_text(event))
        return

    inbox = _inboxes.get(sender_id)
    if inbox is None:
        inbox = {"queue": [], "running": False}
        _inboxes[sender_id] = inbox
    inbox["queue"].append(event)
    # Claim the loop SYNCHRONOUSLY, before awaiting anything. Meta delivers a burst of
    # bubbles in one webhook POST, and enqueueing them back to back used to let the
    # second call see running=False (the first loop task had not been scheduled yet) and
    # spawn a second loop for the same customer — two concurrent turns, out-of-order
    # replies, and a regeneration loop that could not see the other's messages.
    if not inbox["running"]:
        inbox["running"] = True
        _spawn(_run_sender_loop(sender_id, inbox))


def _preview_text(event: dict[str, Any]) -> str:
    message = event.get("message") or {}
    edit = event.get("message_edit") or {}
    return message.get("text") or edit.get("text") or "(attachment)"


def _spawn(coro) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _rescue_failed_batch(sender_id: str, err: Exception) -> None:
    """Last resort when a turn blew up after the customer's messages were consumed."""
    from app.kb.defaults import FALLBACK_ERROR
    from app.meta import graph
    from app.ops import escalate

    with contextlib.suppress(Exception):
        await graph.send_message(sender_id, FALLBACK_ERROR)
    with contextlib.suppress(Exception):
        await escalate.notify_team(
            sender_id, state.sessions.get(sender_id), "other",
            f"বট এই বার্তাটি প্রক্রিয়া করতে ব্যর্থ হয়েছে ({type(err).__name__}) — গ্রাহককে সরাসরি উত্তর দিন।",
        )


async def _handle_direct(event: dict[str, Any]) -> None:
    from app.pipeline import turn

    try:
        await turn.handle_messaging_event(event)
    except Exception as err:
        log.exception("Error handling event: %s", err)


async def _run_sender_loop(sender_id: str, inbox: dict[str, Any]) -> None:
    from app.pipeline import turn

    inbox["running"] = True  # already claimed by enqueue_event; kept for direct callers
    try:
        while inbox["queue"]:
            # Blocked mid-conversation (admin clicked Block while messages were queued):
            # stop answering immediately and throw away whatever is still waiting.
            if blocklist.is_blocked(sender_id):
                log.info("⛔ [%s] blocked mid-queue — dropping %d pending event(s)", sender_id, len(inbox["queue"]))
                inbox["queue"].clear()
                break
            # Unpublished mid-queue (admin switched the bot off while messages waited):
            # go silent immediately, exactly like a mid-queue block.
            if not publish.is_published():
                log.info("⏸ [%s] unpublished mid-queue — dropping %d pending event(s)",
                         sender_id, len(inbox["queue"]))
                inbox["queue"].clear()
                break
            # Force-stopped by the off-topic rule: the closing reply has already gone out,
            # so everything after it is dropped in silence — no reply, no fallback, no
            # typing bubble. Clears itself when the session idles out (state.is_closed).
            if state.is_closed(sender_id):
                log.info("🚫 [%s] conversation closed — dropping %d pending event(s)", sender_id, len(inbox["queue"]))
                inbox["queue"].clear()
                break
            # A colleague picked the chat up while messages were queued.
            if state.is_human_handling(sender_id):
                log.info("🧑 [%s] human took over mid-queue — dropping %d pending event(s)",
                         sender_id, len(inbox["queue"]))
                inbox["queue"].clear()
                break
            if is_text_event(inbox["queue"][0]):
                try:
                    await turn.handle_text_batch(sender_id, inbox)
                except Exception as err:
                    # The customer's messages have already been drained: without a reply
                    # here they get silence and nobody knows. Say something, and page the
                    # team so a person can finish the conversation.
                    log.exception("Error handling batch: %s", err)
                    await _rescue_failed_batch(sender_id, err)
            else:
                event = inbox["queue"].pop(0)
                try:
                    # The inbox rides along so a burst of photos — and any bubble typed
                    # while we are reading them — folds into ONE turn and ONE reply,
                    # the same way consecutive text bubbles always have.
                    await turn.handle_messaging_event(event, inbox)
                except Exception as err:
                    log.exception("Error handling event: %s", err)
    finally:
        inbox["running"] = False
        if not inbox["queue"]:
            _inboxes.pop(sender_id, None)
