"""Multi-process concurrency and crash tests (spec §8.4, §12.3, §19.1; INV-01, INV-02; AT-004, AT-005, AT-019, AT-020).

Everything here crosses real OS process boundaries: a multi-worker uvicorn server, separate client processes
racing each other, several `hela.worker` processes sharing one queue, and processes killed with SIGKILL.
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import httpx
import pytest
from conftest import APP_URL, REDIS_URL, bearer, drain, tomorrow_at

API_DIR = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.multiprocess


def _env(**extra) -> dict:
    return {**os.environ, 'HELA_ENV': 'test', 'HELA_DATABASE_URL': APP_URL, 'HELA_REDIS_URL': REDIS_URL,
            'HELA_S3_BUCKET': 'hela-evidence-test', 'HELA_CLAMD_HOST': '', 'PYTHONUNBUFFERED': '1', **extra}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture
def live_server(world):
    """Three uvicorn worker processes on the test database."""
    port = _free_port()
    proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'hela.asgi:app', '--host', '127.0.0.1', '--port', str(port),
                             '--workers', '3', '--log-level', 'warning'], cwd=API_DIR, env=_env(),
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    base = f'http://127.0.0.1:{port}'
    for _ in range(100):
        try:
            if httpx.get(f'{base}/health', timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    else:
        proc.kill()
        raise RuntimeError(proc.stderr.read().decode()[-2000:])
    yield base
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=15)


def _accept_at(args) -> int:
    """Runs in a separate client process; all processes fire at the same wall-clock instant."""
    base, token, offer_id, fire_at = args
    with httpx.Client(base_url=base, timeout=30) as c:
        while time.time() < fire_at:
            time.sleep(0.001)
        return c.post(f'/v1/offers/{offer_id}/accept', headers={'Authorization': f'Bearer {token}',
                                                               'Idempotency-Key': f'mp-{uuid.uuid4().hex}'}).status_code


def _race(base, pairs) -> list[int]:
    fire_at = time.time() + 1.5
    with ProcessPoolExecutor(max_workers=len(pairs)) as pool:
        return sorted(pool.map(_accept_at, [(base, t['access_token'], o, fire_at) for t, o in pairs]))


def test_competing_providers_across_processes(world, live_server, app, db):
    app.state.services.settings.parallel_offers = 3
    providers = [world.provider(f'+9477100000{i}', spot=spot) for i, spot in enumerate(('near', 'mid', 'far'), 1)]
    for round_no, hour in enumerate((8, 11, 14, 17)):
        c_tok, addr = world.customer(f'+9477000010{round_no}')
        world.request(c_tok, addr, start=tomorrow_at(hour))
        drain(app)
        pairs = []
        for tokens, _ in providers:
            offers = world.offers(tokens)
            assert len(offers) == 1, offers
            pairs.append((tokens, offers[0]['id']))
        assert _race(live_server, pairs) == [200, 409, 409], f'round {round_no}'
    assert db('SELECT count(*) AS n FROM bookings')[0]['n'] == 4
    assert db('SELECT count(*) AS n FROM booking_assignments WHERE active')[0]['n'] == 4


def test_one_provider_overlapping_offers_across_processes(world, live_server, app, db):
    p_tok, pid = world.provider('+94771000001')
    for i in range(4):
        c_tok, addr = world.customer(f'+9477000020{i}')
        world.request(c_tok, addr, start=tomorrow_at(10, 30 * (i % 2)))   # all overlap 10:00-12:30
    drain(app)
    offers = world.offers(p_tok)
    assert len(offers) == 4
    assert _race(live_server, [(p_tok, o['id']) for o in offers]) == [200, 409, 409, 409]
    rows = db('SELECT count(*) AS n FROM booking_assignments WHERE provider_id = :p AND active', p=pid)
    assert rows[0]['n'] == 1


def _queue_notifications(app, user_id: str, n: int) -> None:
    from hela.core.db import tx
    from hela.notifications.outbox import notify
    with tx(app.state.services.engine) as con:
        for i in range(n):
            notify(con, recipient_type='user', recipient_id=user_id, template_key='booking.updated',
                   dedupe_key=f'mp-test:{i}')


def _wait(db, sql, predicate, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        value = db(sql)[0]
        if predicate(value):
            return value
        time.sleep(0.3)
    raise AssertionError(f'timed out: {sql} -> {value}')


_SPAWNED: list[subprocess.Popen] = []


@pytest.fixture(autouse=True)
def _no_leaked_workers():
    """A worker left running after a failed test would keep claiming jobs in the next tests' database."""
    yield
    while _SPAWNED:
        p = _SPAWNED.pop()
        if p.poll() is None:
            p.kill()
            p.wait(timeout=10)


