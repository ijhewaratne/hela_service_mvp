"""Field-key and signing-key rotation (Q65): versioned ciphertexts, batch re-encryption, kid-based JWT rotation."""
import base64
import json
import os

import jwt
import pytest
from conftest import bearer, drain
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from hela.app import create_app
from hela.core.config import Settings
from hela.security import rotation
from hela.security.crypto import FieldCipher, key_id_of
from hela.security.keys import build_key_provider, signing_keys

K1 = 'ZGV2LW9ubHktZmllbGQta2V5LTMyLWJ5dGVzLXh4eHg='   # the default dev field key (id k1)
K2 = base64.b64encode(os.urandom(32)).decode()


def ring(active, **keys):
    return json.dumps({'active': active, 'keys': keys})


def cipher_for(settings, **update):
    return FieldCipher(build_key_provider(settings.model_copy(update=update)))


def legacy_v1(plaintext: str, context: str) -> bytes:
    """What the R1 cipher wrote before key ids existed."""
    nonce = os.urandom(12)
    return b'v1' + nonce + AESGCM(base64.b64decode(K1)).encrypt(nonce, plaintext.encode(), context.encode())


def test_ciphertexts_name_their_key_and_bind_the_header(settings):
    both = cipher_for(settings, field_keyring=ring('k2', k1=K1, k2=K2))
    blob = both.encrypt('0771234567', context='users.phone')
    assert blob[:2] == b'v2' and key_id_of(blob) == 'k2'
    assert both.decrypt(blob, context='users.phone') == '0771234567'
    with pytest.raises(InvalidTag):
        both.decrypt(blob, context='beneficiaries.phone')            # cannot be moved to another column
    relabelled = b'v2\x02k1' + blob[5:]
    with pytest.raises(InvalidTag):
        both.decrypt(relabelled, context='users.phone')              # cannot be relabelled to another key
    only_k1 = cipher_for(settings)
    with pytest.raises(ValueError, match='not in the keyring'):
        only_k1.decrypt(blob, context='users.phone')
    # R1-era values without a key id still decrypt with k1, and are flagged for re-encryption.
    old = legacy_v1('Gate code', 'service_requests.task_note')
    assert both.decrypt(old, context='service_requests.task_note') == 'Gate code'
    assert both.needs_rewrap(old) and both.needs_rewrap(only_k1.encrypt('x', context='c'))
    assert not both.needs_rewrap(blob)


