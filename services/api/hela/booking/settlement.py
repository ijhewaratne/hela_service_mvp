"""Offline payment acknowledgements (FR-PAY-01..06, Q53–Q55, INV-10, AT-016, AT-017). Statements from either
party are evidence, not proof; Hela never records that it received or holds money."""
from __future__ import annotations

import csv
import io
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import Connection

from ..audit import service as audit
from ..authz.deps import AdminPrincipal, UserPrincipal, current_user, require_permission
from ..core.db import many, one, run, tx
from ..core.errors import ApiError
from ..core.services import Services, services
from ..notifications.outbox import notify
from .bookings import booking_context, role_for

router = APIRouter(prefix='/v1', tags=['settlement'])
PAYABLE = ('completed_pending_confirmation', 'completed', 'disputed')


class PaymentReportIn(BaseModel):
    reference: str | None = Field(default=None, max_length=60, pattern=r'^[A-Za-z0-9 ._/-]*$')
    note: str | None = Field(default=None, max_length=300)


class PaymentAckIn(BaseModel):
    received: bool
    note: str | None = Field(default=None, max_length=300)


class ResolveIn(BaseModel):
    resolution: Literal['paid', 'unpaid']
    evidence_note: str = Field(min_length=10, max_length=1000)


def _event(con, svc, booking_id, actor_type, actor_id, old, new, note=None) -> None:
    run(con, '''INSERT INTO settlement_events(booking_id, actor_type, actor_id, from_status, to_status, note_enc)
                VALUES (:b, :t, :a, :o, :n, :note)''', b=booking_id, t=actor_type, a=actor_id, o=old, n=new,
        note=svc.cipher.encrypt(note, context='settlement_events.note') if note else None)
    run(con, 'UPDATE settlements SET status = :n, updated_at = now() WHERE booking_id = :b', n=new, b=booking_id)


def _locked(con: Connection, booking_id, user: UserPrincipal, expected_role: str) -> tuple[dict, dict]:
    b = booking_context(con, booking_id)
    if not b or role_for(b, user) != expected_role:
        raise ApiError(404, 'NOT_FOUND', 'error.not_found')
    s = one(con, 'SELECT * FROM settlements WHERE booking_id = :b FOR UPDATE', b=booking_id)
    return b, s


def _public(s: dict) -> dict:
    return {'booking_id': str(s['booking_id']), 'currency': s['currency'], 'amount_minor': s['agreed_amount_minor'],
            'price_components': s['price_components'], 'status': s['status'], 'resolution': s['resolution'],
            'platform_collected_funds': False,
            'note_key': 'settlement.offline_only'}


