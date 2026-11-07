"""Reviewer workflow: queue, evidence review, mediated evidence access, application and category decisions,
suspension (FR-VER-01..08, §20.1, Q13, Q14, Q35–Q39, AT-014). Every decision needs a reason and is recorded."""
from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import Field

from ..audit import service as audit
from ..authz.deps import AdminPrincipal, current_admin, require_step_up
from ..core.db import many, one, run, tx
from ..core.errors import ApiError
from ..core.schema import Input
from ..core.services import Services, services
from ..security.crypto import new_opaque_token, sha256_hex
from .eligibility import active_policy, provider_category_gaps
from .provider_routes import age_on, app_event

router = APIRouter(prefix='/v1/admin', tags=['admin-verification'])

EXT = {'application/pdf': 'pdf', 'image/jpeg': 'jpg', 'image/png': 'png'}


def reviewer(admin: AdminPrincipal = Depends(current_admin)) -> AdminPrincipal:
    admin.require('verification.review')  # global grant only: providers are not region-scoped yet
    return admin


def safety_actor(admin: AdminPrincipal = Depends(current_admin)) -> AdminPrincipal:
    if not (admin.can('verification.review') or admin.can('incident.respond')):
        raise ApiError(403, 'PERMISSION_DENIED', 'authz.permission_denied')
    return admin


class Reason(Input):
    reason: str = Field(min_length=5, max_length=1000)


class Purpose(Input):
    purpose: str = Field(min_length=5, max_length=300)


class EvidenceReviewIn(Reason):
    decision: Literal['accept', 'reject', 'needs_more_info']
    document_expires_on: date | None = None
    next_review_due: date | None = None


class ApplicationDecisionIn(Reason):
    decision: Literal['start_review', 'needs_more_info', 'approve', 'deny']
    identity_verified: bool = False
    age_verification_method: Literal['id_document_inspected', 'in_person_id_check'] | None = None


class ApprovalDecisionIn(Reason):
    decision: Literal['start_review', 'needs_more_info', 'approve', 'deny', 'revoke']
    probation_until: date | None = None
    probation_min_jobs: int | None = Field(default=None, ge=0, le=100)


APPLICATION_MOVES = {
    'start_review': ({'submitted'}, 'under_review'),
    'needs_more_info': ({'submitted', 'under_review'}, 'needs_more_info'),
    'approve': ({'under_review'}, 'approved'),
    'deny': ({'submitted', 'under_review'}, 'denied'),
}
APPROVAL_MOVES = {
    'start_review': ({'pending', 'needs_more_info', 'denied', 'revoked', 'expired'}, 'under_review'),
    'needs_more_info': ({'pending', 'under_review'}, 'needs_more_info'),
    'approve': ({'under_review'}, 'approved'),
    'deny': ({'pending', 'under_review', 'needs_more_info'}, 'denied'),
    'revoke': ({'approved'}, 'revoked'),
}
# Gaps that the decision itself resolves; everything else blocks approval.
_APPROVAL_STATUS_GAPS = ('CATEGORY_APPROVAL_',)


def _access_log(con, admin: AdminPrincipal, resource_type: str, resource_id: str, kind: str, purpose: str):
    run(con, '''INSERT INTO sensitive_access_logs(admin_id, session_id, resource_type, resource_id,
                                                  access_kind, purpose)
                VALUES (:a, :s, :t, :r, :k, :p)''', a=admin.admin_id, s=admin.session_id, t=resource_type,
        r=resource_id, k=kind, p=purpose)


# --- queues ------------------------------------------------------------------------------------------

