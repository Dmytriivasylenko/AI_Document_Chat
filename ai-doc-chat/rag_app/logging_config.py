import logging
import sys
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from rag_app.config import settings


def setup_logging() -> None:
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt='iso', utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.APP_ENV == 'production':
        processors = shared_processors + [
            structlog.processors.JSONRenderer(),
        ]
    else:
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.LOG_LEVEL)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format='%(message)s',
        stream=sys.stdout,
        level=settings.LOG_LEVEL,
    )


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get('X-Request-ID') or str(uuid.uuid4())

        user_id = None
        auth = request.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            try:
                from jose import jwt
                from rag_app.config import settings as _s
                payload = jwt.decode(
                    auth.removeprefix('Bearer ').strip(),
                    _s.JWT_SECRET,
                    algorithms=[_s.JWT_ALG],
                    options={'verify_exp': False},
                )
                user_id = payload.get('sub')
            except Exception:
                pass

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            user_id=user_id,
            method=request.method,
            path=request.url.path,
        )

        response = await call_next(request)
        structlog.contextvars.bind_contextvars(status_code=response.status_code)
        return response
