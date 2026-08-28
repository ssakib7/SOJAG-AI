"""Admin panel routes — port of admin.js's router.

Role rules (enforced server-side; hiding UI is not security):
- admin: everything.
- editor: courses + books + non-admin-only custom sections + blocklist + the publish
  switch. On save, the editor's submission replaces ONLY those KB fields (see
  forms.merge_editor_kb).

Every successful save triggers kb_config.reload() — hot reload, no restart.
"""

from __future__ import annotations

import contextlib
import csv
import hmac
import io
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy import select

from app.admin import auth
from app.config import get_settings
from app.admin.forms import form_to_kb, merge_editor_kb, parse_bracket_form, prompt_sections_from_form
from app.admin.templates import env
from app.db import state
from app.db.engine import db_session
from app.db.models import LeadRow
from app.kb import config as kb_config
from app.kb import defaults
from app.ops import blocklist, publish

log = logging.getLogger(__name__)
router = APIRouter()

LEADS_PAGE_LIMIT = 500


def _render(request: Request, template: str, **ctx: Any) -> HTMLResponse:
    """Every page also gets the current path and the publish state: the sidebar's publish
    switch is rendered on all of them, and has to send you back where you clicked it."""
    ctx.setdefault("path", request.url.path)
    ctx.setdefault("published", publish.is_published())
    ctx.setdefault("bot_flash", request.query_params.get("bot"))
    return HTMLResponse(env.get_template(template).render(**ctx))


def _auth(request: Request) -> tuple[dict | None, str, str]:
    """(session, role, cookie value). Sessions signed before roles existed have no "r" —
    treat them as admin (only the admin account could hold one)."""
    session, value = auth.current_session(request)
    role = (session or {}).get("r", "admin")
    return session, role, value or ""


def _forbidden(request: Request, username: str, role: str, csrf: str) -> HTMLResponse:
    response = _render(request, "forbidden", title="Not allowed", user=username, role=role, csrf=csrf, active="")
    response.status_code = 403
    return response


async def _form(request: Request) -> tuple[dict[str, Any], bool]:
    """Parsed bracket-notation body + whether the CSRF token matched."""
    raw = await request.form()
    _, _, value = _auth(request)
    ok = hmac.compare_digest(str(raw.get("_csrf", "")), auth.csrf_token(value))
    return parse_bracket_form(raw.multi_items()), ok


def _fmt_when(value: object) -> str:
    """Match the old panel's '02 Aug 2026, 14:05' formatting."""
    dt: datetime | None = None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    elif isinstance(value, (int, float)) and value > 0:
        dt = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            dt = None
    return dt.strftime("%d %b %Y, %H:%M") if dt else "—"


def _nl(value: object) -> str:
    """Textarea text with browser CRLF line endings folded back to plain \n, so a
    value can be compared byte-for-byte against a built-in default."""
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n")


def _snippet(s: object, max_len: int = 70) -> str:
    text = " ".join(str(s or "").split())
    return text[: max_len - 1] + "…" if len(text) > max_len else text


# --- Auth --------------------------------------------------------------------
@router.get("/login")
async def login_page(request: Request) -> Response:
    session, _, _ = _auth(request)
    if session:
        return RedirectResponse("/", status_code=302)
    return _render(request, "login", error=None)


@router.post("/login")
async def login_submit(request: Request) -> Response:
    client_ip = (request.client.host if request.client else "") or "unknown"
    retry_in = auth.lockout_remaining_s(client_ip)
    if retry_in:
        # The panel sits on a public domain, so an unlimited login form is an open
        # invitation to grind passwords around the clock.
        log.warning("Admin login blocked (too many failures) from %s", client_ip)
        return _render(
            request, "login",
            error=f"অনেকবার ভুল হয়েছে। {max(1, round(retry_in / 60))} মিনিট পর আবার চেষ্টা করুন।",
        )

    form = await request.form()
    account = auth.check_login(str(form.get("username", "")), str(form.get("password", "")))
    if not account:
        failures = auth.record_login_failure(client_ip)
        log.warning("Admin login FAILED from %s (attempt %d)", client_ip, failures)
        if failures == auth.MAX_LOGIN_FAILURES:
            await _alert_login_lockout(client_ip, failures)
        return _render(request, "login", error="ইউজারনেম বা পাসওয়ার্ড সঠিক নয়।")

    auth.clear_login_failures(client_ip)
    log.info("Admin login OK: %s (%s) from %s", account["username"], account["role"], client_ip)
    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        auth.COOKIE_NAME, auth.make_session(account["username"], account["role"]),
        httponly=True, samesite="strict", max_age=auth.SESSION_TTL_S, path="/",
        # Set over HTTPS only. The panel is served behind nginx TLS in production; on a
        # plain-HTTP localhost dev run the flag would stop the cookie being stored at all.
        secure=get_settings().public_origin.startswith("https://"),
    )
    return response


