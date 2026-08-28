"""Schema invariants the SQLite test database cannot enforce on its own.

These exist because of a real outage: `utcnow()` returns a timezone-AWARE datetime, but
five columns were declared as bare `Mapped[datetime]`, which is `TIMESTAMP WITHOUT TIME
ZONE` on Postgres. asyncpg refuses to encode an aware datetime for a naive column, so
every write to bot_config, leads, payment_claims, notices and shadow_drafts raised
"can't subtract offset-naive and offset-aware datetimes".

It was invisible for a day. The bot looked healthy, replied fluently, and /health returned
200 — while `enqueue()` (ledger + outbox in ONE transaction) rolled back every lead,
payment claim and escalation before it could reach the Google Sheet or Telegram. Nothing
caught it because the whole suite runs on SQLite, which stores datetimes as strings and
accepts either kind happily.

So this asserts the invariant at the metadata level, where SQLite cannot hide it, instead
of requiring a Postgres server in CI.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import DateTime

from app.db.models import Base, utcnow


def _datetime_columns():
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, DateTime):
                yield table.name, column.name, column.type


def test_utcnow_is_timezone_aware():
    """The premise of the rule below: if this ever returns a naive datetime, the columns
    should change with it rather than the two silently drifting apart again."""
    now = utcnow()
    assert now.tzinfo is not None, "utcnow() must stay timezone-aware"
    assert now.utcoffset() == timezone.utc.utcoffset(None)


def test_every_datetime_column_is_timezone_aware():
    """Every DateTime column must be timezone=True, or Postgres rejects utcnow()'s output.

    A new `at: Mapped[datetime] = mapped_column(default=utcnow)` looks completely correct,
    passes review, passes the SQLite suite — and breaks only in production. This is the
    check that fails instead.
    """
    columns = list(_datetime_columns())
    assert columns, "expected the models to declare DateTime columns"

    naive = [f"{t}.{c}" for t, c, type_ in columns if not type_.timezone]
    assert not naive, (
        "these columns are TIMESTAMP WITHOUT TIME ZONE on Postgres and will reject every "
        f"write fed by utcnow(): {', '.join(naive)}. Declare them as "
        "mapped_column(DateTime(timezone=True), ...)"
    )


@pytest.mark.parametrize("table", ["bot_config", "leads", "payment_claims", "notices", "shadow_drafts"])
def test_outage_tables_still_covered(table):
    """The five tables the outage actually hit, named explicitly so a rename or a drop is a
    deliberate decision rather than a silent loss of coverage."""
    assert table in Base.metadata.tables, f"{table} is missing from the metadata"
    aware = [c.name for c in Base.metadata.tables[table].columns
             if isinstance(c.type, DateTime) and c.type.timezone]
    assert aware, f"{table} lost its timezone-aware timestamp column"


@pytest.mark.asyncio
async def test_config_write_roundtrip_accepts_aware_datetime():
    """A plain smoke test of the write path that failed in production
    (`set_config_value("knowledge_base", ...)`).

    Note what it does NOT do: on SQLite this passes with the buggy naive columns too, which
    is precisely how the outage escaped the suite. The metadata assertions above are the
    real guard; this one only catches a config write that breaks for some other reason.
    """
    from app.db.engine import create_tables
    from app.kb.config import get_config_value, set_config_value

    await create_tables()
    await set_config_value("test_schema_probe", {"text": "ok", "at": datetime.now(timezone.utc).isoformat()})
    assert (await get_config_value("test_schema_probe"))["text"] == "ok"
