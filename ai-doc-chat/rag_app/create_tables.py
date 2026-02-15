from rag_app.db import get_conn

DDL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES users(id),
    filename TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'processing',
    file_bytes BYTEA,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chunks (
    id SERIAL PRIMARY KEY,
    document_id INT NOT NULL REFERENCES documents(id),
    text TEXT NOT NULL,
    embedding vector(1536) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
"""

def main():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
    print(" DB initialized (psycopg)")

if __name__ == "__main__":
    main()
