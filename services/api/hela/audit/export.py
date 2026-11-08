"""Export the sealed audit chain to write-once storage, and verify exports offline (Q41, NFR-12).

The in-database chain (migration 0005) shows tampering by the runtime role, but someone with owner access to the
database could rewrite every row and recompute every hash. Exporting each sealed row to storage that refuses
overwrites and deletes (S3 Object Lock, or a local directory for development) closes that gap: a later rewrite no
longer matches what was exported, and `verify-audit-export --against-db` reports it.

Layout in the target, one pair per export run that had new rows:

    segments/00000001.jsonl     one sealed row per line, exactly the fields the chain hash covers
    manifests/00000001.json     counts, first/last hashes, the segment's SHA-256 and the previous manifest's SHA-256

The manifest is written after its segment, so a manifest only exists for a complete segment. Manifests chain to
each other, so a removed or replaced segment is detected even without the database. Rows contain pseudonymous ids
and scrubbed details only (see `audit.service`), never phone numbers, codes or tokens.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from botocore.exceptions import ClientError
from sqlalchemy import Engine

from ..core.db import many, tx
from . import service as audit

FORMAT = 'hela-audit-export/1'
GENESIS = 'GENESIS'

# The exact fields and formatting of audit_row_digest() in migration 0005. details is the jsonb text Postgres
# produced, kept verbatim so the hash can be recomputed without the database.
_ROWS_SQL = '''
SELECT c.position, c.audit_id, c.prev_hash, c.row_hash,
       to_char(c.sealed_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS sealed_at,
       to_char(a.occurred_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US') AS occurred_at,
       a.actor_type, a.actor_id, a.action, a.entity_type, a.entity_id, a.details::text AS details, a.correlation_id
FROM audit_chain c JOIN audit_logs a ON a.id = c.audit_id
WHERE c.position > :after ORDER BY c.position LIMIT :limit
'''
_DIGEST_FIELDS = ('occurred_at', 'actor_type', 'actor_id', 'action', 'entity_type', 'entity_id', 'details',
                  'correlation_id')


def row_digest(prev: str, row: dict) -> str:
    """Python twin of audit_row_digest(): concat_ws('|', ...) skips NULLs."""
    parts = [prev, str(row['audit_id'])] + [row[f] for f in _DIGEST_FIELDS]
    return hashlib.sha256('|'.join(p for p in parts if p is not None).encode()).hexdigest()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ExportSink(Protocol):
    def names(self, prefix: str) -> list[str]: ...
    def read(self, name: str) -> bytes: ...
    def write_once(self, name: str, data: bytes) -> None: ...   # refuses to replace different content
    def anomalies(self, prefix: str) -> list[str]: ...          # signs of deletion or overwriting in the store


class DirectorySink:
    """Local development target. Files are created exclusively and made read-only; this is not WORM storage."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def names(self, prefix: str) -> list[str]:
        d = self.root / prefix
        return sorted(f'{prefix}/{p.name}' for p in d.iterdir()) if d.is_dir() else []

    def read(self, name: str) -> bytes:
        return (self.root / name).read_bytes()

    def write_once(self, name: str, data: bytes) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ExportError(f'{name} already exists with different content') from None
            return
        with os.fdopen(fd, 'wb') as f:
            f.write(data)

    def anomalies(self, prefix: str) -> list[str]:
        return []          # a plain directory cannot show deletions; use an Object Lock bucket outside development


