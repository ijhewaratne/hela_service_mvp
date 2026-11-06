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
    return Settings(env='test', database_url=APP_URL, redis_url=REDIS_URL, s3_bucket='hela-evidence-test',
                    clamd_host=None)


EICAR = rb'X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*'


class FakeScanner:
    """Deterministic stand-in for clamd: flags the EICAR test string; `available=False` simulates an outage."""

    def __init__(self):
        self.available = True

    def scan(self, data):
        from hela.evidence.scanning import ScanResult
        if not self.available:
            return ScanResult('unavailable', 'scanner_unreachable')
        return ScanResult('infected', 'Eicar-Test-Signature') if EICAR in data else ScanResult('clean')


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
    application.state.services.scanner = FakeScanner()
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


# --- provider trust (M02) -----------------------------------------------------------------------------

PDF = b'%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64


@pytest.fixture
def categories(reset, db):
    """Synthetic taxonomy from the seed command, inserted as the owner (the runtime role cannot)."""
    import json

    from hela.cli import CATEGORIES, PILOT_EVIDENCE, PROHIBITED
    for code, name, risk, enabled, safeguards, tasks in CATEGORIES:
        db('''INSERT INTO service_categories(code, display_name, risk_class, status) VALUES (:c, :n, :r, :s)''',
           c=code, n=name, r=risk, s='enabled' if enabled else 'disabled')
        db('''INSERT INTO category_policies(category_code, version, status, allowed_tasks, prohibited_tasks,
                  min_duration_minutes, max_duration_minutes, max_radius_km, required_evidence, safeguards_required)
              VALUES (:c, 1, 'active', CAST(:a AS jsonb), CAST(:p AS jsonb), 60, 480, 10, CAST(:e AS jsonb), :sg)''',
           c=code, a=json.dumps(tasks), p=json.dumps(PROHIBITED), e=json.dumps(PILOT_EVIDENCE), sg=safeguards)


def upload(client, tokens, evidence_type, data=PDF, media_type='application/pdf', **form):
    return client.post('/v1/provider/evidence', headers=bearer(tokens),
                       data={'evidence_type': evidence_type, **form},
                       files={'file': ('doc', data, media_type)})


@pytest.fixture
def applicant(client, login):
    """A provider who applied, uploaded the pilot evidence set and submitted. Returns (tokens, evidence ids)."""
    def do(phone='+94771000001'):
        tokens = login(phone=phone)
        r = client.post('/v1/provider/apply', headers=bearer(tokens),
                        json={'legal_name': 'Synthetic Provider', 'date_of_birth': '1990-05-17'})
        assert r.status_code == 201, r.text
        ids = {}
        for etype in ('national_id', 'reference', 'reference', 'training_record'):
            r = upload(client, tokens, etype)
            assert r.status_code == 201, r.text
            ids.setdefault(etype, []).append(r.json()['id'])
        assert client.post('/v1/provider/application/submit', headers=bearer(tokens)).status_code == 200
        return tokens, ids
    return do


@pytest.fixture
def reviewer(make_admin, admin_login):
    def do(username='review.rita'):
        aid, clock = make_admin(username, roles=[('verification_reviewer', None)])
        return admin_login(username, clock), clock
    return do


def step_up(client, tokens, clock, action):
    r = client.post('/v1/admin/auth/step-up', headers=bearer(tokens), json={'action': action, 'totp': clock.next()})
    assert r.status_code == 201, r.text


@pytest.fixture
def approved_provider(client, applicant, reviewer):
    """Runs the full reviewer workflow for household_cleaning. Returns (provider tokens, provider id, approval id,
    reviewer tokens, reviewer clock)."""
    def do(phone='+94771000001'):
        tokens, ids = applicant(phone)
        rtok, clock = reviewer()
        pid = client.get('/v1/provider/me', headers=bearer(tokens)).json()['provider_id']
        for evidence_id in [i for group in ids.values() for i in group]:
            r = client.post(f'/v1/admin/evidence/{evidence_id}/review', headers=bearer(rtok),
                            json={'decision': 'accept', 'reason': 'Checked against original'})
            assert r.status_code == 200, r.text
        for decision in ('start_review', 'approve'):
            r = client.post(f'/v1/admin/providers/{pid}/application/decision', headers=bearer(rtok),
                            json={'decision': decision, 'reason': 'Identity and age checked',
                                  'identity_verified': True, 'age_verification_method': 'id_document_inspected'})
            assert r.status_code == 200, r.text
        approval = client.post('/v1/provider/categories', headers=bearer(tokens),
                               json={'category_code': 'household_cleaning'}).json()
        r = client.post(f"/v1/admin/provider-approvals/{approval['id']}/decision", headers=bearer(rtok),
                        json={'decision': 'start_review', 'reason': 'Starting category review'})
        assert r.status_code == 200, r.text
        step_up(client, rtok, clock, 'category.approve')
        r = client.post(f"/v1/admin/provider-approvals/{approval['id']}/decision", headers=bearer(rtok),
                        json={'decision': 'approve', 'reason': 'All pilot evidence accepted',
                              'probation_min_jobs': 3})
        assert r.status_code == 200, r.text
        return tokens, pid, approval['id'], rtok, clock
    return do


@pytest.fixture
def gaps(app):
    from hela.core.db import tx
    from hela.verification.eligibility import provider_category_gaps

    def do(provider_id, category='household_cleaning', at=None):
        with tx(app.state.services.engine) as con:
            return provider_category_gaps(con, provider_id, category, at)
    return do
