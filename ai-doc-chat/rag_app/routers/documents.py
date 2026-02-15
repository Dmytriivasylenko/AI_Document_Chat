from fastapi import APIRouter, UploadFile, File, Depends, BackgroundTasks, HTTPException
from rag_app.auth_service import get_current_user
from rag_app.db import get_conn
from rag_app.pdf import extract_text_from_pdf_bytes
from rag_app.rag import chunk_text, embed_texts

router = APIRouter(prefix="/documents", tags=["documents"])


def process_document(document_id: int):
    """Background processing: pdf -> text -> chunks -> embeddings -> pgvector."""
    from rag_app.db import get_conn  # local import safe for bg task

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT file_bytes FROM documents WHERE id=%s", (document_id,))
            row = cur.fetchone()

            if not row or not row[0]:
                cur.execute("UPDATE documents SET status='failed' WHERE id=%s", (document_id,))
                return

            pdf_bytes = row[0]

    try:
        text = extract_text_from_pdf_bytes(pdf_bytes)
        chunks = chunk_text(text)
        vectors = embed_texts(chunks)

        with get_conn() as conn:
            with conn.cursor() as cur:
                for t, v in zip(chunks, vectors):
                    cur.execute(
                        "INSERT INTO chunks (document_id, text, embedding) VALUES (%s, %s, %s)",
                        (document_id, t, v)
                    )
                cur.execute("UPDATE documents SET status='ready' WHERE id=%s", (document_id,))
            conn.commit()

    except Exception:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE documents SET status='failed' WHERE id=%s", (document_id,))
            conn.commit()
        raise


@router.post("/upload")
async def upload_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user=Depends(get_current_user),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF allowed")

    pdf_bytes = await file.read()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO documents (user_id, filename, status, file_bytes)
                VALUES (%s, %s, 'processing', %s)
                RETURNING id
                """,
                (user["id"], file.filename, pdf_bytes),
            )
            document_id = cur.fetchone()[0]
        conn.commit()

    background_tasks.add_task(process_document, document_id)

    return {"document_id": document_id, "status": "processing"}


@router.get("")
def list_documents(user=Depends(get_current_user)):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, filename, status FROM documents WHERE user_id=%s ORDER BY id DESC",
                (user["id"],),
            )
            rows = cur.fetchall()

    return [{"id": r[0], "filename": r[1], "status": r[2]} for r in rows]
