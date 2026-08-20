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
from app.ops import blocklist, digest, followups

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


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.validate_required()

    await create_tables()
    await blocklist.load()
    await state.load()
    await kb_config.reload()
    followups.restore()

    stop = asyncio.Event()
    tasks = [
        asyncio.create_task(_flush_loop(stop)),
        asyncio.create_task(_prune_loop(stop)),
        asyncio.create_task(_name_resolve_loop(stop)),
        asyncio.create_task(outbox.run_delivery_loop(stop)),
        asyncio.create_task(digest.run_digest_loop(stop)),
    ]

    log.info("De Jure Academy bot (Agno) listening on port %d", settings.port)
    log.info("Lead capture: %s", "enabled" if settings.leads_on else "disabled (no sheet/Telegram configured)")
    log.info("Payment alerts: %s", "enabled (Telegram)" if settings.payments_on else "disabled")
    log.info(
        "Memory: %s",
        f"on — {len(state.sessions)} session(s) restored, {len(state.customers)} known customer(s)"
        if settings.memory_on
        else "PAUSED — no persistence, no customer recall (every chat starts fresh)",
    )
    log.info("LLM: %s (%s)", settings.llm_provider, settings.llm_model)

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
    return app


app = create_app()
