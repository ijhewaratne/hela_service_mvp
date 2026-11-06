"""Locust load test (NFR-02, NFR-03, NFR-11, Q71). Synthetic starting target: 5 new booking requests/min, with a
10x burst of 50/min. These are not production capacity promises.

    HELA_LOAD_TOKENS=tokens.json HELA_LOAD_BOOKINGS_PER_MIN=50 \
      locust -f tests/load/locustfile.py --headless -u 40 -r 10 -t 3m --host http://127.0.0.1:8100 --csv out

Custom metric `matching_decision` = request creation -> first offer issued (status `offering`) or an honest
`unfulfilled`, as seen by the customer (NFR-03 target p95 < 3 s).
"""
from __future__ import annotations

import json
import os
import random
import time
import uuid
from datetime import datetime, timedelta
from itertools import cycle
from zoneinfo import ZoneInfo

from locust import HttpUser, between, constant_pacing, events, task

TOKENS = json.loads(open(os.environ['HELA_LOAD_TOKENS']).read())
BOOKINGS_PER_MIN = float(os.environ.get('HELA_LOAD_BOOKINGS_PER_MIN', '5'))
CUSTOMER_USERS = int(os.environ.get('HELA_LOAD_CUSTOMER_USERS', '10'))
_customers = cycle(TOKENS['customers'])
_providers = cycle(TOKENS['providers'])
ZONE = ZoneInfo('Asia/Colombo')
TASKS = ['sweeping_mopping', 'dusting', 'bathroom_cleaning', 'kitchen_surfaces', 'tidying']


def _start() -> str:
    day = datetime.now(ZONE).date() + timedelta(days=random.randint(1, 28))
    return datetime(day.year, day.month, day.day, random.randint(7, 16), random.choice((0, 30)), tzinfo=ZONE).isoformat()


class Customer(HttpUser):
    weight = 1
    fixed_count = CUSTOMER_USERS
    wait_time = constant_pacing(60.0 * CUSTOMER_USERS / BOOKINGS_PER_MIN)   # aggregate = BOOKINGS_PER_MIN

    def on_start(self):
        me = next(_customers)
        self.h = {'Authorization': f"Bearer {me['token']}"}
        self.address_id = me['address_id']

    @task
    def book(self):
        self.client.get('/v1/categories', name='GET /v1/categories')
        q = self.client.post('/v1/quotes/preview', headers=self.h, name='POST /v1/quotes/preview', json={
            'category_code': 'household_cleaning', 'address_id': self.address_id, 'start_at': _start(),
            'duration_minutes': random.choice((60, 90, 120, 180))})
        if q.status_code != 201:
            return
        q = q.json()
        self.client.post(f"/v1/quotes/{q['quote_id']}/accept", headers=self.h, name='POST /v1/quotes/{id}/accept',
                         json={'policy_version': q['policy_version']})
        t0 = time.perf_counter()
        r = self.client.post('/v1/requests', name='POST /v1/requests',
                             headers={**self.h, 'Idempotency-Key': f'load-{uuid.uuid4().hex}'},
                             json={'quote_id': q['quote_id'], 'task_codes': random.sample(TASKS, 2)})
        if r.status_code != 201:
            return
        rid = r.json()['request_id']
        for _ in range(60):
            s = self.client.get(f'/v1/requests/{rid}', headers=self.h, name='GET /v1/requests/{id}').json()
            if s['status'] not in ('requested',):
                events.request.fire(request_type='METRIC', name='matching_decision',
                                    response_time=(time.perf_counter() - t0) * 1000, response_length=0,
                                    exception=None if s['status'] in ('offering', 'accepted', 'unfulfilled') else
                                    RuntimeError(s['status']), context={})
                return
            time.sleep(0.25)
        events.request.fire(request_type='METRIC', name='matching_decision', response_time=15000, response_length=0,
                            exception=TimeoutError('no decision in 15 s'), context={})


class Provider(HttpUser):
    weight = 3
    wait_time = between(2, 4)

    def on_start(self):
        self.h = {'Authorization': f'Bearer {next(_providers)}'}

    @task(5)
    def poll_offers(self):
        offers = self.client.get('/v1/offers/me', headers=self.h, name='GET /v1/offers/me').json()
        for o in offers[:1]:
            with self.client.post(f"/v1/offers/{o['id']}/accept", name='POST /v1/offers/{id}/accept', catch_response=True,
                                  headers={**self.h, 'Idempotency-Key': f'load-{uuid.uuid4().hex}'}) as r:
                if r.status_code in (200, 409):     # a 409 is a normal lost race or an expired offer, not an error
                    r.success()

    @task(1)
    def profile(self):
        self.client.get('/v1/me', headers=self.h, name='GET /v1/me')
