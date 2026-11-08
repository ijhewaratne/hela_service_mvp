"""Customer/provider identity: phone OTP, sessions, profile, notices (FR-ID-01..08)."""
from __future__ import annotations

import re
import secrets
from datetime import timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import Field, field_validator

from ..audit import service as audit
from ..authz.deps import UserPrincipal, client_ip, current_user
from ..core import ratelimit
from ..core.db import many, one, run, tx
from ..core.errors import ApiError
from ..core.schema import Input
from ..core.services import Services, services
from . import sessions

router = APIRouter(prefix='/v1', tags=['identity'])

E164 = re.compile(r'^\+[1-9][0-9]{7,14}$')


def normalize_phone(raw: str) -> str:
    phone = re.sub(r'[\s()-]', '', raw)
    if phone.startswith('00'):
        phone = '+' + phone[2:]
    if not E164.fullmatch(phone):
        raise ValueError('Phone must be in international format, e.g. +94771234567')
    return phone


class OtpRequestIn(Input):
    phone: str = Field(min_length=8, max_length=24)
    purpose: Literal['login'] = 'login'

    @field_validator('phone')
    @classmethod
    def _phone(cls, v: str) -> str:
        return normalize_phone(v)


class OtpVerifyIn(OtpRequestIn):
    challenge_id: UUID
    code: str = Field(pattern=r'^[0-9]{6}$')
    device_label: str | None = Field(default=None, max_length=60)


class RefreshIn(Input):
    refresh_token: str = Field(min_length=20, max_length=200)


class ProfilePatch(Input):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    preferred_language: Literal['si', 'ta', 'en'] | None = None


class NoticeAcceptIn(Input):
    processing_context: str = Field(min_length=3, max_length=80, pattern=r'^[a-z0-9_.:-]+$')


def _code_hash(svc: Services, challenge_id: str, code: str) -> str:
    return svc.cipher.lookup_hash(f'{challenge_id}:{code}', purpose='otp')


def _code_matches(svc: Services, stored: str, challenge_id: str, code: str) -> bool:
    return svc.cipher.lookup_matches(stored, f'{challenge_id}:{code}', purpose='otp')


@router.post('/auth/otp/request', status_code=202)
def request_otp(body: OtpRequestIn, request: Request, svc: Services = Depends(services)):
    s = svc.settings
    phone_lookup = svc.cipher.lookup_hash(body.phone, purpose='phone')
    candidates = svc.cipher.lookup_candidates(body.phone, purpose='phone')   # all ring keys, during key rotation
    ratelimit.hit(svc.redis, f'otp-ip:{client_ip(request)}', limit=s.otp_ip_limit_per_window,
                  window_seconds=s.otp_send_window_seconds)
    with tx(svc.engine) as con:
        # Serialize sends per phone so concurrent requests cannot exceed the cap.
        run(con, 'SELECT pg_advisory_xact_lock(hashtext(:k))', k=f'otp:{phone_lookup}')
        recent = one(con, '''SELECT count(*) AS n FROM otp_challenges
                             WHERE phone_lookup = ANY(:p) AND created_at > now() - :w''',
                     p=candidates, w=timedelta(seconds=s.otp_send_window_seconds))['n']
        if recent >= s.otp_max_sends_per_window:
            raise ApiError(429, 'RATE_LIMITED', 'error.rate_limited', retryable=True)
        code = f'{secrets.randbelow(10**6):06d}'
        challenge = one(con, '''INSERT INTO otp_challenges(phone_lookup, purpose, code_hash, max_attempts, expires_at)
                                VALUES (:p, :purpose, '', :max, now() + :ttl) RETURNING id''',
                        p=phone_lookup, purpose=body.purpose, max=s.otp_max_attempts,
                        ttl=timedelta(seconds=s.otp_ttl_seconds))
        cid = str(challenge['id'])
        run(con, 'UPDATE otp_challenges SET code_hash = :h WHERE id = :id', h=_code_hash(svc, cid, code), id=cid)
        try:
            svc.sms.send(con, phone_lookup=phone_lookup, phone_e164=body.phone,
                         body=f'Hela Service code: {code}. Valid {s.otp_ttl_seconds // 60} minutes. Never share it.')
        except Exception:  # noqa: BLE001 - any gateway failure: roll back the challenge, never claim it was sent
            raise ApiError(503, 'SMS_UNAVAILABLE', 'error.dependency_unavailable', retryable=True) from None
    # Same response whether or not an account exists for this number (anti-enumeration).
    return {'challenge_id': cid, 'expires_in': s.otp_ttl_seconds, 'message_key': 'auth.otp_sent'}


