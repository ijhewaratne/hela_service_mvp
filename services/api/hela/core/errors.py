"""Common error envelope (spec §13.1). Internal causes go to logs/audit, never to the response."""
from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import Counter
from starlette.exceptions import HTTPException as StarletteHTTPException

from .messages import english

log = logging.getLogger('hela')
correlation_id: ContextVar[str] = ContextVar('correlation_id', default='-')


class ApiError(Exception):
    def __init__(self, status: int, code: str, message_key: str, *, retryable: bool = False,
                 details: dict | None = None):
        super().__init__(code)
        self.status, self.code, self.message_key = status, code, message_key
        self.retryable, self.details = retryable, details or {}


# Error responses by code, e.g. competing acceptances (BOOKING_ALREADY_ASSIGNED, ASSIGNMENT_CONFLICT) for §17 alerts.
API_ERRORS = Counter('hela_api_errors', 'Error responses by error code', ['code'])


def envelope(code: str, message_key: str, *, retryable=False, details=None) -> dict:
    API_ERRORS.labels(code=code).inc()
    return {'error': {'code': code, 'message_key': message_key, 'message': english(message_key),
                      'correlation_id': correlation_id.get(), 'retryable': retryable,
                      'details': details or {}}}


_STATUS_CODES = {400: ('BAD_REQUEST', 'error.bad_request'), 401: ('UNAUTHENTICATED', 'auth.required'),
                 403: ('FORBIDDEN', 'error.forbidden'), 404: ('NOT_FOUND', 'error.not_found'),
                 405: ('METHOD_NOT_ALLOWED', 'error.method_not_allowed'),
                 429: ('RATE_LIMITED', 'error.rate_limited')}


def install(app: FastAPI) -> None:
    @app.middleware('http')
    async def correlate(request: Request, call_next):
        incoming = request.headers.get('X-Correlation-ID', '')
        valid = 8 <= len(incoming) <= 64 and incoming.replace('-', '').isalnum()
        cid = incoming if valid else f'c_{uuid.uuid4().hex[:16]}'
        token = correlation_id.set(cid)
        try:
            response = await call_next(request)
        finally:
            correlation_id.reset(token)
        response.headers['X-Correlation-ID'] = cid
        return response

    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError):
        return JSONResponse(envelope(exc.code, exc.message_key, retryable=exc.retryable, details=exc.details),
                            status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        # Report field locations and error types only; never echo submitted values back.
        fields = [{'field': '.'.join(str(p) for p in e['loc'] if p != 'body'), 'type': e['type']}
                  for e in exc.errors()]
        return JSONResponse(envelope('VALIDATION_FAILED', 'error.validation', details={'fields': fields}),
                            status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        code, key = _STATUS_CODES.get(exc.status_code, ('HTTP_ERROR', 'error.unexpected'))
        return JSONResponse(envelope(code, key), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception):
        log.exception('unhandled error correlation_id=%s', correlation_id.get())
        return JSONResponse(envelope('INTERNAL_ERROR', 'error.unexpected', retryable=True), status_code=500)
