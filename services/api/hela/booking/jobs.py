"""Background job handlers for the booking engine. All are idempotent (at-least-once delivery)."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Connection

from ..audit import service as audit
from ..core.db import one, run
from ..core.jobs import Handler, enqueue
from ..core.services import Services
from ..notifications.outbox import deliver, notify
from .matching import dispatch_round
from .requests import decide_consent, set_request_status


def handlers(svc: Services) -> dict[str, Handler]:
    s = svc.settings

    def dispatch(con: Connection, p: dict) -> None:
        dispatch_round(con, s, p['request_id'])

    def offer_expire(con: Connection, p: dict) -> None:
        o = one(con, 'SELECT * FROM offers WHERE id = :id FOR UPDATE', id=p['offer_id'])
        if o and o['status'] == 'pending' and o['expires_at'] <= datetime.now(UTC):
            run(con, "UPDATE offers SET status = 'expired', responded_at = now() WHERE id = :id", id=o['id'])
            enqueue(con, 'dispatch.request', {'request_id': str(o['request_id'])},
                    dedupe_key=f"dispatch:{o['request_id']}:expired:{o['id']}")

    def request_expire(con: Connection, p: dict) -> None:
        r = one(con, 'SELECT * FROM service_requests WHERE id = :id FOR UPDATE', id=p['request_id'])
        if not r or r['status'] not in ('requested', 'awaiting_beneficiary', 'offering', 'unfulfilled'):
            return
        if one(con, 'SELECT 1 AS ok FROM bookings WHERE request_id = :id', id=r['id']):
            return   # a booking in reassignment is handled by operations, not silently expired
        run(con, "UPDATE offers SET status = 'revoked', responded_at = now() WHERE request_id = :r AND status = 'pending'",
            r=r['id'])
        set_request_status(con, r['id'], r['status'], 'expired', 'system', 'expiry-job', 'start time too close')
        notify(con, recipient_type='user', recipient_id=str(r['customer_id']), template_key='request.expired',
               dedupe_key=f"request-expired:{r['id']}")
        audit.record(con, actor_type='system', actor_id='expiry-job', action='request.expired',
                     entity_type='service_request', entity_id=str(r['id']))

    def consent_expire(con: Connection, p: dict) -> None:
        c = one(con, 'SELECT * FROM beneficiary_consents WHERE id = :id FOR UPDATE', id=p['consent_id'])
        if c and c['status'] == 'pending' and c['expires_at'] <= datetime.now(UTC):
            decide_consent(con, c, 'expired', actor_type='system', actor_id='consent-expiry')

    def completion_review(con: Connection, p: dict) -> None:
        b = one(con, 'SELECT status FROM bookings WHERE id = :id', id=p['booking_id'])
        if b and b['status'] == 'completed_pending_confirmation':
            # Q49: never auto-complete; a human looks at it.
            run(con, '''INSERT INTO ops_alerts(kind, ref_type, ref_id, severity)
                        VALUES ('completion_unconfirmed', 'booking', :b, 'warning') ON CONFLICT DO NOTHING''',
                b=p['booking_id'])

    def notification(con: Connection, p: dict) -> None:
        deliver(con, p, cipher=svc.cipher, sender=svc.sms)

    return {'dispatch.request': dispatch, 'offer.expire': offer_expire, 'request.expire': request_expire,
            'consent.expire': consent_expire, 'completion.review': completion_review,
            'notification.deliver': notification}
