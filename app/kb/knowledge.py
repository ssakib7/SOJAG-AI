"""Optional agentic-RAG knowledge store (Agno Knowledge + PgVector hybrid search).

The course catalog stays rendered into the system prompt — small, admin-edited, and
price answers must never miss retrieval. This store is the GROWTH PATH for long-tail
content: books detail, admin custom sections, future FAQ/syllabus documents. Disabled
by default (KNOWLEDGE_RAG_ENABLED=false) for cutover parity with the old bot; when
enabled, KnowledgeAgent gets `knowledge=` + `search_knowledge=True` and the admin
panel's KB save re-syncs the store.

Requires Postgres (pgvector) and a Gemini API key for embeddings.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

_knowledge = None


def knowledge_enabled() -> bool:
    from app.config import get_settings

    s = get_settings()
    return (
        s.knowledge_rag_enabled
        and s.database_url.startswith("postgresql")
        and bool(s.gemini_api_key)
    )


def get_knowledge():
    """The shared Knowledge instance, or None when the feature is off."""
    global _knowledge
    if not knowledge_enabled():
        return None
    if _knowledge is None:
        from agno.knowledge.embedder.google import GeminiEmbedder
        from agno.knowledge.knowledge import Knowledge
        from agno.vectordb.pgvector import PgVector, SearchType

        s = _settings()
        _knowledge = Knowledge(
            name="dejure-kb",
            vector_db=PgVector(
                table_name="kb_vectors",
                db_url=s.database_url.replace("+asyncpg", "+psycopg"),
                search_type=SearchType.hybrid,
                embedder=GeminiEmbedder(api_key=s.gemini_api_key),
            ),
            max_results=5,
        )
    return _knowledge


def _settings():
    from app.config import get_settings

    return get_settings()


async def sync_from_kb(kb: dict[str, Any]) -> None:
    """Upsert the long-tail KB content into the vector store. Called after every admin
    KB save (and at boot). Safe no-op when the feature is off; failures are logged and
    never break the admin save — the prompt-rendered catalog still carries the facts."""
    knowledge = get_knowledge()
    if knowledge is None:
        return
    try:
        books = str(kb.get("books") or "").strip()
        if books:
            await knowledge.ainsert(name="books-products", text_content=books, upsert=True)
        enroll = str(kb.get("enroll") or "").strip()
        if enroll:
            await knowledge.ainsert(name="how-to-enroll", text_content=enroll, upsert=True)
        for section in kb.get("customSections") or []:
            title = str((section or {}).get("title") or "").strip()
            body = str((section or {}).get("body") or "").strip()
            if title and body:
                await knowledge.ainsert(name=f"section-{title.lower()[:60]}", text_content=body, upsert=True)
        log.info("Knowledge store synced from KB.")
    except Exception as err:
        log.error("Knowledge sync failed (prompt-rendered KB still authoritative): %s", err)


def reset_for_tests() -> None:
    global _knowledge
    _knowledge = None
