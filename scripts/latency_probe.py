"""Latency isolation probe: where do the seconds go?

Times three shapes on the configured provider:
  bare   — tiny prompt, no tools (base model latency)
  member — one agent with the FULL assembled KB prompt + tools (old-bot-equivalent)
  team   — the real Team(route) turn (adds the leader hop)

Usage:
    $env:GEMINI_API_KEY="..." ; uv run python scripts/latency_probe.py
    $env:LLM_PROVIDER="openrouter"; $env:LLM_MODEL="<prod model id>"; ... likewise
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("LLM_PROVIDER", "gemini")
os.environ.setdefault("LLM_MODEL", "gemini-3.1-flash-lite")
for var in ("PAGE_ACCESS_TOKEN", "APP_SECRET", "VERIFY_TOKEN", "ADMIN_USERNAME",
            "ADMIN_PASSWORD", "SESSION_SECRET"):
    os.environ.setdefault(var, "probe")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "probe")
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")
os.environ.setdefault("GOOGLE_SHEET_WEBAPP_URL", "http://localhost/probe")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tempfile.mkdtemp(prefix='probe-')}/probe.db"

QUESTION = "BJS course er dam koto?"


async def timed(label: str, coro_factory, n: int = 2) -> None:
    for i in range(n):
        started = time.perf_counter()
        try:
            await coro_factory()
            print(f"  {label} #{i + 1}: {time.perf_counter() - started:.1f}s")
        except Exception as err:
            print(f"  {label} #{i + 1}: FAILED {type(err).__name__}: {str(err)[:120]}")


async def main() -> None:
    from agno.agent import Agent

    from app.agents.factory import build_team, run_turn
    from app.agents.model import build_model
    from app.agents.turn_context import TurnContext
    from app.db import state
    from app.db.engine import create_tables, dispose_engine
    from app.kb import config as kb_config

    await create_tables()
    kb = json.loads((ROOT / "tests" / "fixtures" / "kb_input.json").read_bytes().decode("utf-8"))
    await kb_config.set_config_value("knowledge_base", kb)
    await kb_config.reload()

    s = __import__("app.config", fromlist=["get_settings"]).get_settings()
    print(f"provider={s.llm_provider} model={s.llm_model}")
    prompt = kb_config.system_prompt_full()
    print(f"assembled prompt: ~{len(prompt) // 4} tokens ({len(prompt)} chars)\n")

    bare = Agent(name="bare", model=build_model(), instructions="Reply in one short Bengali sentence.",
                 telemetry=False)
    await timed("bare  ", lambda: bare.arun(input="hello"))

    member = Agent(name="member", model=build_model(), instructions=prompt, telemetry=False)
    await timed("member", lambda: member.arun(input=QUESTION))

    async def team_turn():
        session = state.get_session(f"probe-{time.monotonic_ns()}")
        turn = TurnContext(sender_id="probe", session=session, combined_text=QUESTION)
        await run_turn(turn)

    await timed("team  ", team_turn)
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