def _workers(n):
    procs = [subprocess.Popen([sys.executable, '-m', 'hela.worker'], cwd=API_DIR, env=_env(),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _ in range(n)]
    _SPAWNED.extend(procs)
    return procs


def test_several_workers_deliver_each_notification_exactly_once(world, app, db, client):
    tokens = world.login(phone='+94770000301')
    uid = client.get('/v1/me', headers=bearer(tokens)).json()['id']
    db('DELETE FROM dev_sms_inbox')
    _queue_notifications(app, uid, 150)
    procs = _workers(3)
    try:
        _wait(db, "SELECT count(*) AS n FROM jobs WHERE kind = 'notification.deliver' AND status <> 'done'",
              lambda r: r['n'] == 0)
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait(timeout=15)
    assert db('SELECT count(*) AS n FROM dev_sms_inbox')[0]['n'] == 150
    stats = db("SELECT count(*) AS n, max(attempts) AS max_attempts, count(DISTINCT status) AS states FROM notifications")[0]
    assert stats == {'n': 150, 'max_attempts': 1, 'states': 1}
    assert db("SELECT count(DISTINCT locked_by) AS w FROM jobs WHERE kind = 'notification.deliver'")[0]['w'] >= 2


def test_worker_killed_mid_run_loses_and_duplicates_nothing(world, app, db, client):
    tokens = world.login(phone='+94770000302')
    uid = client.get('/v1/me', headers=bearer(tokens)).json()['id']
    db('DELETE FROM dev_sms_inbox')
    _queue_notifications(app, uid, 400)
    victim = _workers(1)[0]
    _wait(db, 'SELECT count(*) AS n FROM dev_sms_inbox', lambda r: r['n'] >= 20)
    victim.send_signal(signal.SIGKILL)                       # no cleanup, mid-batch, possibly mid-transaction
    victim.wait(timeout=10)
    partial = db('SELECT count(*) AS n FROM dev_sms_inbox')[0]['n']
    assert 20 <= partial < 400
    stranded = db("SELECT count(*) AS n FROM jobs WHERE status = 'running'")[0]['n']
    db("UPDATE jobs SET locked_until = now() - interval '1 second' WHERE status = 'running'")   # lease expiry, sped up
    survivor = _workers(1)[0]
    try:
        _wait(db, "SELECT count(*) AS n FROM jobs WHERE kind = 'notification.deliver' AND status <> 'done'",
              lambda r: r['n'] == 0)
    finally:
        survivor.terminate()
        survivor.wait(timeout=15)
    assert db('SELECT count(*) AS n FROM dev_sms_inbox')[0]['n'] == 400        # nothing lost, nothing sent twice
    assert db("SELECT count(*) AS n FROM notifications WHERE status = 'sent' AND attempts = 1")[0]['n'] == 400
    print(f'killed after {partial} sends; {stranded} leased jobs recovered')


def _hold_request_lock(request_id, ready_path):
    import psycopg
    con = psycopg.connect(APP_URL.replace('postgresql+psycopg', 'postgresql'))
    con.execute('BEGIN')
    con.execute('SELECT id FROM service_requests WHERE id = %s FOR UPDATE', (request_id,))
    Path(ready_path).write_text('locked')
    time.sleep(120)


def test_crashed_lock_holder_does_not_block_or_corrupt_acceptance(world, live_server, app, db, tmp_path):
    import multiprocessing
    import threading
    c_tok, addr = world.customer()
    p_tok, _ = world.provider('+94771000001')
    rid = world.request(c_tok, addr)
    drain(app)
    offer = world.offers(p_tok)[0]
    ready = tmp_path / 'ready'
    holder = multiprocessing.get_context('spawn').Process(target=_hold_request_lock, args=(rid, str(ready)))
    holder.start()
    for _ in range(100):
        if ready.exists():
            break
        time.sleep(0.1)
    result = {}

    def accept():
        result['code'] = httpx.post(f"{live_server}/v1/offers/{offer['id']}/accept", timeout=60,
                                    headers={**bearer(p_tok), 'Idempotency-Key': 'mp-lock-0001'}).status_code
    t = threading.Thread(target=accept)
    t.start()
    try:
        time.sleep(1.0)
        assert 'code' not in result                     # blocked behind the row lock, as designed
    finally:
        os.kill(holder.pid, signal.SIGKILL)             # the holder dies without committing or rolling back
        holder.join(timeout=10)                         # (also on failure: a live holder would block later tests)
    t.join(timeout=30)
    assert result.get('code') == 200
    assert db('SELECT count(*) AS n FROM bookings')[0]['n'] == 1


def _audit_writer(n):
    """Separate process: many small business-like transactions that each write audit rows."""
    from sqlalchemy import create_engine as ce

    from hela.audit import service as audit
    engine = ce(APP_URL)
    for i in range(n):
        with engine.begin() as con:
            audit.record(con, actor_type='system', actor_id=f'mp-{os.getpid()}', action='mp.audit', entity_type='t',
                         entity_id=str(i))
    engine.dispose()
    return n


def test_concurrent_audit_writers_seal_into_one_intact_chain(world, app, db):
    """Regression for the load-test finding: concurrent writers used to produce a chain that failed verification."""
    from hela.audit import service as audit
    from hela.core.db import tx
    with ProcessPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(_audit_writer, [100] * 4)) == 400
    with tx(app.state.services.engine) as con:
        audit.seal(con)
    assert db('SELECT audit_verify_chain() AS bad')[0]['bad'] is None
    assert db("SELECT count(*) AS n FROM audit_chain c JOIN audit_logs a ON a.id = c.audit_id WHERE a.action = 'mp.audit'")[0]['n'] == 400
