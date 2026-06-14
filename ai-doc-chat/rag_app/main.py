"""
Application entry-point.

Run locally:
    uvicorn rag_app.main:app --reload

Production:
    gunicorn rag_app.main:app -k uvicorn.workers.UvicornWorker -w 4
"""
import time

import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from rag_app.config import settings
from rag_app.db import check_db_connection
from rag_app.limiter import limiter
from rag_app.logging_config import RequestIDMiddleware, setup_logging
from rag_app.routers.auth import router as auth_router
from rag_app.routers.chat import router as chat_router
from rag_app.routers.documents import router as documents_router

#Logging
setup_logging()
logger = structlog.get_logger(__name__)

#App
app = FastAPI(
    title="AI Document Chat (RAG)",
    description=(
        "Upload PDF documents and chat with them using OpenAI embeddings + pgvector. "
        "Authentication via JWT, background processing via Celery + Redis."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(RequestIDMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "request_handled",
        duration_ms=round(duration_ms, 1),
    )
    return response


# Routes
app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(chat_router)


#Health
@app.get("/", tags=["root"], summary="Root ping")
async def root():
    return {"status": "ok", "version": app.version}

@app.get(
    "/health",
    tags=["root"],
    summary="Health check",
    response_description="Returns 200 when the app and DB are healthy, 503 otherwise.",
)
async def health():
    db_ok = await check_db_connection()
    if not db_ok:
        logger.warning("health_check_failed", db=False)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unhealthy", "db": False},
        )
    return {"status": "healthy", "db": True}
