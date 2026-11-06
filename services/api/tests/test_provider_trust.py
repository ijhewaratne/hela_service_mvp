"""Provider onboarding, evidence handling, reviewer decisions, eligibility and expiry
(FR-VER-01..08, FR-ID-06, SEC-05, SEC-07, Q13, Q35–Q39; AT-001, AT-002, AT-010, AT-014, AT-024)."""
from datetime import UTC, date, datetime, timedelta

import boto3
import pytest
from conftest import EICAR, PDF, PNG, bearer, step_up, upload
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.usefixtures('categories')


# --- application and upload -------------------------------------------------------------------------

def test_apply_rejects_underage_and_duplicates(client, login):
    tokens = login()
    young = (datetime.now(UTC).date() - timedelta(days=17 * 365)).isoformat()
    r = client.post('/v1/provider/apply', headers=bearer(tokens), json={'legal_name': 'Too Young', 'date_of_birth': young})
    assert r.json()['error']['code'] == 'PROVIDER_UNDERAGE'
    body = {'legal_name': 'Synthetic Provider', 'date_of_birth': '1990-01-01'}
    assert client.post('/v1/provider/apply', headers=bearer(tokens), json=body).status_code == 201
    assert client.post('/v1/provider/apply', headers=bearer(tokens), json=body).json()['error']['code'] == 'ALREADY_APPLIED'
    me = client.get('/v1/me', headers=bearer(tokens)).json()
    assert me['personas'] == ['customer', 'provider']


def test_identity_fields_are_encrypted(client, login, db):
    tokens = login()
    client.post('/v1/provider/apply', headers=bearer(tokens), json={'legal_name': 'Kamala Perera', 'date_of_birth': '1985-02-03'})
    row = db('SELECT legal_name_enc, date_of_birth_enc FROM provider_profiles')[0]
    assert b'Kamala' not in bytes(row['legal_name_enc']) and b'1985' not in bytes(row['date_of_birth_enc'])


@pytest.mark.parametrize('data,media_type,code,status', [
    (b'GIF89a......', 'image/gif', 'UNSUPPORTED_FILE_TYPE', 422),
    (PDF, 'image/png', 'CONTENT_TYPE_MISMATCH', 422),
    (b'%PDF-1.4 /OpenAction << /S /JavaScript >>', 'application/pdf', 'PDF_ACTIVE_CONTENT', 422),
    (b'', 'application/pdf', 'EMPTY_FILE', 422),
])
def test_upload_validation(client, login, data, media_type, code, status):
    tokens = login()
    client.post('/v1/provider/apply', headers=bearer(tokens), json={'legal_name': 'Pp', 'date_of_birth': '1990-01-01'})
    r = upload(client, tokens, 'national_id', data, media_type)
    assert r.status_code == status and r.json()['error']['code'] == code


def test_upload_size_limit(client, login, app):
    app.state.services.settings.max_evidence_bytes = 1024
    tokens = login()
    client.post('/v1/provider/apply', headers=bearer(tokens), json={'legal_name': 'Pp', 'date_of_birth': '1990-01-01'})
    r = upload(client, tokens, 'national_id', PDF + b'0' * 2000)
    assert r.status_code == 413 and r.json()['error']['code'] == 'FILE_TOO_LARGE'


def test_infected_upload_is_quarantined_not_stored(client, login, db):
    tokens = login()
    client.post('/v1/provider/apply', headers=bearer(tokens), json={'legal_name': 'Pp', 'date_of_birth': '1990-01-01'})
    r = upload(client, tokens, 'national_id', PDF + EICAR)
    assert r.json()['error']['code'] == 'EVIDENCE_INFECTED'
    row = db('SELECT object_key, wrapped_data_key, scan_state, review_state FROM provider_evidence')[0]
    assert row == {'object_key': None, 'wrapped_data_key': None, 'scan_state': 'infected', 'review_state': 'rejected'}
    assert db("SELECT count(*) AS n FROM audit_logs WHERE action = 'evidence.quarantined'")[0]['n'] == 1