class S3LockedSink:
    """S3-compatible bucket with Object Lock: each object is kept, unchangeable, until its retention date."""

    def __init__(self, s3_client, bucket: str, *, retention_days: int, mode: str):
        if mode not in ('GOVERNANCE', 'COMPLIANCE'):
            raise ExportError('Object Lock mode must be GOVERNANCE or COMPLIANCE')
        self._s3, self.bucket, self.retention_days, self.mode = s3_client, bucket, retention_days, mode

    def ensure_bucket(self) -> None:
        try:
            self._s3.head_bucket(Bucket=self.bucket)
        except ClientError:
            self._s3.create_bucket(Bucket=self.bucket, ObjectLockEnabledForBucket=True)
        try:
            enabled = self._s3.get_object_lock_configuration(Bucket=self.bucket)[
                'ObjectLockConfiguration'].get('ObjectLockEnabled') == 'Enabled'
        except ClientError:
            enabled = False
        if not enabled:
            raise ExportError(f'Bucket {self.bucket} does not have Object Lock enabled; refusing to export to it')

    # A plain DELETE on a versioned (Object Lock) bucket adds a delete marker: the locked version survives but
    # disappears from ordinary listings, so a truncated export would look complete. Everything here therefore
    # works on object *versions*: the oldest version of a key is the one written by the export, and any delete
    # marker or later version is reported.

    def _index(self, prefix: str) -> tuple[dict[str, list[dict]], set[str]]:
        versions: dict[str, list[dict]] = {}
        markers: set[str] = set()
        for page in self._s3.get_paginator('list_object_versions').paginate(Bucket=self.bucket, Prefix=prefix):
            for v in page.get('Versions', []):
                versions.setdefault(v['Key'], []).append(v)
            markers |= {d['Key'] for d in page.get('DeleteMarkers', [])}
        return versions, markers

    def names(self, prefix: str) -> list[str]:
        return sorted(self._index(prefix + '/')[0])

    def _original(self, name: str) -> dict | None:
        versions = self._index(name)[0].get(name)
        return min(versions, key=lambda v: v['LastModified']) if versions else None

    def read(self, name: str) -> bytes:
        v = self._original(name)
        if v is None:
            raise FileNotFoundError(name)
        return self._s3.get_object(Bucket=self.bucket, Key=name, VersionId=v['VersionId'])['Body'].read()

    def anomalies(self, prefix: str) -> list[str]:
        versions, markers = self._index(prefix + '/')
        out = [f'{k}: deleted (a delete marker hides it; the locked original is still readable)' for k in sorted(markers)]
        out += [f'{k}: has {len(v)} versions (written more than once)' for k, v in sorted(versions.items()) if len(v) > 1]
        return out

    def write_once(self, name: str, data: bytes) -> None:
        if self._original(name) is not None:        # also catches a key hidden by a delete marker
            if self.read(name) != data:
                raise ExportError(f'{name} already exists with different content')
            return
        until = datetime.now(UTC) + timedelta(days=self.retention_days)
        try:
            self._s3.put_object(Bucket=self.bucket, Key=name, Body=data, IfNoneMatch='*',
                                ContentType='application/json', ChecksumAlgorithm='SHA256',
                                ObjectLockMode=self.mode, ObjectLockRetainUntilDate=until)
        except ClientError as exc:
            if exc.response.get('Error', {}).get('Code') not in ('PreconditionFailed', 'ConditionalRequestConflict'):
                raise
            if self.read(name) != data:
                raise ExportError(f'{name} already exists with different content') from None


class ExportError(RuntimeError):
    pass


@dataclass
class ExportResult:
    sequence: int | None          # manifest written by this run, or None when there was nothing new
    rows: int
    last_position: int


def _latest_manifest(sink: ExportSink) -> tuple[dict | None, bytes | None]:
    names = sink.names('manifests')
    if not names:
        return None, None
    raw = sink.read(names[-1])
    return json.loads(raw), raw


