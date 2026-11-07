"""Operations console API (A02, A04, A05; FR-MAT-03, FR-MAT-07, FR-BKG-02 exception, §20.2, Q19, Q39, Q40, Q49, Q51).
Every intervention needs a scoped permission and a reason, and leaves a full event trail."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import Connection

from ..audit import service as audit
from ..authz.deps import AdminPrincipal, current_admin, require_step_up
from ..core.db import many, one, run, tx
from ..core.errors import ApiError
from ..core.jobs import enqueue
from ..core.schema import Input
from ..core.services import Services, services
from ..notifications.outbox import notify
from .bookings import TransitionIn, apply_transition, booking_context, booking_event
from .requests import decide_consent, set_request_status

router = APIRouter(prefix='/v1/admin', tags=['admin-operations'])


class Reason(Input):
    reason: str = Field(min_length=5, max_length=500)


class VersionedReason(Reason):
    expected_version: int = Field(ge=1)


class AdminTransitionIn(TransitionIn):
    reason: str = Field(min_length=5, max_length=500)


def dispatcher(admin: AdminPrincipal = Depends(current_admin)) -> AdminPrincipal:
    if 'dispatch.manage' not in admin.grants:
        raise ApiError(403, 'PERMISSION_DENIED', 'authz.permission_denied', details={'permission': 'dispatch.manage'})
    return admin


def _region_guard(admin: AdminPrincipal, region_id) -> None:
    admin.require('dispatch.manage', str(region_id))   # region-scoped operations staff see only their region


def _uuids(rows):
    return [{k: (str(v) if isinstance(v, UUID) else v) for k, v in r.items()} for r in rows]


@router.get('/operations/queue')
def operations_queue(admin: AdminPrincipal = Depends(dispatcher), svc: Services = Depends(services)):
    """A02: what needs a human now. Region-scoped admins only see their regions."""
    regions = admin.grants['dispatch.manage']
    scope = None if None in regions else [r for r in regions if r]
    with tx(svc.engine) as con:
        q = lambda sql, **p: _uuids(many(con, sql, scope=scope, **p))  # noqa: E731
        region_filter = '(CAST(:scope AS uuid[]) IS NULL OR r.region_id = ANY(CAST(:scope AS uuid[])))'
        return {
            'unfulfilled_or_expired': q(f'''SELECT r.id, r.status, r.category_code, r.start_at FROM service_requests r
                                           WHERE r.status IN ('unfulfilled','expired') AND r.start_at > now()
                                           AND {region_filter} ORDER BY r.start_at'''),
            'awaiting_beneficiary': q(f'''SELECT r.id, r.start_at FROM service_requests r
                                         WHERE r.status = 'awaiting_beneficiary' AND {region_filter}'''),
            'searching': q(f'''SELECT r.id, r.dispatch_round, r.start_at FROM service_requests r
                              WHERE r.status = 'offering' AND {region_filter} ORDER BY r.start_at'''),
            'bookings_needing_attention': q(f'''SELECT b.id, b.status, b.version, b.completed_pending_since, r.start_at
                                               FROM bookings b JOIN service_requests r ON r.id = b.request_id
                                               WHERE (b.status IN ('disputed','incident_hold','reassigning')
                                                  OR (b.status = 'completed_pending_confirmation'
                                                      AND b.completed_pending_since < now() - make_interval(hours => :h)))
                                               AND {region_filter} ORDER BY r.start_at''',
                                            h=svc.settings.completion_review_hours),
            'alerts': q('''SELECT id, kind, ref_type, ref_id, severity, created_at FROM ops_alerts WHERE status = 'open'
                           ORDER BY severity = 'urgent' DESC, created_at'''),
            'dead_jobs': q("SELECT id, kind, attempts, last_error, updated_at FROM jobs WHERE status = 'dead' "
                           "ORDER BY updated_at DESC LIMIT 20"),
        }


@router.get('/requests/{request_id}')
def request_inspector(request_id: UUID, admin: AdminPrincipal = Depends(dispatcher), svc: Services = Depends(services)):
    """A04/A05: state, eligibility explanation and history, without the household address or task note."""
    with tx(svc.engine) as con:
        r = one(con, '''SELECT id, region_id, category_code, status, start_at, end_at, dispatch_round, task_codes,
                               beneficiary_id IS NOT NULL AS for_beneficiary, created_at
                        FROM service_requests WHERE id = :id''', id=request_id)
        if not r:
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        _region_guard(admin, r['region_id'])
        runs = many(con, '''SELECT id, round, config_version, candidate_count, eligible_count, exclusions, ranked, outcome,
                                   created_at FROM matching_runs WHERE request_id = :r ORDER BY round''', r=request_id)
        offers = many(con, '''SELECT id, provider_id, status, distance_band, expires_at, responded_at FROM offers
                              WHERE request_id = :r ORDER BY created_at''', r=request_id)
        events = many(con, 'SELECT actor_type, from_status, to_status, reason, created_at FROM request_events '
                           'WHERE request_id = :r ORDER BY id', r=request_id)
    return {**_uuids([r])[0], 'matching_runs': _uuids(runs), 'offers': _uuids(offers), 'events': events}


@router.post('/requests/{request_id}/dispatch')
def retry_dispatch(request_id: UUID, body: Reason, admin: AdminPrincipal = Depends(dispatcher),
                   svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        r = one(con, 'SELECT * FROM service_requests WHERE id = :id FOR UPDATE', id=request_id)
        if not r:
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        _region_guard(admin, r['region_id'])
        if r['status'] not in ('unfulfilled', 'requested', 'offering'):
            raise ApiError(409, 'NOT_DISPATCHABLE', 'booking.transition_not_allowed', details={'status': r['status']})
        if r['start_at'] <= datetime.now(UTC):
            raise ApiError(409, 'REQUEST_IN_PAST', 'booking.transition_not_allowed')
        run(con, 'UPDATE service_requests SET dispatch_round = 0 WHERE id = :id', id=request_id)
        if r['status'] == 'unfulfilled':
            set_request_status(con, request_id, 'unfulfilled', 'requested', 'admin', admin.admin_id, body.reason)
        enqueue(con, 'dispatch.request', {'request_id': str(request_id)},
                dedupe_key=f'dispatch:{request_id}:admin:{datetime.now(UTC).isoformat()}')
        audit.record(con, actor_type='admin', actor_id=admin.admin_id, action='request.dispatch_retried',
                     entity_type='service_request', entity_id=str(request_id), details={'reason': body.reason})
    return {'request_id': str(request_id), 'dispatch_queued': True}


@router.post('/requests/{request_id}/beneficiary-consent')
def staff_consent(request_id: UUID, body: Reason, admin: AdminPrincipal = Depends(dispatcher),
                  svc: Services = Depends(services)):
    """Q19: staff-documented assisted consent (e.g. confirmed by phone with the beneficiary)."""
    with tx(svc.engine) as con:
        require_step_up(con, admin, 'consent.staff')
        c = one(con, '''SELECT c.*, r.region_id FROM beneficiary_consents c JOIN service_requests r ON r.id = c.request_id
                        WHERE c.request_id = :r FOR UPDATE OF c''', r=request_id)
        if not c or c['status'] != 'pending':
            raise ApiError(409, 'CONSENT_NOT_PENDING', 'request.consent_invalid')
        _region_guard(admin, c['region_id'])
        decide_consent(con, c, 'staff_confirmed', actor_type='admin', actor_id=admin.admin_id, staff_reason=body.reason)
    return {'request_id': str(request_id), 'consent': 'staff_confirmed'}


def _admin_booking(con: Connection, admin: AdminPrincipal, booking_id) -> dict:
    b = booking_context(con, booking_id, lock=True)
    if not b:
        raise ApiError(404, 'NOT_FOUND', 'error.not_found')
    region = one(con, 'SELECT region_id FROM service_requests WHERE id = :r', r=b['request_id'])['region_id']
    if not (admin.can('dispatch.manage', str(region)) or admin.can('incident.respond')):
        raise ApiError(403, 'PERMISSION_DENIED', 'authz.permission_denied')
    return b


@router.get('/bookings/{booking_id}')
def booking_inspector(booking_id: UUID, admin: AdminPrincipal = Depends(current_admin),
                      svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        b = _admin_booking(con, admin, booking_id)
        events = many(con, '''SELECT actor_type, actor_id, from_status, to_status, version, reason, created_at
                              FROM booking_events WHERE booking_id = :b ORDER BY id''', b=booking_id)
        assignments = many(con, '''SELECT provider_id, active, started_at, ended_at, end_reason FROM booking_assignments
                                   WHERE booking_id = :b ORDER BY started_at''', b=booking_id)
        settlement = one(con, 'SELECT status, agreed_amount_minor, resolution FROM settlements WHERE booking_id = :b',
                         b=booking_id)
    return {'booking_id': str(b['id']), 'status': b['status'], 'version': b['version'],
            'category_code': b['category_code'], 'start_at': b['start_at'], 'end_at': b['end_at'],
            'pin_attempts': b['pin_attempts'], 'events': events, 'assignments': _uuids(assignments),
            'settlement': settlement}   # redacted: no address, task note or contact details


@router.post('/bookings/{booking_id}/transitions')
def admin_transition(booking_id: UUID, body: AdminTransitionIn, admin: AdminPrincipal = Depends(current_admin),
                     svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        b = _admin_booking(con, admin, booking_id)
        return apply_transition(con, svc, b, role='admin', actor_type='admin', actor_id=admin.admin_id, body=body)


@router.post('/bookings/{booking_id}/checkin-exception')
def checkin_exception(booking_id: UUID, body: VersionedReason, admin: AdminPrincipal = Depends(dispatcher),
                      svc: Services = Depends(services)):
    """FR-BKG-02 documented staff-assisted exception (e.g. beneficiary has no phone). Step-up required."""
    with tx(svc.engine) as con:
        require_step_up(con, admin, 'checkin.override')
        b = _admin_booking(con, admin, booking_id)
        if b['status'] != 'arrived' or b['version'] != body.expected_version:
            raise ApiError(409, 'TRANSITION_NOT_ALLOWED', 'booking.transition_not_allowed',
                           details={'status': b['status'], 'version': b['version']})
        version = b['version'] + 1
        run(con, "UPDATE bookings SET status = 'in_progress', version = :v, updated_at = now() WHERE id = :id",
            v=version, id=booking_id)
        booking_event(con, booking_id, 'admin', admin.admin_id, 'arrived', 'in_progress', version,
                      f'check-in exception: {body.reason}')
        audit.record(con, actor_type='admin', actor_id=admin.admin_id, action='booking.checkin_exception',
                     entity_type='booking', entity_id=str(booking_id), details={'reason': body.reason})
    return {'booking_id': str(booking_id), 'status': 'in_progress', 'version': version}


@router.post('/bookings/{booking_id}/reassign')
def reassign(booking_id: UUID, body: VersionedReason, admin: AdminPrincipal = Depends(dispatcher),
             svc: Services = Depends(services)):
    """Q51: same booking id; the old assignment is closed with a reason before any new provider is offered the job.
    The new provider must accept an offer; nobody is assigned without accepting (§6.4)."""
    with tx(svc.engine) as con:
        b = _admin_booking(con, admin, booking_id)
        if b['status'] not in ('accepted', 'en_route') or b['version'] != body.expected_version:
            raise ApiError(409, 'TRANSITION_NOT_ALLOWED', 'booking.transition_not_allowed',
                           details={'status': b['status'], 'version': b['version']})
        old_provider = str(b['provider_id'])
        run(con, '''UPDATE booking_assignments SET active = false, ended_at = now(), end_reason = :r
                    WHERE booking_id = :b AND active''', r=f'reassigned: {body.reason}', b=booking_id)
        version = b['version'] + 1
        run(con, "UPDATE bookings SET status = 'reassigning', version = :v, updated_at = now() WHERE id = :id",
            v=version, id=booking_id)
        booking_event(con, booking_id, 'admin', admin.admin_id, b['status'], 'reassigning', version, body.reason)
        run(con, 'UPDATE service_requests SET dispatch_round = 0 WHERE id = :r', r=b['request_id'])
        set_request_status(con, b['request_id'], 'accepted', 'offering', 'admin', admin.admin_id, body.reason)
        enqueue(con, 'dispatch.request', {'request_id': str(b['request_id'])},
                dedupe_key=f'dispatch:{b["request_id"]}:reassign:{version}')
        notify(con, recipient_type='user', recipient_id=old_provider, template_key='booking.cancelled',
               dedupe_key=f'reassigned:{booking_id}:{version}')
        notify(con, recipient_type='user', recipient_id=str(b['customer_id']), template_key='booking.updated',
               dedupe_key=f'reassigning:{booking_id}:{version}')
        audit.record(con, actor_type='admin', actor_id=admin.admin_id, action='booking.reassign_started',
                     entity_type='booking', entity_id=str(booking_id),
                     details={'reason': body.reason, 'previous_provider': old_provider})
    return {'booking_id': str(booking_id), 'status': 'reassigning', 'version': version}


@router.post('/ops-alerts/{alert_id}/close')
def close_alert(alert_id: UUID, body: Reason, admin: AdminPrincipal = Depends(dispatcher),
                svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        if not run(con, "UPDATE ops_alerts SET status = 'closed', closed_at = now() WHERE id = :id AND status = 'open'",
                   id=alert_id):
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        audit.record(con, actor_type='admin', actor_id=admin.admin_id, action='ops_alert.closed', entity_type='ops_alert',
                     entity_id=str(alert_id), details={'reason': body.reason})
    return {'id': str(alert_id), 'status': 'closed'}


# --- suspension effects (called from verification.admin_routes.suspend_provider) --------------------------

def provider_suspended_effects(con: Connection, provider_id: str) -> dict:
    """Q39: revoke pending offers and block new assignments immediately; active jobs go to humans for triage
    instead of being cancelled automatically."""
    revoked = many(con, '''UPDATE offers SET status = 'revoked', responded_at = now()
                           WHERE provider_id = :p AND status = 'pending' RETURNING id, request_id''', p=provider_id)
    for o in revoked:
        enqueue(con, 'dispatch.request', {'request_id': str(o['request_id'])},
                dedupe_key=f"dispatch:{o['request_id']}:suspended:{o['id']}")
    active = many(con, '''SELECT a.booking_id FROM booking_assignments a JOIN bookings b ON b.id = a.booking_id
                          WHERE a.provider_id = :p AND a.active''', p=provider_id)
    for a in active:
        run(con, '''INSERT INTO ops_alerts(kind, ref_type, ref_id, severity)
                    VALUES ('provider_suspended_active_job', 'booking', :b, 'urgent') ON CONFLICT DO NOTHING''',
            b=a['booking_id'])
    return {'offers_revoked': len(revoked), 'active_jobs_flagged': len(active)}
