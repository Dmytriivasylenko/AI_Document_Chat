"""
RAG pipeline: chunking, embedding (Voyage AI), vector search, LLM streaming (Claude).

Sync variants (embed_texts, store_chunks) are used by the Celery worker.
Async variants are used by FastAPI endpoints.

Model choices:
  Embeddings : voyage-3          — Anthropic-recommended, 1024-dim, strong on RAG tasks
  Chat       : claude-3-5-haiku-20241022 — fast + cheap for Q&A; swap to claude-opus-4-5
                                    for higher accuracy if needed
"""
import logging
import re

import voyageai
import anthropic
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from rag_app.config import settings
from rag_app.models import Chunk

logger = logging.getLogger(__name__)

EMBED_MODEL = "voyage-3"          # 1024-dim; voyage-3-large for higher accuracy
EMBED_DIM = 1024
CHAT_MODEL = "claude-haiku-4-5"   # fast + cheap; swap to claude-opus-4-5 for accuracy
EMBED_BATCH_SIZE = 128             # Voyage AI supports up to 128 inputs per request

# Sync clients
voyage_sync = voyageai.Client(api_key=settings.VOYAGE_API_KEY)
anthropic_sync = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

voyage_async = voyageai.AsyncClient(api_key=settings.VOYAGE_API_KEY)
anthropic_async = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)


def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 200) -> list[str]:
    """
    Split *text* into overlapping chunks of roughly *chunk_size* characters.

    Breaks at sentence boundaries ('. ', '? ', '! ') for semantic coherence,
    which measurably improves retrieval precision vs fixed-size splits.
    Falls back to hard character splits for single sentences longer than chunk_size.
    """
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []

    sentence_endings = re.compile(r"(?<=[.?!])\s+")
    sentences = sentence_endings.split(text)
    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        if len(current) + len(sentence) + 1 <= chunk_size:
            current = f"{current} {sentence}".strip()
        else:
            if current:
                chunks.append(current)
            if len(sentence) > chunk_size:
                for i in range(0, len(sentence), chunk_size - overlap):
                    chunks.append(sentence[i: i + chunk_size])
                current = ""
            else:
                current = sentence

    if current:
        chunks.append(current)

    return chunks


#Embeddings (Voyage AI)

def embed_texts(texts: list[str]) -> list[list[float]]:
    """Synchronous — used by the Celery worker."""
    all_vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i: i + EMBED_BATCH_SIZE]
        result = voyage_sync.embed(batch, model=EMBED_MODEL, input_type="document")
        all_vectors.extend(result.embeddings)
    return all_vectors


async def embed_texts_async(texts: list[str]) -> list[list[float]]:
    all_vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i: i + EMBED_BATCH_SIZE]
        # Voyage async client — query type for questions, document for passages
        input_type = "query" if len(texts) == 1 else "document"
        result = await voyage_async.embed(batch, model=EMBED_MODEL, input_type=input_type)
        all_vectors.extend(result.embeddings)
    return all_vectors


#Persistence

def store_chunks(
    db: Session,
    document_id: int,
    chunks: list[str],
    vectors: list[list[float]],
) -> None:
    db.add_all(
        Chunk(document_id=document_id, text=t, embedding=v)
        for t, v in zip(chunks, vectors)
    )


async def store_chunks_async(
    db: AsyncSession,
    document_id: int,
    chunks: list[str],
    vectors: list[list[float]],
) -> None:
    db.add_all(
        Chunk(document_id=document_id, text=t, embedding=v)
        for t, v in zip(chunks, vectors)
    )


#Vector search

async def search_chunks(
    db: AsyncSession,
    user_id: int,
    query_embedding: list[float],
    k: int = 5,
) -> list:
    q = sql_text("""
        SELECT c.id, c.text, c.document_id, d.filename,
               c.embedding <=> CAST(:qvec AS vector) AS distance
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE d.user_id  = :user_id
          AND d.status   = 'READY'
        ORDER BY distance
        LIMIT :k
    """)
    result = await db.execute(
        q, {"user_id": user_id, "qvec": str(query_embedding), "k": k}
    )
    return result.fetchall()


#LLM streaming (Claude)

async def llm_answer_stream(question: str, context: str):
    system_prompt = (
        "You are a precise document Q&A assistant. "
        "Answer ONLY using the context provided. "
        "If the answer is not in the context, say you don't know. "
        "Be concise and cite the source filename when relevant."
    )
    user_message = (
        f"CONTEXT:\n{context}\n\n"
        f"QUESTION:\n{question}"
    )

    async with anthropic_async.messages.stream(
        model=CHAT_MODEL,
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    ) as stream:
        async for text in stream.text_stream:
            yield text
