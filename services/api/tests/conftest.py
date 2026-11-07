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
from hela.security.crypto import FieldCipher
from hela.security.keys import build_key_provider
from hela.security.passwords import hash_password

API_DIR = Path(__file__).resolve().parents[1]
OWNER_URL = os.environ.get('HELA_TEST_OWNER_URL',
                           'postgresql+psycopg://hela_owner:hela_owner_dev_only@127.0.0.1:55432/hela_test')
APP_URL = os.environ.get('HELA_TEST_APP_URL',
                         'postgresql+psycopg://hela_app:hela_app_dev_only@127.0.0.1:55432/hela_test')
REDIS_URL = os.environ.get('HELA_TEST_REDIS_URL', 'redis://127.0.0.1:56379/15')
ADMIN_PASSWORD = 'synthetic-admin-password-0001'

# Reference data seeded by the migration survives resets.
_KEEP = {'permissions', 'admin_roles', 'admin_role_permissions', 'audit_logs', 'audit_chain', 'alembic_version',
         'retention_policies',
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
        con.execute(text("UPDATE retention_policies SET retention_days = NULL, approval_status = 'UNAPPROVED'"))
        # Test-only: the owner may bypass the append-only trigger to start each test with an empty chain.
        con.execute(text('ALTER TABLE audit_logs DISABLE TRIGGER audit_no_truncate'))
        con.execute(text('ALTER TABLE audit_chain DISABLE TRIGGER audit_chain_no_truncate'))
        con.execute(text('TRUNCATE audit_chain, audit_logs RESTART IDENTITY'))
        con.execute(text('ALTER TABLE audit_chain ENABLE TRIGGER audit_chain_no_truncate'))
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
    return FieldCipher(build_key_provider(settings))


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


# --- booking engine (M03) -----------------------------------------------------------------------------

CUSTOMER_PIN = (7.2906, 80.6337)   # inside the synthetic Kandy polygon
PROVIDER_SPOTS = {'near': (7.2910, 80.6340), 'mid': (7.2990, 80.6420), 'far': (7.3150, 80.6600)}


def drain(app, rounds=40):
    """Run due background jobs until none are left (synchronous stand-in for hela.worker)."""
    from hela.booking.jobs import handlers
    from hela.core.jobs import run_due
    svc = app.state.services
    total = 0
    for _ in range(rounds):
        n = run_due(svc.engine, handlers(svc), worker='test', limit=50)
        total += n
        if not n:
            break
    return total


def tomorrow_at(hour, minute=0):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    zone = ZoneInfo('Asia/Colombo')
    day = (datetime.now(zone) + timedelta(days=1)).date()
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)


