"""Monitoring metrics (§17): token-protected scrape endpoint, state gauges and no personal data in the output."""
import re

import pytest
from conftest import bearer, drain
from fastapi.testclient import TestClient

from hela.app import create_app
from hela.core.db import tx
from hela.ops.metrics import record_heartbeat

TOKEN = 'metrics-token-for-tests-0123456789'


@pytest.fixture
def scrape(reset, settings):
    app = create_app(settings.model_copy(update={'metrics_token': TOKEN}))
    with TestClient(app) as c:
        def do():
            r = c.get('/internal/metrics', headers={'Authorization': f'Bearer {TOKEN}'})
            assert r.status_code == 200, r.text
            return r.text
        do.client, do.app = c, app
        yield do


def value(text, metric, /, default=None, **labels):
    want = ','.join(f'{k}="{v}"' for k, v in labels.items())
    pattern = rf'^{re.escape(metric)}\{{{re.escape(want)}\}} (\S+)$' if labels else rf'^{re.escape(metric)} (\S+)$'
    for line in text.splitlines():
        m = re.match(pattern, line)
        if m:
            return float(m.groups()[-1])
    if default is not None:
        return default
    raise AssertionError(f'{metric}{labels} not in metrics output')


def test_metrics_endpoint_needs_its_own_token(client, scrape, login):
    assert client.get('/internal/metrics').status_code == 404           # not mounted unless a token is configured
    c = scrape.client
    assert c.get('/internal/metrics').status_code == 401
    assert c.get('/internal/metrics', headers={'Authorization': 'Bearer wrong-token-wrong-token-xx'}).status_code == 401
    user = login()
    assert c.get('/internal/metrics', headers=bearer(user)).status_code == 401   # a user token is not a metrics token
    assert value(scrape(), 'hela_db_up') == 1


def test_state_metrics_for_stuck_work_and_heartbeats(scrape, db):
    text = scrape()
    assert value(text, 'hela_jobs_oldest_overdue_seconds') == 0 and value(text, 'hela_jobs', status='dead') == 0
    db('''INSERT INTO jobs(kind, payload, run_at, status) VALUES ('noop', '{}', now() - interval '10 minutes', 'queued'),
                                                               ('noop', '{}', now(), 'dead')''')
    with tx(scrape.app.state.services.engine) as con:
        record_heartbeat(con, 'worker', ok=True)
        record_heartbeat(con, 'backup', ok=True)
        record_heartbeat(con, 'backup', ok=False, detail='restore check failed')
    text = scrape()
    assert value(text, 'hela_jobs_oldest_overdue_seconds') >= 590
    assert value(text, 'hela_jobs', status='dead') == 1
    assert value(text, 'hela_heartbeat_failing', name='backup') == 1
    assert value(text, 'hela_heartbeat_failing', name='worker') == 0
    assert value(text, 'hela_heartbeat_age_seconds', name='worker') < 60


RACE_CODES = ('OFFER_NOT_ACTIVE', 'REQUEST_NOT_OFFERING', 'BOOKING_ALREADY_ASSIGNED', 'ASSIGNMENT_CONFLICT')


def test_competing_acceptances_are_counted_by_error_code(world, scrape):
    world.app.state.services.settings.parallel_offers = 2
    c_tok, addr = world.customer()
    a_tok, _ = world.provider('+94771000001', spot='near')
    b_tok, _ = world.provider('+94771000002', spot='mid')
    world.request(c_tok, addr)
    drain(world.app)
    a_offer, b_offer = world.offers(a_tok)[0], world.offers(b_tok)[0]

    def losers(text):
        return sum(value(text, 'hela_api_errors_total', code=c, default=0) for c in RACE_CODES)
    before = losers(scrape())
    assert world.accept(a_tok, a_offer['id']).status_code == 200
    assert world.accept(b_tok, b_offer['id']).status_code == 409          # the second provider cannot also win
    text = scrape()
    assert losers(text) == before + 1
    assert value(text, 'hela_http_requests_total', method='POST', route='/v1/offers/{offer_id}/accept',
                 status_class='4xx') >= 1
    assert value(text, 'hela_requests_open', status='offering') == 0
    assert value(text, 'hela_evidence_access_1h_max_per_admin') == 0


def test_metrics_output_has_no_personal_data(world, scrape):
    c_tok, addr = world.customer(phone='+94770009876')
    world.request(c_tok, addr)
    text = scrape()
    for secret in ('0009876', '+9477', 'Synthetic Lane', 'Gate code'):
        assert secret not in text
    # Route labels are templates, never raw paths with ids.
    assert not re.search(r'route="[^"]*[0-9a-f]{8}-[0-9a-f]{4}', text)
