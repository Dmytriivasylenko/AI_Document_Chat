from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import List
from rag_app.rag import llm_answer_stream
from rag_app.auth_service import get_current_user
from rag_app.db import get_conn
from rag_app.rag import embed_texts, llm_answer_stream
from fastapi.responses import StreamingResponse
import json

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    question: str = Field(..., example="Please do summary documents")


class Source(BaseModel):
    chunk_id: int
    document_id: int
    preview: str


class ChatResponse(BaseModel):
    answer: str
    sources: List[Source]


def search_chunks(user_id: int, query_embedding: list[float], k: int = 5):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.text, c.document_id
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE d.user_id=%s AND d.status='ready'
                ORDER BY c.embedding <-> %s
                LIMIT %s
                """,
                (user_id, query_embedding, k),
            )
            return cur.fetchall()


@router.post("", response_model=ChatResponse)
def chat(req: ChatRequest, user=Depends(get_current_user)):
    qvec = embed_texts([req.question])[0]
    rows = search_chunks(user["id"], qvec, k=5)

    context = "\n\n".join([r[1] for r in rows])

    if not context.strip():
        return {
            "answer": "Немає контексту. Завантаж PDF і дочекайся статусу ready.",
            "sources": [],
        }

    answer = llm_answer_stream(req.question, context)

    sources = [
        {"chunk_id": r[0], "document_id": r[2], "preview": r[1][:200]}
        for r in rows
    ]

    return {"answer": answer, "sources": sources}

@router.post("/stream")
def chat_stream(req: ChatRequest, user=Depends(get_current_user)):
    qvec = embed_texts([req.question])[0]
    rows = search_chunks(user["id"], qvec, k=5)
    context = "\n\n".join([r[1] for r in rows])

    sources = [
        {"chunk_id": r[0], "document_id": r[2], "preview": r[1][:200]}
        for r in rows
    ]

    def event_generator():
        if not context.strip():
            yield "data: " + json.dumps({"type": "token", "value": "No context. Upload PDF and wait until ready."}) + "\n\n"
            yield "data: " + json.dumps({"type": "done"}) + "\n\n"
            return

        # stream tokens from OpenAI
        for token in llm_answer_stream(req.question, context):
            yield "data: " + json.dumps({"type": "token", "value": token}) + "\n\n"

        # send sources at end
        yield "data: " + json.dumps({"type": "sources", "value": sources}) + "\n\n"
        yield "data: " + json.dumps({"type": "done"}) + "\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
