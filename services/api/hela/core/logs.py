"""Structured logs for central shipping (Q64, NFR-12).

One JSON object per line on stdout, which the platform's log collector ships (see infra/deploy/logging). Every
record carries the request's correlation id, so a support case can be followed from the API error envelope to the
logs without looking up anyone's phone number.

Personal data must never be logged in the first place; that is checked by tests that search every log line of
real flows. `RedactingFilter` is a second line of defence: it masks anything shaped like a phone number, an
e-mail address, a bearer or JSON Web Token, or a one-time code before a record is written. The collector applies
the same masks again (defence in depth for logs from other processes, e.g. uvicorn).
"""
from __future__ import annotations

import json
import logging
import re
import sys
import traceback
from datetime import UTC, datetime

from sqlalchemy.exc import DBAPIError

from .errors import correlation_id

_MASKS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]+'), '[jwt]'),
    (re.compile(r'(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}'), r'\1 [token]'),
    (re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'), '[email]'),
    (re.compile(r'(?<![\w-])\+?94\d{9}(?!\d)|(?<![\w-])0\d{9}(?!\d)|(?<![\w-])\+\d{10,14}(?!\d)'), '[phone]'),
    (re.compile(r'(?i)\b(code|otp|pin)(\W{1,3})\d{4,8}\b'), r'\1\2[code]'),
]

# Standard LogRecord attributes; anything else passed with `extra=` becomes a top-level JSON field.
_RESERVED = set(vars(logging.LogRecord('', 0, '', 0, '', None, None))) | {'message', 'asctime', 'taskName', 'color_message'}


def redact(text: str) -> str:
    for pattern, replacement in _MASKS:
        text = pattern.sub(replacement, text)
    return text


def safe_error(exc: BaseException) -> str:
    """A description of an error that is safe to log or store. Database servers repeat submitted values in their
    messages (e.g. 'value "..." is out of range'), so database errors keep only their type and SQLSTATE."""
    if isinstance(exc, DBAPIError):
        return f'{type(exc).__name__} (sqlstate {getattr(exc.orig, "sqlstate", None) or "unknown"})'
    return redact(f'{type(exc).__name__}: {exc}')[:300]


def _format_exception(exc_info) -> str:
    _etype, exc, tb = exc_info
    if isinstance(exc, DBAPIError):
        return 'Traceback (most recent call last):\n' + ''.join(traceback.format_tb(tb)) + safe_error(exc)
    return redact(logging.Formatter().formatException(exc_info))


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = _format_exception(record.exc_info)
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        record.exc_info = None          # the redacted text is used instead of re-formatting the exception
        # The access log is written outside the request context, so it passes the id explicitly.
        record.correlation_id = getattr(record, 'request_correlation_id', None) or correlation_id.get()
        return True


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        doc = {
            'ts': datetime.fromtimestamp(record.created, UTC).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
            'level': record.levelname.lower(),
            'logger': record.name,
            'service': self.service,
            'correlation_id': getattr(record, 'correlation_id', '-'),
            'message': record.getMessage(),
        }
        if record.exc_text:
            doc['exception'] = record.exc_text
        for key, value in vars(record).items():
            if key not in _RESERVED and key not in doc and key not in ('correlation_id', 'request_correlation_id'):
                doc[key] = value if isinstance(value, (int, float, bool)) or value is None else redact(str(value))
        return json.dumps(doc, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def __init__(self):
        super().__init__('%(asctime)s %(levelname)s %(name)s [%(correlation_id)s] %(message)s')


class _Stdout:
    """Resolves sys.stdout at write time, so a replaced stdout (test capture, reloaders) is honoured."""

    def write(self, data: str) -> int:
        return sys.stdout.write(data)

    def flush(self) -> None:
        sys.stdout.flush()


def configure_logging(settings, service: str) -> None:
    """Route the root logger and uvicorn's loggers through one redacting handler. Idempotent."""
    fmt = settings.log_format or ('text' if settings.env in ('dev', 'test') else 'json')
    handler = logging.StreamHandler(_Stdout())
    handler.addFilter(RedactingFilter())
    handler.setFormatter(JsonFormatter(service) if fmt == 'json' else TextFormatter())
    handler.set_name('hela')
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if h.get_name() != 'hela'] + [handler]
    for h in root.handlers:
        if h is not handler and not any(isinstance(f, RedactingFilter) for f in h.filters):
            h.addFilter(RedactingFilter())      # e.g. pytest's capture handler: it sees redacted text too
    root.setLevel(settings.log_level.upper())
    for name in ('uvicorn', 'uvicorn.error', 'uvicorn.access'):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    # uvicorn's access line has the client IP and raw path but no correlation id; hela.access replaces it.
    logging.getLogger('uvicorn.access').setLevel(logging.WARNING)
    # Never log SQL statements or their parameters, whatever a library's default.
    logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)
