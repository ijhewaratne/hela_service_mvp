"""Privacy requests, own-data export, legal holds and the retention register (Q63, Q66, FR-ID-08, §15).

Not legal advice and not a statutory implementation: timing targets are UNAPPROVED placeholders and nothing is
deleted automatically. Deletion and rectification are manual, after a legal-hold check by a second person.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from ..audit import service as audit
from ..authz.deps import AdminPrincipal, UserPrincipal, current_admin, current_user
from ..core.db import many, one, run, tx
from ..core.errors import ApiError
from ..core.schema import Input
from ..core.services import Services, services

router = APIRouter(prefix='/v1', tags=['privacy'])

TARGET_DAYS = 30            # UNAPPROVED placeholder; PDPA timing to be confirmed by counsel (§15.1)
RECENT_SIGN_IN = timedelta(minutes=15)

HOLDABLE = ('user', 'booking', 'incident', 'provider', 'service_request')


class PrivacyRequestIn(Input):
    kind: Literal['access_export', 'correction', 'deletion', 'objection', 'other']
    details: str | None = Field(default=None, max_length=2000)


class CaseActionIn(Input):
    action: Literal['verify_identity', 'start', 'hold', 'fulfil', 'reject']
    note: str = Field(min_length=5, max_length=2000)


class LegalHoldIn(Input):
    entity_type: Literal['user', 'booking', 'incident', 'provider', 'service_request']
    entity_id: UUID
    reason: str = Field(min_length=10, max_length=1000)


class ReleaseIn(Input):
    reason: str = Field(min_length=10, max_length=1000)


def _event(con, svc, request_id, actor_type, actor_id, old, new, note=None) -> None:
    run(con, '''INSERT INTO privacy_request_events(request_id, actor_type, actor_id, from_status, to_status, note_enc)
                VALUES (:r, :t, :a, :o, :n, :note)''', r=request_id, t=actor_type, a=actor_id, o=old, n=new,
        note=svc.cipher.encrypt(note, context='privacy_request_events.note') if note else None)


# --- user side --------------------------------------------------------------------------------------------

@router.post('/me/privacy-requests', status_code=201)
def file_request(body: PrivacyRequestIn, user: UserPrincipal = Depends(current_user), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        row = one(con, '''INSERT INTO privacy_requests(user_id, kind, details_enc, due_at)
                          VALUES (:u, :k, :d, now() + make_interval(days => :days)) RETURNING id, due_at''',
                  u=user.user_id, k=body.kind, days=TARGET_DAYS,
                  d=svc.cipher.encrypt(body.details, context='privacy_requests.details') if body.details else None)
        _event(con, svc, row['id'], 'user', user.user_id, None, 'received')
        audit.record(con, actor_type='user', actor_id=user.user_id, action='privacy.request_filed',
                     entity_type='privacy_request', entity_id=str(row['id']), details={'kind': body.kind})
    return {'id': str(row['id']), 'kind': body.kind, 'status': 'received', 'target_response_by': row['due_at'],
            'target_status': 'UNAPPROVED'}


@router.get('/me/privacy-requests')
def my_requests(user: UserPrincipal = Depends(current_user), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        rows = many(con, '''SELECT id, kind, status, due_at, created_at, closed_at FROM privacy_requests
                            WHERE user_id = :u ORDER BY created_at DESC''', u=user.user_id)
    return [{**r, 'id': str(r['id'])} for r in rows]


def build_export(con, svc: Services, user_id: str) -> dict:
    """The user's own data only. Other people appear only as masked or display-level references, and incident
    narratives are included only for reports the user wrote themselves (Q66)."""
    dec = svc.cipher.decrypt
    u = one(con, '''SELECT id, phone_enc, display_name, preferred_language, account_state, created_at
                    FROM users WHERE id = :u''', u=user_id)
    out: dict = {
        'generated_at': datetime.now(UTC),
        'notice': 'Your data held by Hela Service (synthetic development system). Other people are shown masked.',
        'account': {'id': str(u['id']), 'phone': dec(u['phone_enc'], context='users.phone'),
                    'display_name': u['display_name'], 'preferred_language': u['preferred_language'],
                    'account_state': u['account_state'], 'created_at': u['created_at']},
        'personas': [r['persona'] for r in many(con, 'SELECT persona FROM user_personas WHERE user_id = :u', u=user_id)],
        'sessions': many(con, '''SELECT device_label, created_at, last_seen_at, revoked_at FROM sessions
                                 WHERE subject_type = 'user' AND subject_id = :u ORDER BY created_at''', u=user_id),
        'notices_accepted': many(con, '''SELECT n.code, n.version, n.language, a.processing_context, a.accepted_at
                                         FROM notice_acceptances a JOIN consent_notices n ON n.id = a.notice_id
                                         WHERE a.user_id = :u''', u=user_id),
        'addresses': [{'label': a['label'], 'address': dec(a['address_enc'], context='customer_addresses.address'),
                       'pin': dec(a['pin_enc'], context='customer_addresses.pin'), 'archived_at': a['archived_at']}
                      for a in many(con, 'SELECT * FROM customer_addresses WHERE owner_id = :u', u=user_id)],
        # Beneficiaries are other people: name the booker entered, masked phone only.
        'family_members': [{'name': dec(b['name_enc'], context='beneficiaries.name'), 'relationship': b['relationship'],
                            'phone_masked': f"*******{b['phone_last3']}"}
                           for b in many(con, 'SELECT * FROM beneficiaries WHERE owner_id = :u', u=user_id)],
        'requests_as_customer': many(con, '''SELECT r.id::text, r.category_code, r.status, r.start_at, r.end_at, r.task_codes,
                                                    b.status AS booking_status, s.agreed_amount_minor, s.status AS payment_status
                                             FROM service_requests r LEFT JOIN bookings b ON b.request_id = r.id
                                             LEFT JOIN settlements s ON s.booking_id = b.id
                                             WHERE r.customer_id = :u ORDER BY r.created_at''', u=user_id),
        'reviews_written': many(con, '''SELECT booking_id::text, rating, moderation_state, created_at FROM reviews
                                        WHERE customer_id = :u''', u=user_id),
        'incidents_reported': [{'kind': i['kind'], 'severity': i['severity'], 'status': i['status'],
                                'created_at': i['created_at'],
                                'your_description': dec(i['description_enc'], context='incidents.description')}
                               for i in many(con, '''SELECT * FROM incidents WHERE reporter_type = 'user'
                                                     AND reporter_id = :u''', u=user_id)],
        'notifications': many(con, '''SELECT template_key, status, created_at, sent_at FROM notifications
                                      WHERE recipient_type = 'user' AND recipient_id = :u ORDER BY created_at''', u=user_id),
        'privacy_requests': many(con, '''SELECT kind, status, created_at, closed_at FROM privacy_requests
                                         WHERE user_id = :u''', u=user_id),
    }
    p = one(con, 'SELECT * FROM provider_profiles WHERE user_id = :u', u=user_id)
    if p:
        out['provider'] = {
            'legal_name': dec(p['legal_name_enc'], context='provider_profiles.legal_name'),
            'date_of_birth': dec(p['date_of_birth_enc'], context='provider_profiles.dob'),
            'application_state': p['application_state'], 'account_state': p['account_state'],
            'identity_verified_at': p['identity_verified_at'], 'age_verified_at': p['age_verified_at'],
            # Document metadata only; the files themselves are released through a support case (identity re-check).
            'documents': many(con, '''SELECT evidence_type, media_type, size_bytes, scan_state, review_state,
                                             document_expires_on, next_review_due, uploaded_at
                                      FROM provider_evidence WHERE provider_id = :u''', u=user_id),
            'category_approvals': many(con, '''SELECT category_code, status, valid_from, valid_until, decision_reason
                                               FROM provider_category_approvals WHERE provider_id = :u''', u=user_id),
            'jobs': many(con, '''SELECT b.id::text AS booking_id, r.category_code, r.start_at, b.status
                                 FROM booking_assignments a JOIN bookings b ON b.id = a.booking_id
                                 JOIN service_requests r ON r.id = b.request_id WHERE a.provider_id = :u''', u=user_id),
        }
    return out


@router.get('/me/export')
def export_my_data(user: UserPrincipal = Depends(current_user), svc: Services = Depends(services)):
    """Own-data export (Q66). Requires a sign-in within the last 15 minutes as a lightweight identity re-check."""
    with tx(svc.engine) as con:
        s = one(con, 'SELECT created_at FROM sessions WHERE id = :s', s=user.session_id)
        if s['created_at'] < datetime.now(UTC) - RECENT_SIGN_IN:
            raise ApiError(403, 'RECENT_SIGN_IN_REQUIRED', 'privacy.recent_sign_in')
        data = build_export(con, svc, user.user_id)
        row = one(con, '''INSERT INTO privacy_requests(user_id, kind, status, due_at, closed_at)
                          VALUES (:u, 'access_export', 'fulfilled', now(), now()) RETURNING id''', u=user.user_id)
        _event(con, svc, row['id'], 'system', 'self-service-export', None, 'fulfilled')
        audit.record(con, actor_type='user', actor_id=user.user_id, action='privacy.self_export',
                     entity_type='privacy_request', entity_id=str(row['id']))
    return data


# --- staff side -------------------------------------------------------------------------------------------

def privacy_officer(admin: AdminPrincipal = Depends(current_admin)) -> AdminPrincipal:
    admin.require('privacy.handle')
    return admin


MOVES = {'verify_identity': ({'received'}, 'identity_verified'),
         'start': ({'identity_verified'}, 'in_progress'),
         'hold': ({'received', 'identity_verified', 'in_progress'}, 'on_legal_hold'),
         'fulfil': ({'in_progress'}, 'fulfilled'),
         'reject': ({'received', 'identity_verified', 'in_progress', 'on_legal_hold'}, 'rejected')}


@router.get('/admin/privacy-requests')
def privacy_queue(status: str | None = Query(default=None, pattern=r'^[a-z_]+$'),
                  admin: AdminPrincipal = Depends(privacy_officer), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        rows = many(con, '''SELECT id, user_id, kind, status, due_at, created_at, due_at < now() AND closed_at IS NULL AS overdue
                            FROM privacy_requests WHERE (CAST(:s AS text) IS NULL OR status = :s)
                            ORDER BY closed_at IS NOT NULL, due_at''', s=status)
    return [{**r, 'id': str(r['id']), 'user_id': str(r['user_id'])} for r in rows]


@router.get('/admin/privacy-requests/{request_id}')
def privacy_case(request_id: UUID, admin: AdminPrincipal = Depends(privacy_officer), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        r = one(con, 'SELECT * FROM privacy_requests WHERE id = :id', id=request_id)
        if not r:
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        events = many(con, '''SELECT actor_type, from_status, to_status, note_enc, created_at FROM privacy_request_events
                              WHERE request_id = :id ORDER BY id''', id=request_id)
        holds = many(con, '''SELECT id, entity_type, entity_id, reason, placed_at FROM legal_holds
                             WHERE released_at IS NULL AND entity_type = 'user' AND entity_id = :u''', u=str(r['user_id']))
        run(con, '''INSERT INTO sensitive_access_logs(admin_id, session_id, resource_type, resource_id, access_kind, purpose)
                    VALUES (:a, :s, 'privacy_request', :r, 'view', 'privacy request handling')''',
            a=admin.admin_id, s=admin.session_id, r=str(request_id))
    dec = lambda b, ctx: svc.cipher.decrypt(b, context=ctx) if b else None  # noqa: E731
    return {'id': str(r['id']), 'user_id': str(r['user_id']), 'kind': r['kind'], 'status': r['status'], 'due_at': r['due_at'],
            'details': dec(r['details_enc'], 'privacy_requests.details'),
            'legal_holds_on_user': [{**h, 'id': str(h['id'])} for h in holds],
            'events': [{'actor_type': e['actor_type'], 'from': e['from_status'], 'to': e['to_status'],
                        'note': dec(e['note_enc'], 'privacy_request_events.note'), 'at': e['created_at']} for e in events]}


@router.post('/admin/privacy-requests/{request_id}/actions')
def privacy_action(request_id: UUID, body: CaseActionIn, admin: AdminPrincipal = Depends(privacy_officer),
                   svc: Services = Depends(services)):
    allowed, target = MOVES[body.action]
    with tx(svc.engine) as con:
        r = one(con, 'SELECT * FROM privacy_requests WHERE id = :id FOR UPDATE', id=request_id)
        if not r:
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        if r['status'] not in allowed:
            raise ApiError(409, 'TRANSITION_NOT_ALLOWED', 'verification.transition_not_allowed',
                           details={'from': r['status'], 'action': body.action})
        if body.action == 'fulfil' and r['kind'] == 'deletion' and one(con, '''SELECT 1 AS x FROM legal_holds
                WHERE released_at IS NULL AND entity_type = 'user' AND entity_id = :u''', u=str(r['user_id'])):
            raise ApiError(409, 'LEGAL_HOLD_ACTIVE', 'privacy.legal_hold')
        run(con, '''UPDATE privacy_requests SET status = :s, handled_by = :a, updated_at = now(),
                    closed_at = CASE WHEN :s IN ('fulfilled','rejected') THEN now() ELSE NULL END WHERE id = :id''',
            s=target, a=admin.admin_id, id=request_id)
        _event(con, svc, request_id, 'admin', admin.admin_id, r['status'], target, body.note)
        audit.record(con, actor_type='admin', actor_id=admin.admin_id, action=f'privacy.{body.action}',
                     entity_type='privacy_request', entity_id=str(request_id), details={'kind': r['kind']})
    return {'id': str(request_id), 'status': target}


@router.post('/admin/legal-holds', status_code=201)
def place_hold(body: LegalHoldIn, admin: AdminPrincipal = Depends(privacy_officer), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        row = one(con, '''INSERT INTO legal_holds(entity_type, entity_id, reason, placed_by) VALUES (:t, :e, :r, :a)
                          ON CONFLICT DO NOTHING RETURNING id''', t=body.entity_type, e=str(body.entity_id), r=body.reason,
                  a=admin.admin_id)
        if not row:
            raise ApiError(409, 'HOLD_EXISTS', 'privacy.legal_hold')
        audit.record(con, actor_type='admin', actor_id=admin.admin_id, action='legal_hold.placed', entity_type=body.entity_type,
                     entity_id=str(body.entity_id), details={'reason': body.reason})
    return {'id': str(row['id'])}


@router.post('/admin/legal-holds/{hold_id}/release')
def release_hold(hold_id: UUID, body: ReleaseIn, admin: AdminPrincipal = Depends(privacy_officer),
                 svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        h = one(con, 'SELECT * FROM legal_holds WHERE id = :id AND released_at IS NULL FOR UPDATE', id=hold_id)
        if not h:
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        if str(h['placed_by']) == admin.admin_id:
            raise ApiError(403, 'SELF_RELEASE_FORBIDDEN', 'authz.permission_denied')   # two people (DB-enforced too)
        run(con, 'UPDATE legal_holds SET released_by = :a, released_at = now() WHERE id = :id', a=admin.admin_id, id=hold_id)
        audit.record(con, actor_type='admin', actor_id=admin.admin_id, action='legal_hold.released',
                     entity_type=h['entity_type'], entity_id=h['entity_id'], details={'reason': body.reason})
    return {'id': str(hold_id), 'released': True}


@router.get('/admin/retention')
def retention_register(admin: AdminPrincipal = Depends(privacy_officer), svc: Services = Depends(services)):
    """Dry run only: what *would* fall outside each period. Nothing here deletes data (DEC-09 pending)."""
    date_column = {'sessions': 'created_at', 'otp_challenges': 'created_at', 'provider_evidence': 'uploaded_at',
                   'provider_category_approvals': 'created_at', 'service_requests': 'created_at', 'bookings': 'created_at',
                   'incidents': 'created_at', 'notifications': 'created_at', 'settlements': 'updated_at',
                   'audit_logs': 'occurred_at'}
    out = []
    with tx(svc.engine) as con:
        for p in many(con, 'SELECT * FROM retention_policies ORDER BY data_category'):
            table, column = p['source_table'], date_column[p['source_table']]   # fixed allowlist, never user input
            total = one(con, f'SELECT count(*) AS n FROM {table}')['n']
            beyond = None
            if p['retention_days']:
                beyond = one(con, f'SELECT count(*) AS n FROM {table} WHERE {column} < now() - make_interval(days => :d)',
                             d=p['retention_days'])['n']
            out.append({**p, 'rows': total, 'would_exceed_period': beyond,
                        'action': 'none: period not decided' if not p['retention_days'] else
                                  'none: report only until APPROVED' if p['approval_status'] != 'APPROVED' else 'manual review'})
        holds = one(con, 'SELECT count(*) AS n FROM legal_holds WHERE released_at IS NULL')['n']
    return {'policies': out, 'active_legal_holds': holds, 'automatic_deletion': False}
