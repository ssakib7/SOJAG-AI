"""One-off migration: old bot's flat JSON files -> Postgres. Idempotent (re-runs upsert).

Usage (from the repo root, with DATABASE_URL set or the compose db running):
    uv run python scripts/migrate_json.py e:/dejure-fb-bot

Reads from the old repo directory:
    data/bot.db.json      -> sessions, customers, recents
    data/leads.jsonl      -> leads (ledger of record)
    data/payments.jsonl   -> payment_claims
    data/lead_outbox.json -> outbox_items (still-undelivered items resume delivery)
    data/blocklist.json   -> blocklist
    data/deletions.json   -> deletion_requests
    data/system_prompt.txt, answering_rules.txt, prompt_sections.json,
    lead_capture.json     -> bot_config
    knowledge_base.json   -> bot_config key "knowledge_base"

Stop the old bot first (its 10s flush would keep changing the files mid-copy).
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.engine import create_tables, db_session, dispose_engine  # noqa: E402
from app.db.models import (  # noqa: E402
    BlockRow, CustomerRow, DeletionRow, LeadRow, OutboxRow, PaymentClaimRow, RecentRow, SessionRow,
)
from app.kb.config import set_config_value  # noqa: E402


def _read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return fallback


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _parse_at(value) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def _snake_session(s: dict) -> dict:
    """Old camelCase session fields -> the new snake_case shape (history is unchanged)."""
    lead = s.get("lead") or {}
    return {
        "history": s.get("history") or [],
        "last_active": s.get("lastActive") or 0,
        "lead": {
            "captured": bool(lead.get("captured")),
            "name": lead.get("name"),
            "asked": bool(lead.get("asked")),
            "msg_count": lead.get("msgCount") or 0,
        },
        "followup_sent": bool(s.get("followUpSent")),
        "returning": bool(s.get("returning")),
        "customer_name": s.get("customerName"),
        "recent_mids": s.get("recentMids") or [],
        "payments": s.get("payments") or [],
        "non_text_prompted_at": s.get("nonTextPromptedAt") or 0,
    }


async def migrate(old_root: Path) -> None:
    data = old_root / "data"
    await create_tables()

    async with db_session() as db:
        bot_db = _read_json(data / "bot.db.json", {})
        for sid, s in bot_db.get("sessions") or []:
            snake = _snake_session(s or {})
            await db.merge(SessionRow(sender_id=sid, data=snake, last_active=snake["last_active"]))
        for sid, c in bot_db.get("customers") or []:
            await db.merge(CustomerRow(
                sender_id=sid, name=c.get("name") or "", phone=c.get("phone") or "",
                interest=c.get("interest") or "", remarks=c.get("remarks") or "",
                captured=bool(c.get("captured", True)),
                first_seen=c.get("firstSeen") or 0, last_seen=c.get("lastSeen") or 0,
            ))
        for sid, r in bot_db.get("recents") or []:
            await db.merge(RecentRow(
                sender_id=sid, name=r.get("name") or "", last_message=r.get("lastMessage") or "",
                last_seen=r.get("lastSeen") or 0, msg_count=r.get("msgCount") or 0,
            ))

        for kind, filename, row_cls in (("lead", "leads.jsonl", LeadRow), ("payment", "payments.jsonl", PaymentClaimRow)):
            for line in _read_text(data / filename).splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue  # skip corrupt line, same as the old counter did
                await db.merge(row_cls(
                    id=str(entry.get("id")), at=_parse_at(entry.get("at")),
                    payload=entry.get(kind) or entry.get("lead") or {},
                ))

        outbox = _read_json(data / "lead_outbox.json", {})
        for entry in outbox.get("pending") or []:
            kind = entry.get("kind") or "lead"
            await db.merge(OutboxRow(
                id=str(entry.get("id")), kind=kind, at=entry.get("at") or 0,
                payload=entry.get("lead") or {}, sinks=entry.get("sinks") or {},
                attempts=entry.get("attempts") or 0, not_before=0, done=False,
            ))

        blocklist = _read_json(data / "blocklist.json", {})
        for sid, info in blocklist.get("blocked") or []:
            await db.merge(BlockRow(
                sender_id=sid, name=(info or {}).get("name") or "", note=(info or {}).get("note") or "",
                blocked_at=(info or {}).get("blockedAt") or "",
            ))

        deletions = _read_json(data / "deletions.json", {})
        for r in deletions.get("requests") or []:
            await db.merge(DeletionRow(
                code=r.get("code"), user_id=str(r.get("userId") or ""),
                purged={k: bool(v) for k, v in (r.get("purged") or {}).items()},
                requested_at=r.get("requestedAt") or "",
            ))
        await db.commit()

    kb = _read_json(old_root / "knowledge_base.json", {})
    kb.pop("answeringRules", None)  # long since migrated to its own file/key
    await set_config_value("knowledge_base", kb)
    await set_config_value("system_prompt", {"text": _read_text(data / "system_prompt.txt")})
    await set_config_value("answering_rules", {"text": _read_text(data / "answering_rules.txt")})
    sections = _read_json(data / "prompt_sections.json", [])
    await set_config_value("prompt_sections", {"sections": sections if isinstance(sections, list) else []})
    lead_capture = _read_json(data / "lead_capture.json", {})
    await set_config_value("lead_capture", {
        "instruction": str(lead_capture.get("instruction") or ""),
        "askAfterTurns": lead_capture.get("askAfterTurns") or 3,
    })

    print("Migration complete.")
    await dispose_engine()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    asyncio.run(migrate(Path(sys.argv[1])))
