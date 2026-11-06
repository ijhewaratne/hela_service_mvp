"""Operator CLI (run locally by a trusted operator, never exposed over HTTP).

  python -m hela.cli create-admin --username ops.alice --role super_admin --reason "initial bootstrap"
  python -m hela.cli seed-synthetic
  python -m hela.cli verify-audit
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from .audit import service as audit
from .core.config import get_settings
from .core.db import make_engine, one, run, tx
from .security.crypto import EnvKeyProvider, FieldCipher
from .security.passwords import hash_password, new_totp_secret, provisioning_uri

# Approximate, fictional boundary around central Kandy for synthetic tests only (Q16, DEC-02 pending).
SYNTHETIC_KANDY_WKT = ('MULTIPOLYGON(((80.600 7.260, 80.670 7.260, 80.670 7.320, 80.600 7.320, 80.600 7.260)))')

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
    with tx(make_engine(settings.database_url)) as con:
        region = one(con, '''INSERT INTO service_regions(code, name, boundary, status, is_synthetic)
                             VALUES ('synthetic_kandy_central', 'Synthetic Kandy Central (fictional boundary)',
                                     ST_GeogFromText(:wkt), 'enabled', true)
                             ON CONFLICT (code) DO NOTHING RETURNING id''', wkt=SYNTHETIC_KANDY_WKT)
        for code, title, body in NOTICES:
            run(con, '''INSERT INTO consent_notices(code, version, language, title, body, translation_status)
                        VALUES (:c, 1, 'en', :t, :b, 'source') ON CONFLICT DO NOTHING''', c=code, t=title, b=body)
        if region:
            audit.record(con, actor_type='system', actor_id='seed-synthetic', action='region.seeded',
                         entity_type='service_region', entity_id=str(region['id']), details={'synthetic': True})
    print('Synthetic seed applied.')
    return 0


def verify_audit(_args) -> int:
    with tx(make_engine(get_settings().database_url)) as con:
        bad = one(con, 'SELECT audit_verify_chain() AS bad')['bad']
        n = one(con, 'SELECT count(*) AS n FROM audit_logs')['n']
    if bad is None:
        print(f'Audit chain intact ({n} rows).')
        return 0
    print(f'AUDIT CHAIN BROKEN at id {bad}.', file=sys.stderr)
    return 1


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
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == '__main__':
    raise SystemExit(main())
