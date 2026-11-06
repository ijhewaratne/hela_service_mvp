"""Server-side sessions with rotating opaque refresh tokens and reuse detection (Q9)."""
from __future__ import annotations

from datetime import timedelta
from typing import Literal

from sqlalchemy import Connection

from ..audit import service as audit
from ..core.config import Settings
from ..core.db import one, run
from ..core.errors import ApiError
from ..security.crypto import new_opaque_token, sha256_hex
from ..security.tokens import issue_access_token

SubjectType = Literal['user', 'admin']
AUDIENCE = {'user': 'hela-app', 'admin': 'hela-admin'}


def _lifetime(settings: Settings, subject_type: SubjectType) -> timedelta:
    if subject_type == 'admin':
        return timedelta(hours=settings.admin_refresh_token_hours)
    return timedelta(days=settings.refresh_token_days)


def _token_pair(settings: Settings, subject_type: SubjectType, subject_id: str, session_id: str,
                refresh: str) -> dict:
    access = issue_access_token(secret=settings.jwt_secret, issuer=settings.jwt_issuer,
                                audience=AUDIENCE[subject_type], subject=subject_id,
                                session_id=session_id, minutes=settings.access_token_minutes)
    return {'access_token': access, 'token_type': 'Bearer', 'expires_in': settings.access_token_minutes * 60,
            'refresh_token': refresh, 'session_id': session_id}


def open_session(con: Connection, settings: Settings, *, subject_type: SubjectType, subject_id: str,
                 device_label: str | None) -> dict:
    life = _lifetime(settings, subject_type)
    session = one(con, '''INSERT INTO sessions(subject_type, subject_id, device_label, expires_at)
                          VALUES (:t, :s, :d, now() + :life) RETURNING id''',
                  t=subject_type, s=subject_id, d=device_label, life=life)
    sid = str(session['id'])
    refresh = new_opaque_token()
    run(con, '''INSERT INTO refresh_tokens(session_id, token_hash, expires_at)
                VALUES (:sid, :h, now() + :life)''', sid=sid, h=sha256_hex(refresh), life=life)
    return _token_pair(settings, subject_type, subject_id, sid, refresh)


def revoke_session(con: Connection, session_id: str, reason: str) -> bool:
    return run(con, '''UPDATE sessions SET revoked_at = now(), revoke_reason = :r
                       WHERE id = :sid AND revoked_at IS NULL''', sid=session_id, r=reason) > 0


def revoke_all(con: Connection, subject_type: SubjectType, subject_id: str, reason: str) -> int:
    return run(con, '''UPDATE sessions SET revoked_at = now(), revoke_reason = :r
                       WHERE subject_type = :t AND subject_id = :s AND revoked_at IS NULL''',
               t=subject_type, s=subject_id, r=reason)


def rotate_refresh(con: Connection, settings: Settings, refresh_token: str,
                   subject_type: SubjectType) -> tuple[dict | None, ApiError | None]:
    """Returns (tokens, None) or (None, error). Errors are returned, not raised, so a reuse-triggered
    revocation still commits before the caller raises."""
    row = one(con, '''SELECT rt.id, rt.rotated_at, rt.expires_at < now() AS expired, s.id AS session_id,
                             s.subject_id, s.subject_type, s.revoked_at, s.expires_at < now() AS session_expired
                      FROM refresh_tokens rt JOIN sessions s ON s.id = rt.session_id
                      WHERE rt.token_hash = :h FOR UPDATE OF rt, s''', h=sha256_hex(refresh_token))
    invalid = ApiError(401, 'REFRESH_INVALID', 'auth.refresh_invalid')
    if row is None or row['subject_type'] != subject_type:
        return None, invalid
    sid, subject = str(row['session_id']), str(row['subject_id'])
    if row['rotated_at'] is not None:
        # A token that was already exchanged is being replayed: assume theft and end the whole session.
        if revoke_session(con, sid, 'refresh_token_reuse'):
            audit.record(con, actor_type=subject_type, actor_id=subject, action='session.refresh_reuse_detected',
                         entity_type='session', entity_id=sid)
        return None, ApiError(401, 'REFRESH_REUSED', 'auth.refresh_reused')
    if row['revoked_at'] is not None or row['expired'] or row['session_expired']:
        return None, invalid
    state_sql = ("SELECT account_state = 'active' AS ok FROM users WHERE id = :id" if subject_type == 'user'
                 else "SELECT state = 'active' AS ok FROM admin_users WHERE id = :id")
    if not (one(con, state_sql, id=subject) or {}).get('ok'):
        return None, ApiError(403, 'ACCOUNT_INACTIVE', 'auth.account_inactive')
    run(con, 'UPDATE refresh_tokens SET rotated_at = now() WHERE id = :id', id=row['id'])
    new_refresh = new_opaque_token()
    run(con, '''INSERT INTO refresh_tokens(session_id, token_hash, expires_at)
                SELECT :sid, :h, expires_at FROM sessions WHERE id = :sid''', sid=sid, h=sha256_hex(new_refresh))
    run(con, 'UPDATE sessions SET last_seen_at = now() WHERE id = :sid', sid=sid)
    return _token_pair(settings, subject_type, subject, sid, new_refresh), None
