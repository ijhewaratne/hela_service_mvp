"""Admin MFA, deny-by-default RBAC, region scoping, step-up and maker/checker (FR-ID-04, SEC-04, SEC-12, SEC-14)."""
import pyotp
import pytest
from conftest import ADMIN_PASSWORD, bearer
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from hela.app import create_app


def _step_up(client, tokens, clock, action):
    r = client.post('/v1/admin/auth/step-up', headers=bearer(tokens), json={'action': action, 'totp': clock.next()})
    assert r.status_code == 201, r.text


def test_admin_login_needs_password_and_totp(client, make_admin, admin_login):
    _, clock = make_admin('ops.alice')
    tokens = admin_login('ops.alice', clock)
    me = client.get('/v1/admin/auth/me', headers=bearer(tokens)).json()
    assert me['username'] == 'ops.alice' and me['permissions']['policy.manage'] == ['*']
    # Admin tokens are not valid on customer endpoints.
    assert client.get('/v1/me', headers=bearer(tokens)).status_code == 401


def test_unknown_user_and_wrong_password_look_the_same(client, make_admin):
    make_admin('ops.alice')
    unknown = client.post('/v1/admin/auth/login', json={'username': 'nobody', 'password': 'x' * 20}).json()
    wrong = client.post('/v1/admin/auth/login', json={'username': 'ops.alice', 'password': 'x' * 20}).json()
    assert unknown['error']['code'] == wrong['error']['code'] == 'ADMIN_LOGIN_FAILED'


def test_lockout_after_failed_passwords(client, make_admin, db):
    make_admin('ops.alice')
    for _ in range(5):
        client.post('/v1/admin/auth/login', json={'username': 'ops.alice', 'password': 'wrong-password-xx'})
    assert db("SELECT state FROM admin_users")[0]['state'] == 'locked'
    r = client.post('/v1/admin/auth/login', json={'username': 'ops.alice', 'password': ADMIN_PASSWORD})
    assert r.json()['error']['code'] == 'ADMIN_LOCKED'


def test_totp_code_cannot_be_replayed(client, make_admin):
    _, clock = make_admin('ops.alice')
    code = clock.next()
    login = lambda: client.post('/v1/admin/auth/login',  # noqa: E731
                                json={'username': 'ops.alice', 'password': ADMIN_PASSWORD}).json()['mfa_token']
    assert client.post('/v1/admin/auth/mfa', json={'mfa_token': login(), 'totp': code}).status_code == 200
    replay = client.post('/v1/admin/auth/mfa', json={'mfa_token': login(), 'totp': code})
    assert replay.json()['error']['code'] == 'MFA_INVALID'


def test_admin_without_enrolled_totp_cannot_sign_in(client, make_admin, db):
    make_admin('ops.alice')
    db('UPDATE admin_users SET totp_enrolled_at = NULL')
    r = client.post('/v1/admin/auth/login', json={'username': 'ops.alice', 'password': ADMIN_PASSWORD})
    assert r.status_code == 401


def test_deny_by_default(client, make_admin, admin_login):
    _, clock = make_admin('audit.bob', roles=[('auditor', None)])
    tokens = admin_login('audit.bob', clock)
    assert client.get('/v1/admin/audit', headers=bearer(tokens)).status_code == 200
    for path in ('/v1/admin/regions', '/v1/admin/admins', '/v1/admin/change-requests'):
        r = client.get(path, headers=bearer(tokens))
        assert r.status_code == 403 and r.json()['error']['code'] == 'PERMISSION_DENIED', path


def test_role_grant_requires_scoped_step_up_and_forbids_self_grant(client, make_admin, admin_login, db):
    alice, clock = make_admin('ops.alice')
    bob, _ = make_admin('ops.bob', roles=[])
    tokens = admin_login('ops.alice', clock)
    grant = {'role_code': 'auditor', 'reason': 'Needs audit access for review'}

    r = client.post(f'/v1/admin/admins/{bob}/roles', headers=bearer(tokens), json=grant)
    assert r.json()['error']['code'] == 'STEP_UP_REQUIRED'
    _step_up(client, tokens, clock, 'policy.change')  # wrong action: does not authorise role grants
    assert client.post(f'/v1/admin/admins/{bob}/roles', headers=bearer(tokens), json=grant).status_code == 403

    _step_up(client, tokens, clock, 'role.grant')
    assert client.post(f'/v1/admin/admins/{bob}/roles', headers=bearer(tokens), json=grant).status_code == 201
    self_grant = client.post(f'/v1/admin/admins/{alice}/roles', headers=bearer(tokens), json=grant)
    assert self_grant.json()['error']['code'] == 'SELF_GRANT_FORBIDDEN'

    db("UPDATE step_up_grants SET expires_at = now() - interval '1 second'")
    again = client.post(f'/v1/admin/admins/{bob}/roles', headers=bearer(tokens),
                        json={'role_code': 'finance_reconciler', 'reason': 'Second grant attempt'})
    assert again.json()['error']['code'] == 'STEP_UP_REQUIRED'
    assert db("SELECT details FROM audit_logs WHERE action = 'admin.role_granted'")[0]['details']['role'] == 'auditor'


