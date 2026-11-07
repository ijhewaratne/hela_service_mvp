"""Category policy and price-rule versions (spec §4.2, §9.1, AT-027).

A draft is a new immutable version. Publishing it (retire the active version, activate the draft) is a maker/checker
change request approved by a *different* administrator after step-up. Marking content APPROVED is not possible here:
that records an external business/legal sign-off (DEC-03/04/06) and stays an owner-level operation.
"""
from __future__ import annotations

import json
import re
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import Field, model_validator
from sqlalchemy import Connection

from ..audit import service as audit
from ..authz.deps import AdminPrincipal, require_permission, require_step_up
from ..core.db import many, one, run, tx
from ..core.errors import ApiError
from ..core.schema import Input
from ..core.services import Services, services

router = APIRouter(prefix='/v1/admin/categories', tags=['admin-policy'])

TASK_CODE = re.compile(r'[a-z0-9_]{3,40}')
EVIDENCE_TYPES = ('national_id', 'address_proof', 'reference', 'training_record', 'police_clearance', 'other')
BASELINE_PROHIBITED = {'medication_administration', 'clinical_tasks'}   # spec §4.1: never in any household category


class EvidenceRequirement(Input):
    type: Literal['national_id', 'address_proof', 'reference', 'training_record', 'police_clearance', 'other']
    count: int = Field(default=1, ge=1, le=5)
    review_interval_days: int = Field(default=365, ge=30, le=1825)


class PolicyDraftIn(Input):
    allowed_tasks: list[str] = Field(min_length=1, max_length=40)
    prohibited_tasks: list[str] = Field(min_length=1, max_length=40)
    min_duration_minutes: int = Field(ge=30, le=1440)
    max_duration_minutes: int = Field(ge=30, le=1440)
    max_radius_km: float = Field(gt=0, le=25)
    required_evidence: list[EvidenceRequirement] = Field(min_length=1, max_length=10)
    safeguards_required: bool = False
    approval_validity_days: int = Field(default=365, ge=30, le=1095)
    min_provider_age: int = Field(default=18, ge=18, le=30)
    cancellation_fee_minor: int = Field(default=0, ge=0)
    justification: str = Field(min_length=10, max_length=1000)

    @model_validator(mode='after')
    def coherent(self):
        for task in [*self.allowed_tasks, *self.prohibited_tasks]:
            if not TASK_CODE.fullmatch(task):
                raise ValueError(f'Invalid task code: {task!r}')
        if set(self.allowed_tasks) & set(self.prohibited_tasks):
            raise ValueError('A task cannot be both allowed and prohibited')
        if not BASELINE_PROHIBITED <= set(self.prohibited_tasks):
            raise ValueError(f'Prohibited tasks must always include {sorted(BASELINE_PROHIBITED)}')
        if self.max_duration_minutes < self.min_duration_minutes:
            raise ValueError('max_duration_minutes must be >= min_duration_minutes')
        if self.min_duration_minutes % 30 or self.max_duration_minutes % 30:
            raise ValueError('Durations must be multiples of 30 minutes')
        if len({r.type for r in self.required_evidence}) != len(self.required_evidence):
            raise ValueError('Each evidence type may appear once')
        if not any(r.type == 'national_id' for r in self.required_evidence):
            raise ValueError('Identity evidence (national_id) is always required')
        return self


class PriceDraftIn(Input):
    hourly_rate_minor: int = Field(ge=0, le=10_000_000)
    base_fee_minor: int = Field(default=0, ge=0, le=10_000_000)
    travel_fee_minor: int = Field(default=0, ge=0, le=10_000_000)
    justification: str = Field(min_length=10, max_length=1000)


def _category(con: Connection, code: str) -> dict:
    cat = one(con, 'SELECT id, code, risk_class FROM service_categories WHERE code = :c', c=code)
    if not cat:
        raise ApiError(404, 'NOT_FOUND', 'error.not_found')
    if cat['risk_class'] in ('disabled', 'out_of_scope'):
        raise ApiError(409, 'CATEGORY_FORBIDDEN', 'catalog.category_forbidden')
    return cat


@router.get('/{code}/policies')
def policy_versions(code: str, admin: AdminPrincipal = Depends(require_permission('policy.manage')),
                    svc: Services = Depends(services)):
    """All versions, newest first, for side-by-side review before approving a publish request."""
    with tx(svc.engine) as con:
        policies = many(con, '''SELECT id, version, status, approval_status, allowed_tasks, prohibited_tasks,
                                       min_duration_minutes, max_duration_minutes, max_radius_km::float AS max_radius_km,
                                       required_evidence, safeguards_required, approval_validity_days, min_provider_age,
                                       terms, drafted_by, created_at
                                FROM category_policies WHERE category_code = :c ORDER BY version DESC''', c=code)
        prices = many(con, '''SELECT id, version, status, approval_status, basis, hourly_rate_minor, base_fee_minor,
                                     travel_fee_minor, drafted_by, created_at
                              FROM price_rules WHERE category_code = :c ORDER BY version DESC''', c=code)
    fix = lambda rows: [{k: (str(v) if isinstance(v, UUID) else v) for k, v in r.items()} for r in rows]  # noqa: E731
    return {'category_code': code, 'policies': fix(policies), 'price_rules': fix(prices)}


