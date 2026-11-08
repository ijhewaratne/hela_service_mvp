"""Audit export to write-once storage (Q41, NFR-12): incremental segments, offline verification, tamper detection."""
import json
import os
import uuid

import pytest
from sqlalchemy import text

from hela.audit import export
from hela.audit import service as audit
from hela.cli import _store
from hela.core.db import make_engine


def add_rows(owner_engine, n, start=0):
    with owner_engine.begin() as con:
        for i in range(start, start + n):
            # Non-ASCII details check that the offline hash matches Postgres' UTF-8 encoding of jsonb text.
            audit.record(con, actor_type='system', actor_id='test', action='test.event', entity_type='thing',
                         entity_id=str(i), details={'n': i, 'note': 'ශ්‍රී ලංකා / இலங்கை', 'nested': {'b': [1, 2]}})


@pytest.fixture
def engine(reset, settings):
    e = make_engine(settings.database_url)          # the runtime role can export: it only reads sealed rows
    yield e
    e.dispose()


def test_incremental_export_verifies_offline_and_against_db(engine, owner_engine, tmp_path):
    sink = export.DirectorySink(tmp_path)
    add_rows(owner_engine, 5)
    first = export.export(engine, sink)
    assert (first.sequence, first.rows) == (1, 5)
    assert export.export(engine, sink).sequence is None                  # nothing new: no empty segment
    add_rows(owner_engine, 3, start=5)
    assert export.export(engine, sink, max_rows=2).rows == 2              # large backlogs go in several segments
    assert export.export(engine, sink).rows == 1
    report = export.verify(sink, engine)
    assert report.ok, report.problems
    assert (report.manifests, report.rows) == (3, 8)
    m3 = json.loads((tmp_path / 'manifests/00000003.json').read_bytes())
    assert m3['first_prev_hash'] == json.loads((tmp_path / 'manifests/00000002.json').read_bytes())['last_row_hash']
    # Written read-only and never replaced.
    assert not os.access(tmp_path / 'segments/00000001.jsonl', os.W_OK)
    with pytest.raises(export.ExportError, match='different content'):
        sink.write_once('segments/00000001.jsonl', b'{}\n')
    line = json.loads((tmp_path / 'segments/00000001.jsonl').read_text().splitlines()[0])
    assert 'phone' not in json.dumps(line) and line['action'] == 'test.event'


def test_edited_or_removed_export_files_are_detected(engine, owner_engine, tmp_path):
    sink = export.DirectorySink(tmp_path)
    add_rows(owner_engine, 4)
    export.export(engine, sink, max_rows=2)
    export.export(engine, sink, max_rows=2)
    seg = tmp_path / 'segments/00000001.jsonl'
    original = seg.read_bytes()
    seg.chmod(0o644)
    seg.write_bytes(original.replace(b'"entity_id":"1"', b'"entity_id":"9"'))
    problems = export.verify(sink).problems
    assert any('does not match its recorded SHA-256' in p for p in problems)
    assert any('content does not match its hash' in p for p in problems)
    seg.write_bytes(original)
    assert export.verify(sink).ok
    (tmp_path / 'manifests/00000001.json').chmod(0o644)
    (tmp_path / 'manifests/00000001.json').unlink()
    assert any('missing or out of order' in p for p in export.verify(sink).problems)


def test_rewritten_database_chain_no_longer_matches_the_export(engine, owner_engine, tmp_path):
    sink = export.DirectorySink(tmp_path)
    add_rows(owner_engine, 3)
    export.export(engine, sink)
    # Someone with owner access edits a row and recomputes the whole chain so the in-database check passes.
    with owner_engine.begin() as con:
        con.execute(text('ALTER TABLE audit_logs DISABLE TRIGGER USER'))
        con.execute(text('ALTER TABLE audit_chain DISABLE TRIGGER USER'))
        con.execute(text("UPDATE audit_logs SET details = '{\"n\": 99}' WHERE entity_id = '1'"))
        con.execute(text('''
          DO $$ DECLARE c record; prev text := 'GENESIS'; h text; BEGIN
            FOR c IN SELECT * FROM audit_chain ORDER BY position LOOP
              h := audit_row_digest(prev, (SELECT a FROM audit_logs a WHERE a.id = c.audit_id));
              UPDATE audit_chain SET prev_hash = prev, row_hash = h WHERE position = c.position;
              prev := h;
            END LOOP; END $$'''))
        con.execute(text('ALTER TABLE audit_chain ENABLE TRIGGER USER'))
        con.execute(text('ALTER TABLE audit_logs ENABLE TRIGGER USER'))
        assert con.execute(text('SELECT audit_verify_chain()')).scalar() is None    # the database alone looks fine
    assert export.verify(sink).ok                                                  # the export itself is intact
    problems = export.verify(sink, engine).problems
    assert any('differ from what was exported' in p for p in problems)
    add_rows(owner_engine, 1, start=3)
    with pytest.raises(export.ExportError, match='does not continue'):
        export.export(engine, sink)                                                # never extends a forked chain


