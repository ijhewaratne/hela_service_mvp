"""Service regions and maker/checker activation (Q15, Q16). Server-enforced; UI hiding is never enough."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..audit import service as audit
from ..authz.deps import AdminPrincipal, require_permission, require_step_up
from ..core.db import many, one, run, tx
from ..core.errors import ApiError
from ..core.services import Services, services

public = APIRouter(prefix='/v1', tags=['catalog'])
admin_router = APIRouter(prefix='/v1/admin', tags=['admin-policy'])


class ChangeRequestIn(BaseModel):
    change_type: Literal['region.enable', 'region.disable']
    justification: str = Field(min_length=10, max_length=1000)


class DecisionIn(BaseModel):
    decision: Literal['approve', 'reject']
    reason: str = Field(min_length=5, max_length=1000)


@public.get('/regions')
def enabled_regions(svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        rows = many(con, '''SELECT code, name, is_synthetic, approval_status FROM service_regions
                            WHERE status = 'enabled' ORDER BY name''')
    return rows


@admin_router.get('/regions')
def all_regions(admin: AdminPrincipal = Depends(require_permission('policy.manage')),
                svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        rows = many(con, '''SELECT id, code, name, status, is_synthetic, approval_status, updated_at
                            FROM service_regions ORDER BY name''')
    return [{**r, 'id': str(r['id'])} for r in rows]


@admin_router.post('/regions/{region_id}/change-requests', status_code=201)
def propose_region_change(region_id: UUID, body: ChangeRequestIn,
                          admin: AdminPrincipal = Depends(require_permission('policy.manage')),
                          svc: Services = Depends(services)):
    admin.require('policy.manage', str(region_id))
    with tx(svc.engine) as con:
        require_step_up(con, admin, 'policy.change')
        region = one(con, 'SELECT id, status FROM service_regions WHERE id = :id', id=region_id)
        if not region:
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        wanted = 'enabled' if body.change_type == 'region.enable' else 'disabled'
        if region['status'] == wanted:
            raise ApiError(409, 'NO_CHANGE', 'error.bad_request', details={'status': wanted})
        row = one(con, '''INSERT INTO policy_change_requests(change_type, target_id, justification, requested_by)
                          VALUES (:t, :target, :j, :by) ON CONFLICT DO NOTHING RETURNING id''',
                  t=body.change_type, target=region_id, j=body.justification, by=admin.admin_id)
        if not row:
            raise ApiError(409, 'CHANGE_ALREADY_PENDING', 'error.bad_request')
        audit.record(con, actor_type='admin', actor_id=admin.admin_id, action='policy.change_requested',
                     entity_type='policy_change_request', entity_id=str(row['id']),
                     details={'change_type': body.change_type, 'target_id': region_id,
                              'justification': body.justification})
    return {'id': str(row['id']), 'status': 'pending'}


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
        admin.require('policy.manage', str(cr['target_id']))
        if str(cr['requested_by']) == admin.admin_id:
            raise ApiError(403, 'SELF_APPROVAL_FORBIDDEN', 'authz.permission_denied')
        if body.decision == 'approve':
            region = one(con, 'SELECT approval_status FROM service_regions WHERE id = :id FOR UPDATE',
                         id=cr['target_id'])
            if (cr['change_type'] == 'region.enable' and region['approval_status'] != 'APPROVED'
                    and svc.settings.env in ('staging', 'production')):
                # A synthetic/approximate boundary can never go live (DEC-02).
                raise ApiError(409, 'REGION_NOT_APPROVED', 'error.bad_request')
            status = 'enabled' if cr['change_type'] == 'region.enable' else 'disabled'
            run(con, 'UPDATE service_regions SET status = :s, updated_at = now() WHERE id = :id',
                s=status, id=cr['target_id'])
        run(con, '''UPDATE policy_change_requests SET status = :s, decided_by = :by, decided_at = now(),
                    decision_reason = :r WHERE id = :id''',
            s='approved' if body.decision == 'approve' else 'rejected', by=admin.admin_id, r=body.reason,
            id=request_id)
        audit.record(con, actor_type='admin', actor_id=admin.admin_id, action=f'policy.change_{body.decision}d',
                     entity_type='policy_change_request', entity_id=str(request_id),
                     details={'change_type': cr['change_type'], 'target_id': cr['target_id'],
                              'requested_by': cr['requested_by'], 'reason': body.reason})
    return {'id': str(request_id), 'status': 'approved' if body.decision == 'approve' else 'rejected'}