@router.post('/auth/otp/verify')
def verify_otp(body: OtpVerifyIn, svc: Services = Depends(services)):
    phone_lookup = svc.cipher.lookup_hash(body.phone, purpose='phone')
    candidates = svc.cipher.lookup_candidates(body.phone, purpose='phone')
    error: ApiError | None = None
    tokens = None
    with tx(svc.engine) as con:
        ch = one(con, '''SELECT id, code_hash, attempts, max_attempts, consumed_at,
                                expires_at < now() AS expired
                         FROM otp_challenges WHERE id = CAST(:id AS uuid) AND phone_lookup = ANY(:p) AND purpose = :purpose
                         FOR UPDATE''', id=str(body.challenge_id), p=candidates, purpose=body.purpose)
        if not ch or ch['consumed_at'] is not None or ch['expired']:
            error = ApiError(400, 'OTP_INVALID', 'auth.otp_invalid')
        elif ch['attempts'] >= ch['max_attempts']:
            error = ApiError(429, 'OTP_LOCKED', 'auth.otp_locked')
        elif not _code_matches(svc, ch['code_hash'], str(body.challenge_id), body.code):
            run(con, 'UPDATE otp_challenges SET attempts = attempts + 1 WHERE id = :id', id=ch['id'])
            if ch['attempts'] + 1 >= ch['max_attempts']:
                # AT-025: lockouts are recorded for abuse review (pseudonymous challenge id, never the phone).
                audit.record(con, actor_type='system', actor_id='otp', action='auth.otp_locked',
                             entity_type='otp_challenge', entity_id=str(ch['id']))
            error = ApiError(400, 'OTP_INVALID', 'auth.otp_invalid')
        else:
            run(con, 'UPDATE otp_challenges SET consumed_at = now() WHERE id = :id', id=ch['id'])
            # An existing account may still carry a hash under an older lookup key while keys rotate; find it
            # under any ring key (never create a second account) and move it to the active key.
            user = one(con, '''SELECT id, account_state, phone_lookup FROM users WHERE phone_lookup = ANY(:p)
                               ORDER BY created_at LIMIT 1 FOR UPDATE''', p=candidates)
            created = None
            if user is None:
                created = one(con, '''INSERT INTO users(phone_lookup, phone_enc, phone_last3)
                                      VALUES (:p, :enc, :last3) ON CONFLICT (phone_lookup) DO NOTHING RETURNING id''',
                              p=phone_lookup, enc=svc.cipher.encrypt(body.phone, context='users.phone'),
                              last3=body.phone[-3:])
                user = one(con, 'SELECT id, account_state, phone_lookup FROM users WHERE phone_lookup = :p',
                           p=phone_lookup)
            elif user['phone_lookup'] != phone_lookup:
                run(con, 'UPDATE users SET phone_lookup = :p, updated_at = now() WHERE id = :id',
                    p=phone_lookup, id=user['id'])
            uid = str(user['id'])
            if created:
                run(con, "INSERT INTO user_personas(user_id, persona) VALUES (:u, 'customer')", u=uid)
                audit.record(con, actor_type='user', actor_id=uid, action='user.registered',
                             entity_type='user', entity_id=uid)
            if user['account_state'] != 'active':
                error = ApiError(403, 'ACCOUNT_INACTIVE', 'auth.account_inactive')
            else:
                tokens = sessions.open_session(con, svc.settings, subject_type='user', subject_id=uid,
                                               device_label=body.device_label)
                audit.record(con, actor_type='user', actor_id=uid, action='session.opened',
                             entity_type='session', entity_id=tokens['session_id'])
    if error:
        raise error
    return tokens


