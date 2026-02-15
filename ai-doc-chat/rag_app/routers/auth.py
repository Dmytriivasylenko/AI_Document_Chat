from fastapi import APIRouter, HTTPException
from fastapi import Depends

from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr

from rag_app.db import get_conn
from rag_app.auth_service import hash_password, verify_password, create_access_token, get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


@router.post("/register")
def register(req: RegisterRequest):
    # bcrypt limit: 72 bytes
    if len(req.password.encode("utf-8")) > 72:
        raise HTTPException(status_code=400, detail="Password too long (max 72 bytes)")

    password_hash = hash_password(req.password)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id, email",
                    (req.email, password_hash)
                )
                row = cur.fetchone()
                conn.commit()  # token need to save
                return {"id": row[0], "email": row[1]}
    except Exception:
        raise HTTPException(status_code=400, detail="Email already registered")

from fastapi import Depends
from fastapi.security import OAuth2PasswordRequestForm


@router.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends()):
    email = form.username
    password = form.password

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, password_hash FROM users WHERE email=%s", (email,))
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    user_id, password_hash = row
    if not verify_password(password, password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token(user_id=user_id)
    return {"access_token": token, "token_type": "bearer"}
