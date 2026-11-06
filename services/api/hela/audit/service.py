"""Append-only audit writes. Hash chaining and immutability are enforced by the database (0001_foundation)."""
from __future__ import annotations

import json
from typing import Literal

from sqlalchemy import Connection, text

from ..core.errors import correlation_id

ActorType = Literal['user', 'admin', 'system']

# Never written into audit details, whatever a caller passes.
_FORBIDDEN_KEYS = {'phone', 'otp', 'otp_code', 'password', 'token', 'refresh_token', 'totp', 'secret', 'address'}


def record(con: Connection, *, actor_type: ActorType, actor_id: str, action: str, entity_type: str,
           entity_id: str, details: dict | None = None) -> None:
    clean = {k: v for k, v in (details or {}).items() if k.lower() not in _FORBIDDEN_KEYS}
    con.execute(text('''INSERT INTO audit_logs(actor_type, actor_id, action, entity_type, entity_id,
                                               details, correlation_id, prev_hash, row_hash)
                        VALUES (:at, :aid, :action, :et, :eid, CAST(:details AS jsonb), :cid, '', '')'''),
                {'at': actor_type, 'aid': actor_id, 'action': action, 'et': entity_type, 'eid': entity_id,
                 'details': json.dumps(clean, sort_keys=True, default=str), 'cid': correlation_id.get()})