@router.get('/verification-queue')
def verification_queue(admin: AdminPrincipal = Depends(reviewer), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        applications = many(con, '''SELECT user_id AS provider_id, application_state, submitted_at,
                                           round(extract(epoch FROM now() - submitted_at) / 3600) AS age_hours
                                    FROM provider_profiles
                                    WHERE application_state IN ('submitted','under_review','needs_more_info')
                                    ORDER BY submitted_at''')
        approvals = many(con, '''SELECT id, provider_id, category_code, status, created_at,
                                        round(extract(epoch FROM now() - created_at) / 3600) AS age_hours
                                 FROM provider_category_approvals
                                 WHERE status IN ('pending','under_review','needs_more_info') ORDER BY created_at''')
        evidence = many(con, '''SELECT id, provider_id, evidence_type, scan_state, uploaded_at FROM provider_evidence
                                WHERE review_state = 'pending' AND object_key IS NOT NULL ORDER BY uploaded_at''')
    fix = lambda rows: [{k: (str(v) if isinstance(v, UUID) else v) for k, v in r.items()} for r in rows]  # noqa: E731
    return {'applications': fix(applications), 'category_approvals': fix(approvals), 'evidence': fix(evidence)}


@router.get('/verification/expiring')
def expiring(days: int = Query(default=30, ge=1, le=180), admin: AdminPrincipal = Depends(reviewer),
             svc: Services = Depends(services)):
    """Renewal reminders (FR-VER-07). Listing only; nothing is ever auto-approved."""
    with tx(svc.engine) as con:
        evidence = many(con, '''SELECT id, provider_id, evidence_type, document_expires_on, next_review_due
                                FROM provider_evidence WHERE review_state = 'accepted'
                                  AND (document_expires_on <= current_date + :d OR next_review_due <= current_date + :d)
                                ORDER BY least(document_expires_on, next_review_due)''', d=days)
        approvals = many(con, '''SELECT id, provider_id, category_code, valid_until FROM provider_category_approvals
                                 WHERE status = 'approved' AND valid_until <= now() + make_interval(days => :d)
                                 ORDER BY valid_until''', d=days)
    fix = lambda rows: [{k: (str(v) if isinstance(v, UUID) else v) for k, v in r.items()} for r in rows]  # noqa: E731
    return {'evidence': fix(evidence), 'approvals': fix(approvals)}


@router.get('/providers/{provider_id}')
def provider_case(provider_id: UUID, admin: AdminPrincipal = Depends(reviewer), svc: Services = Depends(services)):
    """Case summary without identity details; those need /identity-view with step-up and a purpose."""
    pid = str(provider_id)
    with tx(svc.engine) as con:
        p = one(con, '''SELECT application_state, account_state, submitted_at, identity_verified_at,
                               age_verified_at, age_verification_method, suspended_reason
                        FROM provider_profiles WHERE user_id = :p''', p=pid)
        if not p:
            raise ApiError(404, 'PROVIDER_NOT_FOUND', 'provider.not_found')
        evidence = many(con, '''SELECT id, evidence_type, category_code, media_type, size_bytes, sha256, scan_state,
                                       review_state, document_expires_on, next_review_due, reviewed_at, review_reason,
                                       uploaded_at
                                FROM provider_evidence WHERE provider_id = :p ORDER BY uploaded_at''',
                        p=pid)
        approvals = many(con, '''SELECT a.id, a.category_code, a.status, a.valid_from, a.valid_until,
                                        a.probation_until, a.probation_min_jobs, a.decision_reason,
                                        p.version AS policy_version
                                 FROM provider_category_approvals a JOIN category_policies p ON p.id = a.policy_id
                                 WHERE a.provider_id = :p ORDER BY a.category_code''', p=pid)
        for a in approvals:
            a['id'] = str(a['id'])
            a['eligibility_gaps'] = provider_category_gaps(con, pid, a['category_code'])
        history = many(con, '''SELECT actor_type, from_state, to_state, reason, created_at
                               FROM provider_application_events WHERE provider_id = :p ORDER BY id''', p=pid)
    return {'provider_id': pid, **p, 'evidence': [{**e, 'id': str(e['id'])} for e in evidence],
            'approvals': approvals, 'application_history': history}


@router.post('/providers/{provider_id}/identity-view')
def identity_view(provider_id: UUID, body: Purpose, admin: AdminPrincipal = Depends(reviewer),
                  svc: Services = Depends(services)):
    pid = str(provider_id)
    with tx(svc.engine) as con:
        require_step_up(con, admin, 'evidence.read')
        p = one(con, 'SELECT legal_name_enc, date_of_birth_enc FROM provider_profiles WHERE user_id = :p', p=pid)
        if not p:
            raise ApiError(404, 'PROVIDER_NOT_FOUND', 'provider.not_found')
        _access_log(con, admin, 'provider_identity', pid, 'view', body.purpose)
        audit.record(con, actor_type='admin', actor_id=admin.admin_id, action='provider.identity_viewed',
                     entity_type='provider', entity_id=pid, details={'purpose': body.purpose})
        dob = date.fromisoformat(svc.cipher.decrypt(p['date_of_birth_enc'], context='provider_profiles.dob'))
    return {'legal_name': svc.cipher.decrypt(p['legal_name_enc'], context='provider_profiles.legal_name'),
            'date_of_birth': dob, 'age_today': age_on(dob, datetime.now(UTC).date())}


# --- evidence ------------------------------------------------------------------------------------------

@router.post('/evidence/{evidence_id}/review')
def review_evidence(evidence_id: UUID, body: EvidenceReviewIn, admin: AdminPrincipal = Depends(reviewer),
                    svc: Services = Depends(services)):
    today = datetime.now(UTC).date()
    with tx(svc.engine) as con:
        ev = one(con, 'SELECT * FROM provider_evidence WHERE id = :id FOR UPDATE', id=evidence_id)
        if not ev:
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        if ev['scan_state'] != 'clean':
            raise ApiError(409, 'EVIDENCE_NOT_CLEAN', 'verification.evidence_not_clean',
                           details={'scan_state': ev['scan_state']})
        if ev['review_state'] != 'pending':
            raise ApiError(409, 'EVIDENCE_ALREADY_REVIEWED', 'verification.already_decided')
        expires = body.document_expires_on or ev['document_expires_on']
        state = {'accept': 'accepted', 'reject': 'rejected', 'needs_more_info': 'needs_more_info'}[body.decision]
        next_due = None
        if state == 'accepted':
            if expires and expires <= today:
                raise ApiError(409, 'DOCUMENT_ALREADY_EXPIRED', 'provider.document_expired')
            # Periodic re-review is separate from the printed expiry; default one year, never auto-renewed.
            next_due = body.next_review_due or today + timedelta(days=365)
            if next_due <= today:
                raise ApiError(422, 'REVIEW_DATE_IN_PAST', 'error.validation')
        run(con, '''UPDATE provider_evidence SET review_state = :s, document_expires_on = :e, next_review_due = :n,
                    reviewed_by = :a, reviewed_at = now(), review_reason = :r WHERE id = :id''',
            s=state, e=expires, n=next_due, a=admin.admin_id, r=body.reason, id=evidence_id)
        audit.record(con, actor_type='admin', actor_id=admin.admin_id, action=f'evidence.{state}',
                     entity_type='provider_evidence', entity_id=str(evidence_id),
                     details={'provider_id': ev['provider_id'], 'evidence_type': ev['evidence_type'],
                              'reason': body.reason, 'next_review_due': next_due})
    return {'id': str(evidence_id), 'review_state': state, 'next_review_due': next_due}


@router.post('/evidence/{evidence_id}/access-grant', status_code=201)
def evidence_access_grant(evidence_id: UUID, body: Purpose, admin: AdminPrincipal = Depends(reviewer),
                          svc: Services = Depends(services)):
    """One-time download grant valid <= 60 s, bound to this admin, with a recorded purpose (Q38, AT-014)."""
    token = new_opaque_token()
    with tx(svc.engine) as con:
        require_step_up(con, admin, 'evidence.read')
        ev = one(con, 'SELECT provider_id, scan_state, object_key FROM provider_evidence WHERE id = :id',
                 id=evidence_id)
        if not ev:
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        if ev['scan_state'] != 'clean' or ev['object_key'] is None:
            raise ApiError(409, 'EVIDENCE_NOT_CLEAN', 'verification.evidence_not_clean',
                           details={'scan_state': ev['scan_state']})
        run(con, '''INSERT INTO evidence_access_tokens(token_hash, evidence_id, admin_id, expires_at)
                    VALUES (:h, :e, :a, now() + :ttl)''', h=sha256_hex(token), e=evidence_id, a=admin.admin_id,
            ttl=timedelta(seconds=svc.settings.evidence_token_seconds))
        _access_log(con, admin, 'provider_evidence', str(evidence_id), 'download_grant', body.purpose)
        audit.record(con, actor_type='admin', actor_id=admin.admin_id, action='evidence.access_granted',
                     entity_type='provider_evidence', entity_id=str(evidence_id),
                     details={'provider_id': ev['provider_id'], 'purpose': body.purpose})
    return {'download_path': f'/v1/admin/evidence/download/{token}',
            'expires_in': svc.settings.evidence_token_seconds}


@router.get('/evidence/download/{token}')
def evidence_download(token: str, admin: AdminPrincipal = Depends(reviewer), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        grant = one(con, '''UPDATE evidence_access_tokens SET used_at = now()
                            WHERE token_hash = :h AND admin_id = :a AND used_at IS NULL AND expires_at > now()
                            RETURNING evidence_id''', h=sha256_hex(token), a=admin.admin_id)
        if not grant:
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        ev = one(con, '''SELECT id, object_key, wrapped_data_key, media_type, sha256
                         FROM provider_evidence WHERE id = :id''', id=grant['evidence_id'])
        _access_log(con, admin, 'provider_evidence', str(ev['id']), 'download', 'one-time grant redeemed')
    plaintext = svc.sealer.open(str(ev['id']), svc.store.get(ev['object_key']), ev['wrapped_data_key'])
    if hashlib.sha256(plaintext).hexdigest() != ev['sha256']:
        raise ApiError(500, 'EVIDENCE_INTEGRITY_FAILED', 'error.unexpected')
    return Response(plaintext, media_type=ev['media_type'], headers={
        'Content-Disposition': f"attachment; filename=evidence-{ev['id']}.{EXT[ev['media_type']]}",
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': "sandbox; default-src 'none'"})


# --- application decisions -----------------------------------------------------------------------------

@router.post('/providers/{provider_id}/application/decision')
def decide_application(provider_id: UUID, body: ApplicationDecisionIn, admin: AdminPrincipal = Depends(reviewer),
                       svc: Services = Depends(services)):
    pid = str(provider_id)
    allowed_from, target = APPLICATION_MOVES[body.decision]
    with tx(svc.engine) as con:
        p = one(con, 'SELECT * FROM provider_profiles WHERE user_id = :p FOR UPDATE', p=pid)
        if not p:
            raise ApiError(404, 'PROVIDER_NOT_FOUND', 'provider.not_found')
        if p['application_state'] not in allowed_from:
            raise ApiError(409, 'TRANSITION_NOT_ALLOWED', 'verification.transition_not_allowed',
                           details={'from': p['application_state'], 'decision': body.decision})
        if body.decision == 'approve':
            if not body.identity_verified or not body.age_verification_method:
                raise ApiError(422, 'VERIFICATION_ATTESTATION_REQUIRED', 'verification.attestation_required')
            if not one(con, '''SELECT 1 AS ok FROM provider_evidence WHERE provider_id = :p
                               AND evidence_type = 'national_id' AND review_state = 'accepted' ''', p=pid):
                raise ApiError(409, 'IDENTITY_EVIDENCE_REQUIRED', 'verification.identity_evidence_required')
            dob = date.fromisoformat(svc.cipher.decrypt(p['date_of_birth_enc'], context='provider_profiles.dob'))
            if age_on(dob, datetime.now(UTC).date()) < svc.settings.min_provider_age:
                raise ApiError(409, 'PROVIDER_UNDERAGE', 'provider.underage')
            run(con, '''UPDATE provider_profiles SET identity_verified_at = now(), identity_verified_by = :a,
                        age_verified_at = now(), age_verified_by = :a, age_verification_method = :m
                        WHERE user_id = :p''', a=admin.admin_id, m=body.age_verification_method, p=pid)
        run(con, 'UPDATE provider_profiles SET application_state = :s, updated_at = now() WHERE user_id = :p',
            s=target, p=pid)
        app_event(con, pid, 'admin', admin.admin_id, p['application_state'], target, body.reason)
        audit.record(con, actor_type='admin', actor_id=admin.admin_id, action=f'provider.application_{target}',
                     entity_type='provider', entity_id=pid,
                     details={'from': p['application_state'], 'reason': body.reason,
                              'age_verification_method': body.age_verification_method})
    return {'provider_id': pid, 'application_state': target}


# --- category approvals --------------------------------------------------------------------------------

def _approval_validity(con, provider_id: str, policy: dict, now: datetime) -> datetime:
    """Approval cannot outlive the evidence it rests on: earliest of policy validity, document expiry and re-review."""
    limit = now + timedelta(days=policy['approval_validity_days'])
    types = [r['type'] for r in policy['required_evidence']]
    for r in many(con, '''SELECT document_expires_on, next_review_due FROM provider_evidence
                          WHERE provider_id = :p AND review_state = 'accepted' AND evidence_type = ANY(:t)
                            AND (category_code IS NULL OR category_code = :c)''',
                  p=provider_id, t=types, c=policy['category_code']):
        for d in (r['document_expires_on'], r['next_review_due']):
            if d is not None:
                limit = min(limit, datetime(d.year, d.month, d.day, tzinfo=UTC))
    return limit


@router.post('/provider-approvals/{approval_id}/decision')
def decide_category_approval(approval_id: UUID, body: ApprovalDecisionIn, admin: AdminPrincipal = Depends(reviewer),
                             svc: Services = Depends(services)):
    allowed_from, target = APPROVAL_MOVES[body.decision]
    now = datetime.now(UTC)
    with tx(svc.engine) as con:
        a = one(con, 'SELECT * FROM provider_category_approvals WHERE id = :id FOR UPDATE', id=approval_id)
        if not a:
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        if a['status'] not in allowed_from:
            raise ApiError(409, 'TRANSITION_NOT_ALLOWED', 'verification.transition_not_allowed',
                           details={'from': a['status'], 'decision': body.decision})
        pid = str(a['provider_id'])
        policy_id, valid_from, valid_until, evidence_ids = a['policy_id'], a['valid_from'], a['valid_until'], []
        if body.decision == 'approve':
            require_step_up(con, admin, 'category.approve')
            policy = active_policy(con, a['category_code'])
            if not policy:
                raise ApiError(409, 'CATEGORY_POLICY_MISSING', 'catalog.policy_missing')
            blocking = [g for g in provider_category_gaps(con, pid, a['category_code'], now)
                        if not g.startswith(_APPROVAL_STATUS_GAPS)]
            if blocking:
                # No override exists: an admin cannot approve past missing or expired evidence (§8.1).
                raise ApiError(409, 'APPROVAL_GATES_NOT_MET', 'verification.gates_not_met',
                               details={'reason_codes': blocking})
            policy_id, valid_from = policy['id'], now
            valid_until = _approval_validity(con, pid, policy, now)
            if valid_until <= now + timedelta(days=1):
                raise ApiError(409, 'EVIDENCE_EXPIRES_TOO_SOON', 'verification.gates_not_met')
            evidence_ids = [r['id'] for r in many(con, '''SELECT id FROM provider_evidence WHERE provider_id = :p
                                                          AND review_state = 'accepted' ''', p=pid)]
        elif body.decision == 'revoke':
            valid_until = now
        run(con, '''UPDATE provider_category_approvals SET status = :s, policy_id = :pol, valid_from = :vf,
                    valid_until = :vu, probation_until = coalesce(:pu, probation_until),
                    probation_min_jobs = coalesce(:pj, probation_min_jobs), decided_by = :a, decided_at = now(),
                    decision_reason = :r, updated_at = now() WHERE id = :id''',
            s=target, pol=policy_id, vf=valid_from, vu=valid_until, pu=body.probation_until,
            pj=body.probation_min_jobs, a=admin.admin_id, r=body.reason, id=approval_id)
        run(con, '''INSERT INTO provider_approval_events(approval_id, actor_type, actor_id, from_status, to_status,
                        policy_id, reason, evidence_ids)
                    VALUES (:id, 'admin', :a, :f, :t, :pol, :r, CAST(:ev AS uuid[]))''',
            id=approval_id, a=admin.admin_id, f=a['status'], t=target, pol=policy_id, r=body.reason,
            ev=[str(e) for e in evidence_ids])
        audit.record(con, actor_type='admin', actor_id=admin.admin_id, action=f'provider.category_{target}',
                     entity_type='provider_category_approval', entity_id=str(approval_id),
                     details={'provider_id': pid, 'category': a['category_code'], 'from': a['status'],
                              'reason': body.reason, 'valid_until': valid_until})
    return {'id': str(approval_id), 'status': target, 'valid_until': valid_until}


@router.get('/provider-approvals/{approval_id}/history')
def approval_history(approval_id: UUID, admin: AdminPrincipal = Depends(reviewer), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        rows = many(con, '''SELECT e.actor_type, e.actor_id, e.from_status, e.to_status, p.version AS policy_version,
                                   e.reason, e.evidence_ids, e.created_at
                            FROM provider_approval_events e JOIN category_policies p ON p.id = e.policy_id
                            WHERE e.approval_id = :id ORDER BY e.id''', id=approval_id)
    return [{**r, 'evidence_ids': [str(x) for x in r['evidence_ids']]} for r in rows]


# --- suspension (INV-11) --------------------------------------------------------------------------------

def on_provider_suspended(con, provider_id: str) -> dict:
    """Revoke pending offers and flag active jobs for human triage (booking engine owns the details)."""
    from ..booking.admin import provider_suspended_effects
    return provider_suspended_effects(con, provider_id)


@router.post('/providers/{provider_id}/suspend')
def suspend_provider(provider_id: UUID, body: Reason, admin: AdminPrincipal = Depends(safety_actor),
                     svc: Services = Depends(services)):
    """Immediate removal from new matching. No step-up: protective action must not be slowed down."""
    pid = str(provider_id)
    with tx(svc.engine) as con:
        p = one(con, 'SELECT account_state FROM provider_profiles WHERE user_id = :p FOR UPDATE', p=pid)
        if not p:
            raise ApiError(404, 'PROVIDER_NOT_FOUND', 'provider.not_found')
        if p['account_state'] == 'suspended':
            raise ApiError(409, 'ALREADY_SUSPENDED', 'verification.already_decided')
        run(con, '''UPDATE provider_profiles SET account_state = 'suspended', suspended_reason = :r, updated_at = now()
                    WHERE user_id = :p''', r=body.reason, p=pid)
        effects = on_provider_suspended(con, pid)
        app_event(con, pid, 'admin', admin.admin_id, 'account:active', 'account:suspended', body.reason)
        audit.record(con, actor_type='admin', actor_id=admin.admin_id, action='provider.suspended',
                     entity_type='provider', entity_id=pid, details={'reason': body.reason, **effects})
    return {'provider_id': pid, 'account_state': 'suspended', **effects}


@router.post('/providers/{provider_id}/reinstate')
def reinstate_provider(provider_id: UUID, body: Reason, admin: AdminPrincipal = Depends(reviewer),
                       svc: Services = Depends(services)):
    pid = str(provider_id)
    with tx(svc.engine) as con:
        require_step_up(con, admin, 'provider.reinstate')
        p = one(con, 'SELECT account_state FROM provider_profiles WHERE user_id = :p FOR UPDATE', p=pid)
        if not p or p['account_state'] != 'suspended':
            raise ApiError(409, 'NOT_SUSPENDED', 'verification.transition_not_allowed')
        run(con, '''UPDATE provider_profiles SET account_state = 'active', suspended_reason = NULL, updated_at = now()
                    WHERE user_id = :p''', p=pid)
        app_event(con, pid, 'admin', admin.admin_id, 'account:suspended', 'account:active', body.reason)
        audit.record(con, actor_type='admin', actor_id=admin.admin_id, action='provider.reinstated',
                     entity_type='provider', entity_id=pid, details={'reason': body.reason})
    return {'provider_id': pid, 'account_state': 'active'}

