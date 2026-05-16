"""
RAG pipeline helpers: chunking, embedding, vector search, LLM streaming.

All DB operations are async (SQLAlchemy AsyncSession).
Sync variants (store_chunks_sync) are used by the Celery worker.
"""
import logging
import re

from openai import AsyncOpenAI, OpenAI
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from rag_app.config import settings
from rag_app.models import Chunk

logger = logging.getLogger(__name__)

EMBED_MODEL = "text-embedding-3-small"
CHAT_MODEL = "gpt-4o-mini"
EMBED_BATCH_SIZE = 100  # OpenAI allows up to 2048 inputs, 100 is safe

async_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
sync_client = OpenAI(api_key=settings.OPENAI_API_KEY)  # used by Celery worker



def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 200) -> list[str]:
    """
    Split *text* into overlapping chunks of roughly *chunk_size* characters.

    Tries to break at sentence boundaries ('. ', '? ', '! ') so chunks are
    more semantically coherent, which improves retrieval quality.
    """
    # Normalise whitespace
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
            # If a single sentence exceeds chunk_size, hard-split it
            if len(sentence) > chunk_size:
                for i in range(0, len(sentence), chunk_size - overlap):
                    chunks.append(sentence[i : i + chunk_size])
                current = ""
            else:
                current = sentence

    if current:
        chunks.append(current)

    return chunks



def embed_texts(texts: list[str]) -> list[list[float]]:
    """Synchronous — used by the Celery worker."""
    all_vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i : i + EMBED_BATCH_SIZE]
        resp = sync_client.embeddings.create(model=EMBED_MODEL, input=batch)
        all_vectors.extend(x.embedding for x in resp.data)
    return all_vectors


async def embed_texts_async(texts: list[str]) -> list[list[float]]:
    """Async — used from FastAPI endpoints when needed."""
    all_vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i : i + EMBED_BATCH_SIZE]
        resp = await async_client.embeddings.create(model=EMBED_MODEL, input=batch)
        all_vectors.extend(x.embedding for x in resp.data)
    return all_vectors




def store_chunks(db: Session, document_id: int, chunks: list[str], vectors: list[list[float]]) -> None:
    """Sync — called from the Celery task."""
    db.add_all(
        Chunk(document_id=document_id, text=t, embedding=v)
        for t, v in zip(chunks, vectors)
    )


async def store_chunks_async(db: AsyncSession, document_id: int, chunks: list[str], vectors: list[list[float]]) -> None:
    """Async — for any future inline processing path."""
    db.add_all(
        Chunk(document_id=document_id, text=t, embedding=v)
        for t, v in zip(chunks, vectors)
    )



async def search_chunks(
    db: AsyncSession,
    user_id: int,
    query_embedding: list[float],
    k: int = 5,
) -> list:
    """
    Find the *k* most semantically similar chunks belonging to *user_id*.
    Uses pgvector's L2 distance operator (<->).
    """
    q = sql_text("""
        SELECT c.id, c.text, c.document_id, d.filename,
               c.embedding <-> CAST(:qvec AS vector) AS distance
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE d.user_id = :user_id
          AND d.status = 'ready'
        ORDER BY distance
        LIMIT :k
    """)
    result = await db.execute(q, {"user_id": user_id, "qvec": str(query_embedding), "k": k})
    return result.fetchall()




async def llm_answer_stream(question: str, context: str):
    """
    Async generator that streams GPT tokens as they arrive.
    Callers iterate with ``async for token in llm_answer_stream(...)``.
    """
    prompt = (
        "Answer ONLY using the context below.\n"
        "If the answer is not in the context, say you don't know.\n\n"
        f"CONTEXT:\n{context}\n\n"
        f"QUESTION:\n{question}"
    )
    stream = await async_client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": "You are a helpful assistant for document Q&A."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        stream=True,
    )
    async for event in stream:
        delta = event.choices[0].delta
        if delta and delta.content:
            yield delta.content