class World:
    def __init__(self, client, app, db, login, cipher):
        self.client, self.app, self.db, self.login, self.cipher = client, app, db, login, cipher
        self.region_id = None
        self._admin_id = None

    def fixture_admin(self):
        if not self._admin_id:
            self._admin_id = str(self.db('''INSERT INTO admin_users(username, password_hash, created_by)
                                            VALUES ('fixture.admin', 'x', 'test') RETURNING id''')[0]['id'])
        return self._admin_id

    def customer(self, phone='+94770000001'):
        tokens = self.login(phone=phone)
        r = self.client.post('/v1/addresses', headers=bearer(tokens),
                             json={'label': 'Home', 'address_text': '12 Synthetic Lane, Kandy', 'lat': CUSTOMER_PIN[0],
                                   'lon': CUSTOMER_PIN[1], 'pin_confirmed': True})
        assert r.status_code == 201, r.text
        return tokens, r.json()['id']

    def provider(self, phone, spot='near', categories=('household_cleaning',), radius_km=10, hours=(6, 20)):
        """An approved provider created directly in the database (M02 covers the approval flow itself)."""
        from hela.cli import PILOT_EVIDENCE
        tokens = self.login(phone=phone)
        pid = self.client.get('/v1/me', headers=bearer(tokens)).json()['id']
        admin = self.fixture_admin()
        enc = lambda v, ctx: self.cipher.encrypt(v, context=ctx)  # noqa: E731
        self.db('''INSERT INTO provider_profiles(user_id, application_state, legal_name_enc, date_of_birth_enc,
                       identity_verified_at, identity_verified_by, age_verified_at, age_verified_by, age_verification_method)
                   VALUES (:u, 'approved', :n, :d, now(), :a, now(), :a, 'id_document_inspected')''',
                u=pid, n=enc('Fixture Provider', 'provider_profiles.legal_name'),
                d=enc('1990-01-01', 'provider_profiles.dob'), a=admin)
        self.db("INSERT INTO user_personas(user_id, persona) VALUES (:u, 'provider')", u=pid)
        self.db("UPDATE users SET display_name = :n WHERE id = :u", n=f'Provider {phone[-3:]}', u=pid)
        for req in PILOT_EVIDENCE:
            for _ in range(req['count']):
                self.db('''INSERT INTO provider_evidence(provider_id, evidence_type, object_key, wrapped_data_key, sha256,
                               media_type, size_bytes, scan_state, review_state, next_review_due, reviewed_by, reviewed_at)
                           VALUES (:p, :t, 'fixture', :k, 'x', 'application/pdf', 1, 'clean', 'accepted',
                                   current_date + 365, :a, now())''', p=pid, t=req['type'], a=admin,
                        k=enc('00' * 32, 'provider_evidence.data_key'))
        for cat in categories:
            self.db('''INSERT INTO provider_category_approvals(provider_id, category_code, policy_id, status, valid_from,
                           valid_until, decided_by, decided_at)
                       SELECT :p, :c, id, 'approved', now() - interval '1 day', now() + interval '300 days', :a, now()
                       FROM category_policies WHERE category_code = :c AND status = 'active' ''', p=pid, c=cat, a=admin)
        lat, lon = PROVIDER_SPOTS[spot]
        self.client.put('/v1/provider/service-area', headers=bearer(tokens),
                        json={'lat': lat, 'lon': lon, 'radius_km': radius_km}).raise_for_status()
        slots = [{'weekday': d, 'start_local': f'{hours[0]:02d}:00', 'end_local': f'{hours[1]:02d}:00'} for d in range(7)]
        self.client.put('/v1/provider/availability', headers=bearer(tokens), json={'slots': slots}).raise_for_status()
        return tokens, pid

    def quote(self, tokens, address_id, *, category='household_cleaning', start=None, minutes=120):
        start = start or tomorrow_at(10)
        return self.client.post('/v1/quotes/preview', headers=bearer(tokens),
                                json={'category_code': category, 'address_id': address_id, 'start_at': start.isoformat(),
                                      'duration_minutes': minutes})

    def request(self, tokens, address_id, *, key=None, tasks=('dusting', 'tidying'), beneficiary_id=None, **quote_kw):
        import uuid as _uuid
        q = self.quote(tokens, address_id, **quote_kw)
        assert q.status_code == 201, q.text
        q = q.json()
        a = self.client.post(f"/v1/quotes/{q['quote_id']}/accept", headers=bearer(tokens),
                             json={'policy_version': q['policy_version']})
        assert a.status_code == 200, a.text
        body = {'quote_id': q['quote_id'], 'task_codes': list(tasks), 'task_note': 'Gate code is synthetic'}
        if beneficiary_id:
            body['beneficiary_id'] = beneficiary_id
        r = self.client.post('/v1/requests', headers={**bearer(tokens), 'Idempotency-Key': key or f'k-{_uuid.uuid4().hex}'},
                             json=body)
        assert r.status_code == 201, r.text
        return r.json()['request_id']

    def offers(self, tokens):
        return self.client.get('/v1/offers/me', headers=bearer(tokens)).json()

    def accept(self, tokens, offer_id, key=None):
        import uuid as _uuid
        return self.client.post(f'/v1/offers/{offer_id}/accept',
                                headers={**bearer(tokens), 'Idempotency-Key': key or f'k-{_uuid.uuid4().hex}'})

    def move(self, tokens, booking_id, target, version, **extra):
        import uuid as _uuid
        return self.client.post(f'/v1/bookings/{booking_id}/transitions',
                                headers={**bearer(tokens), 'Idempotency-Key': f'k-{_uuid.uuid4().hex}'},
                                json={'target_status': target, 'expected_version': version, **extra})

    def pin(self, customer_tokens, booking_id):
        return self.client.get(f'/v1/bookings/{booking_id}', headers=bearer(customer_tokens)).json()['checkin_pin']


@pytest.fixture
def world(categories, client, app, db, login, cipher, region):
    from hela.cli import SYNTHETIC_RATES
    for code, rate in SYNTHETIC_RATES.items():
        db("INSERT INTO price_rules(category_code, version, status, hourly_rate_minor) VALUES (:c, 1, 'active', :r)",
           c=code, r=rate)
    w = World(client, app, db, login, cipher)
    w.region_id = region()
    return w
