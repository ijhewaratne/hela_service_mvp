"""Restricted audit reads with cursor pagination and a chain-integrity check (§13.2, Q64)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..authz.deps import AdminPrincipal, require_permission
from ..core.db import many, one, tx
from ..core.services import Services, services
from . import service as audit_service

router = APIRouter(prefix='/v1/admin/audit', tags=['audit'])


@router.get('')
def read_audit(before_id: int | None = Query(default=None, ge=1), limit: int = Query(default=50, ge=1, le=200),
               action: str | None = Query(default=None, max_length=80),
               admin: AdminPrincipal = Depends(require_permission('audit.read')),
               svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        rows = many(con, '''SELECT a.id, a.occurred_at, a.actor_type, a.actor_id, a.action, a.entity_type, a.entity_id,
                                   a.details, a.correlation_id, c.row_hash
                            FROM audit_logs a LEFT JOIN audit_chain c ON c.audit_id = a.id
                            WHERE (CAST(:before AS bigint) IS NULL OR a.id < :before)
                              AND (CAST(:action AS text) IS NULL OR a.action = :action)
                            ORDER BY a.id DESC LIMIT :limit''', before=before_id, action=action, limit=limit)
    next_cursor = rows[-1]['id'] if len(rows) == limit else None
    return {'items': rows, 'next_before_id': next_cursor}


@router.get('/verify')
def verify_chain(admin: AdminPrincipal = Depends(require_permission('audit.read')),
                 svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        audit_service.seal(con)
    with tx(svc.engine) as con:
        broken = one(con, 'SELECT audit_verify_chain() AS first_bad')['first_bad']
        total = one(con, 'SELECT count(*) AS n FROM audit_chain')['n']
        unsealed = one(con, 'SELECT count(*) AS n FROM audit_logs a WHERE NOT EXISTS '
                            '(SELECT 1 FROM audit_chain c WHERE c.audit_id = a.id)')['n']
    return {'intact': broken is None, 'first_inconsistent_id': broken, 'rows_checked': total, 'unsealed_rows': unsealed}
