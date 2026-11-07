"""Customer/provider identity: OTP, sessions, revocation, profile, notices (FR-ID-*, SEC-01/02, AT-025, AT-028)."""
from datetime import UTC, datetime, timedelta

import jwt
from conftest import bearer, latest_code
from fastapi.testclient import TestClient

from hela.app import create_app
from hela.core.config import Settings
from hela.security.keys import signing_keys

PHONE = '+94770000001'


def _request(client, phone=PHONE):
    r = client.post('/v1/auth/otp/request', json={'phone': phone})
    assert r.status_code == 202, r.text
    return r.json()['challenge_id']


def _verify(client, challenge_id, code, phone=PHONE):
    return client.post('/v1/auth/otp/verify', json={'phone': phone, 'challenge_id': challenge_id, 'code': code})


def test_otp_login_creates_customer_with_encrypted_phone(client, login, db):
    tokens = login()
    me = client.get('/v1/me', headers=bearer(tokens)).json()
    assert me['personas'] == ['customer']
    assert me['phone_masked'] == '*******001'
    assert 'phone' not in me
    stored = db('SELECT phone_enc, phone_lookup FROM users')[0]
    assert PHONE.encode() not in bytes(stored['phone_enc'])
    assert PHONE not in stored['phone_lookup']


def test_repeat_login_reuses_account(client, login, db):
    first = client.get('/v1/me', headers=bearer(login())).json()['id']
    second = client.get('/v1/me', headers=bearer(login())).json()['id']
    assert first == second
    assert db('SELECT count(*) AS n FROM users')[0]['n'] == 1


def test_phone_formats_normalised_and_invalid_rejected(client):
    assert client.post('/v1/auth/otp/request', json={'phone': '0094 77-000 0001'}).status_code == 202
    r = client.post('/v1/auth/otp/request', json={'phone': '0771234567'})
    assert r.status_code == 422
    err = r.json()['error']
    assert err['code'] == 'VALIDATION_FAILED' and err['message_key'] == 'error.validation'
    assert '0771234567' not in r.text  # submitted values are never echoed back


def test_wrong_code_counts_attempts_then_locks(client, db, cipher):
    cid = _request(client)
    good = latest_code(db, cipher, PHONE)
    bad = '000000' if good != '000000' else '111111'
    for _ in range(5):
        assert _verify(client, cid, bad).json()['error']['code'] == 'OTP_INVALID'
    r = _verify(client, cid, good)
    assert r.status_code == 429 and r.json()['error']['code'] == 'OTP_LOCKED'
    assert db("SELECT count(*) AS n FROM audit_logs WHERE action = 'auth.otp_locked'")[0]['n'] == 1


def test_expired_and_consumed_challenges_rejected(client, db, cipher):
    cid = _request(client)
    code = latest_code(db, cipher, PHONE)
    db("UPDATE otp_challenges SET expires_at = now() - interval '1 second'")
    assert _verify(client, cid, code).json()['error']['code'] == 'OTP_INVALID'

    cid = _request(client)
    code = latest_code(db, cipher, PHONE)
    assert _verify(client, cid, code).status_code == 200
    assert _verify(client, cid, code).json()['error']['code'] == 'OTP_INVALID'  # single use


def test_challenge_is_bound_to_its_phone(client, db, cipher):
    cid = _request(client)
    code = latest_code(db, cipher, PHONE)
    assert _verify(client, cid, code, phone='+94770000002').status_code == 400


def test_send_cap_per_phone(client):
    for _ in range(3):
        _request(client)
    r = client.post('/v1/auth/otp/request', json={'phone': PHONE})
    assert r.status_code == 429 and r.json()['error']['retryable'] is True


def test_otp_fails_closed_when_redis_unavailable(reset, settings):
    broken = create_app(settings.model_copy(update={'redis_url': 'redis://127.0.0.1:1/0'}))
    with TestClient(broken) as c:
        r = c.post('/v1/auth/otp/request', json={'phone': PHONE})
    assert r.status_code == 503 and r.json()['error']['code'] == 'DEPENDENCY_UNAVAILABLE'


