"""Append-only audit writes. Immutability is enforced by the database; hash-chaining happens in audit_seal()."""
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
    # No global lock here: rows are sealed into audit_chain afterwards, in commit order (migration 0005).
    con.execute(text('''INSERT INTO audit_logs(actor_type, actor_id, action, entity_type, entity_id,
                                               details, correlation_id)
                        VALUES (:at, :aid, :action, :et, :eid, CAST(:details AS jsonb), :cid)'''),
                {'at': actor_type, 'aid': actor_id, 'action': action, 'et': entity_type, 'eid': entity_id,
                 'details': json.dumps(clean, sort_keys=True, default=str), 'cid': correlation_id.get()})


def seal(con: Connection) -> int:
    """Append committed, unsealed rows to the hash chain. Safe to call from anywhere, any number of times."""
    return con.execute(text('SELECT audit_seal(1000) AS n')).scalar_one()
