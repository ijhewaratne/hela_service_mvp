"""Authentication and authorization dependencies. Deny by default; never trust client-supplied role claims."""
from __future__ import annotations

from dataclasses import dataclass, field

import jwt
from fastapi import Depends, Header, Request
from sqlalchemy import Connection

from ..core.db import many, one, run, tx
from ..core.errors import ApiError
from ..core.services import Services, services
from ..security.keys import signing_keys
from ..security.tokens import decode_access_token


@dataclass
class UserPrincipal:
    user_id: str
    session_id: str
    personas: set[str]

    def require_persona(self, persona: str) -> None:
        if persona not in self.personas:
            raise ApiError(403, 'PERMISSION_DENIED', 'authz.permission_denied')


@dataclass
class AdminPrincipal:
    admin_id: str
    session_id: str
    # permission code -> region ids the grant covers; None in the set means "all regions"
    grants: dict[str, set[str | None]] = field(default_factory=dict)

    def can(self, permission: str, region_id: str | None = None) -> bool:
        regions = self.grants.get(permission)
        if not regions:
            return False
        return None in regions or (region_id is not None and region_id in regions)

    def require(self, permission: str, region_id: str | None = None) -> None:
        if not self.can(permission, region_id):
            raise ApiError(403, 'PERMISSION_DENIED', 'authz.permission_denied',
                           details={'permission': permission})


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith('Bearer '):
        raise ApiError(401, 'UNAUTHENTICATED', 'auth.required')
    return authorization.removeprefix('Bearer ').strip()


def _live_session(con: Connection, claims: dict, subject_type: str, idle_minutes: int | None = None) -> None:
    session = one(con, '''SELECT revoked_at IS NULL AND expires_at > now() AS live,
                                 last_seen_at < now() - interval '5 minutes' AS stale,
                                 CAST(:idle AS int) IS NOT NULL
                                   AND last_seen_at < now() - make_interval(mins => CAST(:idle AS int)) AS idle
                          FROM sessions WHERE id = :sid AND subject_type = :t AND subject_id = :sub''',
                  sid=claims['sid'], t=subject_type, sub=claims['sub'], idle=idle_minutes)
    if not session or not session['live']:
        raise ApiError(401, 'SESSION_REVOKED', 'auth.session_revoked')
    if session['idle']:
        # A01: admin sessions end after inactivity; activity is tracked at 5-minute granularity.
        raise ApiError(401, 'SESSION_IDLE_TIMEOUT', 'auth.session_idle')
    if session['stale']:
        run(con, 'UPDATE sessions SET last_seen_at = now() WHERE id = :sid', sid=claims['sid'])


def _claims(svc: Services, token: str, audience: str) -> dict:
    try:
        return decode_access_token(token, keys=signing_keys(svc.settings), issuer=svc.settings.jwt_issuer,
                                   audience=audience)
    except jwt.InvalidTokenError:
        raise ApiError(401, 'TOKEN_INVALID', 'auth.invalid_token') from None


def current_user(svc: Services = Depends(services),
                 authorization: str | None = Header(None)) -> UserPrincipal:
    claims = _claims(svc, _bearer(authorization), 'hela-app')
    with tx(svc.engine) as con:
        _live_session(con, claims, 'user')
        user = one(con, 'SELECT account_state FROM users WHERE id = :id', id=claims['sub'])
        if not user or user['account_state'] != 'active':
            raise ApiError(403, 'ACCOUNT_INACTIVE', 'auth.account_inactive')
        personas = {r['persona'] for r in many(con, '''SELECT persona FROM user_personas
                                                       WHERE user_id = :id AND state = 'active' ''',
                                               id=claims['sub'])}
    return UserPrincipal(user_id=claims['sub'], session_id=claims['sid'], personas=personas)


def load_grants(con: Connection, admin_id: str) -> dict[str, set[str | None]]:
    grants: dict[str, set[str | None]] = {}
    for r in many(con, '''SELECT rp.permission_code, a.region_id FROM admin_role_assignments a
                          JOIN admin_role_permissions rp ON rp.role_code = a.role_code
                          WHERE a.admin_id = :id AND a.revoked_at IS NULL''', id=admin_id):
        grants.setdefault(r['permission_code'], set()).add(str(r['region_id']) if r['region_id'] else None)
    return grants


def current_admin(svc: Services = Depends(services),
                  authorization: str | None = Header(None)) -> AdminPrincipal:
    claims = _claims(svc, _bearer(authorization), 'hela-admin')
    with tx(svc.engine) as con:
        _live_session(con, claims, 'admin', svc.settings.admin_idle_minutes)
        admin = one(con, 'SELECT state FROM admin_users WHERE id = :id', id=claims['sub'])
        if not admin or admin['state'] != 'active':
            raise ApiError(403, 'ACCOUNT_INACTIVE', 'auth.account_inactive')
        grants = load_grants(con, claims['sub'])
    return AdminPrincipal(admin_id=claims['sub'], session_id=claims['sid'], grants=grants)


def require_permission(permission: str):
    """Global permission check. Region-scoped checks call principal.require(permission, region_id)."""
    def dep(admin: AdminPrincipal = Depends(current_admin)) -> AdminPrincipal:
        if permission not in admin.grants:
            raise ApiError(403, 'PERMISSION_DENIED', 'authz.permission_denied', details={'permission': permission})
        return admin
    return dep


def require_step_up(con: Connection, admin: AdminPrincipal, action: str) -> None:
    """Call inside the action's own transaction so the grant check and the action are atomic."""
    grant = one(con, '''SELECT 1 AS ok FROM step_up_grants WHERE session_id = :sid AND admin_id = :aid
                        AND action = :action AND expires_at > now() LIMIT 1''',
                sid=admin.session_id, aid=admin.admin_id, action=action)
    if not grant:
        raise ApiError(403, 'STEP_UP_REQUIRED', 'auth.step_up_required', details={'action': action})


def client_ip(request: Request) -> str:
    return request.client.host if request.client else 'unknown'