def test_refresh_rotation_and_reuse_detection(client, login):
    tokens = login()
    rotated = client.post('/v1/auth/refresh', json={'refresh_token': tokens['refresh_token']})
    assert rotated.status_code == 200
    new = rotated.json()
    assert new['refresh_token'] != tokens['refresh_token'] and new['session_id'] == tokens['session_id']

    replay = client.post('/v1/auth/refresh', json={'refresh_token': tokens['refresh_token']})
    assert replay.json()['error']['code'] == 'REFRESH_REUSED'
    # The whole session is now revoked: neither the new refresh token nor the access token work.
    assert client.post('/v1/auth/refresh', json={'refresh_token': new['refresh_token']}).status_code == 401
    assert client.get('/v1/me', headers=bearer(new)).json()['error']['code'] == 'SESSION_REVOKED'


def test_logout_revokes_access_token_immediately(client, login):
    tokens = login()
    assert client.post('/v1/auth/logout', headers=bearer(tokens)).status_code == 204
    assert client.get('/v1/me', headers=bearer(tokens)).status_code == 401


def test_logout_all_and_session_listing(client, login):
    phone_a = login(device='phone-a')
    phone_b = login(device='phone-b')
    listed = client.get('/v1/me/sessions', headers=bearer(phone_a)).json()
    assert {s['device_label'] for s in listed} == {'phone-a', 'phone-b'}
    assert [s['current'] for s in listed if s['device_label'] == 'phone-a'] == [True]
    assert client.post('/v1/auth/logout-all', headers=bearer(phone_a)).json()['revoked_sessions'] == 2
    assert client.get('/v1/me', headers=bearer(phone_b)).status_code == 401


def test_user_cannot_end_someone_elses_session(client, login):
    mine = login(phone='+94770000001')
    theirs = login(phone='+94770000002')
    r = client.delete(f"/v1/me/sessions/{theirs['session_id']}", headers=bearer(mine))
    assert r.status_code == 404
    assert client.get('/v1/me', headers=bearer(theirs)).status_code == 200


def test_suspended_account_blocked_centrally(client, login, db):
    tokens = login()
    db("UPDATE users SET account_state = 'suspended'")
    assert client.get('/v1/me', headers=bearer(tokens)).json()['error']['code'] == 'ACCOUNT_INACTIVE'
    assert client.post('/v1/auth/refresh', json={'refresh_token': tokens['refresh_token']}).status_code == 403


def test_forged_expired_and_wrong_audience_tokens_rejected(client, login, settings):
    tokens = login()
    claims = jwt.decode(tokens['access_token'], options={'verify_signature': False})
    forged = jwt.encode({**claims, 'sub': '00000000-0000-0000-0000-000000000000'}, 'x' * 40, algorithm='HS256')
    keys = signing_keys(settings)
    expired = jwt.encode({**claims, 'exp': datetime.now(UTC) - timedelta(seconds=1)}, keys.private,
                         algorithm='EdDSA', headers={'kid': keys.active})
    for token in (forged, expired, 'not-a-token'):
        r = client.get('/v1/me', headers={'Authorization': f'Bearer {token}'})
        assert r.json()['error']['code'] == 'TOKEN_INVALID'
    # A customer token is not accepted by admin endpoints (different audience).
    assert client.get('/v1/admin/auth/me', headers=bearer(tokens)).status_code == 401


def test_profile_language_preference(client, login, db):
    tokens = login()
    r = client.patch('/v1/me', headers=bearer(tokens), json={'preferred_language': 'si', 'display_name': 'Asha'})
    assert r.json()['preferred_language'] == 'si'
    assert client.patch('/v1/me', headers=bearer(tokens), json={'preferred_language': 'fr'}).status_code == 422
    audit = db("SELECT details FROM audit_logs WHERE action = 'user.profile_updated'")[0]['details']
    assert audit == {'fields': ['display_name', 'preferred_language']}