def test_stored_object_is_ciphertext_and_png_accepted(client, login, db, settings):
    tokens = login()
    client.post('/v1/provider/apply', headers=bearer(tokens), json={'legal_name': 'Pp', 'date_of_birth': '1990-01-01'})
    secret_marker = b'SYNTHETIC-ID-NUMBER-123456789'
    assert upload(client, tokens, 'national_id', PNG + secret_marker, 'image/png').status_code == 201
    key = db('SELECT object_key FROM provider_evidence')[0]['object_key']
    s3 = boto3.client('s3', endpoint_url=settings.s3_endpoint, aws_access_key_id=settings.s3_access_key,
                      aws_secret_access_key=settings.s3_secret_key, region_name='us-east-1')
    raw = s3.get_object(Bucket=settings.s3_bucket, Key=key)['Body'].read()
    assert secret_marker not in raw and not raw.startswith(b'\x89PNG')


def test_scanner_outage_fails_closed(client, login, app, reviewer):
    app.state.services.scanner.available = False
    tokens = login()
    client.post('/v1/provider/apply', headers=bearer(tokens), json={'legal_name': 'Pp', 'date_of_birth': '1990-01-01'})
    r = upload(client, tokens, 'national_id')
    assert r.status_code == 201 and r.json()['scan_state'] == 'pending'
    rtok, clock = reviewer()
    review = client.post(f"/v1/admin/evidence/{r.json()['id']}/review", headers=bearer(rtok),
                         json={'decision': 'accept', 'reason': 'Trying early'})
    assert review.json()['error']['code'] == 'EVIDENCE_NOT_CLEAN'
    step_up(client, rtok, clock, 'evidence.read')
    grant = client.post(f"/v1/admin/evidence/{r.json()['id']}/access-grant", headers=bearer(rtok),
                        json={'purpose': 'Review identity document'})
    assert grant.json()['error']['code'] == 'EVIDENCE_NOT_CLEAN'


def test_submit_requires_evidence_and_locks_uploads_during_review(client, login, reviewer):
    tokens = login()
    client.post('/v1/provider/apply', headers=bearer(tokens), json={'legal_name': 'Pp', 'date_of_birth': '1990-01-01'})
    assert client.post('/v1/provider/application/submit', headers=bearer(tokens)).json()['error']['code'] == 'EVIDENCE_REQUIRED'
    upload(client, tokens, 'national_id')
    assert client.post('/v1/provider/application/submit', headers=bearer(tokens)).status_code == 200
    rtok, _ = reviewer()
    pid = client.get('/v1/provider/me', headers=bearer(tokens)).json()['provider_id']
    queue = client.get('/v1/admin/verification-queue', headers=bearer(rtok)).json()
    assert [a['provider_id'] for a in queue['applications']] == [pid]
    client.post(f'/v1/admin/providers/{pid}/application/decision', headers=bearer(rtok),
                json={'decision': 'start_review', 'reason': 'Starting review'})
    assert upload(client, tokens, 'reference').json()['error']['code'] == 'EVIDENCE_UPLOAD_NOT_ALLOWED'
    client.post(f'/v1/admin/providers/{pid}/application/decision', headers=bearer(rtok),
                json={'decision': 'needs_more_info', 'reason': 'Second reference needed'})
    assert upload(client, tokens, 'reference').status_code == 201
    assert client.post('/v1/provider/application/submit', headers=bearer(tokens)).status_code == 200


# --- reviewer workflow ------------------------------------------------------------------------------

def test_full_workflow_makes_provider_eligible(client, approved_provider, gaps, db):
    tokens, pid, approval_id, rtok, _ = approved_provider()
    assert gaps(pid) == []
    approvals = client.get('/v1/provider/approvals', headers=bearer(tokens)).json()
    assert approvals[0]['status'] == 'approved' and approvals[0]['policy_version'] == 1
    history = client.get(f'/v1/admin/provider-approvals/{approval_id}/history', headers=bearer(rtok)).json()
    assert [h['to_status'] for h in history] == ['pending', 'under_review', 'approved']
    assert len(history[-1]['evidence_ids']) == 4
    case = client.get(f'/v1/admin/providers/{pid}', headers=bearer(rtok)).json()
    assert 'legal_name' not in case and case['approvals'][0]['eligibility_gaps'] == []
    # Approvals are per category: cleaning does not qualify the provider for elder companionship (AT-002).
    assert 'CATEGORY_APPROVAL_MISSING' in gaps(pid, 'elder_companionship')
    assert 'SAFEGUARDS_NOT_APPROVED' in gaps(pid, 'elder_companionship')