def test_every_encrypted_column_is_registered_for_rotation(reset, db):
    in_schema = {(r['table_name'], r['column_name']) for r in db(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND data_type = 'bytea'")}
    registered = {(t, c) for t, _pk, c, _ctx in rotation.ENCRYPTED_COLUMNS}
    assert in_schema == registered


def test_rotation_reencrypts_everything_including_history_rows(world, client, db, owner_engine, settings, app):
    c_tok, addr = world.customer()
    p_tok, _ = world.provider('+94771000001', spot='near')
    world.request(c_tok, addr)
    drain(world.app)
    offer = world.offers(p_tok)[0]
    bid = world.accept(p_tok, offer['id']).json()['booking_id']
    pr = client.post('/v1/me/privacy-requests', headers=bearer(c_tok), json={'kind': 'correction', 'details': 'Fix my name'})
    pr_id = pr.json()['id']
    # One R1-era (v1) value in a mutable table and one in an append-only history table.
    db('UPDATE privacy_requests SET details_enc = :b WHERE id = :i', b=legacy_v1('Fix my name', 'privacy_requests.details'),
       i=pr_id)
    db('''INSERT INTO privacy_request_events(request_id, actor_type, actor_id, to_status, note_enc)
          VALUES (:i, 'admin', 'x', 'received', :n)''', i=pr_id, n=legacy_v1('Called the customer', 'privacy_request_events.note'))

    before = rotation.key_status(owner_engine)
    assert before['privacy_requests.details_enc'] == {'k1 (v1 format)': 1}
    assert before['bookings.pin_enc'] == {'k1': 1}

    new = cipher_for(settings, field_keyring=ring('k2', k1=K1, k2=K2))
    done = rotation.rewrap_all(owner_engine, new, batch=2)          # small batches exercise the resume loop
    assert done['users.phone_enc'] >= 2 and done['privacy_request_events.note_enc'] == 1
    after = rotation.key_status(owner_engine)
    assert all(set(c) <= {'k2'} for c in after.values()) and after['bookings.pin_enc'] == {'k2': 1}
    assert rotation.remaining_under_other_keys(owner_engine, 'k2') == 0
    assert rotation.rewrap_all(owner_engine, new)['users.phone_enc'] == 0          # idempotent

    # k1 can now be removed: everything decrypts with k2 alone, and nothing decrypts with k1 alone.
    k2_only = settings.model_copy(update={'field_keyring': ring('k2', k2=K2)})
    k2_cipher, k1_cipher = FieldCipher(build_key_provider(k2_only)), cipher_for(settings)
    for table, _pk, col, ctx in rotation.ENCRYPTED_COLUMNS:
        for r in db(f'SELECT {col} AS b FROM {table} WHERE {col} IS NOT NULL'):  # noqa: S608
            k2_cipher.decrypt(r['b'], context=ctx)
            with pytest.raises(ValueError):
                k1_cipher.decrypt(r['b'], context=ctx)
    with TestClient(create_app(k2_only)) as c2:
        view = c2.get(f'/v1/bookings/{bid}', headers=bearer(p_tok)).json()
        assert view['address']['text'] == '12 Synthetic Lane, Kandy'
    assert db("SELECT details FROM audit_logs WHERE action = 'keys.field_rewrapped'")[0]['details']['rows'] > 0


def test_history_rows_stay_append_only_except_for_reencryption(world, client, db, owner_engine):
    c_tok, _ = world.customer()
    pr_id = client.post('/v1/me/privacy-requests', headers=bearer(c_tok), json={'kind': 'other'}).json()['id']
    eid = db('''INSERT INTO privacy_request_events(request_id, actor_type, actor_id, to_status, note_enc)
                VALUES (:i, 'admin', 'x', 'received', '\\x7631') RETURNING id''', i=pr_id)[0]['id']

    def attempt(sql, rotating=True):
        with owner_engine.connect() as con:
            from sqlalchemy import text
            with con.begin():
                if rotating:
                    con.execute(text("SET LOCAL hela.key_rotation = 'on'"))
                return con.execute(text(sql), {'i': eid}).rowcount

    from sqlalchemy.exc import DBAPIError
    for sql, rotating in [("UPDATE privacy_request_events SET note_enc = '\\x7632' WHERE id = :i", False),
                          ("UPDATE privacy_request_events SET to_status = 'fulfilled' WHERE id = :i", True),
                          ("UPDATE privacy_request_events SET note_enc = NULL WHERE id = :i", True),
                          ("UPDATE privacy_request_events SET note_enc = '\\x7632', actor_id = 'y' WHERE id = :i", True),
                          ('DELETE FROM privacy_request_events WHERE id = :i', True)]:
        with pytest.raises(DBAPIError, match='append-only'):
            attempt(sql, rotating)
    assert attempt("UPDATE privacy_request_events SET note_enc = '\\x7632' WHERE id = :i") == 1
    # The runtime role cannot update history rows at all, rotation flag or not.
    with pytest.raises(DBAPIError, match='permission denied'):
        with world.app.state.services.engine.begin() as con:
            from sqlalchemy import text
            con.execute(text("SET LOCAL hela.key_rotation = 'on'"))
            con.execute(text("UPDATE privacy_request_events SET note_enc = '\\x7633' WHERE id = :i"), {'i': eid})


# --- access-token signing keys -----------------------------------------------------------------------------

def _jwt_ring(active_seed, active='j2', verify_only=None):
    doc = {'active': active, 'keys': {active: base64.b64encode(active_seed).decode()}}
    if verify_only:
        doc['verify_only'] = verify_only
    return json.dumps(doc)


def test_access_tokens_are_eddsa_with_a_key_id_and_rotate(client, login, settings):
    tokens = login()
    header = jwt.get_unverified_header(tokens['access_token'])
    assert header == {'alg': 'EdDSA', 'kid': 'dev1', 'typ': 'JWT'}

    old_public = signing_keys(settings).private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    seed = os.urandom(32)
    rotated = settings.model_copy(update={'jwt_keyring': _jwt_ring(
        seed, verify_only={'dev1': base64.b64encode(old_public).decode()})})
    with TestClient(create_app(rotated)) as c:
        assert c.get('/v1/me', headers=bearer(tokens)).status_code == 200           # old tokens still verify
        refreshed = c.post('/v1/auth/refresh', json={'refresh_token': tokens['refresh_token']}).json()
        assert jwt.get_unverified_header(refreshed['access_token'])['kid'] == 'j2'   # new tokens use the new key
        assert c.get('/v1/me', headers=bearer(refreshed)).status_code == 200
    retired = settings.model_copy(update={'jwt_keyring': _jwt_ring(seed)})
    with TestClient(create_app(retired)) as c:
        assert c.get('/v1/me', headers=bearer(tokens)).json()['error']['code'] == 'TOKEN_INVALID'
        assert c.get('/v1/me', headers=bearer(refreshed)).status_code == 200


def test_algorithm_confusion_and_unknown_keys_are_rejected(client, login, settings):
    tokens = login()
    claims = jwt.decode(tokens['access_token'], options={'verify_signature': False})
    public = signing_keys(settings).private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    bad = [
        jwt.encode(claims, public, algorithm='HS256', headers={'kid': 'dev1'}),     # public key used as an HMAC secret
        jwt.encode(claims, None, algorithm='none', headers={'kid': 'dev1'}),
        jwt.encode(claims, signing_keys(settings).private, algorithm='EdDSA', headers={'kid': 'other'}),
    ]
    for token in bad:
        r = client.get('/v1/me', headers={'Authorization': f'Bearer {token}'})
        assert r.json()['error']['code'] == 'TOKEN_INVALID', token


def test_key_configuration_is_validated_at_start_up():
    with pytest.raises(ValueError, match='active key'):
        Settings(env='test', field_keyring=ring('k9', k1=K1))
    with pytest.raises(ValueError, match='32 bytes'):
        Settings(env='test', field_keyring=ring('k1', k1=base64.b64encode(b'short').decode()))
    with pytest.raises(ValueError, match='Invalid key id'):
        Settings(env='test', field_keyring=ring('K 1', **{'K 1': K2}))
    with pytest.raises(ValueError):
        Settings(env='test', jwt_keyring='{"active": "j1", "keys": {}}')
    real = {'field_keyring': ring('k2', k2=K2), 'lookup_key_b64': base64.b64encode(os.urandom(32)).decode(),
            's3_secret_key': 'not-a-dev-secret', 'jwt_keyring': _jwt_ring(os.urandom(32))}
    Settings(env='staging', **real)                                             # accepted with real-looking keys
    for dev in ('jwt_keyring', 'field_keyring', 'lookup_key_b64'):
        with pytest.raises(ValueError, match='Development secrets'):
            Settings(env='staging', **{k: v for k, v in real.items() if k != dev})
    with pytest.raises(ValueError, match='not implemented'):
        build_key_provider(Settings(env='test').model_copy(update={'key_provider': 'aws-kms'}))
