"""Field encryption (AES-256-GCM) and keyed lookup hashes, behind a key-provider abstraction (Q38, Q65).

Production keys must come from a managed KMS; EnvKeyProvider is for local development and tests.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = b'v1'


class KeyProvider(Protocol):
    def field_key(self) -> bytes: ...
    def lookup_key(self) -> bytes: ...


class EnvKeyProvider:
    def __init__(self, field_key_b64: str, lookup_key_b64: str):
        self._field = base64.b64decode(field_key_b64)
        self._lookup = base64.b64decode(lookup_key_b64)
        if len(self._field) != 32 or len(self._lookup) != 32:
            raise ValueError('Field and lookup keys must each be 32 bytes (base64-encoded)')

    def field_key(self) -> bytes:
        return self._field

    def lookup_key(self) -> bytes:
        return self._lookup


class FieldCipher:
    def __init__(self, keys: KeyProvider):
        self._keys = keys

    def encrypt(self, plaintext: str, *, context: str) -> bytes:
        """`context` is bound as associated data so a ciphertext cannot be moved to another column."""
        nonce = os.urandom(12)
        ct = AESGCM(self._keys.field_key()).encrypt(nonce, plaintext.encode(), context.encode())
        return _VERSION + nonce + ct

    def decrypt(self, blob: bytes, *, context: str) -> str:
        blob = bytes(blob)
        if blob[:2] != _VERSION:
            raise ValueError('Unknown ciphertext version')
        return AESGCM(self._keys.field_key()).decrypt(blob[2:14], blob[14:], context.encode()).decode()

    def lookup_hash(self, value: str, *, purpose: str) -> str:
        return hmac.new(self._keys.lookup_key(), f'{purpose}:{value}'.encode(), hashlib.sha256).hexdigest()


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
