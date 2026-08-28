"""Admin-editable bot config, stored in Postgres (bot_config), with in-process hot reload.

Assembly order of the full system instruction (ported from kb.js buildSystemPrompt +
server.js loadFromKb) — the admin-editable pieces sit BETWEEN the code-owned guardrails:

  1. system prompt (admin, or DEFAULT_SYSTEM_PROMPT)
  2. === ANSWERING RULES ===        (admin, or default)
  3. === EXAMPLES & SCENARIOS ===   (admin prompt sections)
  4. STRICT_ADHERENCE               (code constant — non-editable)
  5. === KNOWLEDGE BASE ===         (rendered catalog markdown)
  6. KB_FOOTER                      (code constant — last-position weighting)
  7. === LEAD CAPTURE ===           (admin lead instruction, when leads are on)
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.db.engine import db_session
from app.db.models import ConfigRow
from app.kb import defaults
from app.kb.catalog import backfill_admin_only, render_markdown

# Hot-reloadable cache, rebuilt by reload() after every admin save and at boot.
_cache: dict[str, Any] = {
    "system_prompt_full": "",   # assembled instruction incl. lead capture block
    "lead_ask_after_turns": defaults.DEFAULT_ASK_AFTER_TURNS,
    "kb": {},                   # raw knowledge_base dict for the admin panel
}


async def _get(db, key: str) -> dict[str, Any] | None:
    row = (await db.execute(select(ConfigRow).where(ConfigRow.key == key))).scalar_one_or_none()
    return row.value if row else None


async def get_config_value(key: str) -> dict[str, Any] | None:
    async with db_session() as db:
        return await _get(db, key)


BACKUP_PREFIX = "prev:"


async def set_config_value(key: str, value: dict[str, Any]) -> None:
    """Write a config value, keeping the PREVIOUS one under `prev:<key>`.

    Every admin save is a full replace of the live knowledge base or system prompt, and a
    truncated form post or a cleared page instantly becomes what the bot tells customers.
    One slot of history turns that from "restore last night's database dump" into one
    click, which is the difference between a five-minute and a next-day recovery.
    """
    async with db_session() as db:
        if not key.startswith(BACKUP_PREFIX):
            previous = await _get(db, key)
            if previous is not None:
                await db.merge(ConfigRow(key=f"{BACKUP_PREFIX}{key}", value=previous))
        await db.merge(ConfigRow(key=key, value=value))
        await db.commit()


async def restore_previous(key: str) -> bool:
    """Swap a config value back to the version before the last save. Returns False when
    there is nothing to restore."""
    async with db_session() as db:
        previous = await _get(db, f"{BACKUP_PREFIX}{key}")
        if previous is None:
            return False
        current = await _get(db, key)
        await db.merge(ConfigRow(key=key, value=previous))
        # Keep the undone version in the slot so the restore itself can be undone.
        if current is not None:
            await db.merge(ConfigRow(key=f"{BACKUP_PREFIX}{key}", value=current))
        await db.commit()
    await reload()
    return True


async def has_previous(key: str) -> bool:
    return (await get_config_value(f"{BACKUP_PREFIX}{key}")) is not None


def _text_of(value: dict[str, Any] | None) -> str:
    return str((value or {}).get("text") or "")


def build_system_prompt(
    kb: dict[str, Any],
    system_prompt: str,
    prompt_sections: list[dict[str, str]],
    answering_rules: str,
) -> str:
    header = (system_prompt or "").strip() or defaults.DEFAULT_SYSTEM_PROMPT
    rules = (answering_rules or "").strip()
    rules_block = f"\n\n=== ANSWERING RULES ===\n{rules}" if rules else ""

    blocks = ""
    for s in prompt_sections or []:
        title = str((s or {}).get("title") or "").strip()
        body = str((s or {}).get("body") or "").strip()
        if title or body:
            blocks += f"\n\n### {title or 'Scenario'}\n{body}"
    sections_block = f"\n\n=== EXAMPLES & SCENARIOS ==={blocks}" if blocks else ""

    return (
        header
        + rules_block
        + sections_block
        + defaults.STRICT_ADHERENCE
        + defaults.KB_SEPARATOR
        + render_markdown(kb)
        + defaults.KB_FOOTER
    )


async def reload() -> None:
    """Rebuild the assembled prompt + lead threshold from the database. Called at boot and
    after every admin save, so new replies use the new data immediately (no restart)."""
    settings = get_settings()
    async with db_session() as db:
        kb = backfill_admin_only((await _get(db, "knowledge_base")) or {})
        system_prompt = _text_of(await _get(db, "system_prompt"))
        answering_rules = _text_of(await _get(db, "answering_rules")) or defaults.DEFAULT_ANSWERING_RULES
        sections_val = (await _get(db, "prompt_sections")) or {}
        prompt_sections = sections_val.get("sections") or []
        lead_val = (await _get(db, "lead_capture")) or {}

    instruction = str(lead_val.get("instruction") or "").strip() or defaults.DEFAULT_LEAD_INSTRUCTION
    try:
        turns = int(lead_val.get("askAfterTurns") or 0)
    except (TypeError, ValueError):
        turns = 0
    if turns <= 0:
        turns = settings.lead_msg_threshold if settings.lead_msg_threshold > 0 else defaults.DEFAULT_ASK_AFTER_TURNS

    prompt = build_system_prompt(kb, system_prompt, prompt_sections, answering_rules)
    if settings.leads_on:
        prompt += f"\n\n=== LEAD CAPTURE ===\n{instruction}"

    _cache["system_prompt_full"] = prompt
    _cache["lead_ask_after_turns"] = turns
    _cache["kb"] = kb


def system_prompt_full() -> str:
    return _cache["system_prompt_full"]


def lead_ask_after_turns() -> int:
    return _cache["lead_ask_after_turns"]


def current_kb() -> dict[str, Any]:
    return _cache["kb"]
