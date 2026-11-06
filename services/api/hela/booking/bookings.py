"""Offers, atomic acceptance, the booking state machine, check-in PIN, and role-scoped booking views
(spec §7, §8.4, §12.3; FR-MAT-04..06, FR-BKG-01..07; INV-01, INV-02, INV-04, INV-06, INV-08; Q47–Q51)."""
from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from ..audit import service as audit
from ..authz.deps import UserPrincipal, current_user
from ..core import idempotency
from ..core.db import many, one, run, tx
from ..core.errors import ApiError
from ..core.jobs import enqueue
from ..core.services import Services, services
from ..notifications.outbox import notify
from .availability import provider as provider_persona
from .matching import hard_gates
from .requests import set_request_status

router = APIRouter(prefix='/v1', tags=['bookings'])

ACTIVE = ('accepted', 'en_route', 'arrived', 'in_progress', 'completed_pending_confirmation', 'disputed',
          'incident_hold', 'reassigning')
TERMINAL = ('completed', 'cancelled', 'cancelled_after_review')
ADDRESS_VISIBLE = ('accepted', 'en_route', 'arrived', 'in_progress')

# (from, to) -> roles allowed. 'provider_safety' is a provider cancellation that must carry a safety reason.
TRANSITIONS: dict[tuple[str, str], set[str]] = {
    ('accepted', 'en_route'): {'provider'},
    ('accepted', 'cancelled'): {'customer', 'admin', 'provider_safety'},
    ('accepted', 'incident_hold'): {'admin'},
    ('en_route', 'arrived'): {'provider'},
    ('en_route', 'cancelled'): {'customer', 'admin', 'provider_safety'},
    ('en_route', 'incident_hold'): {'admin'},
    ('arrived', 'in_progress'): {'provider'},            # requires the check-in PIN (or admin exception)
    ('arrived', 'cancelled'): {'admin', 'provider_safety'},
    ('arrived', 'incident_hold'): {'admin'},
    ('in_progress', 'completed_pending_confirmation'): {'provider'},
    ('in_progress', 'incident_hold'): {'admin'},
    ('completed_pending_confirmation', 'completed'): {'customer', 'admin'},
    ('completed_pending_confirmation', 'disputed'): {'customer', 'admin'},
    ('disputed', 'completed'): {'admin'},
    ('disputed', 'cancelled_after_review'): {'admin'},
    ('incident_hold', 'disputed'): {'admin'},
    ('incident_hold', 'cancelled_after_review'): {'admin'},
    ('reassigning', 'cancelled'): {'customer', 'admin'},
}
ADMIN_REASON_TARGETS = {'cancelled', 'cancelled_after_review', 'completed', 'incident_hold', 'disputed'}


class TransitionIn(BaseModel):
    target_status: Literal['en_route', 'arrived', 'in_progress', 'completed_pending_confirmation', 'completed',
                           'disputed', 'cancelled', 'incident_hold', 'cancelled_after_review']
    expected_version: int = Field(ge=1)
    reason: str = Field(default='', max_length=500)
    checkin_pin: str | None = Field(default=None, pattern=r'^[0-9]{4}$')
    safety_cancellation: bool = False


# --- offers ----------------------------------------------------------------------------------------------

