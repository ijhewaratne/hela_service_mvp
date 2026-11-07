"""Quotes and service requests (FR-REQ-01..06, FR-BEN-01..03, Q19, Q24–Q27, Q29–Q31).

Flow: POST /v1/quotes/preview -> POST /v1/quotes/{id}/accept -> POST /v1/requests (Idempotency-Key).
A request for someone else waits for the beneficiary's consent before any dispatch.
"""
from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from pydantic import Field, field_validator
from sqlalchemy import Connection

from ..audit import service as audit
from ..authz.deps import UserPrincipal
from ..core import idempotency
from ..core.config import Settings
from ..core.db import many, one, run, tx
from ..core.errors import ApiError
from ..core.jobs import enqueue
from ..core.schema import Input
from ..core.services import Services, services
from ..customers.routes import customer
from ..notifications.outbox import notify
from ..security.crypto import constant_time_equal
from ..verification.eligibility import active_policy

router = APIRouter(prefix='/v1', tags=['requests'])


class QuoteIn(Input):
    category_code: str = Field(pattern=r'^[a-z0-9_]{3,40}$')
    address_id: UUID
    start_at: datetime
    duration_minutes: int = Field(gt=0, le=24 * 60)

    @field_validator('start_at')
    @classmethod
    def aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError('start_at needs a timezone offset (UTC preferred)')
        return v


class QuoteAcceptIn(Input):
    policy_version: int = Field(ge=1)   # the terms version the customer saw


class RequestIn(Input):
    quote_id: UUID
    task_codes: list[str] = Field(min_length=1, max_length=20)
    task_note: str | None = Field(default=None, max_length=500)
    beneficiary_id: UUID | None = None


class ConsentResponseIn(Input):
    code: str = Field(pattern=r'^[0-9]{6}$')
    decision: Literal['confirm', 'decline']


class CancelIn(Input):
    reason: str = Field(min_length=3, max_length=300)


# --- shared validation ---------------------------------------------------------------------------------

def check_schedule(s: Settings, policy: dict, start: datetime, minutes: int) -> None:
    now = datetime.now(UTC)
    problems = []
    if start < now + timedelta(minutes=s.min_notice_minutes):
        problems.append('START_TOO_SOON')
    if start > now + timedelta(days=s.max_advance_days):
        problems.append('START_TOO_FAR_AHEAD')
    if minutes % s.duration_step_minutes:
        problems.append('DURATION_STEP')
    if not policy['min_duration_minutes'] <= minutes <= policy['max_duration_minutes']:
        problems.append('DURATION_OUT_OF_RANGE')
    if problems:
        raise ApiError(422, 'SCHEDULE_INVALID', 'request.schedule_invalid', details={'reason_codes': problems})


def price(rule: dict, minutes: int) -> tuple[list[dict], int]:
    labour = (rule['hourly_rate_minor'] * minutes + 30) // 60   # integer minor units, half-up
    components = [
        {'code': 'base_fee', 'amount_minor': rule['base_fee_minor']},
        {'code': 'labour', 'minutes': minutes, 'hourly_rate_minor': rule['hourly_rate_minor'], 'amount_minor': labour},
        {'code': 'travel_fee', 'amount_minor': rule['travel_fee_minor']},
    ]
    return components, sum(c['amount_minor'] for c in components)


def _owned_address(con: Connection, address_id, user_id: str) -> dict:
    addr = one(con, '''SELECT a.id, a.region_id, r.status AS region_status FROM customer_addresses a
                       JOIN service_regions r ON r.id = a.region_id
                       WHERE a.id = :id AND a.owner_id = :o AND a.archived_at IS NULL''', id=address_id, o=user_id)
    if not addr:
        raise ApiError(404, 'ADDRESS_NOT_FOUND', 'error.not_found')
    if addr['region_status'] != 'enabled':
        raise ApiError(422, 'UNSUPPORTED_LOCALITY', 'customer.unsupported_locality')
    return addr


