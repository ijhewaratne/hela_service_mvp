"""Policy and price versions drafted via the API and published with maker/checker (AT-027, spec §4.2)."""
import uuid

import pytest
from conftest import bearer, drain, step_up
from sqlalchemy.exc import DBAPIError

DRAFT = {
    'allowed_tasks': ['dusting', 'tidying', 'window_cleaning_ground_floor'],
    'prohibited_tasks': ['medication_administration', 'clinical_tasks', 'working_at_height'],
    'min_duration_minutes': 60, 'max_duration_minutes': 360, 'max_radius_km': 8,
    'required_evidence': [{'type': 'national_id'}, {'type': 'reference', 'count': 2}, {'type': 'training_record'},
                          {'type': 'police_clearance'}],
    'justification': 'Add ground-floor windows; require police clearance for pilot',
}


@pytest.fixture
def two_admins(make_admin, admin_login, client):
    _, a_clock = make_admin('ops.alice')
    _, b_clock = make_admin('ops.bob')
    alice, bob = admin_login('ops.alice', a_clock), admin_login('ops.bob', b_clock)
    step_up(client, alice, a_clock, 'policy.change')
    step_up(client, bob, b_clock, 'policy.change')
    return alice, bob


def test_publish_policy_version_with_maker_checker(world, client, two_admins, db):
    alice, bob = two_admins
    c_tok, addr = world.customer()
    _, pid = world.provider('+94771000001')
    old_quote = world.quote(c_tok, addr).json()

    r = client.post('/v1/admin/categories/household_cleaning/policy-drafts', headers=bearer(alice), json=DRAFT)
    assert r.status_code == 201, r.text
    draft = r.json()
    assert draft['version'] == 2 and draft['status'] == 'draft'
    assert [c['policy_version'] for c in client.get('/v1/categories').json() if c['code'] == 'household_cleaning'] == [1]

    cr = draft['change_request']['id']
    own = client.post(f'/v1/admin/change-requests/{cr}/decision', headers=bearer(alice),
                      json={'decision': 'approve', 'reason': 'Self approval attempt'})
    assert own.json()['error']['code'] == 'SELF_APPROVAL_FORBIDDEN'
    ok = client.post(f'/v1/admin/change-requests/{cr}/decision', headers=bearer(bob),
                     json={'decision': 'approve', 'reason': 'Reviewed side by side with v1'})
    assert ok.json()['status'] == 'approved'

    live = [c for c in client.get('/v1/categories').json() if c['code'] == 'household_cleaning'][0]
    assert live['policy_version'] == 2 and 'window_cleaning_ground_floor' in live['allowed_tasks']
    assert live['approval_status'] == 'UNAPPROVED'            # publishing never claims business/legal sign-off
    versions = client.get('/v1/admin/categories/household_cleaning/policies', headers=bearer(bob)).json()['policies']
    assert [(v['version'], v['status']) for v in versions] == [(2, 'active'), (1, 'retired')]

    # Quotes priced under v1 can no longer be turned into requests: the customer must see the new terms.
    client.post(f"/v1/quotes/{old_quote['quote_id']}/accept", headers=bearer(c_tok), json={'policy_version': 1})
    r = client.post('/v1/requests', headers={**bearer(c_tok), 'Idempotency-Key': f'k-{uuid.uuid4().hex}'},
                    json={'quote_id': old_quote['quote_id'], 'task_codes': ['dusting']})
    assert r.json()['error']['code'] == 'TERMS_CHANGED'

    # The stricter evidence rule applies to existing approvals immediately (fail closed).
    from hela.core.db import tx
    from hela.verification.eligibility import provider_category_gaps
    with tx(world.app.state.services.engine) as con:
        assert provider_category_gaps(con, pid, 'household_cleaning') == ['EVIDENCE_MISSING:police_clearance']
    assert db("SELECT count(*) AS n FROM audit_logs WHERE action = 'policy.published'")[0]['n'] == 1


def test_price_publish_changes_new_quotes_only(world, client, two_admins):
    alice, bob = two_admins
    c_tok, addr = world.customer()
    assert world.quote(c_tok, addr).json()['total_minor'] == 240000
    d = client.post('/v1/admin/categories/household_cleaning/price-drafts', headers=bearer(alice),
                    json={'hourly_rate_minor': 150000, 'base_fee_minor': 20000, 'justification': 'Synthetic rate test v2'}).json()
    client.post(f"/v1/admin/change-requests/{d['change_request']['id']}/decision", headers=bearer(bob),
                json={'decision': 'approve', 'reason': 'Synthetic tariff check'})
    q = world.quote(c_tok, addr).json()
    assert q['price_rule_version'] == 2 and q['total_minor'] == 20000 + 300000


def test_rejected_draft_never_goes_live(world, client, two_admins):
    alice, bob = two_admins
    d = client.post('/v1/admin/categories/household_cleaning/policy-drafts', headers=bearer(alice), json=DRAFT).json()
    client.post(f"/v1/admin/change-requests/{d['change_request']['id']}/decision", headers=bearer(bob),
                json={'decision': 'reject', 'reason': 'Police clearance not lawful yet'})
    assert [c['policy_version'] for c in client.get('/v1/categories').json() if c['code'] == 'household_cleaning'] == [1]


@pytest.mark.parametrize('change,message', [
    ({'prohibited_tasks': ['working_at_height']}, 'must always include'),
    ({'allowed_tasks': ['dusting', 'clinical_tasks']}, 'both allowed and prohibited'),
    ({'required_evidence': [{'type': 'reference'}]}, 'national_id'),
    ({'min_duration_minutes': 45}, 'multiples of 30'),
    ({'max_radius_km': 40}, 'less than or equal'),
    ({'approval_status': 'APPROVED'}, 'Extra inputs'),
])
def test_invalid_drafts_refused(world, client, two_admins, change, message):
    alice, _ = two_admins
    r = client.post('/v1/admin/categories/household_cleaning/policy-drafts', headers=bearer(alice), json={**DRAFT, **change})
    assert r.status_code == 422


def test_forbidden_category_and_permissions(world, client, two_admins, make_admin, admin_login):
    alice, _ = two_admins
    assert client.post('/v1/admin/categories/childcare/policy-drafts', headers=bearer(alice),
                       json=DRAFT).json()['error']['code'] == 'CATEGORY_FORBIDDEN'
    _, clock = make_admin('ops.olga', roles=[('operations_admin', None)])
    ops = admin_login('ops.olga', clock)
    assert client.post('/v1/admin/categories/household_cleaning/policy-drafts', headers=bearer(ops),
                       json=DRAFT).json()['error']['code'] == 'PERMISSION_DENIED'


def test_app_role_cannot_mark_approved_or_edit_content(world, client, two_admins):
    from conftest import APP_URL
    from sqlalchemy import create_engine, text
    engine = create_engine(APP_URL)
    for stmt in ("UPDATE category_policies SET approval_status = 'APPROVED'",
                 "UPDATE category_policies SET max_radius_km = 20",
                 "UPDATE price_rules SET hourly_rate_minor = 1"):
        with pytest.raises(DBAPIError):
            with engine.begin() as con:
                con.execute(text(stmt))
    engine.dispose()
    drain(world.app)