async def _alert_login_lockout(client_ip: str, failures: int) -> None:
    from app.leads import sinks

    with contextlib.suppress(Exception):
        await sinks.send_telegram_text(
            f"🔐 De Jure admin panel: {failures} failed login attempts from {client_ip}. "
            f"Locked out for {auth.LOCKOUT_S // 60} minutes.\n"
            "If this wasn't you, change ADMIN_PASSWORD and rotate SESSION_SECRET."
        )


@router.post("/logout")
async def logout(request: Request) -> Response:
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return response


# --- Knowledge base ----------------------------------------------------------
@router.get("/")
async def kb_editor(request: Request) -> Response:
    session, role, value = _auth(request)
    if not session:
        return RedirectResponse("/login", status_code=302)
    kb = kb_config.current_kb()
    sections = kb.get("customSections") or []
    if role != "admin":
        # Editors never even receive admin-only sections in the page payload.
        sections = [s for s in sections if not s.get("adminOnly")]
    return _render(
        request,
        "kb",
        title="Knowledge Base" if role == "admin" else "Courses & products",
        active="kb", user=session["u"], role=role, csrf=auth.csrf_token(value),
        saved=request.query_params.get("saved") == "1",
        kb=kb, contact=kb.get("contact") or {},
        categories=kb.get("courseCategories") or [], custom_sections=sections,
    )


@router.post("/save")
async def kb_save(request: Request) -> Response:
    session, role, value = _auth(request)
    if not session:
        return RedirectResponse("/login", status_code=302)
    body, csrf_ok = await _form(request)
    if not csrf_ok:
        return PlainTextResponse("Invalid CSRF token.", status_code=403)

    current = kb_config.current_kb()
    merged = form_to_kb(body) if role == "admin" else merge_editor_kb(current, body)

    # A save that empties the catalog is almost always a truncated post or a mis-click,
    # not an intention: the bot would immediately start telling customers it has no
    # courses. Refuse it rather than serve it; clearing really is possible one course at
    # a time, and the panel keeps working.
    if current.get("courseCategories") and not merged.get("courseCategories"):
        log.error("Refused a knowledge-base save that would have removed every course.")
        return PlainTextResponse(
            "এই সেভটি সব কোর্স মুছে ফেলত, তাই বাতিল করা হয়েছে। পেজটি রিফ্রেশ করে আবার চেষ্টা করুন।",
            status_code=400,
        )

    await kb_config.set_config_value("knowledge_base", merged)
    await kb_config.reload()
    log.info("Knowledge base reloaded from admin edit.")

    from app.kb.knowledge import sync_from_kb

    await sync_from_kb(merged)  # no-op unless KNOWLEDGE_RAG_ENABLED; never fails the save
    return RedirectResponse("/?saved=1", status_code=302)


@router.post("/undo")
async def kb_undo(request: Request) -> Response:
    """Put the knowledge base back to the version before the last save."""
    session, _, _ = _auth(request)
    if not session:
        return RedirectResponse("/login", status_code=302)
    _, csrf_ok = await _form(request)
    if not csrf_ok:
        return PlainTextResponse("Invalid CSRF token.", status_code=403)
    restored = await kb_config.restore_previous("knowledge_base")
    log.warning("Knowledge base undo by %s: %s", session.get("u"),
                "restored the previous version" if restored else "nothing to restore")
    return RedirectResponse("/?undone=1" if restored else "/?nothing_to_undo=1", status_code=302)


