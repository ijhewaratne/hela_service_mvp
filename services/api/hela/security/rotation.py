"""Field-key rotation: report which key protects each stored value, and re-encrypt under the active key (Q65).

Runs from the operator CLI as the schema owner (history tables are append-only for everyone else; see migration
0008). Work is done in small batches, each in its own transaction with SKIP LOCKED, so it can run while the
service is live and can be stopped and resumed at any point. Plaintext never leaves process memory.
"""
from __future__ import annotations

from collections import Counter

from sqlalchemy import Engine

from ..audit import service as audit
from ..core.db import many, one, run, tx
from .crypto import FieldCipher, _header

# (table, primary key, column, encryption context). Every encrypted column in the schema must be listed here;
# tests/test_key_rotation.py fails if a bytea column is added without an entry.
ENCRYPTED_COLUMNS: list[tuple[str, str, str, str]] = [
    ('users', 'id', 'phone_enc', 'users.phone'),
    ('admin_users', 'id', 'totp_secret_enc', 'admin_users.totp'),
    ('beneficiaries', 'id', 'name_enc', 'beneficiaries.name'),
    ('beneficiaries', 'id', 'phone_enc', 'beneficiaries.phone'),
    ('customer_addresses', 'id', 'address_enc', 'customer_addresses.address'),
    ('customer_addresses', 'id', 'pin_enc', 'customer_addresses.pin'),
    ('provider_profiles', 'user_id', 'legal_name_enc', 'provider_profiles.legal_name'),
    ('provider_profiles', 'user_id', 'date_of_birth_enc', 'provider_profiles.dob'),
    ('provider_evidence', 'id', 'wrapped_data_key', 'provider_evidence.data_key'),
    ('service_requests', 'id', 'task_note_enc', 'service_requests.task_note'),
    ('bookings', 'id', 'pin_enc', 'bookings.pin'),
    ('settlement_events', 'id', 'note_enc', 'settlement_events.note'),
    ('incidents', 'id', 'description_enc', 'incidents.description'),
    ('incident_actions', 'id', 'note_enc', 'incident_actions.note'),
    ('reviews', 'id', 'text_enc', 'reviews.text'),
    ('privacy_requests', 'id', 'details_enc', 'privacy_requests.details'),
    ('privacy_request_events', 'id', 'note_enc', 'privacy_request_events.note'),
]

# Key id of a stored value, computed in SQL from the ciphertext header (see crypto.py for the format). Legacy v1
# values are reported separately: they are re-encrypted into the v2 format even though their key is k1.
_KEY_ID_SQL = ("CASE WHEN length({c}) < 4 THEN 'unreadable' "
               "WHEN substring({c} from 1 for 2) = 'v1'::bytea THEN 'k1 (v1 format)' "
               "ELSE convert_from(substring({c} from 4 for get_byte({c}, 2)), 'SQL_ASCII') END")


def key_status(engine: Engine) -> dict[str, Counter]:
    """{'table.column': Counter({key_id: rows})} for every encrypted column."""
    out: dict[str, Counter] = {}
    with tx(engine) as con:
        for table, _pk, col, _ctx in ENCRYPTED_COLUMNS:
            rows = many(con, f'''SELECT {_KEY_ID_SQL.format(c=col)} AS kid, count(*) AS n FROM {table}
                                 WHERE {col} IS NOT NULL GROUP BY 1''')  # noqa: S608 - fixed identifiers
            out[f'{table}.{col}'] = Counter({r['kid']: r['n'] for r in rows})
    return out


