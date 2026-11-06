"""Background worker: python -m hela.worker  (run one or more; jobs are claimed with SKIP LOCKED)."""
from __future__ import annotations

import logging
import signal
import time

from .app import create_app
from .booking.jobs import handlers
from .core.jobs import run_due

log = logging.getLogger('hela.worker')


def main() -> int:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    svc = create_app().state.services
    table = handlers(svc)
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info('worker started (%s handlers)', len(table))
    while not stopping:
        try:
            done = run_due(svc.engine, table)
        except Exception:   # noqa: BLE001 - keep the loop alive across transient database errors
            log.exception('worker loop error')
            done = 0
        if not done:
            time.sleep(1.0)
    log.info('worker stopped')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
