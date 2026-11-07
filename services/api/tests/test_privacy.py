"""Privacy requests, own-data export scope, legal holds and the retention register (Q63, Q66, FR-ID-08)."""
from conftest import bearer, drain


def test_request_case_lifecycle_and_legal_hold(world, client, make_admin, admin_login, db):
    c_tok, _ = world.customer()
    uid = client.get('/v1/me', headers=bearer(c_tok)).json()['id']
    r = client.post('/v1/me/privacy-requests', headers=bearer(c_tok), json={'kind': 'deletion', 'details': 'Please delete my account'})
    assert r.status_code == 201 and r.json()['target_status'] == 'UNAPPROVED'
    rid = r.json()['id']
    assert b'delete my account' not in bytes(db('SELECT details_enc FROM privacy_requests')[0]['details_enc'])

    _, fclock = make_admin('fin.fred', roles=[('finance_reconciler', None)])
    assert client.get('/v1/admin/privacy-requests', headers=bearer(admin_login('fin.fred', fclock))).status_code == 403
    _, p1 = make_admin('priv.pia', roles=[('privacy_officer', None)])
    _, p2 = make_admin('priv.pat', roles=[('privacy_officer', None)])
    pia, pat = admin_login('priv.pia', p1), admin_login('priv.pat', p2)
    case = client.get(f'/v1/admin/privacy-requests/{rid}', headers=bearer(pia)).json()
    assert case['details'] == 'Please delete my account'
    act = lambda tok, a: client.post(f'/v1/admin/privacy-requests/{rid}/actions', headers=bearer(tok),  # noqa: E731
                                     json={'action': a, 'note': 'Synthetic case handling'}).json()
    assert act(pia, 'fulfil')['error']['code'] == 'TRANSITION_NOT_ALLOWED'
    act(pia, 'verify_identity')
    act(pia, 'start')
    hold = client.post('/v1/admin/legal-holds', headers=bearer(pia),
                       json={'entity_type': 'user', 'entity_id': uid, 'reason': 'Open payment dispute under review'}).json()
    assert act(pia, 'fulfil')['error']['code'] == 'LEGAL_HOLD_ACTIVE'          # deletion blocked by the hold
    assert client.post(f"/v1/admin/legal-holds/{hold['id']}/release", headers=bearer(pia),
                       json={'reason': 'Releasing my own hold'}).json()['error']['code'] == 'SELF_RELEASE_FORBIDDEN'
    assert client.post(f"/v1/admin/legal-holds/{hold['id']}/release", headers=bearer(pat),
                       json={'reason': 'Dispute resolved, reviewed'}).json()['released'] is True
    assert act(pia, 'fulfil')['status'] == 'fulfilled'
    mine = client.get('/v1/me/privacy-requests', headers=bearer(c_tok)).json()
    assert mine[0]['status'] == 'fulfilled'
    assert db("SELECT count(*) AS n FROM sensitive_access_logs WHERE resource_type = 'privacy_request'")[0]['n'] == 1


def test_export_contains_own_data_and_masks_others(world, client, db, cipher):
    c_tok, addr = world.customer()
    p_tok, _ = world.provider('+94771000001')
    client.post('/v1/beneficiaries', headers=bearer(c_tok), json={'name': 'Synthetic Parent', 'phone': '+94779999001',
                                                                  'relationship': 'parent'})
    world.request(c_tok, addr)
    drain(world.app)
    offer = world.offers(p_tok)[0]
    bid = world.accept(p_tok, offer['id']).json()['booking_id']
    client.post('/v1/incidents', headers=bearer(p_tok), json={'booking_id': bid, 'kind': 'safety', 'severity': 'medium',
                                                              'description': 'Provider report about the household'})
    client.post('/v1/incidents', headers=bearer(c_tok), json={'kind': 'other', 'severity': 'low',
                                                              'description': 'Customer own report text'})
    export = client.get('/v1/me/export', headers=bearer(c_tok)).json()
    assert export['account']['phone'] == '+94770000001'
    assert export['addresses'][0]['address'] == '12 Synthetic Lane, Kandy'
    assert export['family_members'][0]['phone_masked'] == '*******001'
    assert '+94779999001' not in str(export)                          # other people stay masked
    assert [i['your_description'] for i in export['incidents_reported']] == ['Customer own report text']
    assert 'Provider report about the household' not in str(export)   # incident narratives about you are not included
    assert export['requests_as_customer'][0]['booking_status'] == 'accepted'
    assert 'provider' not in export
    provider_export = client.get('/v1/me/export', headers=bearer(p_tok)).json()
    assert provider_export['provider']['legal_name'] == 'Fixture Provider'
    assert provider_export['provider']['jobs'][0]['booking_id'] == bid
    assert '12 Synthetic Lane' not in str(provider_export)            # customer's address is not the provider's data
    assert db("SELECT count(*) AS n FROM privacy_requests WHERE kind = 'access_export' AND status = 'fulfilled'")[0]['n'] == 2


def test_export_requires_recent_sign_in(world, client, db):
    c_tok, _ = world.customer()
    db("UPDATE sessions SET created_at = now() - interval '20 minutes'")
    r = client.get('/v1/me/export', headers=bearer(c_tok))
    assert r.status_code == 403 and r.json()['error']['code'] == 'RECENT_SIGN_IN_REQUIRED'


def test_retention_register_is_report_only(world, client, make_admin, admin_login, db):
    world.login()                                   # creates an OTP challenge row to age
    _, clock = make_admin('priv.pia', roles=[('privacy_officer', None)])
    reg = client.get('/v1/admin/retention', headers=bearer(admin_login('priv.pia', clock))).json()
    assert reg['automatic_deletion'] is False and len(reg['policies']) == 10
    assert {p['approval_status'] for p in reg['policies']} == {'UNAPPROVED'}
    assert all(p['retention_days'] is None and p['action'].startswith('none') for p in reg['policies'])
    db("UPDATE retention_policies SET retention_days = 1 WHERE data_category = 'otp_challenges'")
    db("UPDATE otp_challenges SET created_at = now() - interval '3 days'")
    reg = client.get('/v1/admin/retention', headers=bearer(admin_login('priv.pia', clock))).json()
    otp = [p for p in reg['policies'] if p['data_category'] == 'otp_challenges'][0]
    assert otp['would_exceed_period'] >= 1 and otp['action'] == 'none: report only until APPROVED'
    assert db('SELECT count(*) AS n FROM otp_challenges')[0]['n'] >= 1         # still nothing deleted
