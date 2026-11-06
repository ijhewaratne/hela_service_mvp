"""Restricted audit reads with cursor pagination and a chain-integrity check (§13.2, Q64)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..authz.deps import AdminPrincipal, require_permission
from ..core.db import many, one, tx
from ..core.services import Services, services

router = APIRouter(prefix='/v1/admin/audit', tags=['audit'])


@router.get('')
def read_audit(before_id: int | None = Query(default=None, ge=1), limit: int = Query(default=50, ge=1, le=200),
               action: str | None = Query(default=None, max_length=80),
               admin: AdminPrincipal = Depends(require_permission('audit.read')),
               svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        rows = many(con, '''SELECT id, occurred_at, actor_type, actor_id, action, entity_type, entity_id, details,
                                   correlation_id, row_hash FROM audit_logs
                            WHERE (CAST(:before AS bigint) IS NULL OR id < :before)
                              AND (CAST(:action AS text) IS NULL OR action = :action)
                            ORDER BY id DESC LIMIT :limit''', before=before_id, action=action, limit=limit)
    next_cursor = rows[-1]['id'] if len(rows) == limit else None
    return {'items': rows, 'next_before_id': next_cursor}


@router.get('/verify')
def verify_chain(admin: AdminPrincipal = Depends(require_permission('audit.read')),
                 svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        broken = one(con, 'SELECT audit_verify_chain() AS first_bad')['first_bad']
        total = one(con, 'SELECT count(*) AS n FROM audit_logs')['n']
    return {'intact': broken is None, 'first_inconsistent_id': broken, 'rows_checked': total}