def test_unapproved_provider_is_never_eligible(client, applicant, gaps):
    tokens, _ = applicant()
    pid = client.get('/v1/provider/me', headers=bearer(tokens)).json()['provider_id']
    reasons = gaps(pid)
    assert 'PROFILE_NOT_APPROVED' in reasons and 'CATEGORY_APPROVAL_MISSING' in reasons
    assert 'EVIDENCE_MISSING:national_id' in reasons


def test_application_approval_needs_attestation_and_accepted_identity(client, applicant, reviewer):
    tokens, ids = applicant()
    rtok, _ = reviewer()
    pid = client.get('/v1/provider/me', headers=bearer(tokens)).json()['provider_id']
    decide = lambda **b: client.post(f'/v1/admin/providers/{pid}/application/decision', headers=bearer(rtok),  # noqa: E731
                                     json={'reason': 'Reviewing application', **b}).json()
    assert decide(decision='approve')['error']['code'] == 'TRANSITION_NOT_ALLOWED'
    decide(decision='start_review')
    assert decide(decision='approve')['error']['code'] == 'VERIFICATION_ATTESTATION_REQUIRED'
    attest = {'identity_verified': True, 'age_verification_method': 'id_document_inspected'}
    assert decide(decision='approve', **attest)['error']['code'] == 'IDENTITY_EVIDENCE_REQUIRED'
    client.post(f"/v1/admin/evidence/{ids['national_id'][0]}/review", headers=bearer(rtok),
                json={'decision': 'accept', 'reason': 'Matches applicant'})
    assert decide(decision='approve', **attest)['application_state'] == 'approved'


def test_category_approval_gates_cannot_be_bypassed(client, applicant, reviewer):
    tokens, ids = applicant()
    rtok, clock = reviewer()
    pid = client.get('/v1/provider/me', headers=bearer(tokens)).json()['provider_id']
    approval = client.post('/v1/provider/categories', headers=bearer(tokens),
                           json={'category_code': 'household_cleaning'}).json()
    decide = lambda d: client.post(f"/v1/admin/provider-approvals/{approval['id']}/decision",  # noqa: E731
                                   headers=bearer(rtok), json={'decision': d, 'reason': 'Category review'}).json()
    decide('start_review')
    assert decide('approve')['error']['code'] == 'STEP_UP_REQUIRED'
    step_up(client, rtok, clock, 'category.approve')
    blocked = decide('approve')['error']
    assert blocked['code'] == 'APPROVAL_GATES_NOT_MET'
    assert 'PROFILE_NOT_APPROVED' in blocked['details']['reason_codes']
    assert 'EVIDENCE_MISSING:reference' in blocked['details']['reason_codes']
    assert pid  # provider exists; nothing was approved


def test_disabled_category_cannot_be_requested(client, applicant):
    tokens, _ = applicant()
    for code in ('childcare', 'elder_companionship', 'general_assistance', 'no_such_category'):
        r = client.post('/v1/provider/categories', headers=bearer(tokens), json={'category_code': code})
        assert r.json()['error']['code'] == 'CATEGORY_NOT_AVAILABLE', code


def test_provider_cannot_use_reviewer_endpoints(client, applicant):
    tokens, ids = applicant()
    r = client.post(f"/v1/admin/evidence/{ids['national_id'][0]}/review", headers=bearer(tokens),
                    json={'decision': 'accept', 'reason': 'Self approval attempt'})
    assert r.status_code == 401  # customer/provider tokens are not admin tokens