def _bookable(con: Connection, category_code: str) -> tuple[dict, dict]:
    cat = one(con, 'SELECT status FROM service_categories WHERE code = :c', c=category_code)
    policy = active_policy(con, category_code)
    rule = one(con, "SELECT * FROM price_rules WHERE category_code = :c AND status = 'active'", c=category_code)
    if not cat or cat['status'] != 'enabled' or not policy or not rule:
        # INV-09: a disabled category cannot be quoted or requested through any interface.
        raise ApiError(422, 'CATEGORY_NOT_BOOKABLE', 'request.category_not_bookable')
    return policy, rule


def request_event(con, request_id, actor_type, actor_id, old, new, reason=None) -> None:
    run(con, '''INSERT INTO request_events(request_id, actor_type, actor_id, from_status, to_status, reason)
                VALUES (:r, :t, :a, :o, :n, :reason)''', r=request_id, t=actor_type, a=actor_id, o=old, n=new,
        reason=reason)


def set_request_status(con, request_id, old, new, actor_type, actor_id, reason=None) -> None:
    run(con, 'UPDATE service_requests SET status = :n, updated_at = now() WHERE id = :id', n=new, id=request_id)
    request_event(con, request_id, actor_type, actor_id, old, new, reason)


# --- quotes --------------------------------------------------------------------------------------------