@router.post('/auth/refresh')
def refresh(body: RefreshIn, svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        tokens, error = sessions.rotate_refresh(con, svc.settings, body.refresh_token, 'user')
    if error:
        raise error
    return tokens


@router.post('/auth/logout', status_code=204)
def logout(user: UserPrincipal = Depends(current_user), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        sessions.revoke_session(con, user.session_id, 'logout')
        audit.record(con, actor_type='user', actor_id=user.user_id, action='session.logout',
                     entity_type='session', entity_id=user.session_id)


@router.post('/auth/revoke', status_code=204)
def revoke(body: RefreshIn, svc: Services = Depends(services)):
    """Ends a session using only its refresh token, for a device that cannot get an access token (for example
    "sign in with a different number" while the saved session could not be checked). Always 204, so the
    response says nothing about whether the token was valid."""
    with tx(svc.engine) as con:
        ended = sessions.revoke_by_refresh_token(con, body.refresh_token, 'user', 'client_revoke')
        if ended:
            audit.record(con, actor_type='user', actor_id=ended[1], action='session.revoked_by_client',
                         entity_type='session', entity_id=ended[0])


@router.post('/auth/logout-all', status_code=200)
def logout_all(user: UserPrincipal = Depends(current_user), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        n = sessions.revoke_all(con, 'user', user.user_id, 'logout_all')
        audit.record(con, actor_type='user', actor_id=user.user_id, action='session.logout_all',
                     entity_type='user', entity_id=user.user_id, details={'revoked': n})
    return {'revoked_sessions': n}


def _me(con, user_id: str) -> dict:
    u = one(con, '''SELECT id, display_name, preferred_language, account_state, phone_last3, created_at
                    FROM users WHERE id = :id''', id=user_id)
    personas = [r['persona'] for r in many(con, 'SELECT persona FROM user_personas WHERE user_id = :id '
                                                'AND state = \'active\' ORDER BY persona', id=user_id)]
    return {'id': str(u['id']), 'display_name': u['display_name'], 'preferred_language': u['preferred_language'],
            'account_state': u['account_state'], 'phone_masked': f"*******{u['phone_last3']}",
            'personas': personas, 'created_at': u['created_at']}


@router.get('/me')
def get_me(user: UserPrincipal = Depends(current_user), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        return _me(con, user.user_id)


@router.patch('/me')
def patch_me(body: ProfilePatch, user: UserPrincipal = Depends(current_user), svc: Services = Depends(services)):
    changes = body.model_dump(exclude_none=True)
    with tx(svc.engine) as con:
        if changes:
            sets = ', '.join(f'{k} = :{k}' for k in changes)  # keys come from the fixed model, not the client
            run(con, f'UPDATE users SET {sets}, updated_at = now() WHERE id = :id', id=user.user_id, **changes)
            audit.record(con, actor_type='user', actor_id=user.user_id, action='user.profile_updated',
                         entity_type='user', entity_id=user.user_id, details={'fields': sorted(changes)})
        return _me(con, user.user_id)


@router.get('/me/sessions')
def my_sessions(user: UserPrincipal = Depends(current_user), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        rows = many(con, '''SELECT id, device_label, created_at, last_seen_at, expires_at FROM sessions
                            WHERE subject_type = 'user' AND subject_id = :u AND revoked_at IS NULL
                              AND expires_at > now() ORDER BY created_at DESC''', u=user.user_id)
    return [{**r, 'id': str(r['id']), 'current': str(r['id']) == user.session_id} for r in rows]


@router.delete('/me/sessions/{session_id}', status_code=204)
def end_session(session_id: str, user: UserPrincipal = Depends(current_user), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        owned = one(con, '''SELECT 1 AS ok FROM sessions WHERE id = CAST(:id AS uuid) AND subject_type = 'user'
                            AND subject_id = :u''', id=_uuid_or_404(session_id), u=user.user_id)
        if not owned:
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        sessions.revoke_session(con, session_id, 'user_ended')
        audit.record(con, actor_type='user', actor_id=user.user_id, action='session.ended',
                     entity_type='session', entity_id=session_id)


@router.get('/me/notifications')
def my_notifications(user: UserPrincipal = Depends(current_user), svc: Services = Depends(services)):
    """FR-NOT-03: what was sent and whether it is still pending. Bodies are template keys, not message text."""
    with tx(svc.engine) as con:
        return many(con, '''SELECT template_key, status, created_at, sent_at FROM notifications
                            WHERE recipient_type = 'user' AND recipient_id = :u ORDER BY created_at DESC LIMIT 50''',
                    u=user.user_id)


@router.get('/notices/current')
def current_notices(language: Literal['si', 'ta', 'en'] = 'en', svc: Services = Depends(services)):
    """Latest active version of each notice, in the requested language, falling back to English."""
    with tx(svc.engine) as con:
        rows = many(con, '''SELECT DISTINCT ON (code) id, code, version, language, title, body,
                                   approval_status, translation_status
                            FROM consent_notices WHERE status = 'active' AND language IN (:lang, 'en')
                            ORDER BY code, version DESC, (language = :lang) DESC''', lang=language)
    return [{**r, 'id': str(r['id'])} for r in rows]


@router.post('/notices/{notice_id}/accept', status_code=201)
def accept_notice(notice_id: str, body: NoticeAcceptIn, user: UserPrincipal = Depends(current_user),
                  svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        notice = one(con, '''SELECT id, code, version, language FROM consent_notices
                             WHERE id = CAST(:id AS uuid) AND status = 'active' ''', id=_uuid_or_404(notice_id))
        if not notice:
            raise ApiError(404, 'NOTICE_NOT_FOUND', 'notice.not_found')
        run(con, '''INSERT INTO notice_acceptances(user_id, notice_id, processing_context)
                    VALUES (:u, :n, :ctx) ON CONFLICT (user_id, notice_id) DO NOTHING''',
            u=user.user_id, n=notice['id'], ctx=body.processing_context)
        audit.record(con, actor_type='user', actor_id=user.user_id, action='notice.accepted',
                     entity_type='consent_notice', entity_id=str(notice['id']),
                     details={'code': notice['code'], 'version': notice['version'], 'language': notice['language'],
                              'processing_context': body.processing_context})
    return {'notice_id': str(notice['id']), 'accepted': True}


def _uuid_or_404(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        raise ApiError(404, 'NOT_FOUND', 'error.not_found') from None
