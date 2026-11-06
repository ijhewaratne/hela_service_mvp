"""Administrator authentication: argon2id password + TOTP MFA, step-up, separate sessions (Q10, Q14)."""
from __future__ import annotations

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ..audit import service as audit
from ..authz.deps import AdminPrincipal, client_ip, current_admin
from ..core import ratelimit
from ..core.db import one, run, tx
from ..core.errors import ApiError
from ..core.services import Services, services
from ..security.crypto import new_opaque_token, sha256_hex
from ..security.passwords import matching_totp_step, verify_password
from . import sessions

router = APIRouter(prefix='/v1/admin/auth', tags=['admin-auth'])

StepUpAction = Literal['phone.change', 'evidence.read', 'role.grant', 'policy.change', 'category.approve',
                       'provider.reinstate']
MFA_MAX_ATTEMPTS = 5


class AdminLoginIn(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=1, max_length=256)


class MfaIn(BaseModel):
    mfa_token: str = Field(min_length=20, max_length=200)
    totp: str = Field(pattern=r'^[0-9]{6}$')
    device_label: str | None = Field(default=None, max_length=60)


class StepUpIn(BaseModel):
    action: StepUpAction
    totp: str = Field(pattern=r'^[0-9]{6}$')


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=200)


def consume_totp(con, svc: Services, admin_id: str, code: str) -> bool:
    """True only for a valid code whose time step has not been used before (replay protection)."""
    row = one(con, 'SELECT totp_secret_enc FROM admin_users WHERE id = :id', id=admin_id)
    if not row or row['totp_secret_enc'] is None:
        return False
    secret = svc.cipher.decrypt(row['totp_secret_enc'], context='admin_users.totp')
    step = matching_totp_step(secret, code)
    if step is None:
        return False
    return run(con, '''INSERT INTO admin_used_totp_steps(admin_id, time_step) VALUES (:a, :s)
                       ON CONFLICT DO NOTHING''', a=admin_id, s=step) == 1


@router.post('/login')
def admin_login(body: AdminLoginIn, request: Request, svc: Services = Depends(services)):
    ratelimit.hit(svc.redis, f'admin-login-ip:{client_ip(request)}', limit=20, window_seconds=900)
    failed = ApiError(401, 'ADMIN_LOGIN_FAILED', 'auth.admin_login_failed')
    error: ApiError | None = None
    with tx(svc.engine) as con:
        admin = one(con, '''SELECT id, password_hash, state, failed_logins, totp_enrolled_at FROM admin_users
                            WHERE username = :u FOR UPDATE''', u=body.username.lower())
        ok = verify_password(admin['password_hash'] if admin else None, body.password)
        if not admin:
            error = failed
        elif not ok:
            run(con, '''UPDATE admin_users SET failed_logins = failed_logins + 1,
                        state = CASE WHEN failed_logins + 1 >= :max AND state = 'active' THEN 'locked' ELSE state END
                        WHERE id = :id''', id=admin['id'], max=svc.settings.admin_max_failed_logins)
            audit.record(con, actor_type='admin', actor_id=str(admin['id']), action='admin.login_failed',
                         entity_type='admin_user', entity_id=str(admin['id']))
            error = failed
        elif admin['state'] != 'active':
            error = ApiError(403, 'ADMIN_LOCKED', 'auth.admin_locked')
        elif admin['totp_enrolled_at'] is None:
            # MFA enrolment happens out-of-band (scripts/create_admin.py); no password-only admin sessions.
            error = failed
        else:
            token = new_opaque_token()
            run(con, '''INSERT INTO admin_mfa_challenges(admin_id, token_hash, expires_at)
                        VALUES (:a, :h, now() + :ttl)''', a=admin['id'], h=sha256_hex(token),
                ttl=timedelta(seconds=svc.settings.admin_mfa_token_seconds))
    if error:
        raise error
    return {'mfa_required': True, 'mfa_token': token, 'expires_in': svc.settings.admin_mfa_token_seconds}


