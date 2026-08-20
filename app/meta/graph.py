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

import logging
import time
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


async def send_action(recipient_id: str, action: str) -> None:
    """Send mark_seen / typing_on / typing_off. Failures are logged, never raised."""
    s = get_settings()
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


async def send_message(recipient_id: str, text: str) -> None:
    chunks = split_for_messenger(text)
    if len(chunks) > 1:
        log.info("Reply over 2000 chars — sending as %d messages.", len(chunks))
    for chunk in chunks:
        await _send_one(recipient_id, chunk)


async def _send_one(recipient_id: str, text: str) -> None:
    s = get_settings()
    payload: dict[str, Any] = {"recipient": {"id": recipient_id}, "message": {"text": text}}
    persona_id = s.persona_id.strip()
    if persona_id:
        payload["persona_id"] = persona_id
    try:
        url = f"{s.graph_api_base}/{s.send_target}/messages"
        params = {"access_token": s.page_access_token}
        res = await client().post(url, params=params, json=payload)
        if res.status_code >= 400:
            body = res.text
            log.error("Send API error: %s %s", res.status_code, body[:300])
            # A bad/deleted persona id would silence the bot entirely — retry once
            # without it so the customer still gets their reply.
            if persona_id and "persona" in body.lower():
                log.error("Retrying without persona_id — check PERSONA_ID in .env")
                payload.pop("persona_id", None)
                retry = await client().post(url, params=params, json=payload)
                if retry.status_code >= 400:
                    log.error("Send API error (retry): %s %s", retry.status_code, retry.text[:300])
    except Exception as err:
        log.error("Send API request failed: %s", err)


# --- Profile lookup ---------------------------------------------------------
# senderId -> {name, gender, link, at, gender_guess?}. Successes cached for the process
# lifetime; failures only PROFILE_RETRY_MS.
_profiles: dict[str, dict[str, Any]] = {}
PROFILE_RETRY_MS = 10 * 60 * 1000
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
    if name:
        state.record_recent_name(sender_id, name)
    return entry


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