def export(engine: Engine, sink: ExportSink, *, max_rows: int = 50_000) -> ExportResult:
    """Seal pending rows, then write every sealed row after the last exported one as a new segment."""
    with tx(engine) as con:
        audit.seal(con)
    for prefix in ('manifests', 'segments'):
        if found := sink.anomalies(prefix):
            raise ExportError('The export store shows deletion or overwriting: ' + '; '.join(found[:3]))
    previous, previous_raw = _latest_manifest(sink)
    after = previous['last_position'] if previous else 0
    with tx(engine) as con:
        rows = many(con, _ROWS_SQL, after=after, limit=max_rows)
    if not rows:
        return ExportResult(None, 0, after)
    expected_prev = previous['last_row_hash'] if previous else GENESIS
    if rows[0]['prev_hash'] != expected_prev:
        # The database chain no longer continues from what was exported: do not paper over it with a new segment.
        raise ExportError(f'Chain does not continue from the last export (position {after}); '
                          'run verify-audit-export --against-db')
    sequence = (previous['sequence'] + 1) if previous else 1
    segment = ''.join(json.dumps(r, sort_keys=True, separators=(',', ':')) + '\n' for r in rows).encode()
    seg_name = f'segments/{sequence:08d}.jsonl'
    manifest = {
        'format': FORMAT, 'sequence': sequence, 'rows': len(rows),
        'first_position': rows[0]['position'], 'last_position': rows[-1]['position'],
        'first_prev_hash': rows[0]['prev_hash'], 'last_row_hash': rows[-1]['row_hash'],
        'segment': seg_name, 'segment_sha256': _sha(segment),
        'previous_manifest_sha256': _sha(previous_raw) if previous_raw else None,
        'exported_at': datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
    }
    sink.write_once(seg_name, segment)
    sink.write_once(f'manifests/{sequence:08d}.json', json.dumps(manifest, sort_keys=True, indent=1).encode())
    return ExportResult(sequence, len(rows), rows[-1]['position'])


@dataclass
class VerifyReport:
    manifests: int
    rows: int
    problems: list[str]

    @property
    def ok(self) -> bool:
        return not self.problems


def verify(sink: ExportSink, engine: Engine | None = None) -> VerifyReport:
    """Check an export on its own (hashes, links, manifest chain) and, with `engine`, against the live database."""
    problems: list[str] = [*sink.anomalies('manifests'), *sink.anomalies('segments')]
    prev_hash, prev_raw, prev_pos, total = GENESIS, None, 0, 0
    exported: dict[int, str] = {}
    names = sink.names('manifests')
    for i, name in enumerate(names, start=1):
        raw = sink.read(name)
        m = json.loads(raw)
        where = f'manifest {name}'
        if m.get('format') != FORMAT or m.get('sequence') != i or name != f'manifests/{i:08d}.json':
            problems.append(f'{where}: unexpected format or sequence (a manifest is missing or out of order)')
            break
        if m['previous_manifest_sha256'] != (_sha(prev_raw) if prev_raw else None):
            problems.append(f'{where}: does not chain to the previous manifest')
        if m['first_prev_hash'] != prev_hash:
            problems.append(f'{where}: segment does not continue the chain')
        try:
            segment = sink.read(m['segment'])
        except (OSError, ClientError):
            problems.append(f'{where}: segment {m["segment"]} is missing')
            break
        if _sha(segment) != m['segment_sha256']:
            problems.append(f'{where}: segment content does not match its recorded SHA-256')
        lines = segment.decode().splitlines()
        if len(lines) != m['rows']:
            problems.append(f'{where}: expected {m["rows"]} rows, found {len(lines)}')
        for line in lines:
            r = json.loads(line)
            if r['prev_hash'] != prev_hash or r['position'] <= prev_pos:
                problems.append(f'position {r["position"]}: broken link')
            elif row_digest(r['prev_hash'], r) != r['row_hash']:
                problems.append(f'position {r["position"]}: content does not match its hash')
            prev_hash, prev_pos = r['row_hash'], r['position']
            exported[r['position']] = r['row_hash']
            total += 1
        if prev_hash != m['last_row_hash'] or prev_pos != m['last_position']:
            problems.append(f'{where}: last row does not match the manifest')
        prev_raw = raw
    if engine is not None and exported:
        with tx(engine) as con:
            live = {r['position']: r['row_hash'] for r in many(
                con, 'SELECT position, row_hash FROM audit_chain WHERE position = ANY(:p)', p=list(exported))}
        missing = [p for p in exported if p not in live]
        changed = [p for p, h in exported.items() if p in live and live[p] != h]
        if missing:
            problems.append(f'database: {len(missing)} exported rows are missing from audit_chain '
                            f'(first at position {min(missing)})')
        if changed:
            problems.append(f'database: {len(changed)} rows differ from what was exported '
                            f'(first at position {min(changed)})')
    return VerifyReport(len(names), total, problems)
