"""Phase-0 live spike (AGNO_REBUILD_PLAN.md §7): run a scripted Bengali conversation
through the REAL model and the real Team(route) stack, measuring per-turn latency.

Exit criteria checked:
- Bengali tool-calling works (save_lead fires with the customer's name+phone;
  report_payment fires on a claim and survives the verifier)
- multi-turn tool replay works on the configured provider (thought_signature on Gemini)
- p95-ish latency fits inside the 5-10s humanized pacing window

Usage (needs a real key; uses a throwaway SQLite db seeded with the production KB):
    $env:GEMINI_API_KEY = "..."           # or set LLM_PROVIDER/OPENROUTER_API_KEY
    uv run python scripts/live_spike.py
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

# Environment BEFORE app imports. Real model; everything else local/off.
os.environ.setdefault("LLM_PROVIDER", "gemini")
os.environ.setdefault("LLM_MODEL", "gemini-3.1-flash-lite")
os.environ.setdefault("PAGE_ACCESS_TOKEN", "spike")
os.environ.setdefault("APP_SECRET", "spike")
os.environ.setdefault("VERIFY_TOKEN", "spike")
os.environ.setdefault("ADMIN_USERNAME", "spike")
os.environ.setdefault("ADMIN_PASSWORD", "spike")
os.environ.setdefault("SESSION_SECRET", "spike")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "spike")   # payments_on -> report_payment offered
os.environ.setdefault("TELEGRAM_CHAT_ID", "1")
os.environ.setdefault("GOOGLE_SHEET_WEBAPP_URL", "http://localhost/spike")  # leads_on
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tempfile.mkdtemp(prefix='spike-')}/spike.db"

TURNS = [
    ("lead-q", "BJS course er dam koto?"),
    ("interest", "আমি ভর্তি হতে চাই। কিভাবে ভর্তি হবো?"),
    ("contact", "Orko Rahman 01712345678"),
    ("payment", "টাকা পাঠিয়ে দিয়েছি। TrxID 8N7A2B3C4D"),
]


async def main() -> None:
    from app.agents.factory import run_turn
    from app.agents.turn_context import TurnContext
    from app.db import state
    from app.db.engine import create_tables, dispose_engine
    from app.kb import config as kb_config
    from app.utils.text import find_phone

    await create_tables()
    kb = json.loads((ROOT / "tests" / "fixtures" / "kb_input.json").read_bytes().decode("utf-8"))
    await kb_config.set_config_value("knowledge_base", kb)
    await kb_config.reload()

    session = state.get_session("spike-customer")
    latencies: list[float] = []
    failures: list[str] = []

    for label, text in TURNS:
        turn = TurnContext(
            sender_id="spike-customer", session=session, combined_text=text,
            profile={"name": "Orko Rahman"}, offered_phone=find_phone(text),
        )
        started = time.perf_counter()
        try:
            reply = await run_turn(turn)
        except Exception as err:
            failures.append(f"{label}: {type(err).__name__}: {err}")
            print(f"\n[{label}] FAILED: {err}")
            continue
        elapsed = time.perf_counter() - started
        latencies.append(elapsed)

        state.remember_turn(session, text, reply)
        if turn.lead:
            session["lead"]["captured"] = True
            session["lead"]["name"] = turn.lead["name"]

        print(f"\n[{label}] {elapsed:.1f}s")
        print(f"  customer: {text}")
        print(f"  bot     : {reply[:300]}")
        if turn.lead:
            print(f"  ✓ save_lead: {turn.lead}")
        if turn.payment:
            print(f"  ✓ report_payment: {turn.payment}")
        if turn.lead_invalid:
            print("  ! save_lead rejected (invalid phone)")

    print("\n--- Spike summary ---")
    print(f"turns: {len(TURNS)}, failures: {len(failures)}")
    if latencies:
        print(f"latency: min {min(latencies):.1f}s, max {max(latencies):.1f}s, "
              f"avg {sum(latencies) / len(latencies):.1f}s (pacing window is 5-10s)")
    for f in failures:
        print("  FAIL:", f)
    await dispose_engine()
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    asyncio.run(main())