def test_other_admin_roles_cannot_review(client, applicant, make_admin, admin_login):
    _, ids = applicant()
    _, clock = make_admin('fin.fred', roles=[('finance_reconciler', None)])
    tokens = admin_login('fin.fred', clock)
    r = client.post(f"/v1/admin/evidence/{ids['national_id'][0]}/review", headers=bearer(tokens),
                    json={'decision': 'accept', 'reason': 'Not my job'})
    assert r.json()['error']['code'] == 'PERMISSION_DENIED'


# --- sensitive access ---------------------------------------------------------------------------------

def test_evidence_download_is_one_time_logged_and_bound_to_admin(client, applicant, reviewer, db):
    _, ids = applicant()
    eid = ids['national_id'][0]
    rtok, clock = reviewer()
    other, _ = reviewer('review.ravi')
    purpose = {'purpose': 'Verify identity document'}
    assert client.post(f'/v1/admin/evidence/{eid}/access-grant', headers=bearer(rtok),
                       json=purpose).json()['error']['code'] == 'STEP_UP_REQUIRED'
    step_up(client, rtok, clock, 'evidence.read')
    grant = client.post(f'/v1/admin/evidence/{eid}/access-grant', headers=bearer(rtok), json=purpose).json()
    assert grant['expires_in'] <= 60
    assert client.get(grant['download_path'], headers=bearer(other)).status_code == 404  # bound to the grantee
    r = client.get(grant['download_path'], headers=bearer(rtok))
    assert r.status_code == 200 and r.content == PDF
    assert r.headers['cache-control'] == 'no-store' and r.headers['content-disposition'].startswith('attachment')
    assert client.get(grant['download_path'], headers=bearer(rtok)).status_code == 404  # single use
    logs = db('SELECT access_kind, purpose FROM sensitive_access_logs ORDER BY id')
    assert [(x['access_kind'], x['purpose']) for x in logs] == [
        ('download_grant', 'Verify identity document'), ('download', 'one-time grant redeemed')]
    with pytest.raises(DBAPIError):
        db('DELETE FROM sensitive_access_logs')


def test_expired_download_grant_rejected(client, applicant, reviewer, db):
    _, ids = applicant()
    rtok, clock = reviewer()
    step_up(client, rtok, clock, 'evidence.read')
    grant = client.post(f"/v1/admin/evidence/{ids['national_id'][0]}/access-grant", headers=bearer(rtok),
                        json={'purpose': 'Verify identity document'}).json()
    db("UPDATE evidence_access_tokens SET expires_at = now() - interval '1 second'")
    assert client.get(grant['download_path'], headers=bearer(rtok)).status_code == 404


def test_identity_view_requires_step_up_and_is_logged(client, applicant, reviewer, db):
    tokens, _ = applicant()
    pid = client.get('/v1/provider/me', headers=bearer(tokens)).json()['provider_id']
    rtok, clock = reviewer()
    path, body = f'/v1/admin/providers/{pid}/identity-view', {'purpose': 'Age check before approval'}
    assert client.post(path, headers=bearer(rtok), json=body).json()['error']['code'] == 'STEP_UP_REQUIRED'
    step_up(client, rtok, clock, 'evidence.read')
    view = client.post(path, headers=bearer(rtok), json=body).json()
    assert view['legal_name'] == 'Synthetic Provider' and view['date_of_birth'] == '1990-05-17'
    assert db('SELECT resource_type, purpose FROM sensitive_access_logs') == [
        {'resource_type': 'provider_identity', 'purpose': 'Age check before approval'}]


# --- expiry, policy versions, suspension --------------------------------------------------------------

def test_expired_evidence_blocks_immediately_and_job_records_it(client, approved_provider, gaps, db, app):
    _, pid, approval_id, _, _ = approved_provider()
    db("UPDATE provider_evidence SET next_review_due = current_date - 1 WHERE evidence_type = 'training_record'")
    assert gaps(pid) == ['EVIDENCE_EXPIRED:training_record']  # before any job runs: fail closed
    db("UPDATE provider_category_approvals SET valid_from = now() - interval '2 days', valid_until = now() - interval '1 minute'")
    from hela.core.db import tx
    from hela.verification.expiry import expire_due
    with tx(app.state.services.engine) as con:
        assert expire_due(con) == {'evidence_expired': 1, 'approvals_expired': 1}
    with tx(app.state.services.engine) as con:
        assert expire_due(con) == {'evidence_expired': 0, 'approvals_expired': 0}
    assert 'CATEGORY_APPROVAL_EXPIRED' in gaps(pid)
    statuses = [e['to_status'] for e in db('SELECT to_status FROM provider_approval_events WHERE approval_id = :a ORDER BY id',
                                            a=approval_id)]
    assert statuses[-1] == 'expired'


