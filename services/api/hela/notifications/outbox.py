"""Notification outbox (FR-NOT-01/02, Q60). Messages are queued in the business transaction and delivered by
the worker through the configured adapter. Bodies carry no address, names or task details (lock-screen safe)."""
from __future__ import annotations

import json

from sqlalchemy import Connection

from ..core.db import one, run
from ..core.jobs import enqueue

TEMPLATES = {
    'offer.new': 'Hela: a new job offer is waiting. Open the app within {minutes} min to respond.',
    'request.accepted': 'Hela: a provider accepted your booking. Open the app for details.',
    'request.unfulfilled': 'Hela: no provider is available for your request. Open the app for options.',
    'request.expired': 'Hela: your request expired without a provider. Open the app for options.',
    'booking.updated': 'Hela: your booking status changed. Open the app for details.',
    'booking.cancelled': 'Hela: a booking was cancelled. Open the app for details.',
    'consent.request': ('Hela: someone has asked to book a home service for you. Reference {consent_id}. '
                        'Code {code}. Reply through the Hela consent page to confirm or decline.'),
    'checkin.pin': 'Hela: your provider check-in code is {pin}. Share it only with the provider at your door.',
    'payment.reported': 'Hela: the customer reported an offline payment. Open the app to confirm.',
}


def notify(con: Connection, *, recipient_type: str, recipient_id: str, template_key: str, dedupe_key: str,
           params: dict | None = None) -> None:
    if template_key not in TEMPLATES:
        raise KeyError(template_key)
    row = one(con, '''INSERT INTO notifications(recipient_type, recipient_id, channel, template_key, params, dedupe_key)
                      VALUES (:rt, :rid, 'sms', :t, CAST(:p AS jsonb), :d) ON CONFLICT (dedupe_key) DO NOTHING
                      RETURNING id''',
              rt=recipient_type, rid=recipient_id, t=template_key, p=json.dumps(params or {}), d=dedupe_key)
    if row:
        enqueue(con, 'notification.deliver', {'notification_id': str(row['id'])}, dedupe_key=f"notify:{row['id']}",
                max_attempts=6)


def deliver(con: Connection, payload: dict, *, cipher, sender) -> None:
    n = one(con, 'SELECT * FROM notifications WHERE id = :id FOR UPDATE', id=payload['notification_id'])
    if not n or n['status'] != 'queued':
        return  # already delivered: at-least-once job, exactly-once send per notification row
    table = 'users' if n['recipient_type'] == 'user' else 'beneficiaries'
    who = one(con, f'SELECT phone_enc FROM {table} WHERE id = :id', id=n['recipient_id'])
    phone = cipher.decrypt(who['phone_enc'], context=f'{table}.phone')
    run(con, 'UPDATE notifications SET attempts = attempts + 1 WHERE id = :id', id=n['id'])
    sender.send(con, phone_lookup=cipher.lookup_hash(phone, purpose='phone'), phone_e164=phone,
                body=TEMPLATES[n['template_key']].format(**n['params']))
    run(con, "UPDATE notifications SET status = 'sent', sent_at = now() WHERE id = :id", id=n['id'])
