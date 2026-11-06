"""Provider × category trust gates (spec §8.1 subset owned by Verification; FR-VER-04, INV-03, INV-11).

Fail closed: any missing or unreadable fact is a reason to refuse. The rules live in pure functions over loaded
facts so the single-provider check (offer acceptance) and the bulk check (matching) are the same code; only the
loading differs. Callers run inside their own transactions so the check and the assignment see the same data.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import Connection

from ..core.db import many, one


def active_policy(con: Connection, category_code: str) -> dict | None:
    return one(con, '''SELECT * FROM category_policies WHERE category_code = :c AND status = 'active' ''',
               c=category_code)


def category_context(con: Connection, category_code: str) -> dict:
    return {'category': one(con, 'SELECT status FROM service_categories WHERE code = :c', c=category_code),
            'policy': active_policy(con, category_code)}


def load_trust_facts(con: Connection, provider_ids: list[str], category_code: str) -> dict[str, dict]:
    """Two queries for any number of providers. Providers without a user row are simply absent (fail closed)."""
    if not provider_ids:
        return {}
    facts = {str(r['id']): {**r, 'evidence': []} for r in many(con, '''
        SELECT u.id, u.account_state, pp.application_state, pp.account_state AS provider_state, up.state AS persona_state,
               a.status AS approval_status, a.valid_from, a.valid_until
        FROM users u
        LEFT JOIN provider_profiles pp ON pp.user_id = u.id
        LEFT JOIN user_personas up ON up.user_id = u.id AND up.persona = 'provider'
        LEFT JOIN provider_category_approvals a ON a.provider_id = u.id AND a.category_code = :c
        WHERE u.id = ANY(CAST(:ids AS uuid[]))''', ids=provider_ids, c=category_code)}
    for e in many(con, '''SELECT provider_id, evidence_type, document_expires_on, next_review_due FROM provider_evidence
                          WHERE provider_id = ANY(CAST(:ids AS uuid[])) AND review_state = 'accepted'
                            AND scan_state = 'clean' AND (category_code IS NULL OR category_code = :c)''',
                  ids=provider_ids, c=category_code):
        facts[str(e['provider_id'])]['evidence'].append(e)
    return facts


def evidence_gaps_from(rows: list[dict], policy: dict, on: date) -> list[str]:
    """Reason codes for required evidence that is missing, unaccepted or out of date on `on`."""
    gaps = []
    for req in policy['required_evidence']:
        of_type = [r for r in rows if r['evidence_type'] == req['type']]
        current = [r for r in of_type
                   if (r['document_expires_on'] is None or r['document_expires_on'] > on)
                   and (r['next_review_due'] is None or r['next_review_due'] >= on)]
        if len(current) < req.get('count', 1):
            gaps.append(f"EVIDENCE_{'EXPIRED' if len(of_type) > len(current) else 'MISSING'}:{req['type']}")
    return gaps


def gaps_from_facts(f: dict | None, ctx: dict, at: datetime) -> list[str]:
    """Empty list means eligible on trust grounds. Schedule, distance and overlap are checked by matching."""
    if not f or f['application_state'] is None:
        return ['PROVIDER_NOT_FOUND']
    reasons = []
    if f['account_state'] != 'active':
        reasons.append('ACCOUNT_INACTIVE')
    if f['persona_state'] != 'active':
        reasons.append('PROVIDER_PERSONA_INACTIVE')
    if f['provider_state'] != 'active':
        reasons.append('PROVIDER_SUSPENDED')
    if f['application_state'] != 'approved':
        reasons.append('PROFILE_NOT_APPROVED')
    if not ctx['category'] or ctx['category']['status'] != 'enabled':
        reasons.append('CATEGORY_DISABLED')
    policy = ctx['policy']
    if not policy:
        return reasons + ['CATEGORY_POLICY_MISSING']
    if policy['safeguards_required'] and policy['approval_status'] != 'APPROVED':
        reasons.append('SAFEGUARDS_NOT_APPROVED')
    if f['approval_status'] is None:
        reasons.append('CATEGORY_APPROVAL_MISSING')
    elif f['approval_status'] != 'approved':
        reasons.append(f"CATEGORY_APPROVAL_{f['approval_status'].upper()}")
    elif not (f['valid_from'] <= at < f['valid_until']):
        reasons.append('CATEGORY_APPROVAL_EXPIRED')
    # Always re-check evidence against the *current* policy, so a stricter new version applies at once.
    return reasons + evidence_gaps_from(f['evidence'], policy, at.date())


def provider_category_gaps(con: Connection, provider_id: str, category_code: str,
                           at: datetime | None = None) -> list[str]:
    at = at or datetime.now(UTC)
    facts = load_trust_facts(con, [provider_id], category_code).get(provider_id)
    return gaps_from_facts(facts, category_context(con, category_code), at)
