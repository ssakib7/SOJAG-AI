"""Meta Graph API client: Send API, sender actions, profile lookup, inbox deep links.

Hard-won rules preserved from the Node bot:
- SEND_TARGET is FB_PAGE_ID when set ("me" doesn't resolve for System User tokens).
- Two tokens: PAGE_ACCESS_TOKEN for sending; MESSENGER_PAGE_TOKEN (type PAGE) for the
  Conversations API, which refuses system-user tokens with #190.
- Persona send retries once WITHOUT persona_id when Meta rejects a stale persona — a
  misconfiguration must never silence the bot.
- Replies over 2000 chars are split on natural boundaries (Send API rejects them, #100).
- Profile lookups: successes cached for process lifetime, failures retried after 10 min
  (a brand-new thread isn't queryable yet); one global "lookup unavailable" warning.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import get_settings
from app.db import state
from app.utils.text import split_for_messenger

log = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=10.0)
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _record_shadow(recipient_id: str, kind: str, text: str = "") -> None:
    from app.db.engine import db_session
    from app.db.models import ShadowDraftRow

    try:
        async with db_session() as db:
            db.add(ShadowDraftRow(sender_id=recipient_id, kind=kind, text=text))
            await db.commit()
    except Exception as err:
        log.error("shadow draft not recorded: %s", err)


async def send_action(recipient_id: str, action: str) -> None:
    """Send mark_seen / typing_on / typing_off. Failures are logged, never raised."""
    s = get_settings()
    if s.shadow_mode:
        return  # shadow: no outward effect at all for actions
    try:
        res = await client().post(
            f"{s.graph_api_base}/{s.send_target}/messages",
            params={"access_token": s.page_access_token},
            json={"recipient": {"id": recipient_id}, "sender_action": action},
        )
        if res.status_code >= 400:
            log.error("Sender action error: %s %s %s", action, res.status_code, res.text[:300])
    except Exception as err:
        log.error("Sender action request failed: %s", err)


async def send_message(recipient_id: str, text: str) -> bool:
    """Send a reply, split into Messenger-sized chunks. Returns True only if EVERY
    chunk was accepted by Meta.

    The return value is load-bearing: the caller commits the turn to history only for
    a delivered reply. A swallowed failure used to mean the customer got silence while
    the bot's own history claimed it had answered, so every later turn argued with a
    message that was never seen.
    """
    if get_settings().shadow_mode:
        log.info("[shadow] → [%s] %s", recipient_id, text[:200])
        await _record_shadow(recipient_id, "message", text)
        return True
    chunks = split_for_messenger(text)
    if len(chunks) > 1:
        log.info("Reply over 2000 chars — sending as %d messages.", len(chunks))
    for index, chunk in enumerate(chunks):
        if not await _send_one(recipient_id, chunk):
            # Stop at the first failed chunk: half an answer followed by its tail
            # reads worse than a retry of the whole reply.
            if index:
                log.error("[%s] chunk %d of %d failed — abandoning the rest of the reply",
                          recipient_id, index + 1, len(chunks))
            return False
    return True


# Send retries. Meta rate-limits (#613) and 5xx-es under exactly the bursts an ad
# campaign produces; one immediate retry is not enough and an unbounded one would pile
# turns up behind a hard outage.
SEND_ATTEMPTS = 3
SEND_RETRY_BASE_S = 1.0


def _is_transient_send(status: int | None, body: str) -> bool:
    """4xx that we should NOT retry: bad recipient, outside the 24h window, policy
    violations — retrying those just burns quota. 429/5xx and transport errors are
    worth another attempt."""
    if status is None:
        return True  # transport-level failure (timeout, connection reset)
    if status == 429 or status >= 500:
        return True
    return "#613" in body or "rate limit" in body.lower() or "please retry" in body.lower()


async def _post_send(url: str, params: dict[str, Any], payload: dict[str, Any]) -> tuple[int | None, str]:
    try:
        res = await client().post(url, params=params, json=payload)
        return res.status_code, res.text
    except Exception as err:
        return None, f"{type(err).__name__}: {err}"


async def _send_one(recipient_id: str, text: str) -> bool:
    """One chunk, with retries. True = Meta accepted it."""
    s = get_settings()
    payload: dict[str, Any] = {"recipient": {"id": recipient_id}, "message": {"text": text}}
    persona_id = s.persona_id.strip()
    if persona_id:
        payload["persona_id"] = persona_id
    url = f"{s.graph_api_base}/{s.send_target}/messages"
    params = {"access_token": s.page_access_token}

    for attempt in range(1, SEND_ATTEMPTS + 1):
        status, body = await _post_send(url, params, payload)
        if status is not None and status < 400:
            record_send_result(True)
            return True

        log.error("Send API error (attempt %d/%d): %s %s", attempt, SEND_ATTEMPTS, status, body[:300])
        # A bad/deleted persona id would silence the bot entirely — drop it and retry
        # immediately so the customer still gets their reply.
        if persona_id and "persona" in body.lower() and "persona_id" in payload:
            log.error("Retrying without persona_id — check PERSONA_ID in .env")
            payload.pop("persona_id", None)
            continue
        if attempt == SEND_ATTEMPTS or not _is_transient_send(status, body):
            break
        await asyncio.sleep(SEND_RETRY_BASE_S * 2 ** (attempt - 1))

    record_send_result(False)
    log.error("⚠ [%s] REPLY NOT DELIVERED after %d attempt(s) — customer got nothing: %s",
              recipient_id, SEND_ATTEMPTS, text[:120])
    return False


# --- Send health, surfaced on /health so an outage is visible without reading logs ---
send_stats: dict[str, Any] = {"failed_since_boot": 0, "consecutive_failures": 0, "last_failure": None}


def record_send_result(ok: bool) -> None:
    if ok:
        send_stats["consecutive_failures"] = 0
        return
    send_stats["failed_since_boot"] += 1
    send_stats["consecutive_failures"] += 1
    send_stats["last_failure"] = datetime.now(timezone.utc).isoformat()


# --- Profile lookup ---------------------------------------------------------
# senderId -> {name, gender, link, at, gender_guess?}. Successes cached for the process
# lifetime; failures only PROFILE_RETRY_MS.
_profiles: dict[str, dict[str, Any]] = {}
PROFILE_RETRY_MS = 10 * 60 * 1000
# Bounded: one entry per customer for the process lifetime is a slow leak on a bot that
# runs for months across several ad campaigns. Oldest lookups are evicted first; an
# evicted customer simply costs one more Graph call the next time they write.
PROFILES_MAX = 5000
_lookup_warned = False


def _warn_lookup_once(what: str, detail: str) -> None:
    global _lookup_warned
    if _lookup_warned:
        return
    _lookup_warned = True
    log.warning(
        "Customer name lookup unavailable (%s) — alerts will show the name only when the "
        "customer typed it. Set MESSENGER_PAGE_TOKEN to a Page access token to fix. Detail: %s",
        what, detail,
    )


async def fetch_profile(sender_id: str) -> dict[str, Any]:
    s = get_settings()
    cached = _profiles.get(sender_id)
    if cached and (cached.get("name") or time.time() * 1000 - cached["at"] < PROFILE_RETRY_MS):
        if cached.get("name"):
            state.record_recent_name(sender_id, cached["name"])
        return cached

    name: str | None = None
    gender: str | None = None
    link: str | None = None
    try:
        # 1. Conversations API — the source that WORKS for this Page: participant name plus
        #    the thread `link` that resolve_inbox_url() turns into a clickable URL.
        res = await client().get(
            f"{s.graph_api_base}/{s.fb_page_id}/conversations",
            params={"user_id": sender_id, "fields": "participants,link", "access_token": s.lookup_token},
            timeout=5.0,
        )
        data = res.json()
        if res.status_code < 400:
            convo = (data.get("data") or [{}])[0] if data.get("data") else {}
            people = ((convo.get("participants") or {}).get("data")) or []
            person = next((p for p in people if p.get("id") != s.fb_page_id), None)
            name = ((person or {}).get("name") or "").strip() or None
            link = (convo.get("link") or "").strip() or None
        else:
            _warn_lookup_once("conversations API refused", str(data.get("error", data))[:160])

        # 2. User Profile API — the only source that carries gender, so always attempted.
        #    Dead for this app today (#3 / object does not exist), but harmless: one cached
        #    call per customer, and it starts working the day Meta re-enables it.
        r2 = await client().get(
            f"{s.graph_api_base}/{sender_id}",
            params={"fields": "first_name,last_name,gender", "access_token": s.lookup_token},
            timeout=5.0,
        )
        d2 = r2.json()
        if r2.status_code < 400:
            name = name or " ".join(x for x in [d2.get("first_name"), d2.get("last_name")] if x).strip() or None
            gender = (d2.get("gender") or "").strip().lower() or None
    except Exception as err:
        log.warning("Customer name lookup failed: %s", err)

    entry = {"name": name, "gender": gender, "link": link, "at": int(time.time() * 1000)}
    # Preserve an earlier gender classification across a cache refresh.
    if cached and "gender_guess" in cached:
        entry["gender_guess"] = cached["gender_guess"]
    _profiles[sender_id] = entry
    if len(_profiles) > PROFILES_MAX:
        oldest = min(_profiles, key=lambda k: _profiles[k].get("at", 0))
        _profiles.pop(oldest, None)
    if name:
        state.record_recent_name(sender_id, name)
    return entry


async def check_lookup_token() -> bool:
    """Boot probe: can we actually call the Conversations API with the token we have?

    Every Telegram/Sheet alert's customer name and chat link comes from this endpoint. A
    System User token is refused here with #190, and the failure was previously invisible
    until someone wondered why every alert said the name was unknown.
    """
    s = get_settings()
    if not s.fb_page_id:
        return False
    try:
        res = await client().get(
            f"{s.graph_api_base}/{s.fb_page_id}/conversations",
            params={"fields": "id", "limit": 1, "access_token": s.lookup_token},
            timeout=8.0,
        )
    except Exception as err:
        log.warning("Lookup token probe failed: %s", err)
        return False
    if res.status_code < 400:
        return True
    log.warning("Lookup token probe rejected: %s %s", res.status_code, res.text[:200])
    return False


async def fetch_profile_name(sender_id: str) -> str | None:
    return (await fetch_profile(sender_id)).get("name")


def _inbox_url_fallback(sender_id: str) -> str | None:
    """PSID-based Business Suite link. Business Suite does NOT resolve the new 17-digit
    PSIDs, so this is only the last-resort fallback; prefer the Conversations API link."""
    s = get_settings()
    if not s.fb_page_id:
        return None
    params = urlencode({
        "asset_id": s.fb_page_id,
        "mailbox_id": s.fb_page_id,
        "selected_item_id": sender_id,
        "thread_type": "FB_MESSAGE",
    })
    return f"https://business.facebook.com/latest/inbox/all/?{params}"


async def resolve_inbox_url(sender_id: str) -> str | None:
    profile = await fetch_profile(sender_id)
    if profile.get("link"):
        return f"https://www.facebook.com{profile['link']}"
    return _inbox_url_fallback(sender_id)