def rewrap_all(engine: Engine, cipher: FieldCipher, *, batch: int = 200, actor: str = 'operator-cli') -> dict[str, int]:
    """Re-encrypt every value not under the active key. Returns rows rewritten per column."""
    prefix = _header(cipher.active_key_id)
    done: dict[str, int] = {}
    for table, pk, col, ctx in ENCRYPTED_COLUMNS:
        total = 0
        while True:
            with tx(engine) as con:
                run(con, "SET LOCAL hela.key_rotation = 'on'")
                rows = many(con, f'''SELECT {pk} AS pk, {col} AS blob FROM {table}
                                     WHERE {col} IS NOT NULL AND substring({col} from 1 for :n) <> :prefix
                                     ORDER BY {pk} LIMIT :batch FOR UPDATE SKIP LOCKED''',  # noqa: S608
                            n=len(prefix), prefix=prefix, batch=batch)
                for r in rows:
                    run(con, f'UPDATE {table} SET {col} = :v WHERE {pk} = :pk',  # noqa: S608 - fixed identifiers
                        v=cipher.rewrap(r['blob'], context=ctx), pk=r['pk'])
            total += len(rows)
            if len(rows) < batch:
                break
        done[f'{table}.{col}'] = total
    with tx(engine) as con:
        audit.record(con, actor_type='system', actor_id=actor, action='keys.field_rewrapped', entity_type='keyring',
                     entity_id=cipher.active_key_id,
                     details={'rows': sum(done.values()), 'columns': {k: v for k, v in done.items() if v}})
    return done


def remaining_under_other_keys(engine: Engine, active: str) -> int:
    with tx(engine) as con:
        return sum(one(con, f'''SELECT count(*) AS n FROM {table} WHERE {col} IS NOT NULL
                                 AND {_KEY_ID_SQL.format(c=col)} <> :k''', k=active)['n']  # noqa: S608
                   for table, _pk, col, _ctx in ENCRYPTED_COLUMNS)


# --- lookup-key rotation -----------------------------------------------------------------------------------------
# Lookup hashes say which key made them ("<key id>:<hex>", or bare hex for the legacy key 'k1'). Account hashes are
# long-lived and are recomputed from the encrypted phone number. OTP challenges, consent codes and the dev SMS
# inbox are short-lived: they are reported here and simply expire, after which the old key can leave the ring.

_LOOKUP_KEY_ID_SQL = "CASE WHEN position(':' in {c}) = 0 THEN 'k1' ELSE split_part({c}, ':', 1) END"

LOOKUP_COLUMNS: list[tuple[str, str, str]] = [
    # (table, column, which rows still matter)
    ('users', 'phone_lookup', 'true'),
    ('otp_challenges', 'phone_lookup', "created_at > now() - interval '1 day'"),
    ('otp_challenges', 'code_hash', "consumed_at IS NULL AND expires_at > now()"),
    ('beneficiary_consents', 'code_hash', "status = 'pending' AND expires_at > now()"),
    ('dev_sms_inbox', 'phone_lookup', "created_at > now() - interval '1 day'"),
]


def lookup_key_status(engine: Engine) -> dict[str, Counter]:
    out: dict[str, Counter] = {}
    with tx(engine) as con:
        for table, col, live in LOOKUP_COLUMNS:
            rows = many(con, f'''SELECT {_LOOKUP_KEY_ID_SQL.format(c=col)} AS kid, count(*) AS n FROM {table}
                                 WHERE {live} AND {col} <> '' GROUP BY 1''')  # noqa: S608 - fixed identifiers
            out[f'{table}.{col}'] = Counter({r['kid']: r['n'] for r in rows})
    return out


def reindex_user_lookups(engine: Engine, cipher: FieldCipher, *, batch: int = 200,
                         actor: str = 'operator-cli') -> int:
    """Recompute account phone hashes under the active lookup key. Returns rows rewritten."""
    active = cipher.active_lookup_key_id
    other_key = f"({_LOOKUP_KEY_ID_SQL.format(c='phone_lookup')}) <> :k"
    total = 0
    while True:
        with tx(engine) as con:
            rows = many(con, f'''SELECT id, phone_enc FROM users WHERE {other_key}
                                 ORDER BY id LIMIT :batch FOR UPDATE SKIP LOCKED''', k=active, batch=batch)  # noqa: S608
            for r in rows:
                phone = cipher.decrypt(r['phone_enc'], context='users.phone')
                run(con, 'UPDATE users SET phone_lookup = :p, updated_at = now() WHERE id = :id',
                    p=cipher.lookup_hash(phone, purpose='phone'), id=r['id'])
        total += len(rows)
        if len(rows) < batch:
            break
    with tx(engine) as con:
        audit.record(con, actor_type='system', actor_id=actor, action='keys.lookup_reindexed', entity_type='keyring',
                     entity_id=active, details={'rows': total})
    return total
