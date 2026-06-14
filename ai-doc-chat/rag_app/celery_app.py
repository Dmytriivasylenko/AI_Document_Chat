"""
Celery application and background tasks.

Worker start command:
    celery -A rag_app.celery_app worker --loglevel=info --concurrency=4

task_acks_late=True  ensures a task is not removed from the queue until it
completes successfully — so if the worker crashes mid-processing the task
will be retried by another worker instead of being silently lost.
"""
from celery import Celery
from celery.utils.log import get_task_logger

from rag_app.config import settings

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from rag_app.pdf import extract_text_from_pdf_bytes
from rag_app.rag import chunk_text, embed_texts, store_chunks
from rag_app.models import Document, DocumentStatus

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

    sync_url = settings.DATABASE_URL
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

            #Extract text
            text = extract_text_from_pdf_bytes(doc.file_bytes)
            if not text.strip():
                raise ValueError("PDF produced no extractable text")

            #Chunk (sentence-boundary aware, with overlap)
            chunks = chunk_text(text)
            logger.info("Created %d chunks for document %s", len(chunks), document_id)

            #Embed via Voyage AI (batched to stay within rate limits)
            vectors = embed_texts(chunks)

            #Persist chunks + vectors
            store_chunks(db, document_id, chunks, vectors)

            #Mark ready
            doc.status = DocumentStatus.READY
            db.commit()

        logger.info("Document %s processed successfully", document_id)
        return {"status": "ready", "chunks": len(chunks)}

    except Exception as exc:
        logger.error(
            "Failed to process document %s: %s", document_id, exc, exc_info=True
        )
        # Mark as failed before retrying so the UI reflects the error state
        try:
            with Session(engine) as db:
                doc = db.get(Document, document_id)
                if doc:
                    doc.status = DocumentStatus.FAILED
                    db.commit()
        except Exception:
            pass

        raise self.retry(exc=exc)
