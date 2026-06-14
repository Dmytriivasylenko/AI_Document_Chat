# AI Document Chat (RAG)

Upload PDFs and chat with them using natural language. Powered by **Voyage AI embeddings**, **Claude (Anthropic)**, and **pgvector** — with real-time streaming answers via SSE.

![Tests](https://github.com/Dmytriivasylenko/AI_Document_Chat/actions/workflows/ci.yml/badge.svg)

---

## Why this project?

Most RAG tutorials stop at "it works locally". This project solves real production problems:

- **SHA-256 deduplication** — uploading the same PDF twice returns the existing document instantly, no re-processing, no wasted API tokens
- **Rate limiting per user JWT** — not per IP (which breaks behind shared NAT/proxies)
- **MIME-type validation** — checks actual file bytes, not just the `.pdf` extension
- **Celery `acks_late=True`** — tasks survive worker crashes without being silently lost
- **Cosine distance** instead of L2 — better retrieval for normalized Voyage AI embeddings
- **Structured logging** with `request_id` and `user_id` on every log line — production observability

---

## Architecture

```
PDF Upload ──► Celery Worker ──► Voyage AI embeddings ──► pgvector
                                                               │
User question ──► embed (query type) ──► cosine search ──► Claude ──► SSE stream
```

1. User uploads PDF → stored in Postgres, `status = processing`
2. Celery worker: extracts text → sentence-boundary chunking → Voyage AI embeddings → pgvector
3. `status = ready`
4. User asks a question → embedded as `query` type → top-k cosine search → Claude streams answer token by token

---

## Tech Stack

| Layer | Technology |
|---|---|
| API | FastAPI + Uvicorn (async) |
| Database | PostgreSQL 16 + pgvector |
| ORM / Migrations | SQLAlchemy 2 async + Alembic |
| Background tasks | Celery 5 + Redis |
| Embeddings | Voyage AI `voyage-3` (1024-dim, recommended by Anthropic) |
| LLM | Anthropic Claude `claude-haiku-4-5` (SSE streaming) |
| Auth | JWT (python-jose) + bcrypt |
| Rate limiting | slowapi (per user JWT, not per IP) |
| Logging | structlog (JSON in prod, coloured in dev) + request_id middleware |
| File validation | python-magic (MIME-type from bytes) |
| Containerisation | Docker Compose — 5 services with resource limits |
| CI | GitHub Actions |

---

## Features

- **JWT authentication** — register/login, all endpoints protected
- **Async PDF processing** — Celery + Redis worker with retry logic (`max_retries=3`) and status lifecycle (`processing → ready → failed`)
- **RAG pipeline** — sentence-boundary chunking with overlap, Voyage AI embeddings (`input_type=query` vs `document`), cosine vector search via pgvector
- **SSE streaming** — real-time Claude answer stream with source filename references
- **SHA-256 deduplication** — re-uploading the same file returns existing document (`deduplicated: true`)
- **Rate limiting** — 5 uploads/min, 10 chat requests/min per user account
- **Structured logging** — every request carries `request_id`, `user_id`, `duration_ms`
- **Alembic migrations** — async SQLAlchemy 2, versioned schema
- **Health check** — `GET /health` checks DB connectivity (Kubernetes readiness probe)
- **Flower UI** — Celery task monitoring dashboard

---

## Quick Start

```bash
git clone https://github.com/Dmytriivasylenko/AI_Document_Chat.git
cd AI_Document_Chat/ai-doc-chat
cp .env.example .env
# Fill in ANTHROPIC_API_KEY, VOYAGE_API_KEY, JWT_SECRET
docker compose up --build
```

Then run migrations:

```bash
docker compose exec api alembic upgrade head
```

| Service | URL |
|---|---|
| Swagger UI | http://localhost:8000/docs |
| Health check | http://localhost:8000/health |
| Flower (Celery monitor) | http://localhost:5555 |

---

## API

| Method | Endpoint | Description |
|---|---|---|
| POST | `/auth/register` | Create account |
| POST | `/auth/login` | Login → JWT token |
| POST | `/documents/` | Upload PDF (async, returns 202) |
| GET | `/documents/` | List documents + status |
| GET | `/documents/{id}` | Poll processing status |
| DELETE | `/documents/{id}` | Delete document + chunks |
| POST | `/chat/` | Ask question → SSE stream |

---

## Project Structure

```
ai-doc-chat/
├── rag_app/
│   ├── main.py            # app, middleware, /health
│   ├── config.py          # Pydantic Settings
│   ├── db.py              # async SQLAlchemy engine + session
│   ├── models.py          # User, Document, Chunk ORM models
│   ├── auth_service.py    # JWT + bcrypt
│   ├── celery_app.py      # Celery + process_document_task
│   ├── rag.py             # chunking, embedding, vector search, LLM stream
│   ├── pdf.py             # PDF text extraction
│   ├── limiter.py         # rate limiting (slowapi, per JWT user)
│   ├── logging_config.py  # structlog setup + RequestIDMiddleware
│   ├── migrations/        # Alembic migrations
│   └── routers/
│       ├── auth.py        # /auth endpoints
│       ├── documents.py   # /documents endpoints
│       └── chat.py        # /chat SSE endpoint
└── tests/
    └── test_core.py       # 22 unit tests: auth, chunking, PDF, embeddings, LLM
```

---

## Environment Variables

```env
# Database
DATABASE_URL=postgresql+psycopg://user:pass@db:5432/ragdb
DATABASE_URL_ASYNC=postgresql+asyncpg://user:pass@db:5432/ragdb

# Auth
JWT_SECRET=your-long-random-secret

# AI — Anthropic (chat) + Voyage AI (embeddings)
ANTHROPIC_API_KEY=sk-ant-...
VOYAGE_API_KEY=pa-...

# Celery / Redis
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/1
```

---

## Running Tests

```bash
pytest tests/ -v --cov=rag_app --cov-report=term-missing
```

22 tests covering: password hashing, JWT, chunking, PDF extraction, SHA-256 deduplication, Voyage AI batching, Claude streaming.
