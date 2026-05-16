"""
Application entry-point.

Run locally:
    uvicorn rag_app.main:app --reload

Production:
    gunicorn rag_app.main:app -k uvicorn.workers.UvicornWorker -w 4
"""
import logging
import time

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from rag_app.config import settings
from rag_app.db import check_db_connection
from rag_app.routers.auth import router as auth_router
from rag_app.routers.documents import router as documents_router
from rag_app.routers.chat import router as chat_router


logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


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


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every request with method, path, status, and duration."""
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "%s %s → %s  (%.1f ms)",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response



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
    """
    Kubernetes/Docker readiness probe endpoint.
    Checks DB connectivity so load-balancers can route traffic correctly.
    """
    db_ok = await check_db_connection()
    if not db_ok:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unhealthy", "db": False},
        )
    return {"status": "healthy", "db": True}


app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(chat_router)
