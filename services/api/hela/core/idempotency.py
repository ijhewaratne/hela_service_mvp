"""Idempotent commands (FR-REQ-05, INV-05): same actor + scope + key + payload replays the stored response;
the same key with a different payload is a conflict."""
from __future__ import annotations

import hashlib
import json
import re

from sqlalchemy import Connection

from .db import one, run
from .errors import ApiError

_KEY = re.compile(r'^[A-Za-z0-9._:-]{8,80}$')


def require_key(key: str | None) -> str:
    if not key or not _KEY.fullmatch(key):
        raise ApiError(400, 'IDEMPOTENCY_KEY_REQUIRED', 'error.idempotency_key',
                       details={'header': 'Idempotency-Key', 'format': '8-80 of [A-Za-z0-9._:-]'})
    return key


def payload_hash(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def replay(con: Connection, *, actor_type: str, actor_id: str, scope: str, key: str, request_hash: str):
    """Returns the stored response for a replay, None for a new command; raises on a changed payload."""
    row = one(con, '''SELECT request_hash, response FROM idempotency_records
                      WHERE actor_type = :t AND actor_id = :a AND scope = :s AND key = :k''',
              t=actor_type, a=actor_id, s=scope, k=key)
    if row is None:
        return None
    if row['request_hash'] != request_hash:
        raise ApiError(409, 'IDEMPOTENCY_KEY_REUSED', 'error.idempotency_reused')
    return row['response']


def remember(con: Connection, *, actor_type: str, actor_id: str, scope: str, key: str, request_hash: str,
             response: dict, status_code: int = 200) -> None:
    run(con, '''INSERT INTO idempotency_records(actor_type, actor_id, scope, key, request_hash, status_code, response)
                VALUES (:t, :a, :s, :k, :h, :c, CAST(:r AS jsonb))''',
        t=actor_type, a=actor_id, s=scope, k=key, h=request_hash, c=status_code,
        r=json.dumps(response, sort_keys=True, default=str))