@router.get('/offers/me')
def my_offers(user: UserPrincipal = Depends(provider_persona), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        rows = many(con, '''SELECT o.id, o.expires_at, o.distance_band, r.category_code, r.start_at, r.end_at,
                                   r.task_codes, q.total_minor, q.currency, a.area_label
                            FROM offers o JOIN service_requests r ON r.id = o.request_id
                            JOIN quotes q ON q.id = r.quote_id JOIN customer_addresses a ON a.id = r.address_id
                            WHERE o.provider_id = :p AND o.status = 'pending' AND o.expires_at > now()
                            ORDER BY o.expires_at''', p=user.user_id)
    # Pre-acceptance view (P05, FR-LOC-03): coarse area and distance band only; no address, names or notes.
    return [{**r, 'id': str(r['id']), 'seconds_remaining': int((r['expires_at'] - datetime.now(UTC)).total_seconds())}
            for r in rows]


def accept_offer(con: Connection, svc: Services, offer_id: str, provider_id: str) -> dict:
    """Spec §12.3. Lock order: request -> offer -> provider profile. Every hard gate is re-evaluated here."""
    s = svc.settings
    head = one(con, 'SELECT request_id FROM offers WHERE id = :id AND provider_id = :p', id=offer_id, p=provider_id)
    if not head:
        raise ApiError(404, 'NOT_FOUND', 'error.not_found')
    req = one(con, 'SELECT * FROM service_requests WHERE id = :id FOR UPDATE', id=head['request_id'])
    offer = one(con, 'SELECT * FROM offers WHERE id = :id FOR UPDATE', id=offer_id)
    one(con, 'SELECT user_id FROM provider_profiles WHERE user_id = :p FOR SHARE', p=provider_id)
    if offer['status'] == 'accepted':   # AT-020: a retry after a lost response returns the same booking
        prev = one(con, '''SELECT b.id FROM booking_assignments a JOIN bookings b ON b.id = a.booking_id
                           WHERE a.offer_id = :o''', o=offer_id)
        return {'booking_id': str(prev['id']), 'status': 'accepted', 'replayed': True}
    if offer['status'] != 'pending' or offer['expires_at'] <= datetime.now(UTC):
        raise ApiError(409, 'OFFER_NOT_ACTIVE', 'booking.offer_not_active', details={'status': offer['status']})
    if req['status'] != 'offering':
        raise ApiError(409, 'REQUEST_NOT_OFFERING', 'booking.offer_not_active', details={'status': req['status']})
    distance = one(con, '''SELECT ST_Distance(sa.base_point, a.match_point) / 1000.0 AS km,
                                  least(sa.radius_km, p.max_radius_km)::float AS radius
                           FROM provider_service_areas sa, customer_addresses a, category_policies p
                           WHERE sa.provider_id = :pr AND a.id = :addr AND p.id = :pol''',
                   pr=provider_id, addr=req['address_id'], pol=req['policy_id'])
    reasons, _ = hard_gates(con, s, req, provider_id, distance_km=distance and distance['km'],
                            radius_km=distance and distance['radius'])
    if reasons:
        # AT-003 / AT-010 / AT-011: eligibility changed after the offer was made.
        run(con, "UPDATE offers SET status = 'revoked', responded_at = now() WHERE id = :id", id=offer_id)
        enqueue(con, 'dispatch.request', {'request_id': str(req['id'])},
                dedupe_key=f"dispatch:{req['id']}:revoked:{offer_id}")
        raise CommitThenRaise(ApiError(409, 'PROVIDER_INELIGIBLE', 'booking.provider_ineligible',
                                       details={'reason_codes': sorted({r.split(':')[0] for r in reasons})}))
    busy_end = req['end_at'] + timedelta(minutes=s.travel_buffer_minutes)
    booking = one(con, 'SELECT * FROM bookings WHERE request_id = :r FOR UPDATE', r=req['id'])
    if booking is None:
        pin = f'{secrets.randbelow(10**4):04d}'
        booking = one(con, '''INSERT INTO bookings(request_id, status, pin_enc) VALUES (:r, 'accepted', :pin)
                              RETURNING *''', r=req['id'], pin=svc.cipher.encrypt(pin, context='bookings.pin'))
        q = one(con, 'SELECT total_minor, components FROM quotes WHERE id = :q', q=req['quote_id'])
        run(con, '''INSERT INTO settlements(booking_id, currency, agreed_amount_minor, price_components)
                    VALUES (:b, 'LKR', :amt, CAST(:c AS jsonb))''', b=booking['id'], amt=q['total_minor'],
            c=json.dumps(q['components']))
        run(con, 'INSERT INTO commission_receivables(booking_id, rate_bps, amount_minor) VALUES (:b, 0, 0)',
            b=booking['id'])
        booking_event(con, booking['id'], 'user', provider_id, None, 'accepted', 1, f'offer {offer_id}')
        if req['beneficiary_id']:
            notify(con, recipient_type='beneficiary', recipient_id=str(req['beneficiary_id']),
                   template_key='checkin.pin', dedupe_key=f"pin:{booking['id']}", params={'pin': pin})
    elif booking['status'] == 'reassigning':
        new_version = booking['version'] + 1
        run(con, "UPDATE bookings SET status = 'accepted', version = :v, updated_at = now() WHERE id = :id",
            v=new_version, id=booking['id'])
        booking_event(con, booking['id'], 'user', provider_id, 'reassigning', 'accepted', new_version,
                      f'reassigned via offer {offer_id}')
    else:
        raise ApiError(409, 'BOOKING_ALREADY_ASSIGNED', 'booking.already_assigned')
    # INV-01 / INV-02 are enforced by the database: unique live assignment + gist exclusion on time ranges.
    run(con, '''INSERT INTO booking_assignments(booking_id, provider_id, offer_id, busy)
                VALUES (:b, :p, :o, tstzrange(:s, :e, '[)'))''', b=booking['id'], p=provider_id, o=offer_id,
        s=req['start_at'], e=busy_end)
    run(con, "UPDATE offers SET status = 'accepted', responded_at = now() WHERE id = :id", id=offer_id)
    run(con, '''UPDATE offers SET status = 'revoked', responded_at = now()
                WHERE request_id = :r AND id <> :o AND status = 'pending' ''', r=req['id'], o=offer_id)
    set_request_status(con, req['id'], req['status'], 'accepted', 'user', provider_id)
    notify(con, recipient_type='user', recipient_id=str(req['customer_id']), template_key='request.accepted',
           dedupe_key=f'accepted:{offer_id}')
    audit.record(con, actor_type='user', actor_id=provider_id, action='offer.accepted', entity_type='offer',
                 entity_id=offer_id, details={'booking_id': booking['id'], 'request_id': req['id']})
    return {'booking_id': str(booking['id']), 'status': 'accepted', 'replayed': False}


class CommitThenRaise(Exception):
    """The side effects so far (revoking the stale offer, queueing redispatch) must commit before the
    caller sees the error; a plain exception would roll them back."""

    def __init__(self, error: ApiError):
        super().__init__(error.code)
        self.error = error


def _conflict_from_integrity(exc: IntegrityError) -> ApiError:
    code = getattr(exc.orig, 'sqlstate', None)
    if code in ('23P01', '23505'):   # exclusion / unique violation: someone else won, or provider is now busy
        return ApiError(409, 'ASSIGNMENT_CONFLICT', 'booking.assignment_conflict', retryable=False)
    raise exc


@router.post('/offers/{offer_id}/accept')
def accept(offer_id: UUID, idempotency_key: str | None = Header(None, alias='Idempotency-Key'),
           user: UserPrincipal = Depends(provider_persona), svc: Services = Depends(services)):
    key = idempotency.require_key(idempotency_key)
    scope, h = f'offer.accept:{offer_id}', idempotency.payload_hash({'offer_id': str(offer_id)})
    deferred: ApiError | None = None
    try:
        with tx(svc.engine) as con:
            prior = idempotency.replay(con, actor_type='user', actor_id=user.user_id, scope=scope, key=key,
                                       request_hash=h)
            if prior is not None:
                return prior
            try:
                result = accept_offer(con, svc, str(offer_id), user.user_id)
            except CommitThenRaise as late:
                deferred = late.error
            else:
                idempotency.remember(con, actor_type='user', actor_id=user.user_id, scope=scope, key=key,
                                     request_hash=h, response=result)
                return result
    except IntegrityError as exc:
        raise _conflict_from_integrity(exc) from None
    raise deferred


@router.post('/offers/{offer_id}/decline', status_code=204)
def decline(offer_id: UUID, user: UserPrincipal = Depends(provider_persona), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        o = one(con, 'SELECT * FROM offers WHERE id = :id AND provider_id = :p FOR UPDATE', id=offer_id, p=user.user_id)
        if not o:
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        if o['status'] != 'pending':
            raise ApiError(409, 'OFFER_NOT_ACTIVE', 'booking.offer_not_active')
        run(con, "UPDATE offers SET status = 'declined', responded_at = now() WHERE id = :id", id=offer_id)
        enqueue(con, 'dispatch.request', {'request_id': str(o['request_id'])},
                dedupe_key=f"dispatch:{o['request_id']}:declined:{offer_id}")
        audit.record(con, actor_type='user', actor_id=user.user_id, action='offer.declined', entity_type='offer',
                     entity_id=str(offer_id))


# --- bookings ----------------------------------------------------------------------------------------------

def booking_event(con, booking_id, actor_type, actor_id, old, new, version, reason=None) -> None:
    run(con, '''INSERT INTO booking_events(booking_id, actor_type, actor_id, from_status, to_status, version, reason)
                VALUES (:b, :t, :a, :o, :n, :v, :r)''', b=booking_id, t=actor_type, a=actor_id, o=old, n=new, v=version,
        r=reason or None)


def booking_context(con: Connection, booking_id, *, lock: bool = False) -> dict | None:
    return one(con, f'''SELECT b.*, r.customer_id, r.beneficiary_id, r.address_id, r.category_code, r.start_at, r.end_at,
                               r.task_codes, r.task_note_enc, r.id AS request_id, a.provider_id, a.id AS assignment_id
                        FROM bookings b JOIN service_requests r ON r.id = b.request_id
                        LEFT JOIN LATERAL (
                          -- Provider of record: the live assignment, or the last one ended by the booking itself
                          -- (completion/cancellation). A provider replaced by reassignment loses access.
                          SELECT id, provider_id FROM booking_assignments x
                          WHERE x.booking_id = b.id AND (x.active OR x.end_reason NOT LIKE 'reassigned%')
                          ORDER BY x.started_at DESC LIMIT 1) a ON true
                        WHERE b.id = :id {'FOR UPDATE OF b' if lock else ''}''', id=booking_id)


def role_for(b: dict, user: UserPrincipal) -> str | None:
    """Object-level authorization (SEC-03): only the booker and the *currently assigned* provider."""
    if str(b['customer_id']) == user.user_id:
        return 'customer'
    if b['provider_id'] is not None and str(b['provider_id']) == user.user_id:
        return 'provider'
    return None


def view(con: Connection, svc: Services, b: dict, role: str) -> dict:
    out = {'booking_id': str(b['id']), 'request_id': str(b['request_id']), 'status': b['status'],
           'version': b['version'], 'category_code': b['category_code'], 'start_at': b['start_at'],
           'end_at': b['end_at'], 'task_codes': b['task_codes'],
           'support': {'message_key': 'support.route', 'in_app_incident_path': '/v1/incidents'}}
    s = one(con, 'SELECT currency, agreed_amount_minor, status FROM settlements WHERE booking_id = :b', b=b['id'])
    out['price'] = {'currency': s['currency'], 'amount_minor': s['agreed_amount_minor'], 'basis': 'hourly_estimate',
                    'payment_status': s['status'], 'platform_collected_funds': False}
    if role == 'customer':
        if b['provider_id']:
            p = one(con, '''SELECT coalesce(u.display_name, 'Your provider') AS name FROM users u WHERE u.id = :p''',
                    p=b['provider_id'])
            out['provider'] = {'display_name': p['name'], 'approved_category': b['category_code']}
        if b['beneficiary_id'] is None and b['status'] in ('accepted', 'en_route', 'arrived'):
            out['checkin_pin'] = svc.cipher.decrypt(b['pin_enc'], context='bookings.pin')   # booker only (Q48)
    elif role == 'provider' and b['status'] in ADDRESS_VISIBLE:
        # INV-08: the exact address exists in a response only for the live assignment, and only while working.
        addr = one(con, 'SELECT address_enc, pin_enc FROM customer_addresses WHERE id = :a', a=b['address_id'])
        out['address'] = {'text': svc.cipher.decrypt(addr['address_enc'], context='customer_addresses.address'),
                          'pin': svc.cipher.decrypt(addr['pin_enc'], context='customer_addresses.pin')}
        if b['task_note_enc']:
            out['task_note'] = svc.cipher.decrypt(b['task_note_enc'], context='service_requests.task_note')
        if b['beneficiary_id']:
            ben = one(con, 'SELECT name_enc FROM beneficiaries WHERE id = :id', id=b['beneficiary_id'])
            out['service_recipient'] = svc.cipher.decrypt(ben['name_enc'], context='beneficiaries.name')
    return out


@router.get('/provider/bookings')
def provider_jobs(user: UserPrincipal = Depends(provider_persona), svc: Services = Depends(services)):
    """P06: jobs where this provider is the provider of record (never jobs taken away by reassignment)."""
    with tx(svc.engine) as con:
        rows = many(con, '''SELECT b.id, b.status, b.version, r.category_code, r.start_at, r.end_at, a.area_label
                            FROM booking_assignments x JOIN bookings b ON b.id = x.booking_id
                            JOIN service_requests r ON r.id = b.request_id
                            JOIN customer_addresses a ON a.id = r.address_id
                            WHERE x.provider_id = :p AND (x.active OR x.end_reason NOT LIKE 'reassigned%')
                            ORDER BY r.start_at DESC LIMIT 100''', p=user.user_id)
    return [{**r, 'id': str(r['id'])} for r in rows]


@router.get('/bookings/{booking_id}')
def get_booking(booking_id: UUID, user: UserPrincipal = Depends(current_user), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        b = booking_context(con, booking_id)
        role = role_for(b, user) if b else None
        if not role:
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')   # no existence leak (AT-012/AT-013)
        return view(con, svc, b, role)


@router.get('/bookings/{booking_id}/events')
def booking_events(booking_id: UUID, user: UserPrincipal = Depends(current_user), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        b = booking_context(con, booking_id)
        if not b or not role_for(b, user):
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        return many(con, '''SELECT from_status, to_status, version, actor_type, created_at FROM booking_events
                            WHERE booking_id = :b ORDER BY id''', b=booking_id)


def apply_transition(con: Connection, svc: Services, b: dict, *, role: str, actor_type: str, actor_id: str,
                     body: TransitionIn) -> dict:
    target = body.target_status
    effective_role = 'provider_safety' if role == 'provider' and target == 'cancelled' else role
    if b['version'] != body.expected_version:
        raise ApiError(409, 'VERSION_CONFLICT', 'booking.version_conflict', retryable=False,
                       details={'current_version': b['version']})
    if effective_role not in TRANSITIONS.get((b['status'], target), set()):
        raise ApiError(409, 'TRANSITION_NOT_ALLOWED', 'booking.transition_not_allowed',
                       details={'from': b['status'], 'to': target})
    if role == 'admin' and target in ADMIN_REASON_TARGETS and len(body.reason.strip()) < 5:
        raise ApiError(422, 'REASON_REQUIRED', 'booking.reason_required')
    if effective_role == 'provider_safety' and (not body.safety_cancellation or len(body.reason.strip()) < 5):
        raise ApiError(422, 'SAFETY_REASON_REQUIRED', 'booking.reason_required')
    if target == 'in_progress' and role == 'provider':
        _check_pin(con, svc, b, body.checkin_pin)
    version = b['version'] + 1
    run(con, '''UPDATE bookings SET status = :s, version = :v, updated_at = now(),
                completed_pending_since = CASE WHEN :s = 'completed_pending_confirmation' THEN now()
                                               ELSE completed_pending_since END
                WHERE id = :id AND version = :old''', s=target, v=version, id=b['id'], old=b['version'])
    booking_event(con, b['id'], actor_type, actor_id, b['status'], target, version, body.reason)
    if target in TERMINAL:   # frees the provider's calendar; the provider of record keeps read access
        run(con, '''UPDATE booking_assignments SET active = false, ended_at = now(), end_reason = :r
                    WHERE booking_id = :b AND active''', r=f'booking {target}', b=b['id'])
    if target == 'cancelled':
        req_status = one(con, 'SELECT status FROM service_requests WHERE id = :r FOR UPDATE', r=b['request_id'])['status']
        run(con, "UPDATE offers SET status = 'revoked', responded_at = now() WHERE request_id = :r AND status = 'pending'",
            r=b['request_id'])
        set_request_status(con, b['request_id'], req_status, 'cancelled', actor_type, actor_id, body.reason)
    if effective_role == 'provider_safety':
        # FR-BKG-07 / FR-SAF-05: a provider safety exit always opens a case for a human.
        from ..safety.incidents import open_incident
        open_incident(con, svc, booking_id=str(b['id']), reporter_type='user', reporter_id=actor_id, kind='no_entry',
                      severity='high', description=f'Provider cancelled for safety: {body.reason}')
    if target == 'completed_pending_confirmation':
        enqueue(con, 'completion.review', {'booking_id': str(b['id'])},
                run_at=datetime.now(UTC) + timedelta(hours=svc.settings.completion_review_hours),
                dedupe_key=f"completion-review:{b['id']}")
    recipients = {str(b['customer_id'])} | ({str(b['provider_id'])} if b['provider_id'] else set())
    for rid in recipients - {actor_id}:
        notify(con, recipient_type='user', recipient_id=rid,
               template_key='booking.cancelled' if target.startswith('cancelled') else 'booking.updated',
               dedupe_key=f"booking:{b['id']}:{version}:{rid}")
    audit.record(con, actor_type=actor_type, actor_id=actor_id, action='booking.transition', entity_type='booking',
                 entity_id=str(b['id']), details={'from': b['status'], 'to': target, 'version': version,
                                                  'reason': body.reason or None})
    return {'booking_id': str(b['id']), 'status': target, 'version': version}


def _check_pin(con: Connection, svc: Services, b: dict, pin: str | None) -> None:
    if b['pin_attempts'] >= svc.settings.pin_max_attempts:
        raise ApiError(423, 'CHECKIN_LOCKED', 'booking.checkin_locked')
    if pin is None or not secrets.compare_digest(pin, svc.cipher.decrypt(b['pin_enc'], context='bookings.pin')):
        # Commit the failed attempt even though the request fails (see transition route).
        raise _PinFailure()


class _PinFailure(Exception):
    pass


@router.post('/bookings/{booking_id}/transitions')
def transition(booking_id: UUID, body: TransitionIn, idempotency_key: str | None = Header(None, alias='Idempotency-Key'),
               user: UserPrincipal = Depends(current_user), svc: Services = Depends(services)):
    key = idempotency.require_key(idempotency_key)
    scope, h = f'booking.transition:{booking_id}', idempotency.payload_hash(body.model_dump())
    pin_failed = False
    with tx(svc.engine) as con:
        b = booking_context(con, booking_id, lock=True)
        role = role_for(b, user) if b else None
        if not role:
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        prior = idempotency.replay(con, actor_type='user', actor_id=user.user_id, scope=scope, key=key, request_hash=h)
        if prior is not None:
            return prior
        try:
            result = apply_transition(con, svc, b, role=role, actor_type='user', actor_id=user.user_id, body=body)
        except _PinFailure:
            pin_failed = True
            run(con, 'UPDATE bookings SET pin_attempts = pin_attempts + 1 WHERE id = :id', id=booking_id)
            audit.record(con, actor_type='user', actor_id=user.user_id, action='booking.checkin_failed',
                         entity_type='booking', entity_id=str(booking_id))
        if not pin_failed:
            idempotency.remember(con, actor_type='user', actor_id=user.user_id, scope=scope, key=key, request_hash=h,
                                 response=result)
            return result
    raise ApiError(422, 'CHECKIN_PIN_INVALID', 'booking.checkin_invalid')
