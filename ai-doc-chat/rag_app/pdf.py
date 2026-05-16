"""PDF text extraction."""
from pypdf import PdfReader
import io


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """
    Extract plain text from a PDF given as raw bytes.
    Pages that produce no text (e.g. scanned images) are silently skipped.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)
