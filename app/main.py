"""FastAPI application: webhook + health + legal pages + admin panel, with the
background loops (state flush, prune, outbox delivery, roster name resolution, digest)
managed by the lifespan. Run with:  uvicorn app.main:app --port 3000
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import FastAPI

from app.config import get_settings
from app.db import state
from app.db.engine import create_tables, dispose_engine
from app.kb import config as kb_config
from app.leads import outbox
from app.meta import graph
from app.ops import blocklist, digest, followups, publish

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

PERSIST_INTERVAL_S = 10
PRUNE_INTERVAL_S = 30 * 60
NAME_RESOLVE_INTERVAL_S = 3


async def _flush_loop(stop: asyncio.Event) -> None:
    """Snapshot dirty state every 10s; one forced final flush runs at shutdown."""
    while not stop.is_set():
        try:
            await state.flush()
        except Exception as err:
            log.error("State flush failed: %s", err)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), PERSIST_INTERVAL_S)


async def _prune_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), PRUNE_INTERVAL_S)
        state.prune()


async def _name_resolve_loop(stop: asyncio.Event) -> None:
    """Backfilled roster entries start nameless — resolve them in the background, one
    Graph lookup every few seconds, one attempt per sender per boot."""
    tried: set[str] = set()
    while not stop.is_set():
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), NAME_RESOLVE_INTERVAL_S)
        if stop.is_set():
            break
        for sender_id, rec in state.recents.items():
            if rec.get("name") or sender_id in tried or blocklist.is_blocked(sender_id):
                continue
            tried.add(sender_id)
            with contextlib.suppress(Exception):
                await graph.fetch_profile(sender_id)
            break  # one lookup per tick


async def _startup_selfcheck(settings) -> None:  # noqa: ANN001
    """Prove the escalation path works, at boot, instead of the first time it is needed.

    A silent Telegram misconfiguration (wrong chat id, revoked bot token, bot removed from
    the group) is indistinguishable from "no alerts happened" until a payment claim goes
    missing. One startup ping makes it obvious on day one, and doubles as a redeploy notice
    for the team.
    """
    from app.leads import sinks
    from app.meta import graph

    if settings.shadow_mode or settings.selfcheck_disabled:
        return
    problems = settings.startup_warnings()
    # An empty knowledge base is not a config typo — it is the bot booting with nothing to
    # say — so it rides the same restart ping the team already reads. It went unnoticed for
    # a day because every other signal (health 200, no errors, fluent Bengali replies)
    # looked normal while every answer was "a representative will contact you".
    if not kb_config.kb_has_content():
        problems.insert(0, "KNOWLEDGE BASE IS EMPTY — the bot cannot answer a single "
                           "question. Import it before publishing replies.")
    try:
        ok = await sinks.send_telegram_text(
            "✅ De Jure bot restarted and is listening.\n"
            + ("Replies: PUBLISHED\n" if publish.is_published() else "⏸ Replies: UNPUBLISHED\n")
            + f"Lead capture: {'on' if settings.leads_on else 'OFF'} · "
            + f"Payment alerts: {'on' if settings.payments_on else 'OFF'}"
            + ("\n\n⚠ " + "\n⚠ ".join(problems) if problems else "")
        )
        if not ok:
            log.warning("⚠ Startup check: Telegram is not configured — escalations and payment "
                        "claims will reach nobody.")
    except Exception as err:
        log.error("⚠ Startup check: Telegram is configured but UNREACHABLE (%s). Payment claims "
                  "and escalations will queue in the outbox until this is fixed.", err)

    # Prove the Conversations API works with the token we actually have, so alerts are
    # known to carry customer names and clickable chat links (see MESSENGER_PAGE_TOKEN).
    try:
        probe = await graph.check_lookup_token()
        if not probe:
            log.warning("⚠ Startup check: customer-name lookup is NOT working — alerts will show "
                        "'(নাম জানা যায়নি)' and no chat link. Set MESSENGER_PAGE_TOKEN to a PAGE token.")
    except Exception as err:
        log.warning("⚠ Startup check: could not verify the lookup token: %s", err)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.validate_required()

    await create_tables()
    await blocklist.load()
    await publish.load()
    await state.load()
    await kb_config.reload()
    followups.restore()

    from app.kb.knowledge import knowledge_enabled, sync_from_kb

    if knowledge_enabled():
        await sync_from_kb(kb_config.current_kb())

    stop = asyncio.Event()
    tasks = [
        asyncio.create_task(_flush_loop(stop)),
        asyncio.create_task(_prune_loop(stop)),
        asyncio.create_task(_name_resolve_loop(stop)),
        asyncio.create_task(outbox.run_delivery_loop(stop)),
        asyncio.create_task(digest.run_digest_loop(stop)),
    ]

    log.info("De Jure Academy bot (Agno) started")
    log.info("Lead capture: %s", "enabled" if settings.leads_on else "disabled (no sheet/Telegram configured)")
    log.info("Payment alerts: %s", "enabled (Telegram)" if settings.payments_on else "disabled")
    log.info(
        "Memory: %s",
        f"on — {len(state.sessions)} session(s) restored, {len(state.customers)} known customer(s)"
        if settings.memory_on
        else "PAUSED — no persistence, no customer recall (every chat starts fresh)",
    )
    log.info("Replies: %s", "PUBLISHED" if publish.is_published()
             else "UNPUBLISHED — the bot will not answer anyone until an admin publishes it")
    log.info("LLM: %s (%s)", settings.llm_provider, settings.llm_model)
    kb_stats = kb_config.kb_summary()
    log.info(
        "Knowledge base: %d course(s) in %d categor(ies), %d custom section(s), "
        "%d chars of book text — %d char assembled prompt",
        kb_stats["courses"], kb_stats["categories"], kb_stats["customSections"],
        kb_stats["booksChars"], kb_stats["promptChars"],
    )

    # Misconfiguration that silently costs leads is worth shouting about — these used to
    # be invisible until someone noticed the sheet had been empty for a week.
    for warning in settings.startup_warnings():
        log.warning("⚠ CONFIG: %s", warning)
    # Tracked like every other background task so shutdown cancels it — an untracked
    # probe can outlive the event loop and die noisily during teardown.
    tasks.append(asyncio.create_task(_startup_selfcheck(settings)))

    try:
        yield
    finally:
        stop.set()
        outbox.wake_for_shutdown()
        followups.cancel_all()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with contextlib.suppress(Exception):
            await state.flush()  # final snapshot so a redeploy doesn't lose recent state
        await graph.close_client()
        await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(title="dejure-agent", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    from app.webhook.routes import router as webhook_router

    app.include_router(webhook_router)

    from app.admin.routes import router as admin_router

    app.include_router(admin_router)
    return _mount_agentos(app)


def _mount_agentos(app: FastAPI) -> FastAPI:
    """Optionally mount Agno's AgentOS (control plane: runs, traces, sessions) as a
    SUB-application under /os. Only when AGENTOS_ENABLED and OS_SECURITY_KEY are both
    set: AgentOS adds its own API, and on a public domain that must be bearer-protected.
    It is mounted rather than wrapped (base_app) on purpose — wrapping applies AgentOS's
    auth middleware to every route, which would lock out Meta's webhook and the admin
    panel. Point the control-plane UI at https://<host>/os."""
    settings = get_settings()
    if not settings.agentos_enabled:
        return app
    if not settings.os_security_key:
        log.error("AGENTOS_ENABLED is set but OS_SECURITY_KEY is empty — AgentOS NOT mounted.")
        return app
    import os

    os.environ.setdefault("OS_SECURITY_KEY", settings.os_security_key)
    from agno.os import AgentOS

    from app.agents.factory import build_team

    agent_os = AgentOS(id="dejure-agent", name="De Jure Agent", teams=[build_team()], telemetry=False)
    app.mount("/os", agent_os.get_app())
    log.info("AgentOS control plane mounted at /os (bearer-protected).")
    return app


app = create_app()