# --- Bot behaviour -----------------------------------------------------------
@router.get("/system-prompt")
async def prompt_page(request: Request) -> Response:
    session, role, value = _auth(request)
    if not session:
        return RedirectResponse("/login", status_code=302)
    csrf = auth.csrf_token(value)
    if role != "admin":
        return _forbidden(request, session["u"], role, csrf)
    system_prompt = str(((await kb_config.get_config_value("system_prompt")) or {}).get("text") or "")
    answering_rules = str(((await kb_config.get_config_value("answering_rules")) or {}).get("text") or "")
    sections = ((await kb_config.get_config_value("prompt_sections")) or {}).get("sections") or []
    lead = (await kb_config.get_config_value("lead_capture")) or {}
    return _render(
        request,
        "prompt", title="Bot behaviour", active="prompt", user=session["u"], role=role, csrf=csrf,
        saved=request.query_params.get("saved") == "1",
        system_prompt=system_prompt, answering_rules=answering_rules, sections=sections,
        lead_instruction=str(lead.get("instruction") or ""),
        ask_after_turns=int(lead.get("askAfterTurns") or kb_config.lead_ask_after_turns()),
        default_prompt=defaults.DEFAULT_SYSTEM_PROMPT,
        default_rules=defaults.DEFAULT_ANSWERING_RULES,
        default_lead=defaults.DEFAULT_LEAD_INSTRUCTION,
        default_turns=defaults.DEFAULT_ASK_AFTER_TURNS,
    )


@router.post("/system-prompt")
async def prompt_save(request: Request) -> Response:
    session, role, value = _auth(request)
    if not session:
        return RedirectResponse("/login", status_code=302)
    if role != "admin":
        return _forbidden(request, session["u"], role, auth.csrf_token(value))
    body, csrf_ok = await _form(request)
    if not csrf_ok:
        return PlainTextResponse("Invalid CSRF token.", status_code=403)

    try:
        turns = int(str(body.get("leadAskAfterTurns") or ""))
    except ValueError:
        turns = 0

    # A value identical to the built-in default is stored blank, so future default
    # improvements reach panels that never customised it (old-bot behaviour). The boxes
    # are pre-filled with the default, so this is the normal path, not a rare one --
    # hence _nl(): browsers submit textareas with CRLF, which would never match.
    sp = _nl(body.get("systemPrompt"))
    ar = _nl(body.get("answeringRules"))
    await kb_config.set_config_value(
        "system_prompt", {"text": "" if sp.strip() == defaults.DEFAULT_SYSTEM_PROMPT.strip() else sp})
    await kb_config.set_config_value(
        "answering_rules", {"text": "" if ar.strip() == defaults.DEFAULT_ANSWERING_RULES.strip() else ar})
    await kb_config.set_config_value("prompt_sections", {"sections": prompt_sections_from_form(body)})
    li = _nl(body.get("leadInstruction")).strip()
    await kb_config.set_config_value("lead_capture", {
        "instruction": "" if li == defaults.DEFAULT_LEAD_INSTRUCTION.strip() else li,
        "askAfterTurns": turns if turns > 0 else defaults.DEFAULT_ASK_AFTER_TURNS,
    })
    await kb_config.reload()
    log.info("Bot behaviour reloaded from admin edit.")
    return RedirectResponse("/system-prompt?saved=1", status_code=302)


# --- Publish switch ----------------------------------------------------------
def _safe_next(value: object) -> str:
    """Only ever redirect back to a path on this panel — never to another site."""
    path = str(value or "").strip()
    return path if path.startswith("/") and not path.startswith("//") else "/"


@router.post("/publish")
async def publish_toggle(request: Request) -> Response:
    """Master on/off for replies. Open to both roles: the editor watches the inbox day to
    day, so they need to be able to silence the bot without waiting for the owner."""
    session, role, value = _auth(request)
    if not session:
        return RedirectResponse("/login", status_code=302)
    body, csrf_ok = await _form(request)
    if not csrf_ok:
        return PlainTextResponse("Invalid CSRF token.", status_code=403)

    published = str(body.get("published") or "") == "1"
    await publish.set_published(published, by=session["u"])
    target = _safe_next(body.get("next"))
    sep = "&" if "?" in target else "?"
    return RedirectResponse(f"{target}{sep}bot={'published' if published else 'unpublished'}", status_code=302)