@router.get('/bookings/{booking_id}/settlement')
def get_settlement(booking_id: UUID, user: UserPrincipal = Depends(current_user), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        b = booking_context(con, booking_id)
        if not b or not role_for(b, user):
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        return _public(one(con, 'SELECT * FROM settlements WHERE booking_id = :b', b=booking_id))


@router.post('/bookings/{booking_id}/payment-report')
def payment_report(booking_id: UUID, body: PaymentReportIn, user: UserPrincipal = Depends(current_user),
                   svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        b, s = _locked(con, booking_id, user, 'customer')
        if b['status'] not in PAYABLE:
            raise ApiError(409, 'NOT_PAYABLE_YET', 'settlement.not_payable')
        if s['status'] != 'UNPAID':
            raise ApiError(409, 'PAYMENT_ALREADY_REPORTED', 'settlement.already_reported', details={'status': s['status']})
        run(con, 'UPDATE settlements SET customer_reference = :r WHERE booking_id = :b', r=body.reference, b=booking_id)
        _event(con, svc, booking_id, 'user', user.user_id, 'UNPAID', 'CUSTOMER_REPORTED_PAID', body.note)
        notify(con, recipient_type='user', recipient_id=str(b['provider_id']), template_key='payment.reported',
               dedupe_key=f'payment-reported:{booking_id}')
        audit.record(con, actor_type='user', actor_id=user.user_id, action='settlement.customer_reported',
                     entity_type='booking', entity_id=str(booking_id))
        return _public(one(con, 'SELECT * FROM settlements WHERE booking_id = :b', b=booking_id))


@router.post('/bookings/{booking_id}/payment-ack')
def payment_ack(booking_id: UUID, body: PaymentAckIn, user: UserPrincipal = Depends(current_user),
                svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        b, s = _locked(con, booking_id, user, 'provider')
        if b['status'] not in PAYABLE:
            raise ApiError(409, 'NOT_PAYABLE_YET', 'settlement.not_payable')
        if body.received and s['status'] != 'CUSTOMER_REPORTED_PAID':
            raise ApiError(409, 'NO_CUSTOMER_REPORT', 'settlement.no_customer_report')
        if s['status'] in ('PROVIDER_CONFIRMED', 'DISPUTED', 'RESOLVED'):
            raise ApiError(409, 'SETTLEMENT_CLOSED', 'settlement.already_reported', details={'status': s['status']})
        new = 'PROVIDER_CONFIRMED' if body.received else 'DISPUTED'
        _event(con, svc, booking_id, 'user', user.user_id, s['status'], new, body.note)
        if new == 'DISPUTED':
            # Q53: a discrepancy is never auto-settled; it becomes a finance case for a human.
            run(con, '''INSERT INTO ops_alerts(kind, ref_type, ref_id, severity) VALUES ('settlement_dispute', 'booking',
                        :b, 'warning') ON CONFLICT DO NOTHING''', b=booking_id)
        audit.record(con, actor_type='user', actor_id=user.user_id, action=f'settlement.{new.lower()}',
                     entity_type='booking', entity_id=str(booking_id))
        return _public(one(con, 'SELECT * FROM settlements WHERE booking_id = :b', b=booking_id))


@router.post('/admin/settlements/{booking_id}/resolve')
def resolve(booking_id: UUID, body: ResolveIn, admin: AdminPrincipal = Depends(require_permission('finance.reconcile')),
            svc: Services = Depends(services)):
    admin.require('finance.reconcile')
    with tx(svc.engine) as con:
        s = one(con, 'SELECT * FROM settlements WHERE booking_id = :b FOR UPDATE', b=booking_id)
        if not s:
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        if s['status'] != 'DISPUTED':
            raise ApiError(409, 'NOT_DISPUTED', 'settlement.not_disputed')
        run(con, 'UPDATE settlements SET resolution = :r WHERE booking_id = :b', r=body.resolution, b=booking_id)
        _event(con, svc, booking_id, 'admin', admin.admin_id, 'DISPUTED', 'RESOLVED', body.evidence_note)
        run(con, "UPDATE ops_alerts SET status = 'closed', closed_at = now() WHERE kind = 'settlement_dispute' "
                 "AND ref_id = :b", b=booking_id)
        audit.record(con, actor_type='admin', actor_id=admin.admin_id, action='settlement.resolved',
                     entity_type='booking', entity_id=str(booking_id), details={'resolution': body.resolution})
        return _public(one(con, 'SELECT * FROM settlements WHERE booking_id = :b', b=booking_id))


@router.get('/admin/finance/export.csv')
def finance_export(days: int = Query(default=31, ge=1, le=366),
                   admin: AdminPrincipal = Depends(require_permission('finance.reconcile')),
                   svc: Services = Depends(services)):
    """Q55: no names, phone numbers, addresses or free text."""
    admin.require('finance.reconcile')
    with tx(svc.engine) as con:
        rows = many(con, '''SELECT s.booking_id, r.start_at::date AS service_date, r.category_code, s.currency,
                                   s.agreed_amount_minor, s.status, s.resolution, c.rate_bps, c.amount_minor AS commission_minor,
                                   c.status AS commission_status
                            FROM settlements s JOIN bookings b ON b.id = s.booking_id
                            JOIN service_requests r ON r.id = b.request_id
                            JOIN commission_receivables c ON c.booking_id = s.booking_id
                            WHERE r.start_at > now() - make_interval(days => :d) ORDER BY r.start_at''', d=days)
        audit.record(con, actor_type='admin', actor_id=admin.admin_id, action='finance.exported', entity_type='finance',
                     entity_id='settlements', details={'rows': len(rows), 'days': days})
    buf = io.StringIO()
    fields = ['booking_id', 'service_date', 'category_code', 'currency', 'agreed_amount_minor', 'status', 'resolution',
              'rate_bps', 'commission_minor', 'commission_status']
    writer = csv.DictWriter(buf, fieldnames=fields + ['platform_collected_funds'])
    writer.writeheader()
    for r in rows:
        writer.writerow({**{k: r[k] for k in fields}, 'platform_collected_funds': 'false'})
    headers = {'Cache-Control': 'no-store', 'Content-Disposition': 'attachment; filename=settlements.csv'}
    return Response(buf.getvalue(), media_type='text/csv', headers=headers)
