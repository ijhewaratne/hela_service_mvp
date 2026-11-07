"""Prepare the isolated `hela_load` database for Locust: schema, synthetic taxonomy, 30 approved fictional
providers and 100 fictional customers with addresses (spec §2.3), plus session tokens written to a local
JSON file that never leaves the machine.

    python tests/load/seed_load.py /path/to/tokens.json
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

API_DIR = Path(__file__).resolve().parents[2]
OWNER = 'postgresql+psycopg://hela_owner:hela_owner_dev_only@127.0.0.1:55432/{db}'
APP = 'postgresql+psycopg://hela_app:hela_app_dev_only@127.0.0.1:55432/hela_load'
N_PROVIDERS, N_CUSTOMERS = 30, 100


def main(out_path: str) -> None:
    admin = create_engine(OWNER.format(db='hela'), isolation_level='AUTOCOMMIT')
    with admin.connect() as con:
        con.execute(text('DROP DATABASE IF EXISTS hela_load WITH (FORCE)'))
        con.execute(text('CREATE DATABASE hela_load OWNER hela_owner'))
        con.execute(text('GRANT CONNECT ON DATABASE hela_load TO hela_app'))
    owner = create_engine(OWNER.format(db='hela_load'))
    with owner.begin() as con:
        for stmt in ('CREATE EXTENSION IF NOT EXISTS postgis', 'CREATE EXTENSION IF NOT EXISTS btree_gist',
                     'REVOKE CREATE ON SCHEMA public FROM PUBLIC', 'GRANT USAGE ON SCHEMA public TO hela_app'):
            con.execute(text(stmt))
    env = {**os.environ, 'HELA_MIGRATION_DATABASE_URL': OWNER.format(db='hela_load'), 'HELA_DATABASE_URL': APP,
           'HELA_ENV': 'dev'}
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=API_DIR, env=env, check=True)  # noqa: S603
    subprocess.run([sys.executable, '-m', 'hela.cli', 'seed-synthetic'], cwd=API_DIR, env=env, check=True)  # noqa: S603

    os.environ.update({'HELA_DATABASE_URL': APP, 'HELA_ENV': 'dev'})
    sys.path.insert(0, str(API_DIR))
    from hela.cli import PILOT_EVIDENCE
    from hela.core.config import Settings
    from hela.customers.routes import coarse
    from hela.identity.sessions import open_session
    from hela.security.crypto import FieldCipher
    from hela.security.keys import build_key_provider

    settings = Settings(env='dev', database_url=APP, refresh_token_days=1, access_token_minutes=120)
    cipher = FieldCipher(build_key_provider(settings))
    rng = random.Random(20261106)
    tokens = {'customers': [], 'providers': []}
    with owner.begin() as con:
        q = lambda sql, **p: con.execute(text(sql), p).mappings().first()  # noqa: E731
        region = q("SELECT id FROM service_regions WHERE code = 'synthetic_kandy_central'")['id']
        admin_id = q("""INSERT INTO admin_users(username, password_hash, created_by) VALUES ('load.fixture', 'x', 'load')
                        RETURNING id""")['id']

        def user(phone):
            return str(q('''INSERT INTO users(phone_lookup, phone_enc, phone_last3) VALUES (:l, :e, :p3) RETURNING id''',
                         l=cipher.lookup_hash(phone, purpose='phone'), e=cipher.encrypt(phone, context='users.phone'),
                         p3=phone[-3:])['id'])

        for i in range(N_PROVIDERS):
            pid = user(f'+9477800{i:04d}')
            for persona in ('customer', 'provider'):
                con.execute(text('INSERT INTO user_personas(user_id, persona) VALUES (:u, :p)'), {'u': pid, 'p': persona})
            con.execute(text('''INSERT INTO provider_profiles(user_id, application_state, legal_name_enc, date_of_birth_enc,
                                    identity_verified_at, identity_verified_by, age_verified_at, age_verified_by,
                                    age_verification_method)
                                VALUES (:u, 'approved', :n, :d, now(), :a, now(), :a, 'id_document_inspected')'''),
                        {'u': pid, 'n': cipher.encrypt(f'Load Provider {i}', context='provider_profiles.legal_name'),
                         'd': cipher.encrypt('1990-01-01', context='provider_profiles.dob'), 'a': admin_id})
            for req in PILOT_EVIDENCE:
                for _ in range(req['count']):
                    con.execute(text('''INSERT INTO provider_evidence(provider_id, evidence_type, object_key, wrapped_data_key,
                                            sha256, media_type, size_bytes, scan_state, review_state, next_review_due)
                                        VALUES (:p, :t, 'load', :k, 'x', 'application/pdf', 1, 'clean', 'accepted',
                                                current_date + 365)'''),
                                {'p': pid, 't': req['type'], 'k': cipher.encrypt('00' * 32, context='provider_evidence.data_key')})
            con.execute(text('''INSERT INTO provider_category_approvals(provider_id, category_code, policy_id, status,
                                    valid_from, valid_until, decided_by, decided_at)
                                SELECT :p, 'household_cleaning', id, 'approved', now() - interval '1 day',
                                       now() + interval '300 days', :a, now()
                                FROM category_policies WHERE category_code = 'household_cleaning' AND status = 'active' '''),
                        {'p': pid, 'a': admin_id})
            lat, lon = coarse(rng.uniform(7.27, 7.31)), coarse(rng.uniform(80.61, 80.66))
            con.execute(text('''INSERT INTO provider_service_areas(provider_id, base_point, radius_km)
                                VALUES (:p, ST_MakePoint(:lon, :lat)::geography, 10)'''), {'p': pid, 'lat': lat, 'lon': lon})
            for d in range(7):
                con.execute(text('''INSERT INTO provider_weekly_availability(provider_id, weekday, start_local, end_local)
                                    VALUES (:p, :d, '06:00', '20:00')'''), {'p': pid, 'd': d})
            tokens['providers'].append(open_session(con, settings, subject_type='user', subject_id=pid,
                                                    device_label='load')['access_token'])

        for i in range(N_CUSTOMERS):
            cid = user(f'+9477900{i:04d}')
            con.execute(text("INSERT INTO user_personas(user_id, persona) VALUES (:u, 'customer')"), {'u': cid})
            lat, lon = rng.uniform(7.275, 7.305), rng.uniform(80.615, 80.655)
            addr = q('''INSERT INTO customer_addresses(owner_id, label, address_enc, pin_enc, match_point, region_id,
                                                       area_label)
                        VALUES (:o, 'Home', :a, :p, ST_MakePoint(:lon, :lat)::geography, :r, 'Synthetic Kandy Central')
                        RETURNING id''', o=cid, a=cipher.encrypt(f'{i} Load Street', context='customer_addresses.address'),
                     p=cipher.encrypt(f'{lat:.6f},{lon:.6f}', context='customer_addresses.pin'), lon=coarse(lon),
                     lat=coarse(lat), r=region)['id']
            tokens['customers'].append({'token': open_session(con, settings, subject_type='user', subject_id=cid,
                                                              device_label='load')['access_token'], 'address_id': str(addr)})
    Path(out_path).write_text(json.dumps(tokens))
    print(f'seeded hela_load: {N_PROVIDERS} providers, {N_CUSTOMERS} customers -> {out_path}')


if __name__ == '__main__':
    main(sys.argv[1])
