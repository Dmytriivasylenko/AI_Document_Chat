"""
Document management endpoints: upload, list, status, delete.

PDF processing is dispatched to a Celery worker so the upload endpoint
returns immediately (HTTP 202 Accepted) instead of blocking.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_app.auth_service import get_current_user
from rag_app.db import get_db
from rag_app.models import Document, DocumentStatus, User

router = APIRouter(prefix="/documents", tags=["documents"])

MAX_FILE_SIZE_MB = 20
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class DocumentResponse(BaseModel):
    id: int
    filename: str
    status: DocumentStatus
    created_at: str

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_doc(cls, doc: Document) -> "DocumentResponse":
        return cls(
            id=doc.id,
            filename=doc.filename,
            status=doc.status,
            created_at=doc.created_at.isoformat(),
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DocumentResponse,
    summary="Upload a PDF for processing",
)
async def upload_document(
    file: Annotated[UploadFile, File(description="PDF file, max 20 MB")],
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Upload a PDF.  
    - File is stored in the DB as binary.
    - Background processing (text extraction + embedding) is dispatched to **Celery**.
    - Poll `GET /documents/{id}` to check when `status` becomes `ready`.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only PDF files are accepted")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {MAX_FILE_SIZE_MB} MB limit",
        )

    doc = Document(
        user_id=current_user.id,
        filename=file.filename,
        file_bytes=content,
        status=DocumentStatus.PROCESSING,
    )
    db.add(doc)
    await db.flush()
    await db.refresh(doc)

    # Dispatch background task — import here to avoid circular imports
    from rag_app.celery_app import process_document_task
    process_document_task.delay(doc.id)

    return DocumentResponse.from_orm_doc(doc)


@router.get(
    "/",
    response_model=list[DocumentResponse],
    summary="List all documents for the current user",
)
async def list_documents(
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
async def get_document(
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
async def delete_document(
    document_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    doc = await _get_user_document(db, document_id, current_user.id)
    await db.delete(doc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_user_document(db: AsyncSession, document_id: int, user_id: int) -> Document:
    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.user_id == user_id)
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return doc
