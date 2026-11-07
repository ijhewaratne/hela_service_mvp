"""Field encryption (AES-256-GCM) and keyed lookup hashes, behind a key-provider abstraction (Q38, Q65).

Ciphertext format v2: b'v2' | key-id length (1 byte) | key id (ascii) | nonce (12) | ciphertext+tag.
The header and the column context are bound as associated data, so a value cannot be moved to another column
or relabelled with another key id. v1 values (b'v1' | nonce | ct, context-only AAD) predate key ids; they were
written with key 'k1' and are still readable until `rotate-field-keys` has re-encrypted them.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .keys import LEGACY_FIELD_KEY_ID, KeyProvider

V1, V2 = b'v1', b'v2'


def _header(key_id: str) -> bytes:
    kid = key_id.encode('ascii')
    return V2 + bytes([len(kid)]) + kid


def key_id_of(blob: bytes) -> str:
    blob = bytes(blob)
    if blob[:2] == V1:
        return LEGACY_FIELD_KEY_ID
    if blob[:2] == V2:
        return blob[3:3 + blob[2]].decode('ascii')
    raise ValueError('Unknown ciphertext version')


class FieldCipher:
    def __init__(self, keys: KeyProvider):
        self._keys = keys

    @property
    def active_key_id(self) -> str:
        return self._keys.active_field_key()[0]

    def encrypt(self, plaintext: str, *, context: str) -> bytes:
        """`context` is bound as associated data so a ciphertext cannot be moved to another column."""
        kid, key = self._keys.active_field_key()
        header = _header(kid)
        nonce = os.urandom(12)
        return header + nonce + AESGCM(key).encrypt(nonce, plaintext.encode(), header + b'|' + context.encode())

    def decrypt(self, blob: bytes, *, context: str) -> str:
        blob = bytes(blob)
        if blob[:2] == V1:
            key = self._keys.field_key(LEGACY_FIELD_KEY_ID)
            return AESGCM(key).decrypt(blob[2:14], blob[14:], context.encode()).decode()
        if blob[:2] != V2:
            raise ValueError('Unknown ciphertext version')
        n = 3 + blob[2]
        header, kid = blob[:n], blob[3:n].decode('ascii')
        try:
            key = self._keys.field_key(kid)
        except KeyError:
            raise ValueError(f'Ciphertext key {kid!r} is not in the keyring') from None
        return AESGCM(key).decrypt(blob[n:n + 12], blob[n + 12:], header + b'|' + context.encode()).decode()

    def needs_rewrap(self, blob: bytes) -> bool:
        return bytes(blob)[:2] != V2 or key_id_of(blob) != self.active_key_id

    def rewrap(self, blob: bytes, *, context: str) -> bytes:
        return self.encrypt(self.decrypt(blob, context=context), context=context)

    def lookup_hash(self, value: str, *, purpose: str) -> str:
        return hmac.new(self._keys.lookup_key(), f'{purpose}:{value}'.encode(), hashlib.sha256).hexdigest()


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
