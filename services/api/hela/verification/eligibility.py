"""Provider × category trust gates (spec §8.1 subset owned by Verification; FR-VER-04, INV-03, INV-11).

Fail closed: any missing or unreadable fact is a reason to refuse. Matching and offer acceptance (M03)
call this inside their own transactions so the check and the assignment see the same data.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import Connection

from ..core.db import many, one


def active_policy(con: Connection, category_code: str) -> dict | None:
    return one(con, '''SELECT * FROM category_policies WHERE category_code = :c AND status = 'active' ''',
               c=category_code)


def evidence_gaps(con: Connection, provider_id: str, policy: dict, on: date) -> list[str]:
    """Reason codes for required evidence that is missing, unaccepted or out of date on `on`."""
    rows = many(con, '''SELECT evidence_type, document_expires_on, next_review_due FROM provider_evidence
                        WHERE provider_id = :p AND review_state = 'accepted' AND scan_state = 'clean'
                          AND (category_code IS NULL OR category_code = :c)''',
                p=provider_id, c=policy['category_code'])
    gaps = []
    for req in policy['required_evidence']:
        of_type = [r for r in rows if r['evidence_type'] == req['type']]
        current = [r for r in of_type
                   if (r['document_expires_on'] is None or r['document_expires_on'] > on)
                   and (r['next_review_due'] is None or r['next_review_due'] >= on)]
        if len(current) < req.get('count', 1):
            gaps.append(f"EVIDENCE_{'EXPIRED' if len(of_type) > len(current) else 'MISSING'}:{req['type']}")
    return gaps


def provider_category_gaps(con: Connection, provider_id: str, category_code: str,
                           at: datetime | None = None) -> list[str]:
    """Empty list means eligible on trust grounds. Schedule, distance and overlap are checked by matching."""
    at = at or datetime.now(UTC)
    p = one(con, '''SELECT u.account_state, pp.application_state, pp.account_state AS provider_state,
                           up.state AS persona_state
                    FROM users u
                    LEFT JOIN provider_profiles pp ON pp.user_id = u.id
                    LEFT JOIN user_personas up ON up.user_id = u.id AND up.persona = 'provider'
                    WHERE u.id = :p''', p=provider_id)
    if not p or p['application_state'] is None:
        return ['PROVIDER_NOT_FOUND']
    reasons = []
    if p['account_state'] != 'active':
        reasons.append('ACCOUNT_INACTIVE')
    if p['persona_state'] != 'active':
        reasons.append('PROVIDER_PERSONA_INACTIVE')
    if p['provider_state'] != 'active':
        reasons.append('PROVIDER_SUSPENDED')
    if p['application_state'] != 'approved':
        reasons.append('PROFILE_NOT_APPROVED')

    category = one(con, 'SELECT status FROM service_categories WHERE code = :c', c=category_code)
    if not category or category['status'] != 'enabled':
        reasons.append('CATEGORY_DISABLED')
    policy = active_policy(con, category_code)
    if not policy:
        return reasons + ['CATEGORY_POLICY_MISSING']
    if policy['safeguards_required'] and policy['approval_status'] != 'APPROVED':
        reasons.append('SAFEGUARDS_NOT_APPROVED')

    approval = one(con, '''SELECT status, valid_from, valid_until FROM provider_category_approvals
                           WHERE provider_id = :p AND category_code = :c''', p=provider_id, c=category_code)
    if not approval:
        reasons.append('CATEGORY_APPROVAL_MISSING')
    elif approval['status'] != 'approved':
        reasons.append(f"CATEGORY_APPROVAL_{approval['status'].upper()}")
    elif not (approval['valid_from'] <= at < approval['valid_until']):
        reasons.append('CATEGORY_APPROVAL_EXPIRED')
    # Always re-check evidence against the *current* policy, so a stricter new version applies at once.
    reasons += evidence_gaps(con, provider_id, policy, at.date())
    return reasons
