"""Test environment: dummy secrets + SQLite (aiosqlite) instead of Postgres.

Set BEFORE any app import so pydantic-settings and the engine pick them up.
"""

import os

os.environ.setdefault("PAGE_ACCESS_TOKEN", "test-page-token")
os.environ.setdefault("MESSENGER_PAGE_TOKEN", "test-lookup-token")
os.environ.setdefault("APP_SECRET", "test-app-secret")
os.environ.setdefault("VERIFY_TOKEN", "test-verify-token")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin-pass")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("FB_PAGE_ID", "1234567890")
# A file-backed SQLite db: an in-memory one is per-connection, so the lifespan's
# create_tables and later queries could see different (empty) databases.
import tempfile  # noqa: E402

_db_file = os.path.join(tempfile.mkdtemp(prefix="dejure-test-"), "test.db")
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_db_file}")
os.environ.setdefault("LEAD_DIGEST_TIME", "off")
os.environ.setdefault("FOLLOWUP_DELAY_MINUTES", "0")

import pytest  # noqa: E402

from app.db import state  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state():
    state.sessions.clear()
    state.customers.clear()
    state.recents.clear()
    yield
    state.sessions.clear()
    state.customers.clear()
    state.recents.clear()
