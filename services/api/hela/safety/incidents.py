"""Incidents, reviews and safety guidance (FR-SAF-01..05, FR-REV-01, §6.5, §14.4, Q50, Q56–Q59).

Receipt is automatic; judgement is human. Reporters see only their own case status. Narrative text is
encrypted and visible only to incident responders. Nothing here claims to be an emergency service.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy import Connection

from ..audit import service as audit
from ..authz.deps import AdminPrincipal, UserPrincipal, current_admin, current_user, require_permission
from ..core.db import many, one, run, tx
from ..core.errors import ApiError
from ..core.schema import Input
from ..core.services import Services, services

router = APIRouter(prefix='/v1', tags=['safety'])

# Q57: proposed first-response targets, UNAPPROVED until staffing and legal sign-off (DEC-10).
FIRST_RESPONSE = {'critical': timedelta(minutes=15), 'high': timedelta(hours=1), 'medium': timedelta(days=1),
                  'low': timedelta(days=3)}
PII_PATTERN = re.compile(r'(\+?\d[\d\s-]{7,}\d)|([\w.+-]+@[\w-]+\.[\w.]+)')

GUIDANCE = {
    'status': 'PENDING LOCAL VALIDATION',
    'message_key': 'safety.guidance_pending',
    'statement': 'Hela Service is not an emergency, medical or police service.',
    'if_in_danger': 'Contact local emergency services immediately. Verified numbers will be listed here once a '
                    'responsible local reviewer has confirmed them.',
    'emergency_contacts': [],          # intentionally empty until validated (Q58)
    'support_hours': None,             # DEC-02 / DEC-10 pending
}


class IncidentIn(Input):
    booking_id: UUID | None = None
    kind: Literal['safety', 'property_damage', 'harassment', 'service_mismatch', 'emergency', 'payment_dispute',
                  'no_entry', 'other']
    severity: Literal['low', 'medium', 'high', 'critical']
    description: str = Field(min_length=10, max_length=3000)


class IncidentActionIn(Input):
    action: Literal['acknowledge', 'assign_to_me', 'note', 'start_investigation', 'place_booking_on_hold',
                    'resolve', 'close']
    note: str = Field(min_length=5, max_length=2000)
    booking_version: int | None = Field(default=None, ge=1)


class ReviewIn(Input):
    rating: int = Field(ge=1, le=5)
    text: str | None = Field(default=None, max_length=1000)


class ModerateIn(Input):
    decision: Literal['approve', 'reject']
    reason: str = Field(min_length=5, max_length=300)


def open_incident(con: Connection, svc: Services, *, booking_id: str | None, reporter_type: str, reporter_id: str,
                  kind: str, severity: str, description: str) -> dict:
    urgent = severity in ('high', 'critical') or kind == 'emergency'
    due = datetime.now(UTC) + FIRST_RESPONSE[severity]
    row = one(con, '''INSERT INTO incidents(booking_id, reporter_type, reporter_id, kind, severity, description_enc,
                                            urgent, first_response_due)
                      VALUES (:b, :rt, :r, :k, :s, :d, :u, :due) RETURNING id, status''',
              b=booking_id, rt=reporter_type, r=reporter_id, k=kind, s=severity,
              d=svc.cipher.encrypt(description, context='incidents.description'), u=urgent, due=due)
    if urgent:
        # Q50: urgent escalation to a human; no automatic state change of the booking.
        run(con, '''INSERT INTO ops_alerts(kind, ref_type, ref_id, severity) VALUES ('urgent_incident', 'incident', :i,
                    'urgent') ON CONFLICT DO NOTHING''', i=row['id'])
    audit.record(con, actor_type=reporter_type, actor_id=reporter_id, action='incident.created',
                 entity_type='incident', entity_id=str(row['id']),
                 details={'kind': kind, 'severity': severity, 'booking_id': booking_id})
    return {'id': str(row['id']), 'status': 'open', 'urgent': urgent, 'automated_judgement': False,
            'guidance': GUIDANCE if urgent else None}


@router.get('/safety/guidance')
def safety_guidance():
    return GUIDANCE


@router.post('/incidents', status_code=201)
def report_incident(body: IncidentIn, user: UserPrincipal = Depends(current_user), svc: Services = Depends(services)):
    from ..booking.bookings import booking_context, role_for
    with tx(svc.engine) as con:
        if body.booking_id:
            b = booking_context(con, body.booking_id)
            if not b or not role_for(b, user):
                raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        # Reachable without payment, review or a completed booking (§14.4).
        return open_incident(con, svc, booking_id=str(body.booking_id) if body.booking_id else None,
                             reporter_type='user', reporter_id=user.user_id, kind=body.kind, severity=body.severity,
                             description=body.description)


@router.get('/incidents/{incident_id}')
def my_incident(incident_id: UUID, user: UserPrincipal = Depends(current_user), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        i = one(con, '''SELECT id, kind, severity, status, created_at, updated_at FROM incidents
                        WHERE id = :id AND reporter_type = 'user' AND reporter_id = :u''', id=incident_id, u=user.user_id)
    if not i:
        raise ApiError(404, 'NOT_FOUND', 'error.not_found')   # FR-SAF-04: only the reporter, nobody else
    return {**i, 'id': str(i['id'])}


def responder(admin: AdminPrincipal = Depends(current_admin)) -> AdminPrincipal:
    admin.require('incident.respond')
    return admin


@router.get('/admin/incidents')
def incident_queue(status: str | None = Query(default=None, pattern=r'^[a-z]+$'),
                   admin: AdminPrincipal = Depends(responder), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        rows = many(con, '''SELECT id, booking_id, kind, severity, status, urgent, assigned_to, first_response_due,
                                   first_response_at, created_at, (first_response_at IS NULL AND first_response_due < now())
                                   AS response_overdue
                            FROM incidents WHERE (CAST(:s AS text) IS NULL OR status = :s)
                            ORDER BY urgent DESC, first_response_due''', s=status)
    return [{k: (str(v) if isinstance(v, UUID) else v) for k, v in r.items()} for r in rows]


@router.get('/admin/incidents/{incident_id}')
def incident_case(incident_id: UUID, admin: AdminPrincipal = Depends(responder), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        i = one(con, 'SELECT * FROM incidents WHERE id = :id', id=incident_id)
        if not i:
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        actions = many(con, '''SELECT admin_id, action, note_enc, created_at FROM incident_actions WHERE incident_id = :i
                               ORDER BY id''', i=incident_id)
        run(con, '''INSERT INTO sensitive_access_logs(admin_id, session_id, resource_type, resource_id, access_kind,
                                                      purpose) VALUES (:a, :s, 'incident', :i, 'view', 'incident triage')''',
            a=admin.admin_id, s=admin.session_id, i=str(incident_id))
    dec = lambda b, ctx: svc.cipher.decrypt(b, context=ctx) if b else None  # noqa: E731
    return {'id': str(i['id']), 'booking_id': str(i['booking_id']) if i['booking_id'] else None, 'kind': i['kind'],
            'severity': i['severity'], 'status': i['status'], 'urgent': i['urgent'],
            'reporter_type': i['reporter_type'],  # reporter identity is not shown by default (FR-SAF-04)
            'description': dec(i['description_enc'], 'incidents.description'),
            'first_response_due': i['first_response_due'], 'first_response_at': i['first_response_at'],
            'actions': [{'admin_id': str(a['admin_id']) if a['admin_id'] else None, 'action': a['action'],
                         'note': dec(a['note_enc'], 'incident_actions.note'), 'created_at': a['created_at']}
                        for a in actions]}


INCIDENT_MOVES = {'acknowledge': ({'open'}, 'acknowledged'), 'start_investigation': ({'open', 'acknowledged'},
                  'investigating'), 'resolve': ({'acknowledged', 'investigating'}, 'resolved'),
                  'close': ({'resolved'}, 'closed')}


@router.post('/admin/incidents/{incident_id}/actions', status_code=201)
def incident_action(incident_id: UUID, body: IncidentActionIn, admin: AdminPrincipal = Depends(responder),
                    svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        i = one(con, 'SELECT * FROM incidents WHERE id = :id FOR UPDATE', id=incident_id)
        if not i:
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        status = i['status']
        if body.action in INCIDENT_MOVES:
            allowed, status = INCIDENT_MOVES[body.action]
            if i['status'] not in allowed:
                raise ApiError(409, 'TRANSITION_NOT_ALLOWED', 'verification.transition_not_allowed',
                               details={'from': i['status'], 'action': body.action})
        elif body.action == 'assign_to_me':
            run(con, 'UPDATE incidents SET assigned_to = :a WHERE id = :id', a=admin.admin_id, id=incident_id)
        elif body.action == 'place_booking_on_hold':
            _hold_booking(con, svc, admin, i, body)
        run(con, '''UPDATE incidents SET status = :s, updated_at = now(),
                    first_response_at = coalesce(first_response_at, now()) WHERE id = :id''', s=status, id=incident_id)
        run(con, '''INSERT INTO incident_actions(incident_id, admin_id, action, note_enc) VALUES (:i, :a, :act, :n)''',
            i=incident_id, a=admin.admin_id, act=body.action, n=svc.cipher.encrypt(body.note, context='incident_actions.note'))
        if status in ('resolved', 'closed'):
            run(con, "UPDATE ops_alerts SET status = 'closed', closed_at = now() WHERE ref_id = :i AND status = 'open'",
                i=incident_id)
        audit.record(con, actor_type='admin', actor_id=admin.admin_id, action=f'incident.{body.action}',
                     entity_type='incident', entity_id=str(incident_id), details={'status': status})
    return {'id': str(incident_id), 'status': status}


def _hold_booking(con: Connection, svc: Services, admin: AdminPrincipal, incident: dict, body: IncidentActionIn):
    from ..booking.bookings import TransitionIn, apply_transition, booking_context
    if not incident['booking_id'] or body.booking_version is None:
        raise ApiError(422, 'BOOKING_VERSION_REQUIRED', 'error.validation')
    b = booking_context(con, incident['booking_id'], lock=True)
    apply_transition(con, svc, b, role='admin', actor_type='admin', actor_id=admin.admin_id,
                     body=TransitionIn(target_status='incident_hold', expected_version=body.booking_version,
                                       reason=f'Incident {incident["id"]}: {body.note}'))


# --- reviews -------------------------------------------------------------------------------------------

@router.post('/bookings/{booking_id}/reviews', status_code=201)
def post_review(booking_id: UUID, body: ReviewIn, user: UserPrincipal = Depends(current_user),
                svc: Services = Depends(services)):
    from ..booking.bookings import booking_context, role_for
    with tx(svc.engine) as con:
        b = booking_context(con, booking_id)
        if not b or role_for(b, user) != 'customer':
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        if b['status'] != 'completed':
            raise ApiError(409, 'REVIEW_NOT_ALLOWED', 'review.not_allowed')   # verified completed work only
        text = (body.text or '').strip() or None
        row = one(con, '''INSERT INTO reviews(booking_id, customer_id, provider_id, rating, text_enc, moderation_state,
                                              pii_flag)
                          VALUES (:b, :c, :p, :r, :t, :m, :f) ON CONFLICT (booking_id) DO NOTHING RETURNING id''',
                  b=booking_id, c=user.user_id, p=b['provider_id'], r=body.rating,
                  t=svc.cipher.encrypt(text, context='reviews.text') if text else None,
                  m='pending' if text else 'no_text', f=bool(text and PII_PATTERN.search(text)))
        if not row:
            raise ApiError(409, 'REVIEW_EXISTS', 'review.exists')
    return {'id': str(row['id']), 'moderation_state': 'pending' if text else 'no_text'}


@router.get('/admin/reviews/pending')
def pending_reviews(admin: AdminPrincipal = Depends(require_permission('review.moderate')),
                    svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        rows = many(con, '''SELECT id, rating, text_enc, pii_flag, created_at FROM reviews
                            WHERE moderation_state = 'pending' ORDER BY pii_flag DESC, created_at''')
    return [{'id': str(r['id']), 'rating': r['rating'], 'pii_flag': r['pii_flag'], 'created_at': r['created_at'],
             'text': svc.cipher.decrypt(r['text_enc'], context='reviews.text')} for r in rows]


@router.post('/admin/reviews/{review_id}/moderate')
def moderate_review(review_id: UUID, body: ModerateIn,
                    admin: AdminPrincipal = Depends(require_permission('review.moderate')),
                    svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        n = run(con, '''UPDATE reviews SET moderation_state = :s, moderated_by = :a, moderated_at = now()
                        WHERE id = :id AND moderation_state = 'pending' ''',
                s='approved' if body.decision == 'approve' else 'rejected', a=admin.admin_id, id=review_id)
        if not n:
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        audit.record(con, actor_type='admin', actor_id=admin.admin_id, action=f'review.{body.decision}d',
                     entity_type='review', entity_id=str(review_id), details={'reason': body.reason})
    return {'id': str(review_id), 'moderation_state': 'approved' if body.decision == 'approve' else 'rejected'}