def test_maker_checker_region_change(client, make_admin, admin_login, region):
    rid = region()
    _, alice_clock = make_admin('ops.alice')
    _, bob_clock = make_admin('ops.bob')
    alice = admin_login('ops.alice', alice_clock)
    bob = admin_login('ops.bob', bob_clock)
    assert [r['code'] for r in client.get('/v1/regions').json()] == ['synthetic_kandy_central']

    _step_up(client, alice, alice_clock, 'policy.change')
    cr = client.post(f'/v1/admin/regions/{rid}/change-requests', headers=bearer(alice),
                     json={'change_type': 'region.disable', 'justification': 'Pause pilot for review'})
    assert cr.status_code == 201
    cr_id = cr.json()['id']
    decision = {'decision': 'approve', 'reason': 'Reviewed and agreed'}

    own = client.post(f'/v1/admin/change-requests/{cr_id}/decision', headers=bearer(alice), json=decision)
    assert own.json()['error']['code'] == 'SELF_APPROVAL_FORBIDDEN'
    assert client.get('/v1/regions').json() != []  # nothing changed yet

    _step_up(client, bob, bob_clock, 'policy.change')
    ok = client.post(f'/v1/admin/change-requests/{cr_id}/decision', headers=bearer(bob), json=decision)
    assert ok.json()['status'] == 'approved'
    assert client.get('/v1/regions').json() == []
    again = client.post(f'/v1/admin/change-requests/{cr_id}/decision', headers=bearer(bob), json=decision)
    assert again.json()['error']['code'] == 'CHANGE_NOT_PENDING'


def test_region_scoped_permission(client, make_admin, admin_login, region):
    kandy = region('synthetic_kandy_central')
    other = region('synthetic_other_area')
    _, clock = make_admin('ops.scoped', roles=[('super_admin', other)])
    tokens = admin_login('ops.scoped', clock)
    _step_up(client, tokens, clock, 'policy.change')
    body = {'change_type': 'region.disable', 'justification': 'Scoped admin attempt'}
    denied = client.post(f'/v1/admin/regions/{kandy}/change-requests', headers=bearer(tokens), json=body)
    assert denied.json()['error']['code'] == 'PERMISSION_DENIED'
    allowed = client.post(f'/v1/admin/regions/{other}/change-requests', headers=bearer(tokens), json=body)
    assert allowed.status_code == 201


def test_staging_refuses_to_enable_unapproved_region(reset, settings, make_admin, region, cipher):
    rid = region(status='disabled')
    staging = settings.model_copy(update={'env': 'staging', 'jwt_secret': 's' * 48})
    with TestClient(create_app(staging)) as c:
        tokens = {}
        for name in ('ops.alice', 'ops.bob'):
            _, clock = make_admin(name)
            mfa = c.post('/v1/admin/auth/login', json={'username': name, 'password': ADMIN_PASSWORD}).json()
            tokens[name] = (c.post('/v1/admin/auth/mfa', json={'mfa_token': mfa['mfa_token'],
                                                               'totp': clock.next()}).json(), clock)
        a, a_clock = tokens['ops.alice']
        b, b_clock = tokens['ops.bob']
        c.post('/v1/admin/auth/step-up', headers=bearer(a), json={'action': 'policy.change', 'totp': a_clock.next()})
        cr = c.post(f'/v1/admin/regions/{rid}/change-requests', headers=bearer(a),
                    json={'change_type': 'region.enable', 'justification': 'Try enabling synthetic region'}).json()
        c.post('/v1/admin/auth/step-up', headers=bearer(b), json={'action': 'policy.change', 'totp': b_clock.next()})
        r = c.post(f"/v1/admin/change-requests/{cr['id']}/decision", headers=bearer(b),
                   json={'decision': 'approve', 'reason': 'Attempted approval'})
    assert r.json()['error']['code'] == 'REGION_NOT_APPROVED'


def test_audit_chain_is_append_only_and_verifiable(client, make_admin, admin_login, login, db):
    login()
    _, clock = make_admin('audit.bob', roles=[('auditor', None)])
    tokens = admin_login('audit.bob', clock)
    verify = client.get('/v1/admin/audit/verify', headers=bearer(tokens)).json()
    assert verify['intact'] and verify['rows_checked'] >= 3

    from conftest import APP_URL
    app_role = create_engine(APP_URL)
    for stmt in ("UPDATE audit_logs SET action = 'x'", 'DELETE FROM audit_logs', 'TRUNCATE audit_logs'):
        with pytest.raises(DBAPIError):
            with app_role.begin() as con:
                con.execute(text(stmt))
    app_role.dispose()

    # Simulate a privileged tamper that bypasses the trigger: the chain check must detect it.
    db('ALTER TABLE audit_logs DISABLE TRIGGER audit_no_update')
    db("UPDATE audit_logs SET details = '{\"forged\": true}' WHERE id = 2")
    db('ALTER TABLE audit_logs ENABLE TRIGGER audit_no_update')
    broken = client.get('/v1/admin/audit/verify', headers=bearer(tokens)).json()
    assert broken == {'intact': False, 'first_inconsistent_id': 2, 'rows_checked': verify['rows_checked'] + 0}


def test_audit_never_stores_secret_fields(client, login, db):
    from hela.audit import service
    from hela.core.db import tx
    login()
    with tx(client.app.state.services.engine) as con:
        service.record(con, actor_type='system', actor_id='t', action='test.secret', entity_type='t', entity_id='1',
                       details={'phone': '+94770000001', 'otp': '123456', 'Password': 'x', 'note': 'kept'})
    assert db("SELECT details FROM audit_logs WHERE action = 'test.secret'")[0]['details'] == {'note': 'kept'}
    dump = str(db('SELECT details FROM audit_logs'))
    assert '+94770000001' not in dump


def test_totp_helper_rejects_malformed_codes():
    from hela.security.passwords import matching_totp_step
    secret = pyotp.random_base32()
    assert matching_totp_step(secret, 'abcdef') is None
    assert matching_totp_step(secret, pyotp.TOTP(secret).now()) is not None