# --- Blocked users -----------------------------------------------------------
def _recent_senders() -> list[dict[str, Any]]:
    """Everyone who messaged in the last week, busiest first (a nuisance sender's
    defining trait is volume). Names fall back to what the customer typed in chat."""
    rows = []
    for sender_id, r in state.recents.items():
        name = (
            r.get("name")
            or state.customers.get(sender_id, {}).get("name")
            or (state.sessions.get(sender_id) or {}).get("customer_name")
            or ""
        )
        rows.append({
            "sender_id": sender_id, "name": name,
            "last_message": _snippet(r.get("last_message")),
            "msg_count": r.get("msg_count", 0),
            "last_seen": r.get("last_seen", 0),
            "when": _fmt_when(r.get("last_seen")),
        })
    rows.sort(key=lambda x: (-x["msg_count"], -x["last_seen"]))
    return rows[:300]


@router.get("/blocked")
async def blocked_page(request: Request) -> Response:
    session, role, value = _auth(request)
    if not session:
        return RedirectResponse("/login", status_code=302)
    blocked = [{**b, "when": _fmt_when(b.get("blocked_at"))} for b in blocklist.list_blocked()]
    blocked_ids = {b["senderId"] for b in blocked}
    return _render(
        request,
        "blocked", title="Blocked users", active="blocked", user=session["u"], role=role,
        csrf=auth.csrf_token(value), done=request.query_params.get("done"),
        blocked=blocked, recents=[r for r in _recent_senders() if r["sender_id"] not in blocked_ids],
    )


@router.post("/blocked/add")
async def blocked_add(request: Request) -> Response:
    session, _, _ = _auth(request)
    if not session:
        return RedirectResponse("/login", status_code=302)
    body, csrf_ok = await _form(request)
    if not csrf_ok:
        return PlainTextResponse("Invalid CSRF token.", status_code=403)
    ok = await blocklist.block_sender(
        str(body.get("senderId") or ""), str(body.get("name") or ""), str(body.get("note") or ""))
    return RedirectResponse(f"/blocked?done={'blocked' if ok else 'invalid'}", status_code=302)


@router.post("/blocked/remove")
async def blocked_remove(request: Request) -> Response:
    session, _, _ = _auth(request)
    if not session:
        return RedirectResponse("/login", status_code=302)
    body, csrf_ok = await _form(request)
    if not csrf_ok:
        return PlainTextResponse("Invalid CSRF token.", status_code=403)
    await blocklist.unblock_sender(str(body.get("senderId") or ""))
    return RedirectResponse("/blocked?done=unblocked", status_code=302)


# --- Leads -------------------------------------------------------------------
async def _recent_leads() -> list[dict[str, Any]]:
    async with db_session() as db:
        rows = (await db.execute(
            select(LeadRow).order_by(LeadRow.at.desc()).limit(LEADS_PAGE_LIMIT)
        )).scalars().all()
    return [{
        "when": _fmt_when(p.get("time") or row.at),
        "name": p.get("name") or "",
        "phone": p.get("phone") or "",
        "interest": p.get("interest") or "",
        "remarks": p.get("remarks") or "",
        "url": p.get("conversationUrl") or "",
    } for row in rows for p in [row.payload or {}]]


@router.get("/leads")
async def leads_page(request: Request) -> Response:
    session, role, value = _auth(request)
    if not session:
        return RedirectResponse("/login", status_code=302)
    csrf = auth.csrf_token(value)
    if role != "admin":
        return _forbidden(request, session["u"], role, csrf)
    return _render(request, "leads", title="Leads", active="leads", user=session["u"], role=role,
                   csrf=csrf, leads=await _recent_leads(), limit=LEADS_PAGE_LIMIT)


@router.get("/leads.csv")
async def leads_csv(request: Request) -> Response:
    session, role, value = _auth(request)
    if not session:
        return RedirectResponse("/login", status_code=302)
    if role != "admin":
        return _forbidden(request, session["u"], role, auth.csrf_token(value))
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Time", "Name", "Phone", "Interested in", "Remarks", "Open Chat"])
    for lead in await _recent_leads():
        writer.writerow([lead["when"], lead["name"], lead["phone"], lead["interest"], lead["remarks"], lead["url"]])
    return Response(
        content="﻿" + buf.getvalue(),  # BOM so Excel opens Bangla correctly
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=leads.csv"},
    )
