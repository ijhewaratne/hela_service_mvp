"""Keys wrapped by a key service (Q65): OpenBao transit locally (infra/docker/compose.yml, service `openbao`).

Skipped when the key service is not running, unless HELA_REQUIRE_KEY_SERVICE=1 (set in CI), which makes a missing
key service a failure instead of a silent skip.
"""
import base64
import json
import os
import urllib.error
import urllib.request
import uuid

import pytest
from fastapi.testclient import TestClient

from hela import cli
from hela.app import create_app
from hela.core.config import Settings
from hela.security.crypto import FieldCipher
from hela.security.keys import KeyConfigError, build_key_provider
from hela.security.transit import KeyServiceError, TransitWrapper

ADDR = os.environ.get('HELA_TEST_TRANSIT_ADDR', 'http://127.0.0.1:58200')
TOKEN = os.environ.get('HELA_TEST_TRANSIT_TOKEN', 'hela-dev-root-token-only')


def _bao(path, body=None):
    req = urllib.request.Request(  # noqa: S310 - fixed local test address
        f'{ADDR}/v1/{path}', data=json.dumps(body or {}).encode(), method='POST', headers={'X-Vault-Token': TOKEN})
    with urllib.request.urlopen(req, timeout=5) as r:  # noqa: S310
        return r.read()


@pytest.fixture(scope='module')
def kek():
    """A fresh key-encryption key per test module, so rotation here never touches the dev key."""
    try:
        urllib.request.urlopen(f'{ADDR}/v1/sys/health', timeout=2)  # noqa: S310
    except (urllib.error.URLError, OSError):
        if os.environ.get('HELA_REQUIRE_KEY_SERVICE') == '1':
            raise
        pytest.skip('key service (openbao) not running; docker compose -f infra/docker/compose.yml up -d openbao')
    try:
        _bao('sys/mounts/transit', {'type': 'transit'})
    except urllib.error.HTTPError as exc:
        assert exc.code == 400          # already mounted
    name = f'hela-test-{uuid.uuid4().hex[:8]}'
    _bao(f'transit/keys/{name}', {'type': 'aes256-gcm96'})
    return name


@pytest.fixture
def run_cli(monkeypatch, capsys):
    def run(settings, *argv):
        monkeypatch.setattr(cli, 'get_settings', lambda: settings)
        code = cli.main(list(argv))
        return code, capsys.readouterr().out
    return run


def transit_settings(base, kek, **rings):
    return base.model_copy(update={'key_provider': 'transit', 'transit_addr': ADDR, 'transit_token': TOKEN,
                                   'transit_key': kek, **rings})


def new_wrapped_rings(run_cli, base, kek, kid='k1'):
    code, out = run_cli(transit_settings(base, kek, field_keyring='x', lookup_keyring='x', jwt_keyring='x'),
                        'generate-keys', '--key-id', kid, '--wrapped')
    assert code == 0
    doc = json.loads(out)
    # Nothing printed is a usable key: only wrapped values and the Ed25519 public key.
    assert all(v.startswith('vault:v') for part in ('field_key', 'lookup_key', 'jwt_seed') for v in doc[part].values())
    return {'field_keyring': json.dumps({'active': kid, 'keys': doc['field_key']}),
            'lookup_keyring': json.dumps({'active': kid, 'keys': doc['lookup_key']}),
            'jwt_keyring': json.dumps({'active': kid, 'keys': doc['jwt_seed']})}


def test_app_runs_on_wrapped_keys(kek, settings, run_cli, reset, db):
    s = transit_settings(settings, kek, **new_wrapped_rings(run_cli, settings, kek))
    app = create_app(s)
    try:
        with TestClient(app) as client:
            phone = '+94770000201'
            challenge = client.post('/v1/auth/otp/request', json={'phone': phone}).json()['challenge_id']
            cipher = FieldCipher(build_key_provider(s))
            lookups = cipher.lookup_candidates(phone, purpose='phone')
            body = db('SELECT body FROM dev_sms_inbox WHERE phone_lookup = ANY(:p)', p=lookups)[0]['body']
            tokens = client.post('/v1/auth/otp/verify', json={
                'phone': phone, 'challenge_id': challenge, 'code': body.split('code: ')[1][:6],
                'device_label': 'x'}).json()
            assert client.get('/v1/me', headers={'Authorization': f"Bearer {tokens['access_token']}"}).status_code == 200
            stored = db('SELECT phone_enc FROM users')[0]['phone_enc']
            assert cipher.decrypt(stored, context='users.phone') == phone
    finally:
        app.state.services.engine.dispose()


