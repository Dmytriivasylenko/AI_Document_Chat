"""
Celery application and background tasks.

Worker start command:
    celery -A rag_app.celery_app worker --loglevel=info --concurrency=4
"""
import logging
from celery import Celery
from celery.utils.log import get_task_logger

from rag_app.config import settings

logger = get_task_logger(__name__)

celery_app = Celery(
    "rag_app",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["rag_app.celery_app"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,           # re-queue on worker crash
    worker_prefetch_multiplier=1,  # fair dispatch for heavy tasks
    result_expires=3600,
)


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    name="rag_app.process_document",
)
def process_document_task(self, document_id: int) -> dict:
    """
    Background task: extract text from a stored PDF, create OpenAI embeddings,
    and persist the chunks to pgvector.

    Runs outside the async FastAPI context — uses sync SQLAlchemy + psycopg.
    """
    import psycopg
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from rag_app.pdf import extract_text_from_pdf_bytes
    from rag_app.rag import chunk_text, embed_texts, store_chunks
    from rag_app.models import Document, DocumentStatus

    # sync engine (Celery workers are synchronous)
    sync_url = settings.DATABASE_URL  # postgresql+psycopg://...
    engine = create_engine(sync_url, pool_pre_ping=True)

    try:
        with Session(engine) as db:
            doc = db.get(Document, document_id)
            if doc is None:
                logger.error("Document %s not found", document_id)
                return {"status": "not_found"}

            if not doc.file_bytes:
                raise ValueError(f"Document {document_id} has no file_bytes")

            logger.info("Processing document %s (%s)", document_id, doc.filename)

            # 1. Extract text
            text = extract_text_from_pdf_bytes(doc.file_bytes)
            if not text.strip():
                raise ValueError("PDF produced no extractable text")

            # 2. Chunk
            chunks = chunk_text(text)
            logger.info("Created %d chunks for document %s", len(chunks), document_id)

            # 3. Embed (batch to stay within OpenAI rate limits)
            vectors = embed_texts(chunks)

            # 4. Persist
            store_chunks(db, document_id, chunks, vectors)

            # 5. Mark ready
            doc.status = DocumentStatus.READY
            db.commit()

        logger.info("Document %s processed successfully", document_id)
        return {"status": "ready", "chunks": len(chunks)}

    except Exception as exc:
        logger.error("Failed to process document %s: %s", document_id, exc, exc_info=True)

        # Mark document as failed before retrying
        try:
            with Session(engine) as db:
                doc = db.get(Document, document_id)
                if doc:
                    doc.status = DocumentStatus.FAILED
                    db.commit()
        except Exception:
            pass

        raise self.retry(exc=exc)
