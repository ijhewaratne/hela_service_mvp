"""Integration fixtures: real PostgreSQL/PostGIS (hela_test) and Redis (db 15) from infra/docker/compose.yml."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from hela.app import create_app
from hela.core.config import Settings
from hela.security.crypto import EnvKeyProvider, FieldCipher
from hela.security.passwords import hash_password

API_DIR = Path(__file__).resolve().parents[1]
OWNER_URL = os.environ.get('HELA_TEST_OWNER_URL',
                           'postgresql+psycopg://hela_owner:hela_owner_dev_only@127.0.0.1:55432/hela_test')
APP_URL = os.environ.get('HELA_TEST_APP_URL',
                         'postgresql+psycopg://hela_app:hela_app_dev_only@127.0.0.1:55432/hela_test')
REDIS_URL = os.environ.get('HELA_TEST_REDIS_URL', 'redis://127.0.0.1:56379/15')
ADMIN_PASSWORD = 'synthetic-admin-password-0001'

# Reference data seeded by the migration survives resets.
_KEEP = {'permissions', 'admin_roles', 'admin_role_permissions', 'audit_logs', 'alembic_version',
         'spatial_ref_sys'}


@pytest.fixture(scope='session')
def owner_engine():
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=API_DIR, check=True,
                   env={**os.environ, 'HELA_MIGRATION_DATABASE_URL': OWNER_URL})
    engine = create_engine(OWNER_URL)
    yield engine
    engine.dispose()


@pytest.fixture
def settings() -> Settings:
    return Settings(env='test', database_url=APP_URL, redis_url=REDIS_URL)


@pytest.fixture
def reset(owner_engine, settings):
    with owner_engine.begin() as con:
        tables = [r[0] for r in con.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))]
        wipe = [t for t in tables if t not in _KEEP]
        con.execute(text(f'TRUNCATE {", ".join(wipe)} RESTART IDENTITY CASCADE'))
        # Test-only: the owner may bypass the append-only trigger to start each test with an empty chain.
        con.execute(text('ALTER TABLE audit_logs DISABLE TRIGGER audit_no_truncate'))
        con.execute(text('TRUNCATE audit_logs RESTART IDENTITY'))
        con.execute(text('ALTER TABLE audit_logs ENABLE TRIGGER audit_no_truncate'))
    import redis
    redis.Redis.from_url(REDIS_URL).flushdb()


@pytest.fixture
def app(reset, settings):
    application = create_app(settings)
    yield application
    application.state.services.engine.dispose()


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db(owner_engine):
    """Owner connection for assertions and test setup (not what the API uses)."""
    def run(sql, **params):
        with owner_engine.begin() as con:
            result = con.execute(text(sql), params)
            return [dict(r) for r in result.mappings()] if result.returns_rows else result.rowcount
    return run


@pytest.fixture
def cipher(settings):
    return FieldCipher(EnvKeyProvider(settings.field_key_b64, settings.lookup_key_b64))


@pytest.fixture
def login(client, db, cipher):
    """Phone OTP login through the real API, reading the code from the sandbox inbox."""
    def do(phone='+94770000001', device='test-device'):
        r = client.post('/v1/auth/otp/request', json={'phone': phone})
        assert r.status_code == 202, r.text
        code = latest_code(db, cipher, phone)
        r = client.post('/v1/auth/otp/verify', json={'phone': phone, 'challenge_id': r.json()['challenge_id'],
                                                     'code': code, 'device_label': device})
        assert r.status_code == 200, r.text
        return r.json()
    return do


def latest_code(db, cipher, phone):
    lookup = cipher.lookup_hash(phone, purpose='phone')
    body = db('SELECT body FROM dev_sms_inbox WHERE phone_lookup = :p ORDER BY id DESC LIMIT 1', p=lookup)[0]['body']
    return body.split('code: ')[1][:6]


class TotpClock:
    """Each TOTP time step can be used once per admin. Hand out codes for distinct steps within the
    server's +/-1 step window so one test can log in and step up without waiting 30 seconds."""

    def __init__(self, secret):
        self.totp = pyotp.TOTP(secret)
        self.offsets = [0, 30, -30]

    def next(self):
        return self.totp.at(time.time() + self.offsets.pop(0))


@pytest.fixture
def make_admin(db, cipher):
    def do(username, roles=(('super_admin', None),)):
        secret = pyotp.random_base32()
        aid = db('''INSERT INTO admin_users(username, password_hash, totp_secret_enc, totp_enrolled_at, created_by)
                    VALUES (:u, :p, :t, now(), 'test') RETURNING id''', u=username, p=hash_password(ADMIN_PASSWORD),
                 t=cipher.encrypt(secret, context='admin_users.totp'))[0]['id']
        for role, region in roles:
            db('''INSERT INTO admin_role_assignments(admin_id, role_code, region_id, granted_by, reason)
                  VALUES (:a, :r, :reg, 'test', 'test fixture')''', a=aid, r=role, reg=region)
        return str(aid), TotpClock(secret)
    return do


@pytest.fixture
def admin_login(client):
    def do(username, clock):
        r = client.post('/v1/admin/auth/login', json={'username': username, 'password': ADMIN_PASSWORD})
        assert r.status_code == 200, r.text
        r = client.post('/v1/admin/auth/mfa', json={'mfa_token': r.json()['mfa_token'], 'totp': clock.next()})
        assert r.status_code == 200, r.text
        return r.json()
    return do


@pytest.fixture
def region(db):
    def do(code='synthetic_kandy_central', status='enabled'):
        return str(db('''INSERT INTO service_regions(code, name, boundary, status, is_synthetic)
                         VALUES (:c, :c, ST_GeogFromText('MULTIPOLYGON(((80.6 7.26,80.67 7.26,80.67 7.32,80.6 7.32,80.6 7.26)))'),
                                 :s, true) RETURNING id''', c=code, s=status)[0]['id'])
    return do


def bearer(tokens):
    return {'Authorization': f"Bearer {tokens['access_token']}"}