def test_wrapped_keys_are_bound_to_their_purpose_and_id(kek, settings, run_cli):
    rings = new_wrapped_rings(run_cli, settings, kek)
    lookup_value = json.loads(rings['lookup_keyring'])['keys']['k1']
    swapped = {**rings, 'field_keyring': json.dumps({'active': 'k1', 'keys': {'k1': lookup_value}})}
    with pytest.raises(KeyServiceError, match='authentication failed'):
        build_key_provider(transit_settings(settings, kek, **swapped))
    relabelled = {**rings, 'field_keyring': json.dumps({'active': 'k9', 'keys': {
        'k9': json.loads(rings['field_keyring'])['keys']['k1']}})}
    with pytest.raises(KeyServiceError):
        build_key_provider(transit_settings(settings, kek, **relabelled))
    plaintext = {**rings, 'field_keyring': json.dumps({'active': 'k1', 'keys': {'k1': 'A' * 43 + '='}})}
    with pytest.raises(KeyConfigError, match='plaintext keys are refused'):
        build_key_provider(transit_settings(settings, kek, **plaintext))


def test_revoked_or_unreachable_key_service_stops_start_up(kek, settings, run_cli):
    rings = new_wrapped_rings(run_cli, settings, kek)
    with pytest.raises(KeyServiceError, match='permission denied'):
        create_app(transit_settings(settings, kek, **rings).model_copy(update={'transit_token': 'revoked'}))
    with pytest.raises(KeyServiceError, match='unreachable'):
        create_app(transit_settings(settings, kek, **rings).model_copy(update={'transit_addr': 'http://127.0.0.1:9'}))


def test_kek_rotation_rewraps_without_changing_keys(kek, settings, run_cli):
    rings = new_wrapped_rings(run_cli, settings, kek)
    before = transit_settings(settings, kek, **rings)
    blob = FieldCipher(build_key_provider(before)).encrypt('Gate code 4471', context='service_requests.task_note')
    _bao(f'transit/keys/{kek}/rotate')
    rewrapped = {}
    for name in ('field', 'lookup', 'jwt'):
        code, out = run_cli(before, 'rewrap-keyring', '--name', name)
        assert code == 0 and json.loads(out)['keys']['k1'].startswith('vault:v2:')
        rewrapped[f'{name}_keyring'] = out.strip()
    after = transit_settings(settings, kek, **rewrapped)
    assert FieldCipher(build_key_provider(after)).decrypt(blob, context='service_requests.task_note') == 'Gate code 4471'


def test_moving_from_local_keys_keeps_existing_data_readable(kek, settings, run_cli):
    local = FieldCipher(build_key_provider(settings))
    blob = local.encrypt('+94770000202', context='users.phone')
    lookup = local.lookup_hash('+94770000202', purpose='phone')
    with_service = settings.model_copy(update={'transit_addr': ADDR, 'transit_token': TOKEN, 'transit_key': kek})
    rings = {}
    for name in ('field', 'lookup', 'jwt'):
        code, out = run_cli(with_service, 'wrap-keyring', '--name', name)
        assert code == 0 and 'vault:v' in out
        rings[f'{name}_keyring'] = out.strip()
    moved = FieldCipher(build_key_provider(transit_settings(settings, kek, **rings)))
    assert moved.decrypt(blob, context='users.phone') == '+94770000202'
    assert moved.lookup_hash('+94770000202', purpose='phone') == lookup


def test_settings_for_the_key_service():
    with pytest.raises(ValueError, match='HELA_TRANSIT_ADDR'):
        Settings(env='test', key_provider='transit')
    ring = json.dumps({'active': 'k1', 'keys': {'k1': 'vault:v1:x'}})
    rings = {'field_keyring': ring, 'lookup_keyring': ring, 'jwt_keyring': ring, 'transit_token': 't'}
    with pytest.raises(ValueError, match='HTTPS'):
        Settings(env='staging', key_provider='transit', transit_addr='http://bao:8200', s3_secret_key='x' * 20, **rings)
    Settings(env='production', key_provider='transit', transit_addr='https://bao:8200', s3_secret_key='x' * 20, **rings)
    real = lambda: base64.b64encode(os.urandom(32)).decode()  # noqa: E731
    local = {'field_keyring': json.dumps({'active': 'k2', 'keys': {'k2': real()}}),
             'lookup_keyring': json.dumps({'active': 'k2', 'keys': {'k2': real()}}),
             'jwt_keyring': json.dumps({'active': 'j2', 'keys': {'j2': real()}}), 's3_secret_key': 'x' * 20}
    Settings(env='staging', **local)                                   # staging (synthetic) may still use local keys
    with pytest.raises(ValueError, match='not allowed in production'):
        Settings(env='production', **local)
    with pytest.raises(KeyServiceError, match='http'):
        TransitWrapper(addr='file:///etc/passwd')
