from slowapi import Limiter
from slowapi.util import get_remote_address
from jose import jwt, JWTError
from rag_app.config import settings


def _get_user_key(request) -> str:
    auth: str = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        token = auth.removeprefix('Bearer ').strip()
        try:
            payload = jwt.decode(
                token,
                settings.JWT_SECRET,
                algorithms=[settings.JWT_ALG],
                options={'verify_exp': False},
            )
            return f"user:{payload['sub']}"
        except (JWTError, KeyError):
            pass
    return get_remote_address(request)


limiter = Limiter(key_func=_get_user_key, default_limits=['200/minute'])