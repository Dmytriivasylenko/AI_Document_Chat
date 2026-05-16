"""
Unit tests for core business logic.

Run: pytest tests/ -v
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from rag_app.auth_service import hash_password, verify_password, create_access_token
from rag_app.rag import chunk_text
from rag_app.pdf import extract_text_from_pdf_bytes



class TestPasswordHashing:
    def test_hash_is_not_plaintext(self):
        assert hash_password("secret123") != "secret123"

    def test_verify_correct_password(self):
        hashed = hash_password("correct_password")
        assert verify_password("correct_password", hashed) is True

    def test_reject_wrong_password(self):
        hashed = hash_password("correct_password")
        assert verify_password("wrong_password", hashed) is False


class TestJWT:
    def test_token_is_string(self):
        token = create_access_token(user_id=42)
        assert isinstance(token, str)
        assert len(token) > 20

    def test_different_users_get_different_tokens(self):
        t1 = create_access_token(user_id=1)
        t2 = create_access_token(user_id=2)
        assert t1 != t2



class TestChunkText:
    def test_empty_string_returns_empty_list(self):
        assert chunk_text("") == []

    def test_short_text_is_single_chunk(self):
        text = "Hello world. This is a test."
        chunks = chunk_text(text, chunk_size=1200)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_long_text_produces_multiple_chunks(self):
        text = "This is a sentence. " * 200   # ~4000 chars
        chunks = chunk_text(text, chunk_size=500, overlap=50)
        assert len(chunks) > 1

    def test_no_chunk_exceeds_size(self):
        text = "Word " * 1000
        chunks = chunk_text(text, chunk_size=300, overlap=50)
        for chunk in chunks:
            assert len(chunk) <= 300

    def test_whitespace_is_normalised(self):
        text = "Hello    world.\n\nNew   paragraph."
        chunks = chunk_text(text, chunk_size=1200)
        assert "  " not in chunks[0]



class TestPdfExtraction:
    def test_returns_string(self):
        from io import BytesIO
        from unittest.mock import patch, MagicMock

        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Page one text."
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]

        with patch("rag_app.pdf.PdfReader", return_value=mock_reader):
            result = extract_text_from_pdf_bytes(b"fake-pdf-bytes")

        assert result == "Page one text."

    def test_handles_none_page_text(self):
        from unittest.mock import patch, MagicMock

        mock_page = MagicMock()
        mock_page.extract_text.return_value = None
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]

        with patch("rag_app.pdf.PdfReader", return_value=mock_reader):
            result = extract_text_from_pdf_bytes(b"fake-pdf-bytes")

        assert result == ""
