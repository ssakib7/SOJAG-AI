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
os.environ.setdefault("SELFCHECK_DISABLED", "true")  # no boot-time Telegram/Graph probes
# The admin cookie is marked Secure whenever PUBLIC_URL is https (it is, in production).
# TestClient speaks plain http and would silently drop such a cookie, so tests run against
# an http origin — the flag itself is covered by tests/test_webhook.py.
os.environ.setdefault("PUBLIC_URL", "http://testserver")
os.environ.setdefault("FOLLOWUP_DELAY_MINUTES", "0")

import pytest  # noqa: E402

from app.db import state  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state():
    state.sessions.clear()
    state.customers.clear()
    state.recents.clear()
    state.off_topic_closes.clear()
    yield
    state.sessions.clear()
    state.customers.clear()
    state.recents.clear()
    state.off_topic_closes.clear()


@pytest.fixture(autouse=True)
def reset_graph_client():
    """Drop the shared Graph httpx client between tests.

    It is a module global created lazily on first use, so it binds to whichever event loop
    happened to be running then. Tests each get their own loop, and a client held over from
    a closed one raises "Event loop is closed" during a later teardown. Production has a
    single loop for the process lifetime, so this only matters here.
    """
    yield
    from app.meta import graph

    graph._client = None
