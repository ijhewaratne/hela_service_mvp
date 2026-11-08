"""Background worker: python -m hela.worker  (run one or more; jobs are claimed with SKIP LOCKED)."""
from __future__ import annotations

import logging
import signal
import time

import psycopg

from .app import create_app
from .audit import service as audit
from .booking.jobs import handlers
from .core.config import get_settings
from .core.db import tx
from .core.jobs import CHANNEL, run_due
from .core.logs import configure_logging
from .ops.metrics import record_heartbeat

log = logging.getLogger('hela.worker')
HEARTBEAT_SECONDS = 15


def _listen(url: str):
    try:
        con = psycopg.connect(url.replace('postgresql+psycopg://', 'postgresql://'), autocommit=True)
        con.execute(f'LISTEN {CHANNEL}')
        return con
    except psycopg.Error:
        log.warning('LISTEN unavailable; falling back to polling')
        return None


def _wait_for_work(listener, timeout: float) -> None:
    if listener is None:
        time.sleep(timeout)
        return
    try:
        for _ in listener.notifies(timeout=timeout, stop_after=1):
            pass
    except psycopg.Error:
        time.sleep(timeout)


def main() -> int:
    settings = get_settings()
    configure_logging(settings, 'worker')
    svc = create_app(settings).state.services
    table = handlers(svc)
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    listener = _listen(svc.settings.database_url)
    log.info('worker started (%s handlers)', len(table))
    last_seal = last_beat = 0.0
    while not stopping:
        try:
            if time.monotonic() - last_seal >= 1.0:
                with tx(svc.engine) as con:
                    audit.seal(con)          # one sealer at a time; others skip via a try-lock
                last_seal = time.monotonic()
            done = run_due(svc.engine, table)
            if time.monotonic() - last_beat >= HEARTBEAT_SECONDS:
                with tx(svc.engine) as con:
                    record_heartbeat(con, 'worker', ok=True)   # alert fires if this stops (worker down or stuck)
                last_beat = time.monotonic()
        except Exception:   # noqa: BLE001 - keep the loop alive across transient database errors
            log.exception('worker loop error')
            done = 0
        if not done:
            _wait_for_work(listener, timeout=1.0)   # scheduled jobs are still picked up within a second
    log.info('worker stopped')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
