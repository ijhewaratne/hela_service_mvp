"""Scheduled expiry of evidence and approvals (FR-VER-04, FR-VER-07). Idempotent; safe to run repeatedly.

Eligibility checks dates directly, so a late or missed run never makes an expired provider eligible;
this job keeps stored statuses honest and records the history.
"""
from __future__ import annotations

from sqlalchemy import Connection

from ..audit import service as audit
from ..core.db import many, run


def expire_due(con: Connection) -> dict:
    evidence = many(con, '''UPDATE provider_evidence SET review_state = 'expired'
                            WHERE review_state = 'accepted'
                              AND (document_expires_on <= current_date OR next_review_due < current_date)
                            RETURNING id, provider_id, evidence_type''')
    for e in evidence:
        audit.record(con, actor_type='system', actor_id='expiry-job', action='evidence.expired',
                     entity_type='provider_evidence', entity_id=str(e['id']),
                     details={'provider_id': e['provider_id'], 'evidence_type': e['evidence_type']})
    approvals = many(con, '''UPDATE provider_category_approvals SET status = 'expired', updated_at = now()
                             WHERE status = 'approved' AND valid_until <= now()
                             RETURNING id, provider_id, category_code, policy_id''')
    for a in approvals:
        run(con, '''INSERT INTO provider_approval_events(approval_id, actor_type, actor_id, from_status, to_status,
                        policy_id, reason)
                    VALUES (:id, 'system', 'expiry-job', 'approved', 'expired', :pol, 'validity period ended')''',
            id=a['id'], pol=a['policy_id'])
        audit.record(con, actor_type='system', actor_id='expiry-job', action='provider.category_expired',
                     entity_type='provider_category_approval', entity_id=str(a['id']),
                     details={'provider_id': a['provider_id'], 'category': a['category_code']})
    return {'evidence_expired': len(evidence), 'approvals_expired': len(approvals)}