@router.post('/quotes/preview', status_code=201)
def preview_quote(body: QuoteIn, user: UserPrincipal = Depends(customer), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        addr = _owned_address(con, body.address_id, user.user_id)
        policy, rule = _bookable(con, body.category_code)
        check_schedule(svc.settings, policy, body.start_at, body.duration_minutes)
        components, total = price(rule, body.duration_minutes)
        q = one(con, '''INSERT INTO quotes(customer_id, address_id, region_id, category_code, policy_id, price_rule_id,
                            start_at, duration_minutes, basis, currency, components, total_minor, expires_at)
                        VALUES (:c, :a, :r, :cat, :pol, :rule, :s, :m, :b, 'LKR', CAST(:comp AS jsonb), :t, now() + :ttl)
                        RETURNING id, expires_at''',
                c=user.user_id, a=body.address_id, r=addr['region_id'], cat=body.category_code, pol=policy['id'],
                rule=rule['id'], s=body.start_at, m=body.duration_minutes, b=rule['basis'],
                comp=json.dumps(components), t=total, ttl=timedelta(seconds=svc.settings.quote_ttl_seconds))
    return {'quote_id': str(q['id']), 'currency': 'LKR', 'basis': rule['basis'], 'components': components,
            'total_minor': total, 'expires_at': q['expires_at'], 'policy_version': policy['version'],
            'price_rule_version': rule['version'], 'price_approval_status': rule['approval_status'],
            'allowed_tasks': policy['allowed_tasks'], 'prohibited_tasks': policy['prohibited_tasks'],
            'cancellation_terms': policy['terms'].get('cancellation', {'fee_lkr_minor': 0}),
            'assumptions': ['Indicative estimate for the stated duration; no extra time is charged in R1.',
                            'Payment is made offline to the provider. Hela does not collect or hold money.']}


@router.post('/quotes/{quote_id}/accept')
def accept_quote(quote_id: UUID, body: QuoteAcceptIn, user: UserPrincipal = Depends(customer),
                 svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        q = one(con, '''SELECT q.*, p.version AS policy_version FROM quotes q JOIN category_policies p ON p.id = q.policy_id
                        WHERE q.id = :id AND q.customer_id = :c FOR UPDATE OF q''', id=quote_id, c=user.user_id)
        if not q:
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        if q['expires_at'] <= datetime.now(UTC):
            raise ApiError(409, 'QUOTE_EXPIRED', 'request.quote_expired')
        if body.policy_version != q['policy_version']:
            raise ApiError(409, 'TERMS_CHANGED', 'request.terms_changed')
        run(con, 'UPDATE quotes SET accepted_at = coalesce(accepted_at, now()) WHERE id = :id', id=quote_id)
        audit.record(con, actor_type='user', actor_id=user.user_id, action='quote.accepted', entity_type='quote',
                     entity_id=str(quote_id), details={'total_minor': q['total_minor'],
                                                       'policy_version': q['policy_version']})
    return {'quote_id': str(quote_id), 'accepted': True}


# --- requests ------------------------------------------------------------------------------------------

def _request_view(r: dict) -> dict:
    return {'request_id': str(r['id']), 'status': r['status'], 'category_code': r['category_code'],
            'start_at': r['start_at'], 'end_at': r['end_at'], 'created_at': r['created_at']}


@router.post('/requests', status_code=201)
def create_request(body: RequestIn, idempotency_key: str | None = Header(None, alias='Idempotency-Key'),
                   user: UserPrincipal = Depends(customer), svc: Services = Depends(services)):
    key = idempotency.require_key(idempotency_key)
    h = idempotency.payload_hash(body.model_dump(mode='json'))
    s = svc.settings
    with tx(svc.engine) as con:
        old = one(con, 'SELECT * FROM service_requests WHERE customer_id = :c AND idempotency_key = :k',
                  c=user.user_id, k=key)
        if old:
            if old['payload_hash'] != h:
                raise ApiError(409, 'IDEMPOTENCY_KEY_REUSED', 'error.idempotency_reused')
            return _request_view(old)
        q = one(con, 'SELECT * FROM quotes WHERE id = :id AND customer_id = :c FOR UPDATE', id=body.quote_id,
                c=user.user_id)
        if not q:
            raise ApiError(404, 'QUOTE_NOT_FOUND', 'error.not_found')
        if q['accepted_at'] is None:
            raise ApiError(409, 'QUOTE_NOT_ACCEPTED', 'request.quote_not_accepted')
        if q['used_by_request'] is not None:
            raise ApiError(409, 'QUOTE_ALREADY_USED', 'request.quote_used')
        if q['expires_at'] <= datetime.now(UTC):
            raise ApiError(409, 'QUOTE_EXPIRED', 'request.quote_expired')
        addr = _owned_address(con, q['address_id'], user.user_id)
        policy, rule = _bookable(con, q['category_code'])
        if policy['id'] != q['policy_id'] or rule['id'] != q['price_rule_id'] or addr['region_id'] != q['region_id']:
            raise ApiError(409, 'TERMS_CHANGED', 'request.terms_changed')   # FR-REQ-06: re-quote on any change
        check_schedule(s, policy, q['start_at'], q['duration_minutes'])
        allowed, prohibited = set(policy['allowed_tasks']), set(policy['prohibited_tasks'])
        bad = sorted(set(body.task_codes) - allowed)
        if bad or set(body.task_codes) & prohibited:
            raise ApiError(422, 'TASK_NOT_ALLOWED', 'request.task_not_allowed', details={'task_codes': bad})
        if body.beneficiary_id and not one(con, '''SELECT 1 AS ok FROM beneficiaries WHERE id = :b AND owner_id = :o
                                                    AND archived_at IS NULL''', b=body.beneficiary_id, o=user.user_id):
            raise ApiError(404, 'BENEFICIARY_NOT_FOUND', 'error.not_found')
        status = 'awaiting_beneficiary' if body.beneficiary_id else 'requested'
        end = q['start_at'] + timedelta(minutes=q['duration_minutes'])
        r = one(con, '''INSERT INTO service_requests(customer_id, address_id, region_id, category_code, policy_id,
                            quote_id, beneficiary_id, task_codes, task_note_enc, start_at, end_at, status,
                            idempotency_key, payload_hash)
                        VALUES (:c, :a, :reg, :cat, :pol, :q, :b, CAST(:t AS jsonb), :note, :s, :e, :st, :k, :h)
                        RETURNING *''',
                c=user.user_id, a=q['address_id'], reg=q['region_id'], cat=q['category_code'], pol=policy['id'],
                q=q['id'], b=body.beneficiary_id, t=json.dumps(sorted(body.task_codes)),
                note=svc.cipher.encrypt(body.task_note, context='service_requests.task_note') if body.task_note else None,
                s=q['start_at'], e=end, st=status, k=key, h=h)
        rid = str(r['id'])
        run(con, 'UPDATE quotes SET used_by_request = :r WHERE id = :q', r=rid, q=q['id'])
        request_event(con, rid, 'user', user.user_id, None, status)
        audit.record(con, actor_type='user', actor_id=user.user_id, action='request.created',
                     entity_type='service_request', entity_id=rid,
                     details={'category': q['category_code'], 'for_beneficiary': bool(body.beneficiary_id)})
        lead = timedelta(minutes=s.request_expiry_lead_minutes)
        enqueue(con, 'request.expire', {'request_id': rid}, run_at=q['start_at'] - lead, dedupe_key=f'expire:{rid}')
        if body.beneficiary_id:
            _start_consent(con, svc, rid, str(body.beneficiary_id), q['start_at'] - lead)
        else:
            enqueue(con, 'dispatch.request', {'request_id': rid}, dedupe_key=f'dispatch:{rid}:0')
    return _request_view(r)


def _start_consent(con: Connection, svc: Services, request_id: str, beneficiary_id: str, latest: datetime) -> None:
    code = f'{secrets.randbelow(10**6):06d}'
    expires = min(datetime.now(UTC) + timedelta(minutes=svc.settings.consent_ttl_minutes), latest)
    c = one(con, '''INSERT INTO beneficiary_consents(request_id, beneficiary_id, code_hash, expires_at)
                    VALUES (:r, :b, '', :e) RETURNING id''', r=request_id, b=beneficiary_id, e=expires)
    cid = str(c['id'])
    run(con, 'UPDATE beneficiary_consents SET code_hash = :h WHERE id = :id',
        h=svc.cipher.lookup_hash(f'{cid}:{code}', purpose='consent'), id=cid)
    notify(con, recipient_type='beneficiary', recipient_id=beneficiary_id, template_key='consent.request',
           dedupe_key=f'consent:{cid}', params={'consent_id': cid, 'code': code})
    enqueue(con, 'consent.expire', {'consent_id': cid}, run_at=expires, dedupe_key=f'consent-expire:{cid}')


@router.get('/requests')
def my_requests(user: UserPrincipal = Depends(customer), svc: Services = Depends(services)):
    """C12 history: the customer's own requests, newest first, with the booking if one exists."""
    with tx(svc.engine) as con:
        rows = many(con, '''SELECT r.*, b.id AS booking_id, b.status AS booking_status FROM service_requests r
                            LEFT JOIN bookings b ON b.request_id = r.id
                            WHERE r.customer_id = :c ORDER BY r.created_at DESC LIMIT 100''', c=user.user_id)
    return [{**_request_view(r), 'booking_id': str(r['booking_id']) if r['booking_id'] else None,
             'booking_status': r['booking_status']} for r in rows]


@router.get('/requests/{request_id}')
def get_request(request_id: UUID, user: UserPrincipal = Depends(customer), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        r = one(con, 'SELECT * FROM service_requests WHERE id = :id AND customer_id = :c', id=request_id, c=user.user_id)
        if not r:
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        booking = one(con, 'SELECT id, status FROM bookings WHERE request_id = :id', id=request_id)
        consent = one(con, 'SELECT status FROM beneficiary_consents WHERE request_id = :id', id=request_id)
    view = _request_view(r)
    # FR-REQ / NFR-08: "accepted" is only ever shown once a booking exists server-side.
    view['booking_id'] = str(booking['id']) if booking else None
    view['beneficiary_consent'] = consent['status'] if consent else None
    if r['status'] in ('unfulfilled', 'expired'):
        view['next_steps'] = ['change_schedule', 'cancel']   # no human follow-up promised unless staffed (§6.4)
    return view


@router.post('/requests/{request_id}/cancel')
def cancel_request(request_id: UUID, body: CancelIn, user: UserPrincipal = Depends(customer),
                   svc: Services = Depends(services)):
    """Cancel before acceptance. After acceptance, cancel the booking instead (state machine)."""
    with tx(svc.engine) as con:
        r = one(con, 'SELECT * FROM service_requests WHERE id = :id AND customer_id = :c FOR UPDATE',
                id=request_id, c=user.user_id)
        if not r:
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        if r['status'] not in ('requested', 'awaiting_beneficiary', 'offering', 'unfulfilled'):
            raise ApiError(409, 'REQUEST_NOT_CANCELLABLE', 'request.not_cancellable', details={'status': r['status']})
        run(con, "UPDATE offers SET status = 'revoked', responded_at = now() WHERE request_id = :r AND status = 'pending'",
            r=request_id)
        set_request_status(con, request_id, r['status'], 'cancelled', 'user', user.user_id, body.reason)
        audit.record(con, actor_type='user', actor_id=user.user_id, action='request.cancelled',
                     entity_type='service_request', entity_id=str(request_id))
    return {'request_id': str(request_id), 'status': 'cancelled'}


# --- beneficiary consent (no account; code sent by SMS) ------------------------------------------------

@router.post('/beneficiary-consents/{consent_id}/respond')
def respond_consent(consent_id: UUID, body: ConsentResponseIn, svc: Services = Depends(services)):
    error = None
    with tx(svc.engine) as con:
        c = one(con, 'SELECT * FROM beneficiary_consents WHERE id = :id FOR UPDATE', id=consent_id)
        if not c or c['status'] != 'pending' or c['expires_at'] <= datetime.now(UTC) or c['attempts'] >= 5:
            error = ApiError(400, 'CONSENT_INVALID', 'request.consent_invalid')
        elif not constant_time_equal(c['code_hash'], svc.cipher.lookup_hash(f'{consent_id}:{body.code}',
                                                                            purpose='consent')):
            run(con, 'UPDATE beneficiary_consents SET attempts = attempts + 1 WHERE id = :id', id=consent_id)
            error = ApiError(400, 'CONSENT_INVALID', 'request.consent_invalid')
        else:
            decide_consent(con, c, 'confirmed' if body.decision == 'confirm' else 'declined',
                           actor_type='system', actor_id='beneficiary')
    if error:
        raise error
    return {'status': 'confirmed' if body.decision == 'confirm' else 'declined'}


def decide_consent(con: Connection, c: dict, status: str, *, actor_type: str, actor_id: str,
                   staff_reason: str | None = None) -> None:
    run(con, '''UPDATE beneficiary_consents SET status = :s, decided_at = now(), staff_admin_id = :a, staff_reason = :r
                WHERE id = :id''', s=status, a=actor_id if actor_type == 'admin' else None, r=staff_reason, id=c['id'])
    r = one(con, 'SELECT status FROM service_requests WHERE id = :id FOR UPDATE', id=c['request_id'])
    if r['status'] != 'awaiting_beneficiary':
        return
    if status in ('confirmed', 'staff_confirmed'):
        set_request_status(con, c['request_id'], r['status'], 'requested', actor_type, actor_id, f'consent {status}')
        enqueue(con, 'dispatch.request', {'request_id': str(c['request_id'])},
                dedupe_key=f"dispatch:{c['request_id']}:0")
    else:
        # FR-BEN-03: a beneficiary refusal (or silence) closes the request safely; nothing is dispatched.
        set_request_status(con, c['request_id'], r['status'], 'cancelled', actor_type, actor_id, f'consent {status}')
    audit.record(con, actor_type=actor_type, actor_id=actor_id, action=f'consent.{status}',
                 entity_type='service_request', entity_id=str(c['request_id']), details={'reason': staff_reason})
