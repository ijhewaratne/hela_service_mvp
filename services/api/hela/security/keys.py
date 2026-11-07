"""Versioned key material for field encryption and token signing (Q38, Q65).

Every ciphertext and every access token names the key that produced it, so keys can be rotated without downtime:

* Field encryption uses a *keyring*: one active key that encrypts, plus older keys that only decrypt. After a new
  key becomes active, `python -m hela.cli rotate-field-keys` re-encrypts stored values in batches; once
  `key-status` shows no rows left under an old key, that key can be removed from the ring.
* Access tokens are signed with Ed25519 (EdDSA) and carry a `kid` header. Retired signing keys stay in the ring as
  verify-only public keys for one access-token lifetime (10 minutes), then are dropped.

Key material here comes from environment variables, which is acceptable only for local development and tests.
A managed KMS integration implements the same `KeyProvider` / `SigningKeys` interfaces (for example by unwrapping
data keys with the KMS at start-up) and is selected with `HELA_KEY_PROVIDER`; until one exists, any other value is
refused rather than silently falling back to environment keys.
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

KEY_ID = re.compile(r'^[a-z0-9][a-z0-9_-]{0,31}$')
LEGACY_FIELD_KEY_ID = 'k1'           # v1 ciphertexts carry no key id; they were written with this key
DEV_MARKER = b'dev-only'             # every committed development key starts with this; refused outside dev/test


class KeyConfigError(ValueError):
    pass


def _b64_32(value: str, what: str) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise KeyConfigError(f'{what} is not valid base64') from exc
    if len(raw) != 32:
        raise KeyConfigError(f'{what} must decode to exactly 32 bytes')
    return raw


def _check_id(kid: str) -> str:
    if not KEY_ID.match(kid):
        raise KeyConfigError(f'Invalid key id {kid!r}: use 1-32 lowercase letters, digits, "_" or "-"')
    return kid


@dataclass(frozen=True)
class Keyring:
    active: str
    keys: dict[str, bytes]

    @classmethod
    def parse(cls, raw: str, what: str) -> Keyring:
        """`{"active": "k2", "keys": {"k1": "<base64 32 bytes>", "k2": "<base64 32 bytes>"}}`"""
        try:
            doc = json.loads(raw)
            active, keys = doc['active'], doc['keys']
        except (ValueError, KeyError, TypeError) as exc:
            raise KeyConfigError(f'{what} must be JSON with "active" and "keys"') from exc
        parsed = {_check_id(k): _b64_32(v, f'{what} key {k!r}') for k, v in keys.items()}
        if active not in parsed:
            raise KeyConfigError(f'{what}: the active key {active!r} is not in the ring')
        return cls(active=active, keys=parsed)

    def has_dev_material(self) -> bool:
        return any(k.startswith(DEV_MARKER) for k in self.keys.values())


class KeyProvider(Protocol):
    """What the field cipher needs. A KMS-backed provider implements the same three methods."""

    def active_field_key(self) -> tuple[str, bytes]: ...
    def field_key(self, key_id: str) -> bytes: ...          # raises KeyError for an unknown or removed key
    def lookup_key(self) -> bytes: ...


class LocalKeyProvider:
    """Keys held in process memory, loaded from environment variables. Development and tests only."""

    def __init__(self, field: Keyring, lookup: bytes):
        self._field = field
        self._lookup = lookup

    @classmethod
    def from_settings(cls, settings) -> LocalKeyProvider:
        return cls(field_keyring(settings), _b64_32(settings.lookup_key_b64, 'HELA_LOOKUP_KEY_B64'))

    def active_field_key(self) -> tuple[str, bytes]:
        return self._field.active, self._field.keys[self._field.active]

    def field_key(self, key_id: str) -> bytes:
        return self._field.keys[key_id]

    def lookup_key(self) -> bytes:
        return self._lookup


def field_keyring(settings) -> Keyring:
    if settings.field_keyring:
        return Keyring.parse(settings.field_keyring, 'HELA_FIELD_KEYRING')
    return Keyring(LEGACY_FIELD_KEY_ID, {LEGACY_FIELD_KEY_ID: _b64_32(settings.field_key_b64, 'HELA_FIELD_KEY_B64')})


def build_key_provider(settings) -> KeyProvider:
    if settings.key_provider == 'local':
        return LocalKeyProvider.from_settings(settings)
    raise KeyConfigError(f'Key provider {settings.key_provider!r} is not implemented; refusing to start')


@dataclass(frozen=True)
class SigningKeys:
    active: str
    private: Ed25519PrivateKey
    public: dict[str, Ed25519PublicKey]
    dev_material: bool

    @classmethod
    def parse(cls, raw: str) -> SigningKeys:
        """`{"active": "j2", "keys": {"j2": "<base64 32-byte Ed25519 seed>"}, "verify_only": {"j1": "<base64 public>"}}`

        Only the active signing key needs its private seed; retired keys are listed as public keys.
        """
        try:
            doc = json.loads(raw)
            active, seeds = doc['active'], doc['keys']
            verify_only = doc.get('verify_only', {})
        except (ValueError, KeyError, TypeError) as exc:
            raise KeyConfigError('HELA_JWT_KEYRING must be JSON with "active" and "keys"') from exc
        privates = {_check_id(k): _b64_32(v, f'JWT key {k!r}') for k, v in seeds.items()}
        if active not in privates:
            raise KeyConfigError(f'HELA_JWT_KEYRING: the active key {active!r} has no private seed')
        public = {k: Ed25519PrivateKey.from_private_bytes(v).public_key() for k, v in privates.items()}
        for k, v in verify_only.items():
            if _check_id(k) in public:
                raise KeyConfigError(f'HELA_JWT_KEYRING: key {k!r} is listed twice')
            public[k] = Ed25519PublicKey.from_public_bytes(_b64_32(v, f'JWT public key {k!r}'))
        return cls(active=active, private=Ed25519PrivateKey.from_private_bytes(privates[active]), public=public,
                   dev_material=any(v.startswith(DEV_MARKER) for v in privates.values()))


@lru_cache(maxsize=8)
def _signing_keys(raw: str) -> SigningKeys:
    return SigningKeys.parse(raw)


def signing_keys(settings) -> SigningKeys:
    return _signing_keys(settings.effective_jwt_keyring())
