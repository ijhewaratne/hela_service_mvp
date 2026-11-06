"""Operator CLI (run locally by a trusted operator, never exposed over HTTP).

  python -m hela.cli create-admin --username ops.alice --role super_admin --reason "initial bootstrap"
  python -m hela.cli seed-synthetic
  python -m hela.cli verify-audit
  python -m hela.cli init-storage
  python -m hela.cli expire-verifications
  python -m hela.cli rescan-evidence
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys

from .audit import service as audit
from .core.config import get_settings
from .core.db import make_engine, many, one, run, tx
from .evidence.scanning import ClamdScanner
from .evidence.storage import EvidenceSealer, S3ObjectStore
from .security.crypto import EnvKeyProvider, FieldCipher
from .security.passwords import hash_password, new_totp_secret, provisioning_uri
from .verification.expiry import expire_due

# Approximate, fictional boundary around central Kandy for synthetic tests only (Q16, DEC-02 pending).
SYNTHETIC_KANDY_WKT = ('MULTIPOLYGON(((80.600 7.260, 80.670 7.260, 80.670 7.320, 80.600 7.320, 80.600 7.260)))')

# Spec §4.1 taxonomy. Only the three Q21 pilot categories start enabled; every policy is UNAPPROVED.
PILOT_EVIDENCE = [{'type': 'national_id', 'count': 1, 'review_interval_days': 365},
                  {'type': 'reference', 'count': 2, 'review_interval_days': 365},
                  {'type': 'training_record', 'count': 1, 'review_interval_days': 365}]
CATEGORIES = [
    # code, display name, risk class, enabled, safeguards required, allowed tasks
    ('household_cleaning', 'Household cleaning', 'pilot_candidate', True, False,
     ['sweeping_mopping', 'dusting', 'bathroom_cleaning', 'kitchen_surfaces', 'tidying']),
    ('laundry_kitchen', 'Laundry and kitchen help', 'pilot_candidate', True, False,
     ['washing_clothes', 'ironing', 'dish_washing', 'meal_prep_assistance']),
    ('errands', 'Shopping and errands', 'pilot_candidate', True, False,
     ['grocery_shopping', 'pharmacy_pickup_non_controlled', 'parcel_dropoff']),
    ('general_assistance', 'General hourly assistance', 'pilot_candidate', False, False, []),  # Q21: no task list yet
    ('house_pet_sitting', 'House and pet sitting', 'gated', False, True, []),
    ('elder_companionship', 'Non-clinical elder companionship', 'gated', False, True, []),
    ('gardening_handyman', 'Gardening and handyman', 'gated', False, True, []),
    ('trades_repair', 'Plumbing, electrical and appliance repair', 'gated', False, True, []),
    ('driver_tutoring', 'Driver services and tutoring', 'gated', False, True, []),
    ('childcare', 'Childcare (not offered)', 'disabled', False, True, []),
    ('clinical_emergency', 'Clinical care or emergency response (out of scope)', 'out_of_scope', False, True, []),
]
PROHIBITED = ['medication_administration', 'clinical_tasks', 'hazardous_work', 'working_at_height',
              'handling_cash_over_limit', 'unsupervised_child_care']

# Q30: synthetic test-only rates in LKR minor units (cents). No rate is approved for real commerce (DEC-06).
SYNTHETIC_RATES = {'household_cleaning': 120000, 'laundry_kitchen': 110000, 'errands': 100000,
                   'general_assistance': 100000}

NOTICES = [
    ('privacy_notice', 'Privacy notice (DRAFT, UNAPPROVED)',
     'Synthetic development text. The final privacy notice requires legal review before any real use.'),
    ('service_terms', 'Service terms (DRAFT, UNAPPROVED)',
     'Synthetic development text. Service terms are pending legal and business sign-off (DEC-06/DEC-07).'),
]


def _cipher(settings):
    return FieldCipher(EnvKeyProvider(settings.field_key_b64, settings.lookup_key_b64))


def create_admin(args) -> int:
    settings = get_settings()
    password = os.environ.get('HELA_NEW_ADMIN_PASSWORD') or getpass.getpass('New admin password (14+ chars): ')
    secret = new_totp_secret()
    with tx(make_engine(settings.database_url)) as con:
        row = one(con, '''INSERT INTO admin_users(username, password_hash, totp_secret_enc, totp_enrolled_at,
                                                  created_by)
                          VALUES (:u, :p, :t, now(), 'operator-cli') RETURNING id''',
                  u=args.username.lower(), p=hash_password(password),
                  t=_cipher(settings).encrypt(secret, context='admin_users.totp'))
        aid = str(row['id'])
        run(con, '''INSERT INTO admin_role_assignments(admin_id, role_code, granted_by, reason)
                    VALUES (:a, :r, 'operator-cli', :reason)''', a=aid, r=args.role, reason=args.reason)
        audit.record(con, actor_type='system', actor_id='operator-cli', action='admin.created',
                     entity_type='admin_user', entity_id=aid, details={'role': args.role, 'reason': args.reason})
    # Shown once, to the operator's terminal only. Enrol it in an authenticator app now.
    print(f'admin_id={aid}')
    print(f'totp_secret={secret}')
    print(f'totp_uri={provisioning_uri(secret, args.username.lower())}')
    return 0


def seed_synthetic(_args) -> int:
    settings = get_settings()
    if settings.env not in ('dev', 'test'):
        print('Refusing to seed synthetic data outside dev/test.', file=sys.stderr)
        return 2
    # Category policies are privileged reference data the runtime role cannot write, so seed as the owner.
    owner_url = os.environ.get('HELA_MIGRATION_DATABASE_URL',
                               'postgresql+psycopg://hela_owner:hela_owner_dev_only@127.0.0.1:55432/hela')
    with tx(make_engine(owner_url)) as con:
        region = one(con, '''INSERT INTO service_regions(code, name, boundary, status, is_synthetic)
                             VALUES ('synthetic_kandy_central', 'Synthetic Kandy Central (fictional boundary)',
                                     ST_GeogFromText(:wkt), 'enabled', true)
                             ON CONFLICT (code) DO NOTHING RETURNING id''', wkt=SYNTHETIC_KANDY_WKT)
        for code, name, risk, enabled, safeguards, tasks in CATEGORIES:
            run(con, '''INSERT INTO service_categories(code, display_name, risk_class, status)
                        VALUES (:c, :n, :r, :s) ON CONFLICT (code) DO NOTHING''',
                c=code, n=name, r=risk, s='enabled' if enabled else 'disabled')
            run(con, '''INSERT INTO category_policies(category_code, version, status, allowed_tasks, prohibited_tasks,
                            min_duration_minutes, max_duration_minutes, max_radius_km, required_evidence,
                            safeguards_required, terms)
                        VALUES (:c, 1, 'active', CAST(:a AS jsonb), CAST(:p AS jsonb), 60, 480, 10,
                                CAST(:e AS jsonb), :sg, CAST(:t AS jsonb))
                        ON CONFLICT (category_code, version) DO NOTHING''',
                c=code, a=json.dumps(tasks), p=json.dumps(PROHIBITED), e=json.dumps(PILOT_EVIDENCE), sg=safeguards,
                t=json.dumps({'note': 'Synthetic UNAPPROVED fixture (DEC-03/DEC-04 pending)',
                              'cancellation': {'fee_lkr_minor': 0}}))
        for code, rate in SYNTHETIC_RATES.items():
            run(con, '''INSERT INTO price_rules(category_code, version, status, hourly_rate_minor)
                        VALUES (:c, 1, 'active', :r) ON CONFLICT (category_code, version) DO NOTHING''', c=code, r=rate)
        for code, title, body in NOTICES:
            run(con, '''INSERT INTO consent_notices(code, version, language, title, body, translation_status)
                        VALUES (:c, 1, 'en', :t, :b, 'source') ON CONFLICT DO NOTHING''', c=code, t=title, b=body)
        if region:
            audit.record(con, actor_type='system', actor_id='seed-synthetic', action='region.seeded',
                         entity_type='service_region', entity_id=str(region['id']), details={'synthetic': True})
    print('Synthetic seed applied.')
    return 0


def verify_audit(_args) -> int:
    engine = make_engine(get_settings().database_url)
    with tx(engine) as con:
        audit.seal(con)
    with tx(engine) as con:
        bad = one(con, 'SELECT audit_verify_chain() AS bad')['bad']
        n = one(con, 'SELECT count(*) AS n FROM audit_chain')['n']
    if bad is None:
        print(f'Audit chain intact ({n} rows).')
        return 0
    print(f'AUDIT CHAIN BROKEN at id {bad}.', file=sys.stderr)
    return 1


def _store(settings):
    return S3ObjectStore(endpoint=settings.s3_endpoint, access_key=settings.s3_access_key,
                         secret_key=settings.s3_secret_key, bucket=settings.s3_bucket)


def init_storage(_args) -> int:
    settings = get_settings()
    _store(settings).ensure_bucket()
    print(f'Bucket {settings.s3_bucket} ready (private; no public policy).')
    return 0


def expire_verifications(_args) -> int:
    with tx(make_engine(get_settings().database_url)) as con:
        print(json.dumps(expire_due(con)))
    return 0


def rescan_evidence(_args) -> int:
    """Rescan evidence stored while the scanner was unavailable. Infected files are crypto-shredded:
    the wrapped data key is discarded, so the stored ciphertext can never be decrypted again."""
    settings = get_settings()
    if not settings.clamd_host:
        print('No scanner configured.', file=sys.stderr)
        return 2
    engine, store = make_engine(settings.database_url), _store(settings)
    sealer, scanner = EvidenceSealer(_cipher(settings)), ClamdScanner(settings.clamd_host, settings.clamd_port)
    with tx(engine) as con:
        pending = many(con, '''SELECT id, object_key, wrapped_data_key FROM provider_evidence
                               WHERE scan_state = 'pending' ORDER BY uploaded_at LIMIT 200''')
    counts = {'clean': 0, 'infected': 0, 'unavailable': 0}
    for ev in pending:
        result = scanner.scan(sealer.open(str(ev['id']), store.get(ev['object_key']), ev['wrapped_data_key']))
        counts[result.state] += 1
        if result.state == 'unavailable':
            continue
        with tx(engine) as con:
            if result.state == 'clean':
                run(con, "UPDATE provider_evidence SET scan_state = 'clean' WHERE id = :id AND scan_state = 'pending'",
                    id=ev['id'])
            else:
                run(con, '''UPDATE provider_evidence SET scan_state = 'infected', scan_detail = :d,
                            review_state = 'rejected', object_key = NULL, wrapped_data_key = NULL
                            WHERE id = :id AND scan_state = 'pending' ''',
                    d=result.detail, id=ev['id'])
            audit.record(con, actor_type='system', actor_id='rescan-job',
                         action='evidence.scan_clean' if result.state == 'clean' else 'evidence.quarantined',
                         entity_type='provider_evidence', entity_id=str(ev['id']))
    print(json.dumps(counts))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='hela.cli')
    sub = parser.add_subparsers(dest='cmd', required=True)
    ca = sub.add_parser('create-admin')
    ca.add_argument('--username', required=True)
    ca.add_argument('--role', required=True)
    ca.add_argument('--reason', required=True)
    ca.set_defaults(fn=create_admin)
    sub.add_parser('seed-synthetic').set_defaults(fn=seed_synthetic)
    sub.add_parser('verify-audit').set_defaults(fn=verify_audit)
    sub.add_parser('init-storage').set_defaults(fn=init_storage)
    sub.add_parser('expire-verifications').set_defaults(fn=expire_verifications)
    sub.add_parser('rescan-evidence').set_defaults(fn=rescan_evidence)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == '__main__':
    raise SystemExit(main())
