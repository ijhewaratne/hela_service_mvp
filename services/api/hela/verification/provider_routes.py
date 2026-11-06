"""Provider self-service: apply, upload evidence, submit, request category approvals (FR-ID-06, FR-VER-01..06).
A provider can never approve anything; they see their own statuses and metadata only."""
from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field

from ..audit import service as audit
from ..authz.deps import UserPrincipal, current_user
from ..core import ratelimit
from ..core.db import many, one, run, tx
from ..core.errors import ApiError
from ..core.services import Services, services
from ..evidence.scanning import validate_upload
from .eligibility import active_policy

router = APIRouter(prefix='/v1/provider', tags=['provider'])

EvidenceType = Literal['national_id', 'address_proof', 'reference', 'training_record', 'police_clearance', 'other']


class ApplyIn(BaseModel):
    legal_name: str = Field(min_length=2, max_length=120)
    date_of_birth: date


class CategoryRequestIn(BaseModel):
    category_code: str = Field(pattern=r'^[a-z0-9_]{3,40}$')


def age_on(dob: date, on: date) -> int:
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


def _profile(con, user_id: str, *, lock: bool = False) -> dict:
    p = one(con, f"SELECT * FROM provider_profiles WHERE user_id = :u{' FOR UPDATE' if lock else ''}", u=user_id)
    if not p:
        raise ApiError(404, 'PROVIDER_NOT_FOUND', 'provider.not_found')
    return p


def app_event(con, provider_id: str, actor_type: str, actor_id: str, old: str | None, new: str,
              reason: str | None = None) -> None:
    run(con, '''INSERT INTO provider_application_events(provider_id, actor_type, actor_id, from_state, to_state, reason)
                VALUES (:p, :at, :a, :o, :n, :r)''', p=provider_id, at=actor_type, a=actor_id, o=old, n=new, r=reason)


@router.post('/apply', status_code=201)
def apply(body: ApplyIn, user: UserPrincipal = Depends(current_user), svc: Services = Depends(services)):
    # Self-declared DOB is only a pre-filter; a reviewer verifies age against ID before approval (Q13).
    if age_on(body.date_of_birth, datetime.now(UTC).date()) < svc.settings.min_provider_age:
        raise ApiError(422, 'PROVIDER_UNDERAGE', 'provider.underage',
                       details={'min_age': svc.settings.min_provider_age})
    with tx(svc.engine) as con:
        created = one(con, '''INSERT INTO provider_profiles(user_id, legal_name_enc, date_of_birth_enc)
                              VALUES (:u, :n, :d) ON CONFLICT (user_id) DO NOTHING RETURNING user_id''',
                      u=user.user_id, n=svc.cipher.encrypt(body.legal_name, context='provider_profiles.legal_name'),
                      d=svc.cipher.encrypt(body.date_of_birth.isoformat(), context='provider_profiles.dob'))
        if not created:
            raise ApiError(409, 'ALREADY_APPLIED', 'provider.already_applied')
        run(con, '''INSERT INTO user_personas(user_id, persona) VALUES (:u, 'provider')
                    ON CONFLICT (user_id, persona) DO NOTHING''', u=user.user_id)
        app_event(con, user.user_id, 'user', user.user_id, None, 'draft')
        audit.record(con, actor_type='user', actor_id=user.user_id, action='provider.applied',
                     entity_type='provider', entity_id=user.user_id)
    return {'provider_id': user.user_id, 'application_state': 'draft'}