def test_notice_acceptance_records_version_language_and_context(client, login, db):
    nid = db('''INSERT INTO consent_notices(code, version, language, title, body)
                VALUES ('privacy_notice', 2, 'en', 't', 'b') RETURNING id''')[0]['id']
    db("INSERT INTO consent_notices(code, version, language, title, body) VALUES ('privacy_notice', 1, 'en', 'old', 'b')")
    notices = client.get('/v1/notices/current?language=ta').json()
    assert [(n['code'], n['version'], n['language']) for n in notices] == [('privacy_notice', 2, 'en')]
    tokens = login()
    r = client.post(f'/v1/notices/{nid}/accept', headers=bearer(tokens), json={'processing_context': 'signup'})
    assert r.status_code == 201
    row = db("SELECT details FROM audit_logs WHERE action = 'notice.accepted'")[0]['details']
    assert row == {'code': 'privacy_notice', 'version': 2, 'language': 'en', 'processing_context': 'signup'}


def test_error_envelope_and_correlation_id(client):
    r = client.get('/v1/me', headers={'X-Correlation-ID': 'test-corr-0001'})
    body = r.json()['error']
    assert r.status_code == 401 and body['code'] == 'UNAUTHENTICATED'
    assert body['correlation_id'] == 'test-corr-0001' == r.headers['X-Correlation-ID']
    assert set(body) == {'code', 'message_key', 'message', 'correlation_id', 'retryable', 'details'}
    assert client.get('/v1/does-not-exist').json()['error']['code'] == 'NOT_FOUND'


def test_dev_inbox_only_in_dev_with_token(reset, settings, client):
    assert client.post('/v1/dev/sms-inbox/latest', json={'phone': PHONE}).status_code == 404
    dev = create_app(settings.model_copy(update={'env': 'dev', 'dev_inbox_token': 'inbox-token-000001'}))
    with TestClient(dev) as c:
        c.post('/v1/auth/otp/request', json={'phone': PHONE})
        wrong = c.post('/v1/dev/sms-inbox/latest', json={'phone': PHONE}, headers={'X-Dev-Inbox-Token': 'nope'})
        right = c.post('/v1/dev/sms-inbox/latest', json={'phone': PHONE},
                      headers={'X-Dev-Inbox-Token': 'inbox-token-000001'})
    assert wrong.status_code == 404
    assert right.status_code == 200 and 'Hela Service code' in right.json()['body']


def test_production_refuses_dev_secrets_and_sandbox():
    import pytest
    with pytest.raises(ValueError):
        Settings(env='production')


def test_health(client):
    assert client.get('/health').json() == {'status': 'ok', 'database': 'ok', 'cache': 'ok'}


def test_security_headers_and_no_store(client):
    r = client.get('/v1/regions')
    for header, value in [('x-content-type-options', 'nosniff'), ('referrer-policy', 'no-referrer'),
                          ('x-frame-options', 'DENY'), ('cache-control', 'no-store')]:
        assert r.headers[header] == value, header
    assert "frame-ancestors 'none'" in r.headers['content-security-policy']
    assert 'strict-transport-security' not in r.headers   # only behind TLS in staging/production


def test_oversized_bodies_rejected_even_without_content_length(client):
    big = b'{"phone": "' + b'1' * 70_000 + b'"}'
    r = client.post('/v1/auth/otp/request', content=big, headers={'Content-Type': 'application/json'})
    assert r.status_code == 413 and r.json()['error']['code'] == 'PAYLOAD_TOO_LARGE'

    def chunks():
        for _ in range(10):
            yield b'x' * 10_000
    r = client.post('/v1/auth/otp/request', content=chunks(), headers={'Content-Type': 'application/json'})
    assert r.status_code == 413


def test_unknown_fields_are_rejected_not_ignored(client, login):
    tokens = login()
    r = client.patch('/v1/me', headers=bearer(tokens), json={'preferred_language': 'si', 'account_state': 'active'})
    assert r.status_code == 422
    assert {'field': 'account_state', 'type': 'extra_forbidden'} in r.json()['error']['details']['fields']
    assert client.get('/v1/me', headers=bearer(tokens)).json()['preferred_language'] == 'en'   # nothing half-applied
