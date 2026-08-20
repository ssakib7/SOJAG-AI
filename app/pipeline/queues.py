"""Per-sender inbox queues + webhook dedupe (port of server.js enqueueForSender et al).

Different customers process fully in parallel, but each customer's own messages run
strictly in order — and consecutive TEXT messages are answered as ONE batch rather than
one reply per bubble. Customers routinely split a thought across quick messages
("Orko" / "01745..." / "Elephant Road"); answering each separately produced replies
answering stale snapshots. Non-text events are handled one at a time, in arrival order.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Any

from app.ops import blocklist

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
    if not sender_id:
        _spawn(_handle_direct(event))
        return
    inbox = _inboxes.get(sender_id)
    if inbox is None:
        inbox = {"queue": [], "running": False}
        _inboxes[sender_id] = inbox
    inbox["queue"].append(event)
    if not inbox["running"]:
        _spawn(_run_sender_loop(sender_id, inbox))


def _spawn(coro) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _handle_direct(event: dict[str, Any]) -> None:
    from app.pipeline import turn

    try:
        await turn.handle_messaging_event(event)
    except Exception as err:
        log.exception("Error handling event: %s", err)


async def _run_sender_loop(sender_id: str, inbox: dict[str, Any]) -> None:
    from app.pipeline import turn

    inbox["running"] = True
    try:
        while inbox["queue"]:
            # Blocked mid-conversation (admin clicked Block while messages were queued):
            # stop answering immediately and throw away whatever is still waiting.
            if blocklist.is_blocked(sender_id):
                log.info("⛔ [%s] blocked mid-queue — dropping %d pending event(s)", sender_id, len(inbox["queue"]))
                inbox["queue"].clear()
                break
            if is_text_event(inbox["queue"][0]):
                try:
                    await turn.handle_text_batch(sender_id, inbox)
                except Exception as err:
                    log.exception("Error handling batch: %s", err)
            else:
                event = inbox["queue"].pop(0)
                try:
                    await turn.handle_messaging_event(event)
                except Exception as err:
                    log.exception("Error handling event: %s", err)
    finally:
        inbox["running"] = False
        if not inbox["queue"]:
            _inboxes.pop(sender_id, None)
