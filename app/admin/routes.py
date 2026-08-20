"""Admin panel routes. Role rules ported from admin.js:

- admin: everything.
- editor: courses + books + custom sections (non-admin-only) + blocklist. On save, the
  editor's submission replaces ONLY those fields; everything else (about, contact,
  enroll, admin-only sections) is kept from the stored KB — and adminOnly is forced
  false on submitted sections so a hand-crafted POST can't hide one from the admin.

Every successful save triggers kb_config.reload() — hot reload, no restart.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy import select

from app.admin import auth
from app.admin.templates import env
from app.db import state
from app.db.engine import db_session
from app.db.models import LeadRow
from app.kb import config as kb_config
from app.kb import defaults
from app.ops import blocklist

log = logging.getLogger(__name__)
router = APIRouter()

LEADS_PAGE_LIMIT = 500


def _render(template: str, **ctx: Any) -> HTMLResponse:
    return HTMLResponse(env.get_template(template).render(**ctx))


def _auth(request: Request) -> tuple[dict | None, str, str]:
    """Returns (session, role, cookie value). Sessions signed before roles existed have
    no "r" — treat them as admin (only the admin account could hold one)."""
    session, value = auth.current_session(request)
    role = (session or {}).get("r", "admin")
    return session, role, value or ""


def _forbidden() -> HTMLResponse:
    return HTMLResponse("<h1>403</h1><p>This account cannot access that page.</p>", status_code=403)


async def _check_csrf(request: Request) -> tuple[dict[str, Any], bool]:
    form = dict(await request.form())
    _, _, value = _auth(request)
    import hmac as _hmac

    ok = _hmac.compare_digest(str(form.get("_csrf", "")), auth.csrf_token(value))
    return form, ok


@router.get("/login")
async def login_page(request: Request) -> Response:
    session, _, _ = _auth(request)
    if session:
        return RedirectResponse("/", status_code=302)
    return _render("login", title="Login", user=None, page="", csrf="", saved=False, error=None)


@router.post("/login")
async def login_submit(request: Request) -> Response:
    form = dict(await request.form())
    account = auth.check_login(str(form.get("username", "")), str(form.get("password", "")))
    if not account:
        return _render("login", title="Login", user=None, page="", csrf="", saved=False,
                       error="Wrong username or password.")
    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        auth.COOKIE_NAME, auth.make_session(account["username"], account["role"]),
        httponly=True, samesite="strict", max_age=auth.SESSION_TTL_S, path="/",
    )
    return response


@router.post("/logout")
async def logout(request: Request) -> Response:
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return response


@router.get("/")
async def kb_editor(request: Request) -> Response:
    session, role, value = _auth(request)
    if not session:
        return RedirectResponse("/login", status_code=302)
    kb = kb_config.current_kb()
    view = kb
    if role != "admin":
        # Editors never even receive admin-only sections in the page payload.
        view = {**kb, "customSections": [s for s in kb.get("customSections") or [] if not s.get("adminOnly")]}
    return _render(
        "kb", title="Knowledge base", user=session["u"], role=role, page="kb",
        csrf=auth.csrf_token(value), saved=request.query_params.get("saved") == "1", error=None,
        kb=view, kb_json=json.dumps(view, ensure_ascii=False),
    )


@router.post("/save")
async def kb_save(request: Request) -> Response:
    session, role, _ = _auth(request)
    if not session:
        return RedirectResponse("/login", status_code=302)
    form, csrf_ok = await _check_csrf(request)
    if not csrf_ok:
        return PlainTextResponse("Invalid CSRF token.", status_code=403)
    try:
        submitted = json.loads(str(form.get("kb_json") or "{}"))
        assert isinstance(submitted, dict)
    except Exception:
        return PlainTextResponse("Malformed knowledge-base payload.", status_code=400)

    current = kb_config.current_kb()
    if role == "admin":
        merged = submitted
    else:
        admin_only = [s for s in current.get("customSections") or [] if s.get("adminOnly")]
        merged = {
            **current,
            "courseCategories": submitted.get("courseCategories") or [],
            "books": submitted.get("books") or "",
            "customSections": [
                *admin_only,
                *[{**s, "adminOnly": False} for s in submitted.get("customSections") or []],
            ],
        }

    await kb_config.set_config_value("knowledge_base", merged)
    await kb_config.reload()
    log.info("Knowledge base reloaded from admin edit.")

    from app.kb.knowledge import sync_from_kb

    await sync_from_kb(merged)  # no-op unless KNOWLEDGE_RAG_ENABLED; never fails the save
    return RedirectResponse("/?saved=1", status_code=302)


@router.get("/system-prompt")
async def prompt_page(request: Request) -> Response:
    session, role, value = _auth(request)
    if not session:
        return RedirectResponse("/login", status_code=302)
    if role != "admin":
        return _forbidden()
    system_prompt = str(((await kb_config.get_config_value("system_prompt")) or {}).get("text") or "")
    answering_rules = str(((await kb_config.get_config_value("answering_rules")) or {}).get("text") or "")
    sections = ((await kb_config.get_config_value("prompt_sections")) or {}).get("sections") or []
    lead = (await kb_config.get_config_value("lead_capture")) or {}
    return _render(
        "prompt", title="Bot behaviour", user=session["u"], role=role, page="prompt",
        csrf=auth.csrf_token(value), saved=request.query_params.get("saved") == "1", error=None,
        system_prompt=system_prompt or defaults.DEFAULT_SYSTEM_PROMPT,
        answering_rules=answering_rules or defaults.DEFAULT_ANSWERING_RULES,
        sections_json=json.dumps(sections, ensure_ascii=False),
        lead_instruction=str(lead.get("instruction") or defaults.DEFAULT_LEAD_INSTRUCTION),
        ask_after_turns=int(lead.get("askAfterTurns") or kb_config.lead_ask_after_turns()),
    )


@router.post("/system-prompt")
async def prompt_save(request: Request) -> Response:
    session, role, _ = _auth(request)
    if not session:
        return RedirectResponse("/login", status_code=302)
    if role != "admin":
        return _forbidden()
    form, csrf_ok = await _check_csrf(request)
    if not csrf_ok:
        return PlainTextResponse("Invalid CSRF token.", status_code=403)
    try:
        sections = json.loads(str(form.get("sections_json") or "[]"))
        assert isinstance(sections, list)
    except Exception:
        sections = []
    sections = [
        {"title": str(s.get("title") or "").strip(), "body": str(s.get("body") or "")}
        for s in sections
        if isinstance(s, dict) and (str(s.get("title") or "").strip() or str(s.get("body") or "").strip())
    ]
    try:
        turns = int(str(form.get("ask_after_turns") or ""))
    except ValueError:
        turns = 0

    # A value identical to the built-in default is stored as blank, so future default
    # improvements reach panels that never customized it (old-bot behaviour).
    sp = str(form.get("system_prompt") or "")
    ar = str(form.get("answering_rules") or "")
    await kb_config.set_config_value("system_prompt", {"text": "" if sp.strip() == defaults.DEFAULT_SYSTEM_PROMPT.strip() else sp})
    await kb_config.set_config_value("answering_rules", {"text": "" if ar.strip() == defaults.DEFAULT_ANSWERING_RULES.strip() else ar})
    await kb_config.set_config_value("prompt_sections", {"sections": sections})
    await kb_config.set_config_value("lead_capture", {
        "instruction": str(form.get("lead_instruction") or "").strip(),
        "askAfterTurns": turns if turns > 0 else defaults.DEFAULT_ASK_AFTER_TURNS,
    })
    await kb_config.reload()
    log.info("Bot behaviour reloaded from admin edit.")
    return RedirectResponse("/system-prompt?saved=1", status_code=302)


def _recent_senders() -> list[dict[str, Any]]:
    """Everyone who messaged in the last week, busiest first (a nuisance sender's
    defining trait is volume). Names fall back to what the customer typed in chat."""
    rows = []
    for sender_id, r in state.recents.items():
        name = r.get("name") or state.customers.get(sender_id, {}).get("name") or (
            state.sessions.get(sender_id, {}) or {}).get("customer_name") or ""
        rows.append({
            "sender_id": sender_id,
            "name": name,
            "last_message": r.get("last_message", ""),
            "last_seen": r.get("last_seen", 0),
            "last_seen_str": datetime.fromtimestamp((r.get("last_seen") or 0) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "msg_count": r.get("msg_count", 0),
        })
    rows.sort(key=lambda x: (-x["msg_count"], -x["last_seen"]))
    return rows[:300]


@router.get("/blocked")
async def blocked_page(request: Request) -> Response:
    session, role, value = _auth(request)
    if not session:
        return RedirectResponse("/login", status_code=302)
    return _render(
        "blocked", title="Blocked users", user=session["u"], role=role, page="blocked",
        csrf=auth.csrf_token(value), saved=request.query_params.get("saved") == "1", error=None,
        blocked=blocklist.list_blocked(), recents=_recent_senders(),
    )


@router.post("/blocked/add")
async def blocked_add(request: Request) -> Response:
    session, _, _ = _auth(request)
    if not session:
        return RedirectResponse("/login", status_code=302)
    form, csrf_ok = await _check_csrf(request)
    if not csrf_ok:
        return PlainTextResponse("Invalid CSRF token.", status_code=403)
    await blocklist.block_sender(str(form.get("sender_id") or ""), str(form.get("name") or ""), str(form.get("note") or ""))
    return RedirectResponse("/blocked?saved=1", status_code=302)


@router.post("/blocked/remove")
async def blocked_remove(request: Request) -> Response:
    session, _, _ = _auth(request)
    if not session:
        return RedirectResponse("/login", status_code=302)
    form, csrf_ok = await _check_csrf(request)
    if not csrf_ok:
        return PlainTextResponse("Invalid CSRF token.", status_code=403)
    await blocklist.unblock_sender(str(form.get("sender_id") or ""))
    return RedirectResponse("/blocked?saved=1", status_code=302)


async def _recent_leads() -> list[dict[str, Any]]:
    async with db_session() as db:
        rows = (await db.execute(select(LeadRow).order_by(LeadRow.at.desc()).limit(LEADS_PAGE_LIMIT))).scalars().all()
    out = []
    for row in rows:
        p = row.payload or {}
        out.append({
            "time": str(p.get("time") or "")[:16].replace("T", " "),
            "name": p.get("name") or "",
            "phone": p.get("phone") or "",
            "interest": p.get("interest") or "",
            "remarks": p.get("remarks") or "",
            "url": p.get("conversationUrl") or "",
        })
    return out


@router.get("/leads")
async def leads_page(request: Request) -> Response:
    session, role, value = _auth(request)
    if not session:
        return RedirectResponse("/login", status_code=302)
    if role != "admin":
        return _forbidden()
    return _render(
        "leads", title="Leads", user=session["u"], role=role, page="leads",
        csrf=auth.csrf_token(value), saved=False, error=None, leads=await _recent_leads(),
    )


@router.get("/leads.csv")
async def leads_csv(request: Request) -> Response:
    session, role, _ = _auth(request)
    if not session:
        return RedirectResponse("/login", status_code=302)
    if role != "admin":
        return _forbidden()
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Time", "Name", "Phone", "Interested in", "Remarks", "Open Chat"])
    for lead in await _recent_leads():
        writer.writerow([lead["time"], lead["name"], lead["phone"], lead["interest"], lead["remarks"], lead["url"]])
    return Response(
        content="﻿" + buf.getvalue(),  # BOM so Excel opens Bangla correctly
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=leads.csv"},
    )
