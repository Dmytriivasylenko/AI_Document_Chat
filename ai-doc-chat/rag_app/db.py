import psycopg
from rag_app.config import settings

def get_conn():
    return psycopg.connect(settings.DATABASE_URL)
