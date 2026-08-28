"""Optional agentic-RAG knowledge store (Agno Knowledge + PgVector hybrid search).

The course catalog stays rendered into the system prompt — small, admin-edited, and
price answers must never miss retrieval. This store is the GROWTH PATH for long-tail
content: books detail, admin custom sections, future FAQ/syllabus documents. Disabled
by default (KNOWLEDGE_RAG_ENABLED=false) for cutover parity with the old bot; when
enabled, KnowledgeAgent gets `knowledge=` + `search_knowledge=True` and the admin
panel's KB save re-syncs the store.

Requires Postgres (pgvector) and an embedding key. Embeddings follow LLM_PROVIDER:
OpenRouter's OpenAI-compatible /v1/embeddings endpoint when that is the provider,
GeminiEmbedder otherwise — so a single-provider deployment needs a single key.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

_knowledge = None

# Both paths emit 1536-dim vectors, so PgVector's column width (taken from
# embedder.dimensions) is the same either way and switching provider needs no
# migration — only a re-embed, since vectors from different models are not comparable.
DIMENSIONS = 1536
OPENROUTER_EMBED_MODEL = "openai/text-embedding-3-small"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def knowledge_enabled() -> bool:
    from app.config import get_settings

    s = get_settings()
    return (
        s.knowledge_rag_enabled
        and s.database_url.startswith("postgresql")
        and bool(s.openrouter_api_key if s.llm_provider == "openrouter" else s.gemini_api_key)
    )


def get_knowledge():
    """The shared Knowledge instance, or None when the feature is off."""
    global _knowledge
    if not knowledge_enabled():
        return None
    if _knowledge is None:
        from agno.knowledge.knowledge import Knowledge
        from agno.vectordb.pgvector import PgVector, SearchType

        s = _settings()
        _knowledge = Knowledge(
            name="dejure-kb",
            vector_db=PgVector(
                table_name="kb_vectors",
                db_url=s.database_url.replace("+asyncpg", "+psycopg"),
                search_type=SearchType.hybrid,
                embedder=build_embedder(),
            ),
            max_results=5,
        )
    return _knowledge


def build_embedder():
    """The embedder for the configured provider — mirrors agents/model.py's switch.

    Agno has no OpenRouter embedder yet (agno-agi/agno#5749), but OpenRouter exposes an
    OpenAI-schema /v1/embeddings endpoint, which is exactly what OpenAILikeEmbedder is for.
    """
    s = _settings()
    if s.llm_provider == "openrouter":
        from agno.knowledge.embedder.openai_like import OpenAILikeEmbedder

        return OpenAILikeEmbedder(
            id=OPENROUTER_EMBED_MODEL,
            api_key=s.openrouter_api_key,
            base_url=s.openrouter_api_base or OPENROUTER_BASE_URL,
            dimensions=DIMENSIONS,
        )
    from agno.knowledge.embedder.google import GeminiEmbedder

    return GeminiEmbedder(api_key=s.gemini_api_key, dimensions=DIMENSIONS)


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
