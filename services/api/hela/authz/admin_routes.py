"""Super-admin role grants with step-up, reasons and no self-grants (Q11, Q12, Q14)."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..audit import service as audit
from ..core.db import many, one, tx
from ..core.errors import ApiError
from ..core.services import Services, services
from .deps import AdminPrincipal, require_permission, require_step_up

router = APIRouter(prefix='/v1/admin', tags=['admin-access'])


class RoleGrantIn(BaseModel):
    role_code: str = Field(pattern=r'^[a-z_]{3,40}$')
    region_id: UUID | None = None
    reason: str = Field(min_length=5, max_length=500)


class RoleRevokeIn(BaseModel):
    reason: str = Field(min_length=5, max_length=500)


@router.get('/admins')
def list_admins(admin: AdminPrincipal = Depends(require_permission('admin.manage')),
                svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        rows = many(con, '''SELECT a.id, a.username, a.state, a.last_login_at,
                                   coalesce(json_agg(json_build_object('assignment_id', r.id, 'role', r.role_code,
                                                                       'region_id', r.region_id))
                                            FILTER (WHERE r.id IS NOT NULL), '[]') AS roles
                            FROM admin_users a LEFT JOIN admin_role_assignments r
                              ON r.admin_id = a.id AND r.revoked_at IS NULL
                            GROUP BY a.id ORDER BY a.username''')
    return [{**r, 'id': str(r['id'])} for r in rows]


@router.post('/admins/{admin_id}/roles', status_code=201)
def grant_role(admin_id: UUID, body: RoleGrantIn,
               admin: AdminPrincipal = Depends(require_permission('admin.manage')),
               svc: Services = Depends(services)):
    if str(admin_id) == admin.admin_id:
        raise ApiError(403, 'SELF_GRANT_FORBIDDEN', 'authz.permission_denied')
    with tx(svc.engine) as con:
        require_step_up(con, admin, 'role.grant')
        if not one(con, 'SELECT 1 AS ok FROM admin_users WHERE id = :id', id=admin_id):
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        if not one(con, 'SELECT 1 AS ok FROM admin_roles WHERE code = :c', c=body.role_code):
            raise ApiError(422, 'UNKNOWN_ROLE', 'error.validation')
        if body.region_id and not one(con, 'SELECT 1 AS ok FROM service_regions WHERE id = :r', r=body.region_id):
            raise ApiError(422, 'UNKNOWN_REGION', 'error.validation')
        row = one(con, '''INSERT INTO admin_role_assignments(admin_id, role_code, region_id, granted_by, reason)
                          VALUES (:a, :r, :reg, :by, :reason) ON CONFLICT DO NOTHING RETURNING id''',
                  a=admin_id, r=body.role_code, reg=body.region_id, by=admin.admin_id, reason=body.reason)
        if not row:
            raise ApiError(409, 'ROLE_ALREADY_GRANTED', 'error.bad_request')
        audit.record(con, actor_type='admin', actor_id=admin.admin_id, action='admin.role_granted',
                     entity_type='admin_user', entity_id=str(admin_id),
                     details={'role': body.role_code, 'region_id': body.region_id, 'reason': body.reason})
    return {'assignment_id': str(row['id']), 'role_code': body.role_code,
            'region_id': str(body.region_id) if body.region_id else None}


@router.post('/role-assignments/{assignment_id}/revoke')
def revoke_role(assignment_id: UUID, body: RoleRevokeIn,
                admin: AdminPrincipal = Depends(require_permission('admin.manage')),
                svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        require_step_up(con, admin, 'role.grant')
        row = one(con, '''UPDATE admin_role_assignments SET revoked_at = now()
                          WHERE id = :id AND revoked_at IS NULL RETURNING admin_id, role_code''', id=assignment_id)
        if not row:
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        audit.record(con, actor_type='admin', actor_id=admin.admin_id, action='admin.role_revoked',
                     entity_type='admin_user', entity_id=str(row['admin_id']),
                     details={'role': row['role_code'], 'assignment_id': assignment_id, 'reason': body.reason})
    return {'revoked': True}
