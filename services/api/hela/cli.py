"""Operator CLI (run locally by a trusted operator, never exposed over HTTP).

  python -m hela.cli create-admin --username ops.alice --role super_admin --reason "initial bootstrap"
  python -m hela.cli seed-synthetic
  python -m hela.cli verify-audit
  python -m hela.cli init-storage
  python -m hela.cli expire-verifications
  python -m hela.cli rescan-evidence
  python -m hela.cli generate-keys            (local provider: prints base64 keys)
  python -m hela.cli generate-keys --wrapped  (transit provider: prints only wrapped keys)
  python -m hela.cli rewrap-keyring --name field   (after rotating the key-encryption key)
  python -m hela.cli wrap-keyring --name field     (moving from local keys to the key service)
  python -m hela.cli key-status
  python -m hela.cli rotate-field-keys
  python -m hela.cli rotate-lookup-key
  python -m hela.cli export-audit --dir ./audit-export          (or --bucket NAME for S3 Object Lock)
  python -m hela.cli verify-audit-export --dir ./audit-export --against-db
  python -m hela.cli record-heartbeat --name backup --status ok
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
from .ops import metrics as heartbeats
from .security import rotation
from .security.crypto import FieldCipher
from .security.keys import build_key_provider
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


def _owner_url() -> str:
    return os.environ.get('HELA_MIGRATION_DATABASE_URL',
                          'postgresql+psycopg://hela_owner:hela_owner_dev_only@127.0.0.1:55432/hela')


def _cipher(settings):
    return FieldCipher(build_key_provider(settings))


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
    with tx(make_engine(_owner_url())) as con:
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


def _audit_sink(args, settings):
    from .audit import export
    if args.dir:
        return export.DirectorySink(args.dir)
    store = _store(settings.model_copy(update={'s3_bucket': args.bucket}))
    sink = export.S3LockedSink(store._s3, args.bucket, retention_days=args.retention_days, mode=args.mode.upper())
    sink.ensure_bucket()
    return sink


def export_audit(args) -> int:
    """Run on a schedule (see infra/deploy). Records the `audit_export` heartbeat used by the monitoring alerts."""
    from .audit import export
    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        result = export.export(engine, _audit_sink(args, settings))
    except Exception as exc:
        with tx(engine) as con:
            heartbeats.record_heartbeat(con, 'audit_export', ok=False, detail=type(exc).__name__)
        print(f'Audit export failed: {exc}', file=sys.stderr)
        return 1
    with tx(engine) as con:
        heartbeats.record_heartbeat(con, 'audit_export', ok=True, detail=f'{result.rows} rows')
    print(json.dumps({'manifest': result.sequence, 'rows': result.rows, 'last_position': result.last_position}))
    return 0


def verify_audit_export(args) -> int:
    from .audit import export
    settings = get_settings()
    engine = make_engine(settings.database_url) if args.against_db else None
    report = export.verify(_audit_sink(args, settings), engine)
    for problem in report.problems:
        print(f'PROBLEM: {problem}', file=sys.stderr)
    print(f'{report.manifests} manifests, {report.rows} rows: ' + ('intact' if report.ok else 'NOT INTACT'))
    return 0 if report.ok else 1


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


def generate_keys(args) -> int:
    """Print fresh key material for a keyring entry. Store it in the secret manager, never in a file in the repo.

    With --wrapped (HELA_KEY_PROVIDER=transit) the new keys are wrapped by the key service before anything is
    printed, so plaintext keys never reach the terminal.
    """
    import base64
    import secrets

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    if args.wrapped:
        from .security.keys import _check_id, key_wrapper
        wrapper = key_wrapper(get_settings())
        if wrapper is None:
            print('--wrapped needs HELA_KEY_PROVIDER=transit', file=sys.stderr)
            return 2
        kid = _check_id(args.key_id)
        seed = secrets.token_bytes(32)
        public = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        print(json.dumps({
            'field_key': {kid: wrapper.wrap(secrets.token_bytes(32), purpose='field', key_id=kid)},
            'lookup_key': {kid: wrapper.wrap(secrets.token_bytes(32), purpose='lookup', key_id=kid)},
            'jwt_seed': {kid: wrapper.wrap(seed, purpose='jwt', key_id=kid)},
            'jwt_public_for_verify_only': {kid: base64.b64encode(public).decode()}}, indent=2))
        return 0
    field = base64.b64encode(secrets.token_bytes(32)).decode()
    seed = secrets.token_bytes(32)
    public = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    lookup = base64.b64encode(secrets.token_bytes(32)).decode()
    print(json.dumps({'field_key': {args.key_id: field}, 'lookup_key': {args.key_id: lookup},
                      'jwt_seed': {args.key_id: base64.b64encode(seed).decode()},
                      'jwt_public_for_verify_only': {args.key_id: base64.b64encode(public).decode()}}, indent=2))
    return 0


def rewrap_keyring(args) -> int:
    """Re-wrap one configured keyring under the newest key-encryption-key version; prints the new keyring JSON."""
    from .security.keys import key_wrapper
    settings = get_settings()
    wrapper = key_wrapper(settings)
    raw = {'field': settings.field_keyring, 'lookup': settings.lookup_keyring, 'jwt': settings.jwt_keyring}[args.name]
    if wrapper is None or not raw:
        print('rewrap-keyring needs HELA_KEY_PROVIDER=transit and the keyring set', file=sys.stderr)
        return 2
    doc = json.loads(raw)
    doc['keys'] = {k: wrapper.rewrap(v, purpose=args.name, key_id=k) for k, v in doc['keys'].items()}
    print(json.dumps(doc))
    return 0


def wrap_keyring(args) -> int:
    """Moving from HELA_KEY_PROVIDER=local to transit: wrap the current plaintext keyring (same key ids), so data
    already encrypted stays readable. Run with key_provider still 'local' and HELA_TRANSIT_* set; prints only
    wrapped values. Afterwards, remove the plaintext keys from the secret manager."""
    import base64

    from .security.keys import field_keyring, lookup_keyring
    from .security.transit import wrapper_from_settings
    settings = get_settings()
    if settings.key_provider != 'local':
        print('wrap-keyring reads the local (plaintext) keyring; run it with HELA_KEY_PROVIDER=local', file=sys.stderr)
        return 2
    wrapper = wrapper_from_settings(settings)
    if args.name == 'jwt':
        doc = json.loads(settings.effective_jwt_keyring())
        doc['keys'] = {k: wrapper.wrap(base64.b64decode(v), purpose='jwt', key_id=k) for k, v in doc['keys'].items()}
    else:
        ring = (field_keyring if args.name == 'field' else lookup_keyring)(settings)
        doc = {'active': ring.active,
               'keys': {k: wrapper.wrap(v, purpose=args.name, key_id=k) for k, v in ring.keys.items()}}
    print(json.dumps(doc))
    return 0


def key_status(_args) -> int:
    settings = get_settings()
    status = rotation.key_status(make_engine(_owner_url()))
    active = _cipher(settings).active_key_id
    print(f'Active field key: {active}')
    for column, counts in status.items():
        if counts:
            print(f'  {column:45} ' + ', '.join(f'{k}={n}' for k, n in sorted(counts.items())))
    left = sum(n for counts in status.values() for k, n in counts.items() if k != active)
    print(f'Values under other keys: {left}' + ('' if left else ' (older keys can be removed from the ring)'))
    lookup_active = _cipher(settings).active_lookup_key_id
    lookups = rotation.lookup_key_status(make_engine(_owner_url()))
    print(f'Active lookup key: {lookup_active}')
    for column, counts in lookups.items():
        if counts:
            print(f'  {column:45} ' + ', '.join(f'{k}={n}' for k, n in sorted(counts.items())))
    left = sum(n for counts in lookups.values() for k, n in counts.items() if k != lookup_active)
    print(f'Live lookup hashes under other keys: {left}'
          + ('' if left else ' (older lookup keys can be removed from the ring)'))
    return 0


def rotate_field_keys(args) -> int:
    settings = get_settings()
    cipher = _cipher(settings)
    engine = make_engine(_owner_url())
    done = rotation.rewrap_all(engine, cipher, batch=args.batch)
    left = rotation.remaining_under_other_keys(engine, cipher.active_key_id)
    print(json.dumps({'active_key': cipher.active_key_id, 'rewrapped': {k: v for k, v in done.items() if v},
                      'remaining_under_other_keys': left}))
    return 0 if left == 0 else 3      # rows locked by live traffic are skipped; run again


def rotate_lookup_key(args) -> int:
    settings = get_settings()
    cipher = _cipher(settings)
    engine = make_engine(_owner_url())
    done = rotation.reindex_user_lookups(engine, cipher, batch=args.batch)
    status = rotation.lookup_key_status(engine)
    users_left = sum(n for k, n in status['users.phone_lookup'].items() if k != cipher.active_lookup_key_id)
    short_lived = sum(n for col, counts in status.items() if col != 'users.phone_lookup'
                      for k, n in counts.items() if k != cipher.active_lookup_key_id)
    print(json.dumps({'active_lookup_key': cipher.active_lookup_key_id, 'accounts_reindexed': done,
                      'accounts_remaining': users_left, 'short_lived_hashes_under_other_keys': short_lived}))
    return 0 if users_left == 0 else 3


def record_heartbeat(args) -> int:
    """Called by scheduled operational checks (e.g. scripts/backup_restore_check.sh) for the monitoring alerts."""
    with tx(make_engine(get_settings().database_url)) as con:
        heartbeats.record_heartbeat(con, args.name, ok=args.status == 'ok', detail=args.detail)
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
    gk = sub.add_parser('generate-keys')
    gk.add_argument('--key-id', required=True)
    gk.add_argument('--wrapped', action='store_true', help='wrap with the key service (HELA_KEY_PROVIDER=transit)')
    gk.set_defaults(fn=generate_keys)
    rw = sub.add_parser('rewrap-keyring')
    rw.add_argument('--name', choices=['field', 'lookup', 'jwt'], required=True)
    rw.set_defaults(fn=rewrap_keyring)
    wk = sub.add_parser('wrap-keyring')
    wk.add_argument('--name', choices=['field', 'lookup', 'jwt'], required=True)
    wk.set_defaults(fn=wrap_keyring)
    sub.add_parser('key-status').set_defaults(fn=key_status)
    rk = sub.add_parser('rotate-field-keys')
    rk.add_argument('--batch', type=int, default=200)
    rk.set_defaults(fn=rotate_field_keys)
    rl = sub.add_parser('rotate-lookup-key')
    rl.add_argument('--batch', type=int, default=200)
    rl.set_defaults(fn=rotate_lookup_key)
    for name, fn in (('export-audit', export_audit), ('verify-audit-export', verify_audit_export)):
        ex = sub.add_parser(name)
        target = ex.add_mutually_exclusive_group(required=True)
        target.add_argument('--dir', help='local directory (development only; not write-once storage)')
        target.add_argument('--bucket', help='S3-compatible bucket with Object Lock enabled')
        ex.add_argument('--retention-days', type=int, default=7,
                        help='Object Lock retention; the real period is DEC-09 (pending)')
        ex.add_argument('--mode', choices=['governance', 'compliance'], default='governance')
        if name == 'verify-audit-export':
            ex.add_argument('--against-db', action='store_true', help='also compare with the live audit_chain')
        ex.set_defaults(fn=fn)
    hb = sub.add_parser('record-heartbeat')
    hb.add_argument('--name', required=True)
    hb.add_argument('--status', choices=['ok', 'failed'], required=True)
    hb.add_argument('--detail')
    hb.set_defaults(fn=record_heartbeat)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == '__main__':
    raise SystemExit(main())
