# AI Document Chat (RAG)

> Upload PDFs and chat with them. Powered by OpenAI embeddings, pgvector, and real-time streaming answers via SSE.

**Stack:** FastAPI · PostgreSQL + pgvector · Celery + Redis · OpenAI API · Docker Compose · Alembic · GitHub Actions

---

## How it works

```
PDF Upload → text extraction (pypdf) → OpenAI embeddings → pgvector storage
                                                                    ↓
User question → embed question → top-k vector search → GPT answer → SSE stream
```

1. User uploads a PDF — stored in Postgres, status = `processing`
2. Celery worker picks it up: extracts text → creates embeddings → saves chunks to pgvector
3. Document status → `ready`
4. User asks a question → answer streams back token by token via SSE

---

## Features

- **JWT authentication** — register/login, all endpoints protected
- **Async PDF processing** — Celery + Redis worker with retry logic and status lifecycle (`processing → ready → failed`)
- **RAG pipeline** — sentence-boundary chunking, batch OpenAI embeddings (100/request), L2 vector search via pgvector
- **SSE streaming** — real-time answer stream with source filename references in context
- **Alembic migrations** — async SQLAlchemy 2, no raw DDL
- **Health check** — `GET /health` checks DB connectivity (Docker/Kubernetes readiness probe)
- **Request logging middleware** — method, path, status, response time on every request
- **Flower UI** — Celery task monitoring dashboard
- **GitHub Actions CI** — ruff lint + pytest + Docker build on every push

---

## Tech Stack

| Layer | Technology |
|---|---|
| API | FastAPI + Uvicorn (async) |
| Database | PostgreSQL 16 + pgvector |
| ORM / Migrations | SQLAlchemy 2 async + Alembic |
| Background tasks | Celery 5 + Redis |
| AI | OpenAI `text-embedding-3-small` + `gpt-4o-mini` |
| Auth | JWT (python-jose) + bcrypt |
| Containerisation | Docker Compose — 5 services |
| CI | GitHub Actions |

---

## Quick Start

```bash
git clone https://github.com/Dmytriivasylenko/AI_Document_Chat.git
cd AI_Document_Chat

cp .env.example .env
# Fill in OPENAI_API_KEY and JWT_SECRET in .env

docker compose up --build
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
| `POST` | `/auth/register` | Create account |
| `POST` | `/auth/login` | Login → JWT token |
| `POST` | `/documents/` | Upload PDF (async, returns 202) |
| `GET` | `/documents/` | List documents + status |
| `GET` | `/documents/{id}` | Poll processing status |
| `DELETE` | `/documents/{id}` | Delete document + chunks |
| `POST` | `/chat/` | Ask question → SSE stream |

---

## Project Structure

```
rag_app/
├── main.py           # app, middleware, /health
├── config.py         # Pydantic Settings
├── db.py             # async SQLAlchemy engine + session
├── models.py         # User, Document, Chunk ORM models
├── auth_service.py   # JWT + bcrypt
├── celery_app.py     # Celery + process_document_task
├── rag.py            # chunking, embedding, vector search, LLM stream
├── pdf.py            # PDF text extraction
├── migrations/       # Alembic async migrations
└── routers/
    ├── auth.py       # /auth endpoints
    ├── documents.py  # /documents endpoints
    └── chat.py       # /chat SSE endpoint
tests/
└── test_core.py      # unit tests: auth, chunking, PDF
```

---

## Environment Variables

```env
DATABASE_URL=postgresql+psycopg://user:pass@db:5432/ragdb
DATABASE_URL_ASYNC=postgresql+asyncpg://user:pass@db:5432/ragdb
JWT_SECRET=your-secret
OPENAI_API_KEY=sk-...
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/1
```
