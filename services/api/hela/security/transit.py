"""Key wrapping with a Vault-compatible transit engine (OpenBao or HashiCorp Vault), for `HELA_KEY_PROVIDER=transit`.

The service's keys (field, lookup and token-signing keys) are stored only in wrapped form, encrypted by a
key-encryption key (KEK) that never leaves the key service. At start-up each process asks the key service to
unwrap them once and keeps the plaintext in memory only. Every wrapped key is bound, as associated data, to its
purpose and key id (`hela:field:k2`), so a wrapped lookup key cannot be passed off as a field key.

Rotating the KEK (`transit/keys/<name>/rotate`) and then `rewrap-keyring` re-wraps the stored values under
the new KEK version; plaintext keys are never printed or written anywhere. Revoking the service's access to the
KEK stops new processes from starting, which is the intended kill switch.

A cloud KMS (AWS KMS, Google Cloud KMS, Azure Key Vault) fits the same `KeyWrapper` interface with Encrypt /
Decrypt and an encryption context; only this adapter has been built and tested (against OpenBao locally).
"""
from __future__ import annotations

import base64
import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class KeyServiceError(RuntimeError):
    pass


class KeyWrapper(Protocol):
    def wrap(self, plaintext: bytes, *, purpose: str, key_id: str) -> str: ...
    def unwrap(self, wrapped: str, *, purpose: str, key_id: str) -> bytes: ...
    def rewrap(self, wrapped: str, *, purpose: str, key_id: str) -> str: ...


def _aad(purpose: str, key_id: str) -> str:
    return base64.b64encode(f'hela:{purpose}:{key_id}'.encode()).decode()


@dataclass(frozen=True)
class TransitWrapper:
    addr: str                     # e.g. https://openbao.hela-platform.svc:8200
    token: str = ''
    token_file: str | None = None  # e.g. written by an agent sidecar after Kubernetes auth; read on each call
    mount: str = 'transit'
    key: str = 'hela-keys'
    ca_file: str | None = None
    timeout: float = 5.0

    def __post_init__(self):
        if not self.addr.startswith(('https://', 'http://')):
            raise KeyServiceError('HELA_TRANSIT_ADDR must be an http(s) URL')

    def _token(self) -> str:
        if self.token_file:
            return Path(self.token_file).read_text().strip()
        return self.token

    def _call(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(  # noqa: S310 - http(s) only, checked in __post_init__
            f'{self.addr.rstrip("/")}/v1/{self.mount}/{path}/{self.key}',
            data=json.dumps(body).encode(), method='POST',
            headers={'X-Vault-Token': self._token(), 'Content-Type': 'application/json'})
        context = ssl.create_default_context(cafile=self.ca_file) if self.addr.startswith('https') else None
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=context) as resp:  # noqa: S310 - fixed scheme
                return json.load(resp)['data']
        except urllib.error.HTTPError as exc:
            # The key service's message names the failure (permission denied, authentication failed); it never
            # contains key material. The token is not included.
            try:
                detail = '; '.join(json.load(exc).get('errors', []))[:200]
            except ValueError:
                detail = ''
            raise KeyServiceError(f'Key service refused {path} ({exc.code}): {detail}') from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise KeyServiceError(f'Key service unreachable for {path}: {type(exc).__name__}') from None

    def wrap(self, plaintext: bytes, *, purpose: str, key_id: str) -> str:
        return self._call('encrypt', {'plaintext': base64.b64encode(plaintext).decode(),
                                      'associated_data': _aad(purpose, key_id)})['ciphertext']

    def unwrap(self, wrapped: str, *, purpose: str, key_id: str) -> bytes:
        data = self._call('decrypt', {'ciphertext': wrapped, 'associated_data': _aad(purpose, key_id)})
        return base64.b64decode(data['plaintext'])

    def rewrap(self, wrapped: str, *, purpose: str, key_id: str) -> str:
        # The transit `rewrap` endpoint does not accept associated data, so this unwraps and wraps again under the
        # newest KEK version. The plaintext exists only in this (operator) process's memory and is never printed.
        return self.wrap(self.unwrap(wrapped, purpose=purpose, key_id=key_id), purpose=purpose, key_id=key_id)


def wrapper_from_settings(settings) -> TransitWrapper:
    if not settings.transit_addr or not (settings.transit_token or settings.transit_token_file):
        raise KeyServiceError('HELA_KEY_PROVIDER=transit needs HELA_TRANSIT_ADDR and a token '
                              '(HELA_TRANSIT_TOKEN or HELA_TRANSIT_TOKEN_FILE)')
    return TransitWrapper(addr=settings.transit_addr, token=settings.transit_token or '',
                          token_file=settings.transit_token_file, mount=settings.transit_mount,
                          key=settings.transit_key, ca_file=settings.transit_ca_file)