@router.get('/me')
def provider_me(user: UserPrincipal = Depends(current_user), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        p = _profile(con, user.user_id)
        evidence = many(con, '''SELECT id, evidence_type, category_code, media_type, size_bytes, scan_state,
                                       review_state, document_expires_on, next_review_due, review_reason, uploaded_at
                                FROM provider_evidence WHERE provider_id = :u ORDER BY uploaded_at''', u=user.user_id)
        approvals = _approvals(con, user.user_id)
    return {'provider_id': user.user_id, 'application_state': p['application_state'],
            'account_state': p['account_state'], 'identity_verified': p['identity_verified_at'] is not None,
            'age_verified': p['age_verified_at'] is not None,
            'evidence': [{**e, 'id': str(e['id'])} for e in evidence], 'approvals': approvals}


def _approvals(con, provider_id: str) -> list[dict]:
    rows = many(con, '''SELECT a.id, a.category_code, a.status, a.valid_from, a.valid_until, a.probation_until,
                               a.decision_reason, p.version AS policy_version
                        FROM provider_category_approvals a JOIN category_policies p ON p.id = a.policy_id
                        WHERE a.provider_id = :p ORDER BY a.category_code''', p=provider_id)
    return [{**r, 'id': str(r['id'])} for r in rows]


@router.get('/approvals')
def my_approvals(user: UserPrincipal = Depends(current_user), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        _profile(con, user.user_id)
        return _approvals(con, user.user_id)


@router.post('/evidence', status_code=201)
def upload_evidence(evidence_type: EvidenceType = Form(...), category_code: str | None = Form(None),
                      document_expires_on: date | None = Form(None), file: UploadFile = File(...),
                      user: UserPrincipal = Depends(current_user), svc: Services = Depends(services)):
    s = svc.settings
    ratelimit.hit(svc.redis, f'evidence-upload:{user.user_id}', limit=20, window_seconds=3600)
    # Sync endpoint: scanning and storage calls block, so FastAPI runs this in its threadpool.
    data = file.file.read(s.max_evidence_bytes + 1)  # bounded read; never buffer an unbounded body
    media_type, rejection = validate_upload(data, file.content_type, s.max_evidence_bytes)
    with tx(svc.engine) as con:
        p = _profile(con, user.user_id)
        if p['account_state'] != 'active' or p['application_state'] in ('denied', 'under_review'):
            raise ApiError(409, 'EVIDENCE_UPLOAD_NOT_ALLOWED', 'provider.upload_not_allowed',
                           details={'application_state': p['application_state']})
        if category_code and not one(con, 'SELECT 1 AS ok FROM service_categories WHERE code = :c', c=category_code):
            raise ApiError(422, 'UNKNOWN_CATEGORY', 'error.validation')
        if document_expires_on and document_expires_on <= datetime.now(UTC).date():
            raise ApiError(422, 'DOCUMENT_ALREADY_EXPIRED', 'provider.document_expired')
    if rejection:
        with tx(svc.engine) as con:
            audit.record(con, actor_type='user', actor_id=user.user_id, action='evidence.upload_rejected',
                         entity_type='provider', entity_id=user.user_id, details={'reason': rejection})
        raise ApiError(413 if rejection == 'FILE_TOO_LARGE' else 422, rejection, 'provider.file_rejected')

    evidence_id = str(uuid.uuid4())
    digest = hashlib.sha256(data).hexdigest()
    result = svc.scanner.scan(data)
    object_key = wrapped = None
    if result.state != 'infected':
        object_key = f'evidence/{user.user_id}/{evidence_id}'
        ciphertext, wrapped = svc.sealer.seal(evidence_id, data)
        try:
            svc.store.put(object_key, ciphertext)
        except Exception:
            raise ApiError(503, 'DEPENDENCY_UNAVAILABLE', 'error.dependency_unavailable', retryable=True) from None
    scan_state = {'clean': 'clean', 'infected': 'infected', 'unavailable': 'pending'}[result.state]
    with tx(svc.engine) as con:
        run(con, '''INSERT INTO provider_evidence(id, provider_id, evidence_type, category_code, object_key,
                        wrapped_data_key, sha256, media_type, size_bytes, scan_state, scan_detail, review_state,
                        document_expires_on)
                    VALUES (:id, :p, :t, :c, :k, :w, :h, :m, :n, :s, :d, :r, :e)''',
            id=evidence_id, p=user.user_id, t=evidence_type, c=category_code, k=object_key, w=wrapped, h=digest,
            m=media_type, n=len(data), s=scan_state, d=result.detail or None,
            r='rejected' if scan_state == 'infected' else 'pending', e=document_expires_on)
        audit.record(con, actor_type='user', actor_id=user.user_id,
                     action='evidence.quarantined' if scan_state == 'infected' else 'evidence.uploaded',
                     entity_type='provider_evidence', entity_id=evidence_id,
                     details={'evidence_type': evidence_type, 'scan_state': scan_state, 'sha256': digest})
    if scan_state == 'infected':
        # Not stored; the row is kept as a security record with the hash only.
        raise ApiError(422, 'EVIDENCE_INFECTED', 'provider.file_rejected')
    return {'id': evidence_id, 'evidence_type': evidence_type, 'scan_state': scan_state, 'review_state': 'pending'}


@router.post('/application/submit')
def submit_application(user: UserPrincipal = Depends(current_user), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        p = _profile(con, user.user_id, lock=True)
        if p['application_state'] not in ('draft', 'needs_more_info'):
            raise ApiError(409, 'APPLICATION_NOT_SUBMITTABLE', 'provider.not_submittable',
                           details={'application_state': p['application_state']})
        if not one(con, '''SELECT 1 AS ok FROM provider_evidence WHERE provider_id = :u
                           AND object_key IS NOT NULL LIMIT 1''', u=user.user_id):
            raise ApiError(409, 'EVIDENCE_REQUIRED', 'provider.evidence_required')
        run(con, '''UPDATE provider_profiles SET application_state = 'submitted', submitted_at = now(),
                    updated_at = now() WHERE user_id = :u''', u=user.user_id)
        app_event(con, user.user_id, 'user', user.user_id, p['application_state'], 'submitted')
        audit.record(con, actor_type='user', actor_id=user.user_id, action='provider.application_submitted',
                     entity_type='provider', entity_id=user.user_id)
    return {'application_state': 'submitted'}


@router.post('/categories', status_code=201)
def request_category(body: CategoryRequestIn, user: UserPrincipal = Depends(current_user),
                     svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        p = _profile(con, user.user_id)
        if p['application_state'] == 'denied' or p['account_state'] != 'active':
            raise ApiError(409, 'CATEGORY_REQUEST_NOT_ALLOWED', 'provider.category_not_allowed')
        cat = one(con, 'SELECT status FROM service_categories WHERE code = :c', c=body.category_code)
        policy = active_policy(con, body.category_code)
        if not cat or cat['status'] != 'enabled' or not policy:
            raise ApiError(409, 'CATEGORY_NOT_AVAILABLE', 'provider.category_not_allowed')
        row = one(con, '''INSERT INTO provider_category_approvals(provider_id, category_code, policy_id)
                          VALUES (:p, :c, :pol) ON CONFLICT (provider_id, category_code) DO NOTHING RETURNING id''',
                  p=user.user_id, c=body.category_code, pol=policy['id'])
        if not row:
            raise ApiError(409, 'CATEGORY_ALREADY_REQUESTED', 'provider.category_already_requested')
        run(con, '''INSERT INTO provider_approval_events(approval_id, actor_type, actor_id, to_status, policy_id)
                    VALUES (:a, 'user', :u, 'pending', :pol)''', a=row['id'], u=user.user_id, pol=policy['id'])
        audit.record(con, actor_type='user', actor_id=user.user_id, action='provider.category_requested',
                     entity_type='provider_category_approval', entity_id=str(row['id']),
                     details={'category': body.category_code, 'policy_version': policy['version']})
    return {'id': str(row['id']), 'category_code': body.category_code, 'status': 'pending',
            'policy_version': policy['version'],
            'required_evidence': policy['required_evidence']}
