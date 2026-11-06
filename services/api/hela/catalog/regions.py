"""Regions, categories and maker/checker activation (Q15, Q16, Q21, SEC-14). Server-enforced; UI hiding is
never enough. One admin proposes, a different admin approves, both after step-up."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import Connection

from ..audit import service as audit
from ..authz.deps import AdminPrincipal, require_permission, require_step_up
from ..core.db import many, one, run, tx
from ..core.errors import ApiError
from ..core.services import Services, services

public = APIRouter(prefix='/v1', tags=['catalog'])
admin_router = APIRouter(prefix='/v1/admin', tags=['admin-policy'])

ChangeType = Literal['region.enable', 'region.disable', 'category.enable', 'category.disable']


class RegionChangeIn(BaseModel):
    change_type: Literal['region.enable', 'region.disable']
    justification: str = Field(min_length=10, max_length=1000)


class CategoryChangeIn(BaseModel):
    change_type: Literal['category.enable', 'category.disable']
    justification: str = Field(min_length=10, max_length=1000)


class DecisionIn(BaseModel):
    decision: Literal['approve', 'reject']
    reason: str = Field(min_length=5, max_length=1000)


# --- public catalogue --------------------------------------------------------------------------------

@public.get('/regions')
def enabled_regions(svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        return many(con, '''SELECT code, name, is_synthetic, approval_status FROM service_regions
                            WHERE status = 'enabled' ORDER BY name''')


@public.get('/categories')
def enabled_categories(svc: Services = Depends(services)):
    """Only enabled categories with an active policy version (FR-CAT-01)."""
    with tx(svc.engine) as con:
        return many(con, '''SELECT c.code, c.display_name, p.version AS policy_version, p.approval_status,
                                   p.min_duration_minutes, p.max_duration_minutes, p.allowed_tasks,
                                   p.prohibited_tasks
                            FROM service_categories c
                            JOIN category_policies p ON p.category_code = c.code AND p.status = 'active'
                            WHERE c.status = 'enabled' ORDER BY c.code''')


# --- admin views -------------------------------------------------------------------------------------

@admin_router.get('/regions')
def all_regions(admin: AdminPrincipal = Depends(require_permission('policy.manage')),
                svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        rows = many(con, '''SELECT id, code, name, status, is_synthetic, approval_status, updated_at
                            FROM service_regions ORDER BY name''')
    return [{**r, 'id': str(r['id'])} for r in rows]


@admin_router.get('/categories')
def all_categories(admin: AdminPrincipal = Depends(require_permission('policy.manage')),
                   svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        rows = many(con, '''SELECT c.id, c.code, c.display_name, c.risk_class, c.status,
                                   p.version AS active_policy_version, p.approval_status AS policy_approval_status,
                                   p.safeguards_required
                            FROM service_categories c
                            LEFT JOIN category_policies p ON p.category_code = c.code AND p.status = 'active'
                            ORDER BY c.code''')
    return [{**r, 'id': str(r['id'])} for r in rows]


# --- maker ---------------------------------------------------------------------------------------------

def _propose(con: Connection, admin: AdminPrincipal, change_type: ChangeType, target_id: UUID,
             justification: str) -> dict:
    row = one(con, '''INSERT INTO policy_change_requests(change_type, target_id, justification, requested_by)
                      VALUES (:t, :target, :j, :by) ON CONFLICT DO NOTHING RETURNING id''',
              t=change_type, target=target_id, j=justification, by=admin.admin_id)
    if not row:
        raise ApiError(409, 'CHANGE_ALREADY_PENDING', 'error.bad_request')
    audit.record(con, actor_type='admin', actor_id=admin.admin_id, action='policy.change_requested',
                 entity_type='policy_change_request', entity_id=str(row['id']),
                 details={'change_type': change_type, 'target_id': target_id, 'justification': justification})
    return {'id': str(row['id']), 'status': 'pending'}


@admin_router.post('/regions/{region_id}/change-requests', status_code=201)
def propose_region_change(region_id: UUID, body: RegionChangeIn,
                          admin: AdminPrincipal = Depends(require_permission('policy.manage')),
                          svc: Services = Depends(services)):
    admin.require('policy.manage', str(region_id))
    with tx(svc.engine) as con:
        require_step_up(con, admin, 'policy.change')
        region = one(con, 'SELECT status FROM service_regions WHERE id = :id', id=region_id)
        if not region:
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        wanted = 'enabled' if body.change_type == 'region.enable' else 'disabled'
        if region['status'] == wanted:
            raise ApiError(409, 'NO_CHANGE', 'error.bad_request', details={'status': wanted})
        return _propose(con, admin, body.change_type, region_id, body.justification)


@admin_router.post('/categories/{code}/change-requests', status_code=201)
def propose_category_change(code: str, body: CategoryChangeIn,
                            admin: AdminPrincipal = Depends(require_permission('policy.manage')),
                            svc: Services = Depends(services)):
    admin.require('policy.manage')  # categories are global; region-scoped grants do not cover them
    with tx(svc.engine) as con:
        require_step_up(con, admin, 'policy.change')
        cat = one(con, 'SELECT id, status, risk_class FROM service_categories WHERE code = :c', c=code)
        if not cat:
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        wanted = 'enabled' if body.change_type == 'category.enable' else 'disabled'
        if cat['status'] == wanted:
            raise ApiError(409, 'NO_CHANGE', 'error.bad_request', details={'status': wanted})
        if body.change_type == 'category.enable' and cat['risk_class'] in ('disabled', 'out_of_scope'):
            raise ApiError(409, 'CATEGORY_FORBIDDEN', 'catalog.category_forbidden',
                           details={'risk_class': cat['risk_class']})
        return _propose(con, admin, body.change_type, cat['id'], body.justification)


# --- checker -------------------------------------------------------------------------------------------

def _apply_region(con: Connection, svc: Services, cr: dict) -> None:
    region = one(con, 'SELECT approval_status FROM service_regions WHERE id = :id FOR UPDATE', id=cr['target_id'])
    if (cr['change_type'] == 'region.enable' and region['approval_status'] != 'APPROVED'
            and svc.settings.env in ('staging', 'production')):
        # A synthetic/approximate boundary can never go live (DEC-02).
        raise ApiError(409, 'REGION_NOT_APPROVED', 'error.bad_request')
    run(con, 'UPDATE service_regions SET status = :s, updated_at = now() WHERE id = :id',
        s='enabled' if cr['change_type'] == 'region.enable' else 'disabled', id=cr['target_id'])


def _apply_category(con: Connection, svc: Services, cr: dict) -> None:
    cat = one(con, 'SELECT code, risk_class FROM service_categories WHERE id = :id FOR UPDATE', id=cr['target_id'])
    if cr['change_type'] == 'category.enable':
        if cat['risk_class'] in ('disabled', 'out_of_scope'):
            raise ApiError(409, 'CATEGORY_FORBIDDEN', 'catalog.category_forbidden')
        policy = one(con, '''SELECT approval_status FROM category_policies
                             WHERE category_code = :c AND status = 'active' ''', c=cat['code'])
        if not policy:
            raise ApiError(409, 'CATEGORY_POLICY_MISSING', 'catalog.policy_missing')
        # Gated categories need signed-off safeguards everywhere; nothing unapproved goes live outside dev/test.
        if policy['approval_status'] != 'APPROVED' and (cat['risk_class'] == 'gated'
                                                        or svc.settings.env in ('staging', 'production')):
            raise ApiError(409, 'CATEGORY_POLICY_NOT_APPROVED', 'catalog.policy_not_approved')
    run(con, 'UPDATE service_categories SET status = :s, updated_at = now() WHERE id = :id',
        s='enabled' if cr['change_type'] == 'category.enable' else 'disabled', id=cr['target_id'])


@admin_router.get('/change-requests')
def list_change_requests(admin: AdminPrincipal = Depends(require_permission('policy.manage')),
                         svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        rows = many(con, '''SELECT id, change_type, target_id, justification, requested_by, requested_at, status,
                                   decided_by, decided_at, decision_reason
                            FROM policy_change_requests ORDER BY requested_at DESC LIMIT 100''')
    return [{k: (str(v) if isinstance(v, UUID) else v) for k, v in r.items()} for r in rows]


@admin_router.post('/change-requests/{request_id}/decision')
def decide_change(request_id: UUID, body: DecisionIn,
                  admin: AdminPrincipal = Depends(require_permission('policy.manage')),
                  svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        require_step_up(con, admin, 'policy.change')
        cr = one(con, 'SELECT * FROM policy_change_requests WHERE id = :id FOR UPDATE', id=request_id)
        if not cr:
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        if cr['status'] != 'pending':
            raise ApiError(409, 'CHANGE_NOT_PENDING', 'error.bad_request')
        is_region = cr['change_type'].startswith('region.')
        admin.require('policy.manage', str(cr['target_id']) if is_region else None)
        if str(cr['requested_by']) == admin.admin_id:
            raise ApiError(403, 'SELF_APPROVAL_FORBIDDEN', 'authz.permission_denied')
        if body.decision == 'approve':
            (_apply_region if is_region else _apply_category)(con, svc, cr)
        status = 'approved' if body.decision == 'approve' else 'rejected'
        run(con, '''UPDATE policy_change_requests SET status = :s, decided_by = :by, decided_at = now(),
                    decision_reason = :r WHERE id = :id''', s=status, by=admin.admin_id, r=body.reason, id=request_id)
        audit.record(con, actor_type='admin', actor_id=admin.admin_id, action=f'policy.change_{body.decision}d',
                     entity_type='policy_change_request', entity_id=str(request_id),
                     details={'change_type': cr['change_type'], 'target_id': cr['target_id'],
                              'requested_by': cr['requested_by'], 'reason': body.reason})
    return {'id': str(request_id), 'status': status}