def test_approval_validity_bounded_by_document_expiry(client, applicant, reviewer, db):
    tokens, ids = applicant()
    expiry = date.today() + timedelta(days=60)
    db('UPDATE provider_evidence SET document_expires_on = :d WHERE id = :id', d=expiry, id=ids['national_id'][0])
    rtok, clock = reviewer()
    pid = client.get('/v1/provider/me', headers=bearer(tokens)).json()['provider_id']
    for i in [i for g in ids.values() for i in g]:
        client.post(f'/v1/admin/evidence/{i}/review', headers=bearer(rtok), json={'decision': 'accept', 'reason': 'Checked'})
    for d in ('start_review', 'approve'):
        client.post(f'/v1/admin/providers/{pid}/application/decision', headers=bearer(rtok),
                    json={'decision': d, 'reason': 'Checked', 'identity_verified': True,
                          'age_verification_method': 'in_person_id_check'})
    a = client.post('/v1/provider/categories', headers=bearer(tokens), json={'category_code': 'errands'}).json()
    client.post(f"/v1/admin/provider-approvals/{a['id']}/decision", headers=bearer(rtok),
                json={'decision': 'start_review', 'reason': 'Checked'})
    step_up(client, rtok, clock, 'category.approve')
    r = client.post(f"/v1/admin/provider-approvals/{a['id']}/decision", headers=bearer(rtok),
                    json={'decision': 'approve', 'reason': 'Checked'}).json()
    assert datetime.fromisoformat(r['valid_until']).date() == expiry


def test_stricter_policy_version_applies_to_existing_approvals(client, approved_provider, gaps, db):
    _, pid, _, _, _ = approved_provider()
    db("UPDATE category_policies SET status = 'retired' WHERE category_code = 'household_cleaning'")
    db('''INSERT INTO category_policies(category_code, version, status, min_duration_minutes, max_duration_minutes,
              max_radius_km, required_evidence)
          VALUES ('household_cleaning', 2, 'active', 60, 480, 10,
                  '[{"type": "national_id", "count": 1}, {"type": "police_clearance", "count": 1}]')''')
    assert gaps(pid) == ['EVIDENCE_MISSING:police_clearance']


def test_policy_content_is_immutable(db):
    with pytest.raises(DBAPIError):
        db("UPDATE category_policies SET max_radius_km = 50 WHERE category_code = 'errands'")
    assert db("UPDATE category_policies SET approval_status = 'APPROVED' WHERE category_code = 'errands'") == 1


def test_forbidden_categories_cannot_be_enabled_even_directly(db):
    with pytest.raises(DBAPIError):
        db("UPDATE service_categories SET status = 'enabled' WHERE code = 'childcare'")


def test_suspension_is_immediate_and_reinstatement_needs_step_up(client, approved_provider, gaps, make_admin,
                                                                 admin_login):
    _, pid, _, rtok, clock = approved_provider()
    _, ir_clock = make_admin('incident.ira', roles=[('incident_responder', None)])
    responder = admin_login('incident.ira', ir_clock)
    r = client.post(f'/v1/admin/providers/{pid}/suspend', headers=bearer(responder),
                    json={'reason': 'Safety report under investigation'})
    assert r.status_code == 200 and r.json()['account_state'] == 'suspended'
    assert 'PROVIDER_SUSPENDED' in gaps(pid)
    reinstate = lambda: client.post(f'/v1/admin/providers/{pid}/reinstate', headers=bearer(rtok),  # noqa: E731
                                    json={'reason': 'Investigation closed, no finding'})
    assert reinstate().json()['error']['code'] == 'STEP_UP_REQUIRED'
    step_up(client, rtok, clock, 'provider.reinstate')
    assert reinstate().json()['account_state'] == 'active'
    assert gaps(pid) == []
    # Responders can suspend but not reinstate.
    client.post(f'/v1/admin/providers/{pid}/suspend', headers=bearer(responder), json={'reason': 'Second report'})
    assert client.post(f'/v1/admin/providers/{pid}/reinstate', headers=bearer(responder),
                       json={'reason': 'Trying to reinstate'}).json()['error']['code'] == 'PERMISSION_DENIED'


