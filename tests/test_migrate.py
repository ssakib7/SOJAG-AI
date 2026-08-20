"""Migration script: old JSON file shapes -> Postgres/SQLite rows (synthetic fixtures in
the exact shapes the Node bot wrote, since production data/ only exists on the VPS)."""

import asyncio
import json

from sqlalchemy import select

from app.db.engine import db_session, reset_engine_for_tests
from app.db.models import (
    BlockRow, ConfigRow, CustomerRow, DeletionRow, LeadRow, OutboxRow, PaymentClaimRow, RecentRow, SessionRow,
)
from scripts.migrate_json import migrate


def _write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def test_migrate_old_shapes(tmp_path, monkeypatch):
    # Own database: the migrated outbox row (pending, epoch ~1970) would otherwise make
    # /health report "stuck" in other tests sharing the session-wide SQLite file.
    from app.config import get_settings

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'migrate.db').as_posix()}")
    get_settings.cache_clear()
    reset_engine_for_tests()
    try:
        _run_migration_case(tmp_path)
    finally:
        get_settings.cache_clear()
        reset_engine_for_tests()


def _run_migration_case(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _write(tmp_path / "knowledge_base.json", {
        "about": "x", "contact": {}, "courseCategories": [], "books": "", "enroll": "",
        "customSections": [{"title": "Payment procedure", "body": "b"}], "answeringRules": "legacy",
    })
    _write(data / "bot.db.json", {
        "version": 1,
        "sessions": [["111", {"history": [{"role": "user", "parts": [{"text": "hi"}]}], "lastActive": 1700,
                              "lead": {"captured": True, "name": "Orko", "asked": True, "msgCount": 2},
                              "followUpSent": False, "returning": True, "customerName": "Orko",
                              "recentMids": [], "payments": ["8N7A2B3C4D"], "nonTextPromptedAt": 0}]],
        "customers": [["111", {"name": "Orko", "phone": "01712345678", "captured": True,
                               "firstSeen": 1600, "lastSeen": 1700}]],
        "recents": [["222", {"name": "", "lastMessage": "(স্টিকার)", "lastSeen": 1700, "msgCount": 4}]],
    })
    (data / "leads.jsonl").write_text(
        json.dumps({"id": "L1", "at": "2026-08-01T10:00:00.000Z", "kind": "lead",
                    "lead": {"name": "Orko", "phone": "01712345678", "leadId": "L1"}}) + "\n"
        + "corrupt line\n", encoding="utf-8")
    (data / "payments.jsonl").write_text(
        json.dumps({"id": "P1", "at": "2026-08-02T10:00:00.000Z", "kind": "payment",
                    "payment": {"trxId": "8N7A2B3C4D"}}) + "\n", encoding="utf-8")
    _write(data / "lead_outbox.json", {"version": 1, "pending": [
        {"id": "L1", "at": 1700, "kind": "lead", "lead": {"name": "Orko", "phone": "01712345678"},
         "sinks": {"sheet": "sent", "telegram": "pending"}, "attempts": 3, "notBefore": 99999999999999}]})
    _write(data / "blocklist.json", {"version": 1, "blocked": [["333", {"name": "Spam", "note": "", "blockedAt": "2026-08-03T00:00:00Z"}]]})
    _write(data / "deletions.json", {"version": 1, "requests": [{"code": "abc123", "userId": "444", "purged": {"matched": False}, "requestedAt": "2026-08-04T00:00:00Z"}]})
    (data / "system_prompt.txt").write_text("custom prompt", encoding="utf-8")
    (data / "answering_rules.txt").write_text("", encoding="utf-8")
    _write(data / "prompt_sections.json", [{"title": "T", "body": "B"}])
    _write(data / "lead_capture.json", {"instruction": "ask nicely", "askAfterTurns": 4})

    reset_engine_for_tests()
    asyncio.run(migrate(tmp_path))
    reset_engine_for_tests()

    async def check():
        async with db_session() as db:
            s = (await db.execute(select(SessionRow).where(SessionRow.sender_id == "111"))).scalar_one()
            assert s.data["lead"] == {"captured": True, "name": "Orko", "asked": True, "msg_count": 2}
            assert s.data["payments"] == ["8N7A2B3C4D"] and s.last_active == 1700
            c = (await db.execute(select(CustomerRow).where(CustomerRow.sender_id == "111"))).scalar_one()
            assert c.phone == "01712345678" and c.first_seen == 1600
            r = (await db.execute(select(RecentRow).where(RecentRow.sender_id == "222"))).scalar_one()
            assert r.last_message == "(স্টিকার)" and r.msg_count == 4
            lead = (await db.execute(select(LeadRow).where(LeadRow.id == "L1"))).scalar_one()
            assert lead.payload["phone"] == "01712345678"
            assert (await db.execute(select(PaymentClaimRow).where(PaymentClaimRow.id == "P1"))).scalar_one().payload["trxId"] == "8N7A2B3C4D"
            ob = (await db.execute(select(OutboxRow).where(OutboxRow.id == "L1"))).scalar_one()
            assert ob.sinks == {"sheet": "sent", "telegram": "pending"} and ob.not_before == 0 and not ob.done
            assert (await db.execute(select(BlockRow).where(BlockRow.sender_id == "333"))).scalar_one().name == "Spam"
            assert (await db.execute(select(DeletionRow).where(DeletionRow.code == "abc123"))).scalar_one().user_id == "444"
            cfg = {row.key: row.value for row in (await db.execute(select(ConfigRow))).scalars()}
            assert cfg["system_prompt"] == {"text": "custom prompt"}
            assert cfg["lead_capture"] == {"instruction": "ask nicely", "askAfterTurns": 4}
            assert cfg["prompt_sections"] == {"sections": [{"title": "T", "body": "B"}]}
            assert "answeringRules" not in cfg["knowledge_base"]

    asyncio.run(check())
