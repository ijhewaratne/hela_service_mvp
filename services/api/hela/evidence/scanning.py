"""Malware scanning and file validation for evidence uploads (Q28, SEC-07). Unavailable scanning fails closed:
the file is stored encrypted as `pending` and cannot be reviewed or downloaded until a rescan says clean."""
from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from typing import Literal, Protocol

ScanState = Literal['clean', 'infected', 'unavailable']


@dataclass(frozen=True)
class ScanResult:
    state: ScanState
    detail: str = ''


class MalwareScanner(Protocol):
    def scan(self, data: bytes) -> ScanResult: ...


class ClamdScanner:
    """Speaks clamd's INSTREAM protocol over TCP; no third-party client library."""

    def __init__(self, host: str, port: int, timeout: float = 30.0):
        self.host, self.port, self.timeout = host, port, timeout

    def scan(self, data: bytes) -> ScanResult:
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                sock.sendall(b'zINSTREAM\0')
                for i in range(0, len(data), 64 * 1024):
                    chunk = data[i:i + 64 * 1024]
                    sock.sendall(struct.pack('!L', len(chunk)) + chunk)
                sock.sendall(struct.pack('!L', 0))
                reply = b''
                while not reply.endswith(b'\0'):
                    part = sock.recv(4096)
                    if not part:
                        break
                    reply += part
        except OSError:
            return ScanResult('unavailable', 'scanner_unreachable')
        text = reply.rstrip(b'\0').decode(errors='replace')
        if text.endswith('OK'):
            return ScanResult('clean')
        if text.endswith('FOUND'):
            return ScanResult('infected', text.split(':', 1)[-1].removesuffix('FOUND').strip()[:120])
        return ScanResult('unavailable', 'scanner_error')


class NoScanner:
    def scan(self, data: bytes) -> ScanResult:
        return ScanResult('unavailable', 'scanner_not_configured')


# --- file validation -------------------------------------------------------------------------------

_MAGIC = {'application/pdf': (b'%PDF-',), 'image/jpeg': (b'\xff\xd8\xff',),
          'image/png': (b'\x89PNG\r\n\x1a\n',)}
# Active PDF content has no place in an identity document and is a common malware carrier. Short names
# such as /JS or /AA are left to ClamAV: they collide with random bytes in compressed streams.
_PDF_ACTIVE = (b'/JavaScript', b'/Launch', b'/EmbeddedFile', b'/OpenAction')


def sniff_media_type(data: bytes) -> str | None:
    for media_type, prefixes in _MAGIC.items():
        if any(data.startswith(p) for p in prefixes):
            return media_type
    return None


def validate_upload(data: bytes, declared_type: str | None, max_bytes: int) -> tuple[str | None, str | None]:
    """Returns (media_type, rejection_reason). Exactly one is None."""
    if not data:
        return None, 'EMPTY_FILE'
    if len(data) > max_bytes:
        return None, 'FILE_TOO_LARGE'
    sniffed = sniff_media_type(data)
    if sniffed is None:
        return None, 'UNSUPPORTED_FILE_TYPE'
    if declared_type and declared_type.split(';')[0].strip().lower() != sniffed:
        return None, 'CONTENT_TYPE_MISMATCH'
    if sniffed == 'application/pdf' and any(marker in data for marker in _PDF_ACTIVE):
        return None, 'PDF_ACTIVE_CONTENT'
    return sniffed, None