def test_revoke_and_re_review(client, approved_provider, gaps):
    _, pid, approval_id, rtok, _ = approved_provider()
    decide = lambda d: client.post(f'/v1/admin/provider-approvals/{approval_id}/decision', headers=bearer(rtok),  # noqa: E731
                                   json={'decision': d, 'reason': 'Conduct concern'}).json()
    assert decide('revoke')['status'] == 'revoked'
    assert 'CATEGORY_APPROVAL_REVOKED' in gaps(pid)
    assert decide('start_review')['status'] == 'under_review'


def test_category_maker_checker_and_gated_rules(client, make_admin, admin_login, approved_provider, gaps):
    _, pid, _, _, _ = approved_provider()
    _, a_clock = make_admin('ops.alice')
    _, b_clock = make_admin('ops.bob')
    alice, bob = admin_login('ops.alice', a_clock), admin_login('ops.bob', b_clock)
    step_up(client, alice, a_clock, 'policy.change')
    step_up(client, bob, b_clock, 'policy.change')

    forbidden = client.post('/v1/admin/categories/childcare/change-requests', headers=bearer(alice),
                            json={'change_type': 'category.enable', 'justification': 'Should never be possible'})
    assert forbidden.json()['error']['code'] == 'CATEGORY_FORBIDDEN'

    gated = client.post('/v1/admin/categories/elder_companionship/change-requests', headers=bearer(alice),
                        json={'change_type': 'category.enable', 'justification': 'Trying gated category'}).json()
    r = client.post(f"/v1/admin/change-requests/{gated['id']}/decision", headers=bearer(bob),
                    json={'decision': 'approve', 'reason': 'Approve gated'})
    assert r.json()['error']['code'] == 'CATEGORY_POLICY_NOT_APPROVED'

    off = client.post('/v1/admin/categories/household_cleaning/change-requests', headers=bearer(alice),
                      json={'change_type': 'category.disable', 'justification': 'Pause cleaning for review'}).json()
    assert client.post(f"/v1/admin/change-requests/{off['id']}/decision", headers=bearer(bob),
                       json={'decision': 'approve', 'reason': 'Agreed'}).json()['status'] == 'approved'
    assert 'household_cleaning' not in [c['code'] for c in client.get('/v1/categories').json()]
    assert gaps(pid) == ['CATEGORY_DISABLED']


def test_public_catalogue_shows_only_enabled_with_policy(client):
    codes = [c['code'] for c in client.get('/v1/categories').json()]
    assert codes == ['errands', 'household_cleaning', 'laundry_kitchen']


def test_expiring_list_for_reminders(client, approved_provider, db):
    _, _, _, rtok, _ = approved_provider()
    db("UPDATE provider_evidence SET next_review_due = current_date + 10 WHERE evidence_type = 'reference'")
    r = client.get('/v1/admin/verification/expiring?days=30', headers=bearer(rtok)).json()
    assert len(r['evidence']) == 2


def test_real_clamd_when_available():
    """Talks to the Compose clamav service; skipped when it is not running (it takes minutes to start)."""
    from hela.evidence.scanning import ClamdScanner
    scanner = ClamdScanner('127.0.0.1', 53310, timeout=10)
    if scanner.scan(PDF).state == 'unavailable':
        pytest.skip('clamd not reachable on 127.0.0.1:53310')
    assert scanner.scan(PDF).state == 'clean'
    assert scanner.scan(EICAR).state == 'infected'
    assert ClamdScanner('127.0.0.1', 1, timeout=1).scan(PDF).state == 'unavailable'