@router.post('/{code}/policy-drafts', status_code=201)
def draft_policy(code: str, body: PolicyDraftIn, admin: AdminPrincipal = Depends(require_permission('policy.manage')),
                 svc: Services = Depends(services)):
    from .regions import _propose
    admin.require('policy.manage')
    with tx(svc.engine) as con:
        require_step_up(con, admin, 'policy.change')
        _category(con, code)
        run(con, 'SELECT pg_advisory_xact_lock(hashtext(:k))', k=f'policy-version:{code}')
        version = one(con, 'SELECT coalesce(max(version), 0) + 1 AS v FROM category_policies WHERE category_code = :c',
                      c=code)['v']
        draft = one(con, '''INSERT INTO category_policies(category_code, version, status, allowed_tasks, prohibited_tasks,
                                min_duration_minutes, max_duration_minutes, max_radius_km, required_evidence,
                                safeguards_required, approval_validity_days, min_provider_age, terms, drafted_by)
                            VALUES (:c, :v, 'draft', CAST(:a AS jsonb), CAST(:p AS jsonb), :mn, :mx, :r, CAST(:e AS jsonb),
                                    :sg, :val, :age, CAST(:t AS jsonb), :by) RETURNING id''',
                    c=code, v=version, a=json.dumps(sorted(set(body.allowed_tasks))),
                    p=json.dumps(sorted(set(body.prohibited_tasks))), mn=body.min_duration_minutes,
                    mx=body.max_duration_minutes, r=body.max_radius_km,
                    e=json.dumps([r.model_dump() for r in body.required_evidence]), sg=body.safeguards_required,
                    val=body.approval_validity_days, age=body.min_provider_age,
                    t=json.dumps({'cancellation': {'fee_lkr_minor': body.cancellation_fee_minor},
                                  'note': 'Drafted via admin API; UNAPPROVED until business/legal sign-off'}),
                    by=admin.admin_id)
        change = _propose(con, admin, 'policy.publish', draft['id'], body.justification)
    return {'policy_id': str(draft['id']), 'version': version, 'status': 'draft', 'change_request': change}


@router.post('/{code}/price-drafts', status_code=201)
def draft_price(code: str, body: PriceDraftIn, admin: AdminPrincipal = Depends(require_permission('policy.manage')),
                svc: Services = Depends(services)):
    from .regions import _propose
    admin.require('policy.manage')
    with tx(svc.engine) as con:
        require_step_up(con, admin, 'policy.change')
        _category(con, code)
        run(con, 'SELECT pg_advisory_xact_lock(hashtext(:k))', k=f'price-version:{code}')
        version = one(con, 'SELECT coalesce(max(version), 0) + 1 AS v FROM price_rules WHERE category_code = :c',
                      c=code)['v']
        draft = one(con, '''INSERT INTO price_rules(category_code, version, status, hourly_rate_minor, base_fee_minor,
                                travel_fee_minor, drafted_by)
                            VALUES (:c, :v, 'draft', :h, :b, :t, :by) RETURNING id''',
                    c=code, v=version, h=body.hourly_rate_minor, b=body.base_fee_minor, t=body.travel_fee_minor,
                    by=admin.admin_id)
        change = _propose(con, admin, 'price.publish', draft['id'], body.justification)
    return {'price_rule_id': str(draft['id']), 'version': version, 'status': 'draft', 'change_request': change}


def apply_publish(con: Connection, cr: dict) -> None:
    """Checker step: retire the active version and activate the draft in one transaction."""
    table = 'category_policies' if cr['change_type'] == 'policy.publish' else 'price_rules'
    draft = one(con, f'SELECT id, category_code, status, version FROM {table} WHERE id = :id FOR UPDATE',
                id=cr['target_id'])
    if not draft or draft['status'] != 'draft':
        raise ApiError(409, 'DRAFT_NOT_PUBLISHABLE', 'error.bad_request')
    run(con, f"UPDATE {table} SET status = 'retired' WHERE category_code = :c AND status = 'active'",
        c=draft['category_code'])
    run(con, f"UPDATE {table} SET status = 'active' WHERE id = :id", id=draft['id'])
    audit.record(con, actor_type='system', actor_id='policy-publish', action=f'{cr["change_type"]}ed',
                 entity_type=table, entity_id=str(draft['id']),
                 details={'category': draft['category_code'], 'version': draft['version']})
