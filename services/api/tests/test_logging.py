"""Structured, redacted logs for central shipping (Q64, NFR-12)."""
import json
import logging

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError

from hela.core.db import make_engine
from hela.core.logs import configure_logging, redact, safe_error

PHONE = '+94770000123'


@pytest.mark.parametrize('raw, expected', [
    (f'sms to {PHONE} failed', 'sms to [phone] failed'),
    ('local 0771234567 and 94771234567', 'local [phone] and [phone]'),
    ('Authorization: Bearer abc.def-ghi_123456', 'Authorization: Bearer [token]'),
    ('tok eyJhbGciOiJFZERTQSJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl end', 'tok [jwt] end'),
    ('Hela Service code: 123456. Valid 5 minutes', 'Hela Service code: [code]. Valid 5 minutes'),
    ('pin=4821 otp 998877', 'pin=[code] otp [code]'),
    ('mail ops@example.org', 'mail [email]'),
    # Left alone: ids, status codes, timings and counts.
    ('booking 0d8f6a52-3c1e-4c55-9d3e-6f1b2a7c9e10 status_code=200 run_ms=1234 rows=120',
     'booking 0d8f6a52-3c1e-4c55-9d3e-6f1b2a7c9e10 status_code=200 run_ms=1234 rows=120'),
])
def test_redaction(raw, expected):
    assert redact(raw) == expected


@pytest.fixture
def json_logs(settings, capsys):
    configure_logging(settings.model_copy(update={'log_format': 'json'}), 'api')
    yield lambda: [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith('{')]
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if h.get_name() != 'hela']


def test_json_lines_carry_correlation_and_are_redacted(json_logs):
    log = logging.getLogger('hela.test')
    log.info('sending to %s', PHONE, extra={'kind': 'notify', 'attempt': 2})
    try:
        raise ValueError(f'bad number {PHONE}')
    except ValueError:
        log.exception('failed')
    first, second = json_logs()
    assert first['message'] == 'sending to [phone]' and first['service'] == 'api'
    assert first['kind'] == 'notify' and first['attempt'] == 2 and first['level'] == 'info'
    assert first['correlation_id'] == '-' and first['ts'].endswith('Z')
    assert 'ValueError: bad number [phone]' in second['exception'] and PHONE not in json.dumps(second)


def test_real_flows_log_no_personal_data(json_logs, client, login, db):
    tokens = login(PHONE)
    client.get('/v1/me', headers={'Authorization': f"Bearer {tokens['access_token']}"})
    client.post('/v1/auth/otp/verify', json={'phone': PHONE, 'challenge_id': '0d8f6a52-3c1e-4c55-9d3e-6f1b2a7c9e10',
                                             'code': '000000', 'device_label': 'x'})
    lines = json_logs()
    blob = json.dumps(lines)
    for secret in (PHONE, PHONE[1:], tokens['access_token'], tokens['refresh_token']):
        assert secret not in blob
    assert all({'ts', 'level', 'logger', 'service', 'correlation_id', 'message'} <= set(line) for line in lines)
    # The access log names the route template and the response's correlation id, never the client IP.
    me = [x for x in lines if x['logger'] == 'hela.access' and x['route'] == '/v1/me']
    assert me and me[0]['status'] == 200 and me[0]['correlation_id'].startswith('c_')
    assert 'testclient' not in json.dumps(me) and 'request_correlation_id' not in me[0]


def test_database_errors_do_not_carry_bound_values(settings):
    engine = make_engine(settings.database_url)
    try:
        with pytest.raises(DataError) as err, engine.connect() as con:
            con.execute(text('SELECT CAST(:p AS int)'), {'p': PHONE})
        assert 'hidden due to hide_parameters' in str(err.value)
        # The server itself repeats the value in its message, so stored and logged errors keep only the SQLSTATE.
        assert PHONE in str(err.value) and safe_error(err.value) == 'DataError (sqlstate 22003)'
    finally:
        engine.dispose()


def test_logged_database_exceptions_drop_the_server_message(json_logs, settings):
    engine = make_engine(settings.database_url)
    name = 'Kamala Perera'          # in a variable: tracebacks print source lines
    try:
        with engine.connect() as con:
            con.execute(text('SELECT CAST(:p AS uuid)'), {'p': name})
    except DataError:
        logging.getLogger('hela').exception('unhandled error')
    finally:
        engine.dispose()
    (line,) = json_logs()
    assert 'Kamala' not in json.dumps(line) and line['exception'].endswith('DataError (sqlstate 22P02)')


def test_failed_job_stores_only_a_safe_error(app, db):
    from hela.core.jobs import enqueue, run_due

    def boom(con, payload):
        con.execute(text('SELECT CAST(:p AS uuid)'), {'p': payload['name']})

    with app.state.services.engine.begin() as con:
        enqueue(con, 'test.boom', {'name': 'Kamala Perera'})
    run_due(app.state.services.engine, {'test.boom': boom})
    (job,) = db("SELECT last_error FROM jobs WHERE kind = 'test.boom'")
    assert job['last_error'] == 'DataError (sqlstate 22P02)'
