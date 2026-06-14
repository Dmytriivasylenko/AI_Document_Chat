"""
Chat endpoint — streams Claude answers via Server-Sent Events (SSE).

Rate limited to 10 requests/minute per user to protect Anthropic API budget.
"""
from typing import Annotated, AsyncGenerator

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from rag_app.auth_service import get_current_user
from rag_app.db import get_db
from rag_app.limiter import limiter
from rag_app.models import User
from rag_app.rag import embed_texts_async, llm_answer_stream, search_chunks
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/chat", tags=["chat"])
logger = structlog.get_logger(__name__)


class ChatRequest(BaseModel):
    question: str
    top_k: int = 5


@router.post(
    "/",
    summary="Ask a question across your documents",
    response_description="Server-Sent Events stream of answer tokens",
)
@limiter.limit("10/minute")
async def chat(
    request: Request,  # required by slowapi
    body: ChatRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    RAG chat endpoint (rate-limited: **10 requests/minute** per account).

    1. Embeds the question via Voyage AI (`input_type=query`).
    2. Retrieves top-k most relevant chunks from the user's documents (cosine distance).
    3. Streams the Claude answer back as **SSE** (`text/event-stream`).

    Consume with `EventSource` in the browser or `httpx` with streaming enabled.
    Each SSE event is one token; the stream ends with `data: [DONE]`.
    """
    if not body.question.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Question cannot be empty",
        )

    # 1. Embed question (query input_type for better retrieval)
    [query_vec] = await embed_texts_async([body.question])

    # 2. Retrieve relevant chunks
    rows = await search_chunks(db, current_user.id, query_vec, k=body.top_k)

    if not rows:
        async def no_docs():
            yield "data: I don't have any documents to answer from. Please upload a PDF first.\n\n"
        return StreamingResponse(no_docs(), media_type="text/event-stream")

    # 3. Build context with source filenames for traceability
    context_parts = [f"[{row.filename}]\n{row.text}" for row in rows]
    context = "\n\n---\n\n".join(context_parts)

    logger.info(
        "chat_request",
        question_len=len(body.question),
        chunks_retrieved=len(rows),
        sources=list({row.filename for row in rows}),
    )

    # 4. Stream Claude answer
    return StreamingResponse(
        _sse_generator(body.question, context),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _sse_generator(question: str, context: str) -> AsyncGenerator[str, None]:
    """Wrap the Claude token stream in SSE format."""
    try:
        async for token in llm_answer_stream(question, context):
            yield f"data: {token}\n\n"
        yield "data: [DONE]\n\n"
    except Exception as exc:
        logger.error("sse_stream_error", error=str(exc))
        yield f"data: [ERROR] {exc}\n\n"
