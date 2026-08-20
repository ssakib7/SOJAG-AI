"""SQLAlchemy models. Postgres replaces the old bot's flat JSON files 1:1:

  bot.db.json sessions   -> sessions        (snapshot JSONB; runtime truth stays in memory)
  bot.db.json customers  -> customers
  bot.db.json recents    -> recents         (roster for the admin Blocked-users page)
  leads.jsonl            -> leads           (append-only ledger, never rewritten)
  payments.jsonl         -> payment_claims  (append-only ledger)
  lead_outbox.json       -> outbox_items
  blocklist.json         -> blocklist
  deletions.json         -> deletion_requests
  system_prompt.txt etc. -> bot_config      (key/value; JSON values)
  knowledge_base.json    -> bot_config key "knowledge_base"

Agno's own tables (sessions/traces/memories) live in the same database under its
default names — separate concern, never read by this app.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, Boolean, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# JSONB on Postgres; plain JSON on SQLite (unit tests).
JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SessionRow(Base):
    """Snapshot of one live conversation session (runtime truth is the in-memory dict)."""

    __tablename__ = "sessions"

    sender_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    data: Mapped[dict] = mapped_column(JSONType, default=dict)
    last_active: Mapped[int] = mapped_column(BigInteger, default=0)  # epoch ms, matches old shape


class CustomerRow(Base):
    """Long-term roster of captured customers, for returning-customer recall."""

    __tablename__ = "customers"

    sender_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(Text, default="")
    phone: Mapped[str] = mapped_column(String(32), default="")
    interest: Mapped[str] = mapped_column(Text, default="")
    remarks: Mapped[str] = mapped_column(Text, default="")
    captured: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen: Mapped[int] = mapped_column(BigInteger, default=0)  # epoch ms
    last_seen: Mapped[int] = mapped_column(BigInteger, default=0)


class RecentRow(Base):
    """Rolling roster of everyone who messaged recently (7-day TTL, admin block-by-name)."""

    __tablename__ = "recents"

    sender_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(Text, default="")
    last_message: Mapped[str] = mapped_column(Text, default="")
    last_seen: Mapped[int] = mapped_column(BigInteger, default=0, index=True)
    msg_count: Mapped[int] = mapped_column(Integer, default=0)


class LeadRow(Base):
    """Append-only lead ledger — the recovery source of last resort. Never updated."""

    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # outbox item id
    at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)


class PaymentClaimRow(Base):
    """Append-only payment-claim ledger."""

    __tablename__ = "payment_claims"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)


class OutboxRow(Base):
    """Pending sink deliveries with per-sink status and retry state.

    The matching ledger row (leads/payment_claims) is written in the SAME transaction
    before any network delivery is attempted — log-before-network, as in the old bot.
    """

    __tablename__ = "outbox_items"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # "lead" | "payment"
    at: Mapped[int] = mapped_column(BigInteger, default=0)  # epoch ms
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    sinks: Mapped[dict] = mapped_column(JSONType, default=dict)  # name -> pending|sent|skipped
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    not_before: Mapped[int] = mapped_column(BigInteger, default=0)  # epoch ms
    done: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class BlockRow(Base):
    __tablename__ = "blocklist"

    sender_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    blocked_at: Mapped[str] = mapped_column(String(40), default="")  # ISO string, matches old shape


class DeletionRow(Base):
    __tablename__ = "deletion_requests"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), default="")
    purged: Mapped[dict] = mapped_column(JSONType, default=dict)
    requested_at: Mapped[str] = mapped_column(String(40), default="")


class ConfigRow(Base):
    """Admin-editable config: system_prompt, answering_rules, prompt_sections,
    lead_capture, knowledge_base. JSON values (strings stored as {"text": ...})."""

    __tablename__ = "bot_config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONType, default=dict)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class ShadowDraftRow(Base):
    """Replies the bot WOULD have sent while running in shadow mode, for
    scripts/shadow_compare.py to diff against the live bot's actual replies."""

    __tablename__ = "shadow_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    sender_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="message")  # message | action
    text: Mapped[str] = mapped_column(Text, default="")


Index("ix_outbox_pending", OutboxRow.done, OutboxRow.not_before)