@pytest.fixture
def locked_bucket(settings):
    s3 = _store(settings)._s3
    sink = export.S3LockedSink(s3, f'hela-audit-test-{uuid.uuid4().hex[:8]}', retention_days=1, mode='GOVERNANCE')
    sink.ensure_bucket()
    yield s3, sink
    versions = s3.list_object_versions(Bucket=sink.bucket)
    for v in versions.get('Versions', []) + versions.get('DeleteMarkers', []):
        s3.delete_object(Bucket=sink.bucket, Key=v['Key'], VersionId=v['VersionId'], BypassGovernanceRetention=True)
    s3.delete_bucket(Bucket=sink.bucket)


def test_object_lock_bucket_keeps_originals_and_reports_deletions(engine, owner_engine, locked_bucket, settings):
    s3, sink = locked_bucket
    add_rows(owner_engine, 3)
    export.export(engine, sink)
    add_rows(owner_engine, 2, start=3)
    export.export(engine, sink)
    assert export.verify(sink, engine).ok
    lock = s3.get_object_retention(Bucket=sink.bucket, Key='manifests/00000002.json')['Retention']
    assert lock['Mode'] == 'GOVERNANCE'
    # Deleting the newest manifest only adds a delete marker; without version awareness the export would look
    # complete with one manifest fewer.
    s3.delete_object(Bucket=sink.bucket, Key='manifests/00000002.json')
    original = s3.list_object_versions(Bucket=sink.bucket, Prefix='manifests/00000002.json')['Versions'][0]
    from botocore.exceptions import ClientError
    with pytest.raises(ClientError):           # the locked version itself cannot be removed
        s3.delete_object(Bucket=sink.bucket, Key='manifests/00000002.json', VersionId=original['VersionId'])
    report = export.verify(sink, engine)
    assert report.rows == 5 and any('delete marker' in p for p in report.problems)
    with pytest.raises(export.ExportError, match='deletion or overwriting'):
        export.export(engine, sink)
    # A bucket without Object Lock is refused outright.
    plain = f'hela-audit-plain-{uuid.uuid4().hex[:8]}'
    s3.create_bucket(Bucket=plain)
    try:
        with pytest.raises(export.ExportError, match='Object Lock'):
            export.S3LockedSink(s3, plain, retention_days=1, mode='GOVERNANCE').ensure_bucket()
    finally:
        s3.delete_bucket(Bucket=plain)


def test_export_cli_records_heartbeat(reset, settings, tmp_path, monkeypatch, db):
    from hela import cli
    monkeypatch.setattr(cli, 'get_settings', lambda: settings)
    insert = ("INSERT INTO audit_logs(actor_type, actor_id, action, entity_type, entity_id, details) "
              "VALUES ('system', 't', 'test.event', 'thing', 'x', '{}')")
    db(insert)
    assert cli.main(['export-audit', '--dir', str(tmp_path)]) == 0
    assert cli.main(['verify-audit-export', '--dir', str(tmp_path), '--against-db']) == 0
    hb = db("SELECT last_success_at, last_failure_at FROM ops_heartbeats WHERE name = 'audit_export'")[0]
    assert hb['last_success_at'] is not None and hb['last_failure_at'] is None
    (tmp_path / 'segments').chmod(0o500)       # unwritable target: the run fails and says so
    try:
        db(insert)
        assert cli.main(['export-audit', '--dir', str(tmp_path)]) == 1
    finally:
        (tmp_path / 'segments').chmod(0o755)
    assert db("SELECT last_failure_at FROM ops_heartbeats WHERE name = 'audit_export'")[0]['last_failure_at']
