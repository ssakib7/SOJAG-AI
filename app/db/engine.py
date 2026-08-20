"""Async SQLAlchemy engine/session factory."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.models import Base

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _session_factory
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def db_session() -> AsyncSession:
    get_engine()
    assert _session_factory is not None
    return _session_factory()


async def create_tables() -> None:
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


def reset_engine_for_tests() -> None:
    """Drop the cached engine so tests can point DATABASE_URL somewhere else."""
    global _engine, _session_factory
    _engine = None
    _session_factory = None
