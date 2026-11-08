"""Versioned key material for field encryption and token signing (Q38, Q65).

Every ciphertext and every access token names the key that produced it, so keys can be rotated without downtime:

* Field encryption uses a *keyring*: one active key that encrypts, plus older keys that only decrypt. After a new
  key becomes active, `python -m hela.cli rotate-field-keys` re-encrypts stored values in batches; once
  `key-status` shows no rows left under an old key, that key can be removed from the ring.
* Lookup hashes (keyed HMACs used to find an account by phone number without storing it in clear) also use a
  keyring. A hash made with a key other than the legacy 'k1' is stored as `<key id>:<hex>`. While a new lookup
  key is being introduced, lookups try every key in the ring; `rotate-lookup-key` recomputes stored account
  hashes from the encrypted phone number, and short-lived OTP and consent hashes simply expire.
* Access tokens are signed with Ed25519 (EdDSA) and carry a `kid` header. Retired signing keys stay in the ring as
  verify-only public keys for one access-token lifetime (10 minutes), then are dropped.

Where the key material comes from is set by `HELA_KEY_PROVIDER`:

* `local`: plaintext base64 keys in environment variables. Development and tests only; refused in production.
* `transit`: the same keyring JSON, but every key is *wrapped* by a key-encryption key held in a Vault-compatible
  transit engine (see transit.py). Each process unwraps the keys once at start-up and keeps them in memory only.
  The environment never holds a usable key.
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
LEGACY_LOOKUP_KEY_ID = 'k1'          # lookup hashes without a "<key id>:" prefix were made with this key
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


def _material(value, what: str, *, purpose: str, key_id: str, wrapper) -> bytes:
    """Plaintext key bytes: decoded from base64 (local) or unwrapped by the key service (transit)."""
    if wrapper is None:
        return _b64_32(value, what)
    if not isinstance(value, str) or not value.startswith('vault:v'):
        raise KeyConfigError(f'{what} must be a wrapped value ("vault:v..."); plaintext keys are refused '
                             'with HELA_KEY_PROVIDER=transit')
    raw = wrapper.unwrap(value, purpose=purpose, key_id=key_id)
    if len(raw) != 32:
        raise KeyConfigError(f'{what} must unwrap to exactly 32 bytes')
    return raw


@dataclass(frozen=True)
class Keyring:
    active: str
    keys: dict[str, bytes]

    @classmethod
    def parse(cls, raw: str, what: str, *, purpose: str = 'field', wrapper=None) -> Keyring:
        """`{"active": "k2", "keys": {"k1": "<key>", "k2": "<key>"}}`; a key is base64 (local) or wrapped (transit)."""
        try:
            doc = json.loads(raw)
            active, keys = doc['active'], doc['keys']
        except (ValueError, KeyError, TypeError) as exc:
            raise KeyConfigError(f'{what} must be JSON with "active" and "keys"') from exc
        parsed = {_check_id(k): _material(v, f'{what} key {k!r}', purpose=purpose, key_id=k, wrapper=wrapper)
                  for k, v in keys.items()}
        if active not in parsed:
            raise KeyConfigError(f'{what}: the active key {active!r} is not in the ring')
        return cls(active=active, keys=parsed)

    def has_dev_material(self) -> bool:
        return any(k.startswith(DEV_MARKER) for k in self.keys.values())


class KeyProvider(Protocol):
    """What the field cipher needs."""

    def active_field_key(self) -> tuple[str, bytes]: ...
    def field_key(self, key_id: str) -> bytes: ...          # raises KeyError for an unknown or removed key
    def active_lookup_key(self) -> tuple[str, bytes]: ...
    def lookup_keys(self) -> dict[str, bytes]: ...          # every lookup key still in the ring, active included


class InMemoryKeyProvider:
    """Keys held in process memory once loaded, whichever provider supplied them."""

    def __init__(self, field: Keyring, lookup: Keyring):
        self._field = field
        self._lookup = lookup

    @classmethod
    def from_settings(cls, settings) -> InMemoryKeyProvider:
        return cls(field_keyring(settings), lookup_keyring(settings))

    def active_field_key(self) -> tuple[str, bytes]:
        return self._field.active, self._field.keys[self._field.active]

    def field_key(self, key_id: str) -> bytes:
        return self._field.keys[key_id]

    def active_lookup_key(self) -> tuple[str, bytes]:
        return self._lookup.active, self._lookup.keys[self._lookup.active]

    def lookup_keys(self) -> dict[str, bytes]:
        return dict(self._lookup.keys)


LocalKeyProvider = InMemoryKeyProvider     # name used before the transit provider existed


def key_wrapper(settings):
    """The transit wrapper for `HELA_KEY_PROVIDER=transit`, else None."""
    if settings.key_provider == 'local':
        return None
    if settings.key_provider == 'transit':
        from .transit import wrapper_from_settings
        return wrapper_from_settings(settings)
    raise KeyConfigError(f'Key provider {settings.key_provider!r} is not implemented; refusing to start')


@lru_cache(maxsize=16)
def _keyring(raw: str, what: str, purpose: str, wrapper) -> Keyring:
    # Cached so each process asks the key service once per keyring, not per request.
    return Keyring.parse(raw, what, purpose=purpose, wrapper=wrapper)


def _require(value, name: str, settings) -> str:
    if not value:
        raise KeyConfigError(f'{name} must be set with HELA_KEY_PROVIDER={settings.key_provider}')
    return value


def field_keyring(settings) -> Keyring:
    wrapper = key_wrapper(settings)
    if wrapper is not None:
        return _keyring(_require(settings.field_keyring, 'HELA_FIELD_KEYRING', settings), 'HELA_FIELD_KEYRING',
                        'field', wrapper)
    if settings.field_keyring:
        return _keyring(settings.field_keyring, 'HELA_FIELD_KEYRING', 'field', None)
    return Keyring(LEGACY_FIELD_KEY_ID, {LEGACY_FIELD_KEY_ID: _b64_32(settings.field_key_b64, 'HELA_FIELD_KEY_B64')})


def lookup_keyring(settings) -> Keyring:
    wrapper = key_wrapper(settings)
    if wrapper is not None:
        return _keyring(_require(settings.lookup_keyring, 'HELA_LOOKUP_KEYRING', settings), 'HELA_LOOKUP_KEYRING',
                        'lookup', wrapper)
    if settings.lookup_keyring:
        return _keyring(settings.lookup_keyring, 'HELA_LOOKUP_KEYRING', 'lookup', None)
    return Keyring(LEGACY_LOOKUP_KEY_ID,
                   {LEGACY_LOOKUP_KEY_ID: _b64_32(settings.lookup_key_b64, 'HELA_LOOKUP_KEY_B64')})


def build_key_provider(settings) -> KeyProvider:
    provider = InMemoryKeyProvider.from_settings(settings)
    signing_keys(settings)          # load (and, with transit, unwrap) every key at start-up, not on first use
    return provider


@dataclass(frozen=True)
class SigningKeys:
    active: str
    private: Ed25519PrivateKey
    public: dict[str, Ed25519PublicKey]
    dev_material: bool

    @classmethod
    def parse(cls, raw: str, wrapper=None) -> SigningKeys:
        """`{"active": "j2", "keys": {"j2": "<32-byte Ed25519 seed>"}, "verify_only": {"j1": "<base64 public>"}}`

        Only the active signing key needs its private seed (base64, or wrapped with transit); retired keys are
        listed as public keys.
        """
        try:
            doc = json.loads(raw)
            active, seeds = doc['active'], doc['keys']
            verify_only = doc.get('verify_only', {})
        except (ValueError, KeyError, TypeError) as exc:
            raise KeyConfigError('HELA_JWT_KEYRING must be JSON with "active" and "keys"') from exc
        privates = {_check_id(k): _material(v, f'JWT key {k!r}', purpose='jwt', key_id=k, wrapper=wrapper)
                    for k, v in seeds.items()}
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
def _signing_keys(raw: str, wrapper) -> SigningKeys:
    return SigningKeys.parse(raw, wrapper)


def signing_keys(settings) -> SigningKeys:
    wrapper = key_wrapper(settings)
    if wrapper is not None:
        return _signing_keys(_require(settings.jwt_keyring, 'HELA_JWT_KEYRING', settings), wrapper)
    return _signing_keys(settings.effective_jwt_keyring(), None)
