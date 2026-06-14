"""
Document management endpoints: upload, list, status, delete.

Production features:
  - SHA-256 deduplication: re-uploading the same PDF returns the existing
    document instantly without re-processing or re-billing for embeddings.
  - MIME-type validation: rejects non-PDF files even if renamed to .pdf.
  - Rate limiting: 5 uploads/minute per user (slowapi).
  - PDF processing dispatched to Celery (HTTP 202 Accepted).
"""
import hashlib
from typing import Annotated

import magic  # python-magic — checks actual MIME type, not just extension
import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_app.auth_service import get_current_user
from rag_app.db import get_db
from rag_app.limiter import limiter
from rag_app.models import Document, DocumentStatus, User

router = APIRouter(prefix="/documents", tags=["documents"])
logger = structlog.get_logger(__name__)

MAX_FILE_SIZE_MB = 20
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
ALLOWED_MIME_TYPES = {"application/pdf"}


# ── Schemas ───────────────────────────────────────────────────────────────────

class DocumentResponse(BaseModel):
    id: int
    filename: str
    status: DocumentStatus
    created_at: str
    deduplicated: bool = False  # True when an existing doc was returned

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_doc(cls, doc: Document, *, deduplicated: bool = False) -> "DocumentResponse":
        return cls(
            id=doc.id,
            filename=doc.filename,
            status=doc.status,
            created_at=doc.created_at.isoformat(),
            deduplicated=deduplicated,
        )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DocumentResponse,
    summary="Upload a PDF for processing",
)
@limiter.limit("5/minute")
async def upload_document(
    request: Request,  # required by slowapi
    file: Annotated[UploadFile, File(description="PDF file, max 20 MB")],
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Upload a PDF document.

    **Deduplication**: if you upload the same file twice, the existing document
    is returned immediately (status 202, `deduplicated: true`) — no re-processing,
    no extra embedding API calls.

    **Validation**: file size ≤ 20 MB, MIME type must be `application/pdf`
    (checked against actual file bytes, not just the extension).

    Poll `GET /documents/{id}` until `status` becomes `ready` or `failed`.
    """
    # ── 1. Read & size-check ──────────────────────────────────────────────────
    content = await file.read()

    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {MAX_FILE_SIZE_MB} MB limit",
        )

    # ── 2. MIME-type validation (bytes, not extension) ────────────────────────
    mime = magic.from_buffer(content, mime=True)
    if mime not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Expected PDF, got '{mime}'",
        )

    # ── 3. SHA-256 hash for deduplication ─────────────────────────────────────
    file_hash = hashlib.sha256(content).hexdigest()

    existing = await db.execute(
        select(Document).where(
            Document.user_id == current_user.id,
            Document.file_hash == file_hash,
        )
    )
    existing_doc = existing.scalar_one_or_none()

    if existing_doc is not None:
        logger.info(
            "document_deduplicated",
            document_id=existing_doc.id,
            filename=file.filename,
            hash=file_hash[:12],
        )
        return DocumentResponse.from_orm_doc(existing_doc, deduplicated=True)

    # ── 4. Persist & dispatch ─────────────────────────────────────────────────
    doc = Document(
        user_id=current_user.id,
        filename=file.filename or "upload.pdf",
        file_bytes=content,
        file_hash=file_hash,
        status=DocumentStatus.PROCESSING,
    )
    db.add(doc)
    await db.flush()
    await db.refresh(doc)

    from rag_app.celery_app import process_document_task
    process_document_task.delay(doc.id)

    logger.info(
        "document_uploaded",
        document_id=doc.id,
        filename=doc.filename,
        size_kb=round(len(content) / 1024, 1),
    )
    return DocumentResponse.from_orm_doc(doc)


@router.get(
    "/",
    response_model=list[DocumentResponse],
    summary="List all documents for the current user",
)
@limiter.limit("60/minute")
async def list_documents(
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(
        select(Document)
        .where(Document.user_id == current_user.id)
        .order_by(Document.created_at.desc())
    )
    docs = result.scalars().all()
    return [DocumentResponse.from_orm_doc(d) for d in docs]


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Get document status",
)
@limiter.limit("60/minute")
async def get_document(
    request: Request,
    document_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    doc = await _get_user_document(db, document_id, current_user.id)
    return DocumentResponse.from_orm_doc(doc)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document and its chunks",
)
@limiter.limit("20/minute")
async def delete_document(
    request: Request,
    document_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    doc = await _get_user_document(db, document_id, current_user.id)
    await db.delete(doc)
    logger.info("document_deleted", document_id=document_id)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_user_document(
    db: AsyncSession, document_id: int, user_id: int
) -> Document:
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.user_id == user_id,
        )
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
    return doc
