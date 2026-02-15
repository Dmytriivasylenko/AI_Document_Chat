from sqlalchemy.orm import Session
from sqlalchemy import text as sql_text

from openai import OpenAI
from rag_app.config import settings
from rag_app.models import Chunk

client = OpenAI(api_key=settings.OPENAI_API_KEY)

EMBED_MODEL = "text-embedding-3-small"
CHAT_MODEL = "gpt-4o-mini"


def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 200) -> list[str]:
    text = " ".join(text.split())
    chunks = []
    i = 0
    while i < len(text):
        chunks.append(text[i:i + chunk_size])
        i += chunk_size - overlap
    return chunks


def embed_texts(texts: list[str]) -> list[list[float]]:
    resp = client.embeddings.create(model=EMBED_MODEL, input=texts)
    return [x.embedding for x in resp.data]


def store_chunks(db: Session, document_id: int, chunks: list[str], vectors: list[list[float]]):
    for t, v in zip(chunks, vectors):
        db.add(Chunk(document_id=document_id, text=t, embedding=v))


def search_chunks(db: Session, user_id: int, query_embedding: list[float], k: int = 5):
    q = sql_text("""
        SELECT c.id, c.text, c.document_id
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE d.user_id = :user_id AND d.status = 'ready'
        ORDER BY c.embedding <-> :qvec
        LIMIT :k
    """)
    rows = db.execute(q, {"user_id": user_id, "qvec": query_embedding, "k": k}).fetchall()
    return rows


def llm_answer_stream(question: str, context: str):
    prompt = f"""
Answer ONLY using the context.
If you can't find the answer in the context, say you don't know.

CONTEXT:
{context}

QUESTION:
{question}
"""
    stream = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": "You are a helpful assistant for document Q&A."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        stream=True,
    )

    for event in stream:
        delta = event.choices[0].delta
        if delta and delta.content:
            yield delta.content
