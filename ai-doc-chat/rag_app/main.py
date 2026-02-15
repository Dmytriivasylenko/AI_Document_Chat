from fastapi import FastAPI

from rag_app.routers.auth import router as auth_router
from rag_app.routers.documents import router as documents_router
from rag_app.routers.chat import router as chat_router

app = FastAPI(title="AI Document Chat (RAG) - Postgres Only")

@app.get("/")
def root():
    return {"status": "ok"}

app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(chat_router)