@router.post('/mfa')
def admin_mfa(body: MfaIn, svc: Services = Depends(services)):
    error: ApiError | None = None
    with tx(svc.engine) as con:
        ch = one(con, '''SELECT c.id, c.admin_id, c.attempts, c.consumed_at, c.expires_at < now() AS expired,
                                a.state FROM admin_mfa_challenges c JOIN admin_users a ON a.id = c.admin_id
                         WHERE c.token_hash = :h FOR UPDATE OF c''', h=sha256_hex(body.mfa_token))
        if not ch or ch['consumed_at'] or ch['expired'] or ch['attempts'] >= MFA_MAX_ATTEMPTS:
            error = ApiError(401, 'MFA_INVALID', 'auth.mfa_invalid')
        elif ch['state'] != 'active':
            error = ApiError(403, 'ADMIN_LOCKED', 'auth.admin_locked')
        elif not consume_totp(con, svc, str(ch['admin_id']), body.totp):
            run(con, 'UPDATE admin_mfa_challenges SET attempts = attempts + 1 WHERE id = :id', id=ch['id'])
            error = ApiError(401, 'MFA_INVALID', 'auth.mfa_invalid')
        else:
            aid = str(ch['admin_id'])
            run(con, 'UPDATE admin_mfa_challenges SET consumed_at = now() WHERE id = :id', id=ch['id'])
            run(con, 'UPDATE admin_users SET failed_logins = 0, last_login_at = now() WHERE id = :id', id=aid)
            tokens = sessions.open_session(con, svc.settings, subject_type='admin', subject_id=aid,
                                           device_label=body.device_label)
            audit.record(con, actor_type='admin', actor_id=aid, action='admin.session_opened',
                         entity_type='session', entity_id=tokens['session_id'])
    if error:
        raise error
    return tokens


@router.post('/refresh')
def admin_refresh(body: RefreshIn, svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        tokens, error = sessions.rotate_refresh(con, svc.settings, body.refresh_token, 'admin')
    if error:
        raise error
    return tokens


@router.post('/logout', status_code=204)
def admin_logout(admin: AdminPrincipal = Depends(current_admin), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        sessions.revoke_session(con, admin.session_id, 'logout')
        audit.record(con, actor_type='admin', actor_id=admin.admin_id, action='admin.logout',
                     entity_type='session', entity_id=admin.session_id)


@router.post('/step-up', status_code=201)
def step_up(body: StepUpIn, admin: AdminPrincipal = Depends(current_admin), svc: Services = Depends(services)):
    error: ApiError | None = None
    with tx(svc.engine) as con:
        if not consume_totp(con, svc, admin.admin_id, body.totp):
            audit.record(con, actor_type='admin', actor_id=admin.admin_id, action='admin.step_up_failed',
                         entity_type='session', entity_id=admin.session_id, details={'action': body.action})
            error = ApiError(401, 'MFA_INVALID', 'auth.mfa_invalid')
        else:
            run(con, '''INSERT INTO step_up_grants(session_id, admin_id, action, expires_at)
                        VALUES (:s, :a, :act, now() + :ttl)''', s=admin.session_id, a=admin.admin_id,
                act=body.action, ttl=timedelta(seconds=svc.settings.step_up_seconds))
            audit.record(con, actor_type='admin', actor_id=admin.admin_id, action='admin.step_up_granted',
                         entity_type='session', entity_id=admin.session_id, details={'action': body.action})
    if error:
        raise error
    return {'action': body.action, 'expires_in': svc.settings.step_up_seconds}


@router.get('/me')
def admin_me(admin: AdminPrincipal = Depends(current_admin), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        row = one(con, 'SELECT id, username, last_login_at FROM admin_users WHERE id = :id', id=admin.admin_id)
    return {'id': str(row['id']), 'username': row['username'], 'last_login_at': row['last_login_at'],
            'permissions': {p: sorted('*' if r is None else r for r in regions)
                            for p, regions in sorted(admin.grants.items())}}
