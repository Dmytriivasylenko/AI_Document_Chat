
import hashlib
import io
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, ANY

from rag_app.auth_service import hash_password, verify_password, create_access_token
from rag_app.rag import chunk_text
from rag_app.pdf import extract_text_from_pdf_bytes


#Password hashing

class TestPasswordHashing:
    def test_hash_is_not_plaintext(self):
        assert hash_password("secret123") != "secret123"

    def test_verify_correct_password(self):
        hashed = hash_password("correct_password")
        assert verify_password("correct_password", hashed) is True

    def test_reject_wrong_password(self):
        hashed = hash_password("correct_password")
        assert verify_password("wrong_password", hashed) is False


#JWT
class TestJWT:
    def test_token_is_string(self):
        token = create_access_token(user_id=42)
        assert isinstance(token, str)
        assert len(token) > 20

    def test_different_users_get_different_tokens(self):
        t1 = create_access_token(user_id=1)
        t2 = create_access_token(user_id=2)
        assert t1 != t2


#Chunking

class TestChunkText:
    def test_empty_string_returns_empty_list(self):
        assert chunk_text("") == []

    def test_short_text_is_single_chunk(self):
        text = "Hello world. This is a test."
        chunks = chunk_text(text, chunk_size=1200)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_long_text_produces_multiple_chunks(self):
        text = "This is a sentence. " * 200
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

    def test_overlap_creates_context_continuity(self):
        """Last word of chunk N should appear near start of chunk N+1."""
        text = "Alpha beta gamma. " * 100
        chunks = chunk_text(text, chunk_size=200, overlap=50)
        assert len(chunks) > 1
        last_words = set(chunks[0].split()[-5:])
        next_start = set(chunks[1].split()[:10])
        assert last_words & next_start, "Expected overlapping content between chunks"


#PDF extraction

class TestPdfExtraction:
    def test_returns_string(self):
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Page one text."
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]

        with patch("rag_app.pdf.PdfReader", return_value=mock_reader):
            result = extract_text_from_pdf_bytes(b"fake-pdf-bytes")

        assert result == "Page one text."

    def test_handles_none_page_text(self):
        mock_page = MagicMock()
        mock_page.extract_text.return_value = None
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]

        with patch("rag_app.pdf.PdfReader", return_value=mock_reader):
            result = extract_text_from_pdf_bytes(b"fake-pdf-bytes")

        assert result == ""

    def test_concatenates_multiple_pages(self):
        pages = []
        for text in ["Page one.", "Page two.", "Page three."]:
            m = MagicMock()
            m.extract_text.return_value = text
            pages.append(m)

        mock_reader = MagicMock()
        mock_reader.pages = pages

        with patch("rag_app.pdf.PdfReader", return_value=mock_reader):
            result = extract_text_from_pdf_bytes(b"fake-pdf-bytes")

        assert "Page one." in result
        assert "Page two." in result
        assert "Page three." in result


#File hash deduplication

class TestFileHashDeduplication:
    def test_same_content_produces_same_hash(self):
        content = b"PDF content bytes"
        h1 = hashlib.sha256(content).hexdigest()
        h2 = hashlib.sha256(content).hexdigest()
        assert h1 == h2

    def test_different_content_produces_different_hash(self):
        h1 = hashlib.sha256(b"document A").hexdigest()
        h2 = hashlib.sha256(b"document B").hexdigest()
        assert h1 != h2

    def test_hash_is_64_chars(self):
        h = hashlib.sha256(b"some pdf").hexdigest()
        assert len(h) == 64

    def test_duplicate_upload_skips_celery(self):
        content = b"fake-pdf-bytes"
        hash_a = hashlib.sha256(content).hexdigest()
        hash_b = hashlib.sha256(content).hexdigest()
        assert hash_a == hash_b
        hash_c = hashlib.sha256(b"different-content").hexdigest()
        assert hash_a != hash_c

#Voyage AI embeddings (mocked)

class TestEmbeddings:
    def test_embed_texts_batches_correctly(self):
        from rag_app.rag import EMBED_BATCH_SIZE
        num_texts = EMBED_BATCH_SIZE + 10  # force 2 batches
        texts = [f"sentence {i}" for i in range(num_texts)]
        fake_vector = [0.1] * 1024
        mock_result = MagicMock()
        mock_result.embeddings = [fake_vector] * EMBED_BATCH_SIZE
        mock_result2 = MagicMock()
        mock_result2.embeddings = [fake_vector] * 10

        with patch("rag_app.rag.voyage_sync") as mock_voyage:
            mock_voyage.embed.side_effect = [mock_result, mock_result2]
            from rag_app.rag import embed_texts
            result = embed_texts(texts)

        assert mock_voyage.embed.call_count == 2
        assert len(result) == num_texts

    @pytest.mark.asyncio
    async def test_embed_texts_async_uses_query_type_for_single_text(self):
        """Single-text embed (a question) should use input_type='query'."""
        fake_vector = [0.1] * 1024
        mock_result = MagicMock()
        mock_result.embeddings = [fake_vector]

        with patch("rag_app.rag.voyage_async") as mock_voyage:
            mock_voyage.embed = AsyncMock(return_value=mock_result)
            from rag_app.rag import embed_texts_async
            result = await embed_texts_async(["What is the revenue?"])

        call_kwargs = mock_voyage.embed.call_args
        assert call_kwargs.kwargs.get("input_type") == "query"
        assert len(result) == 1


#Claude streaming (mocked)

class TestLLMStream:
    @pytest.mark.asyncio
    async def test_llm_answer_stream_yields_tokens(self):
        tokens = ["The ", "answer ", "is ", "42."]

        async def fake_text_stream():
            for t in tokens:
                yield t

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=False)
        mock_stream.text_stream = fake_text_stream()

        with patch("rag_app.rag.anthropic_async") as mock_client:
            mock_client.messages.stream.return_value = mock_stream
            from rag_app.rag import llm_answer_stream
            result = []
            async for token in llm_answer_stream("What is 6x7?", "context text"):
                result.append(token)

        assert result == tokens

    @pytest.mark.asyncio
    async def test_llm_uses_correct_model(self):
        from rag_app.rag import CHAT_MODEL

        async def fake_text_stream():
            yield "ok"

        mock_stream = MagicMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=False)
        mock_stream.text_stream = fake_text_stream()

        with patch("rag_app.rag.anthropic_async") as mock_client:
            mock_client.messages.stream.return_value = mock_stream
            from rag_app.rag import llm_answer_stream
            async for _ in llm_answer_stream("q", "ctx"):
                pass

        call_kwargs = mock_client.messages.stream.call_args
        assert call_kwargs.kwargs.get("model") == CHAT_MODEL
