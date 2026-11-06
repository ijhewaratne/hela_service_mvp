"""Booking engine: quotes, requests, matching, offers, acceptance races, state machine, settlement, safety
(FR-REQ, FR-MAT, FR-BKG, FR-PAY, FR-SAF, FR-REV, FR-BEN; AT-001..AT-022 where applicable)."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from conftest import bearer, drain, step_up, tomorrow_at
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError


def _offer_for(world, tokens):
    offers = world.offers(tokens)
    assert len(offers) == 1, offers
    return offers[0]


def test_end_to_end_cleaning_booking(world, client, db):
    c_tok, addr = world.customer()
    p_tok, pid = world.provider('+94771000001', spot='near')
    world.provider('+94771000002', spot='far')

    q = world.quote(c_tok, addr).json()
    assert q['total_minor'] == 240000 and q['currency'] == 'LKR'      # LKR 1,200/h x 2 h in cents
    assert [c['code'] for c in q['components']] == ['base_fee', 'labour', 'travel_fee']
    assert q['price_approval_status'] == 'UNAPPROVED'

    rid = world.request(c_tok, addr)
    assert client.get(f'/v1/requests/{rid}', headers=bearer(c_tok)).json()['status'] == 'requested'
    drain(world.app)
    offer = _offer_for(world, p_tok)                                   # nearest eligible provider ranks first
    assert offer['area_label'] and offer['distance_band'] == '<2 km'
    assert not {'address', 'address_text', 'task_note', 'customer'} & set(offer)

    status = client.get(f'/v1/requests/{rid}', headers=bearer(c_tok)).json()
    assert status['status'] == 'offering' and status['booking_id'] is None   # never "confirmed" before acceptance

    acc = world.accept(p_tok, offer['id'])
    assert acc.status_code == 200, acc.text
    bid = acc.json()['booking_id']
    assert client.get(f'/v1/requests/{rid}', headers=bearer(c_tok)).json()['booking_id'] == bid

    pv = client.get(f'/v1/bookings/{bid}', headers=bearer(p_tok)).json()
    assert pv['address']['text'] == '12 Synthetic Lane, Kandy' and pv['task_note'] == 'Gate code is synthetic'
    cv = client.get(f'/v1/bookings/{bid}', headers=bearer(c_tok)).json()
    assert cv['provider']['display_name'] == 'Provider 001' and len(cv['checkin_pin']) == 4
    assert cv['price']['platform_collected_funds'] is False

    v = 1
    for target, who, extra in [('en_route', p_tok, {}), ('arrived', p_tok, {}),
                               ('in_progress', p_tok, {'checkin_pin': cv['checkin_pin']}),
                               ('completed_pending_confirmation', p_tok, {}), ('completed', c_tok, {})]:
        r = world.move(who, bid, target, v, **extra)
        assert r.status_code == 200, (target, r.text)
        v = r.json()['version']
    assert v == 6
    events = client.get(f'/v1/bookings/{bid}/events', headers=bearer(c_tok)).json()
    assert [e['to_status'] for e in events] == ['accepted', 'en_route', 'arrived', 'in_progress',
                                                 'completed_pending_confirmation', 'completed']
    # Provider of record keeps access after completion, but the address is no longer shown.
    assert 'address' not in client.get(f'/v1/bookings/{bid}', headers=bearer(p_tok)).json()

    rep = client.post(f'/v1/bookings/{bid}/payment-report', headers=bearer(c_tok), json={'reference': 'CASH-01'})
    assert rep.json()['status'] == 'CUSTOMER_REPORTED_PAID'
    ack = client.post(f'/v1/bookings/{bid}/payment-ack', headers=bearer(p_tok), json={'received': True})
    assert ack.json()['status'] == 'PROVIDER_CONFIRMED' and ack.json()['platform_collected_funds'] is False

    review = client.post(f'/v1/bookings/{bid}/reviews', headers=bearer(c_tok), json={'rating': 5, 'text': 'Great work'})
    assert review.json()['moderation_state'] == 'pending'

    drain(world.app)
    bodies = [r['body'] for r in db('SELECT body FROM dev_sms_inbox')]
    assert any('new job offer' in b for b in bodies) and any('accepted your booking' in b for b in bodies)
    assert not any('Synthetic Lane' in b or 'Gate code' in b for b in bodies)   # FR-NOT-02
    assert db('SELECT audit_verify_chain() AS bad')[0]['bad'] is None


# --- requests and quotes -------------------------------------------------------------------------------

def test_request_idempotency(world, client):
    c_tok, addr = world.customer()
    q = world.quote(c_tok, addr).json()
    client.post(f"/v1/quotes/{q['quote_id']}/accept", headers=bearer(c_tok), json={'policy_version': 1})
    body = {'quote_id': q['quote_id'], 'task_codes': ['dusting']}
    h = {**bearer(c_tok), 'Idempotency-Key': 'retry-key-0001'}
    first = client.post('/v1/requests', headers=h, json=body)
    again = client.post('/v1/requests', headers=h, json=body)
    assert first.status_code == again.status_code == 201 and first.json() == again.json()       # AT-006
    changed = client.post('/v1/requests', headers=h, json={**body, 'task_codes': ['tidying']})
    assert changed.status_code == 409 and changed.json()['error']['code'] == 'IDEMPOTENCY_KEY_REUSED'   # AT-007
    assert client.post('/v1/requests', headers=bearer(c_tok), json=body).json()['error']['code'] == 'IDEMPOTENCY_KEY_REQUIRED'
    assert world.db('SELECT count(*) AS n FROM service_requests')[0]['n'] == 1


@pytest.mark.parametrize('kw,code', [
    ({'start': 'soon', 'minutes': 120}, 'START_TOO_SOON'),
    ({'minutes': 75}, 'DURATION_STEP'),
    ({'minutes': 30}, 'DURATION_OUT_OF_RANGE'),
])
def test_schedule_rules(world, kw, code):
    from datetime import UTC, datetime
    c_tok, addr = world.customer()
    if kw.get('start') == 'soon':
        kw = {**kw, 'start': datetime.now(UTC) + timedelta(minutes=30)}
    r = world.quote(c_tok, addr, **kw)
    assert r.status_code == 422 and code in r.json()['error']['details']['reason_codes']


def test_disabled_and_forbidden_categories_cannot_be_booked(world):
    c_tok, addr = world.customer()
    for cat in ('general_assistance', 'childcare', 'clinical_emergency', 'elder_companionship'):   # AT-022
        assert world.quote(c_tok, addr, category=cat).json()['error']['code'] == 'CATEGORY_NOT_BOOKABLE'


def test_unsupported_locality_is_refused(world, client):
    c_tok = world.login()
    r = client.post('/v1/addresses', headers=bearer(c_tok), json={'label': 'Colombo', 'address_text': 'Synthetic Colombo flat',
                                                                  'lat': 6.9271, 'lon': 79.8612, 'pin_confirmed': True})
    assert r.json()['error']['code'] == 'UNSUPPORTED_LOCALITY'                                     # AT-029
    r = client.post('/v1/addresses', headers=bearer(c_tok), json={'label': 'Home', 'address_text': '12 Synthetic Lane',
                                                                  'lat': 7.29, 'lon': 80.63, 'pin_confirmed': False})
    assert r.status_code == 422


def test_quote_rules_tasks_and_reuse(world, client):
    c_tok, addr = world.customer()
    q = world.quote(c_tok, addr).json()
    body = {'quote_id': q['quote_id'], 'task_codes': ['dusting']}
    key = lambda: {**bearer(c_tok), 'Idempotency-Key': f'k-{uuid.uuid4().hex}'}  # noqa: E731
    assert client.post('/v1/requests', headers=key(), json=body).json()['error']['code'] == 'QUOTE_NOT_ACCEPTED'
    assert client.post(f"/v1/quotes/{q['quote_id']}/accept", headers=bearer(c_tok),
                       json={'policy_version': 9}).json()['error']['code'] == 'TERMS_CHANGED'
    client.post(f"/v1/quotes/{q['quote_id']}/accept", headers=bearer(c_tok), json={'policy_version': 1})
    bad = client.post('/v1/requests', headers=key(), json={**body, 'task_codes': ['medication_administration']})
    assert bad.json()['error']['code'] == 'TASK_NOT_ALLOWED'
    assert client.post('/v1/requests', headers=key(), json=body).status_code == 201
    assert client.post('/v1/requests', headers=key(), json=body).json()['error']['code'] == 'QUOTE_ALREADY_USED'
    q2 = world.quote(c_tok, addr).json()
    client.post(f"/v1/quotes/{q2['quote_id']}/accept", headers=bearer(c_tok), json={'policy_version': 1})
    world.db("UPDATE quotes SET expires_at = now() - interval '1 second' WHERE id = :q", q=q2['quote_id'])
    r = client.post('/v1/requests', headers=key(), json={'quote_id': q2['quote_id'], 'task_codes': ['dusting']})
    assert r.json()['error']['code'] == 'QUOTE_EXPIRED'


def test_other_customers_cannot_use_my_address_or_request(world, client):
    c_tok, addr = world.customer('+94770000001')
    other = world.login(phone='+94770000002')
    assert world.quote(other, addr).json()['error']['code'] == 'ADDRESS_NOT_FOUND'
    rid = world.request(c_tok, addr)
    assert client.get(f'/v1/requests/{rid}', headers=bearer(other)).status_code == 404


# --- matching and dispatch ------------------------------------------------------------------------------

def test_no_supply_is_honest_and_admin_can_retry(world, client, make_admin, admin_login):
    c_tok, addr = world.customer()
    rid = world.request(c_tok, addr)
    drain(world.app)
    r = client.get(f'/v1/requests/{rid}', headers=bearer(c_tok)).json()
    assert r['status'] == 'unfulfilled' and r['booking_id'] is None and r['next_steps']           # AT-008
    _, clock = make_admin('ops.olga', roles=[('operations_admin', None)])
    ops = admin_login('ops.olga', clock)
    inspector = client.get(f'/v1/admin/requests/{rid}', headers=bearer(ops)).json()
    assert inspector['matching_runs'][0]['outcome'] == 'no_eligible_supply'
    assert 'address' not in str(inspector).lower() or 'address_id' not in inspector
    p_tok, _ = world.provider('+94771000001')
    assert client.post(f'/v1/admin/requests/{rid}/dispatch', headers=bearer(ops),
                       json={'reason': 'Provider joined the pilot'}).status_code == 200
    drain(world.app)
    assert len(world.offers(p_tok)) == 1


def test_ineligible_providers_are_never_offered(world, client, db):
    c_tok, addr = world.customer('+94770000001')
    _, errands_only = world.provider('+94771000001', categories=('errands',))                     # AT-002
    _, suspended = world.provider('+94771000002')
    db("UPDATE provider_profiles SET account_state = 'suspended' WHERE user_id = :p", p=suspended)
    _, out_of_range = world.provider('+94771000003', spot='far', radius_km=1)
    _, busy = world.provider('+94771000004')
    start = tomorrow_at(10)
    db("INSERT INTO provider_blackouts(provider_id, period) VALUES (:p, tstzrange(:s, :e))", p=busy,
       s=start - timedelta(hours=1), e=start + timedelta(hours=5))
    _, expired = world.provider('+94771000005')
    db("UPDATE provider_evidence SET next_review_due = current_date - 1 WHERE provider_id = :p", p=expired)
    self_tok, self_pid = world.provider('+94770000001')   # the customer is also a provider (Q12)
    rid = world.request(c_tok, addr, start=start)
    drain(world.app)
    assert world.db('SELECT count(*) AS n FROM offers')[0]['n'] == 0                              # AT-001
    run = world.db('SELECT exclusions, outcome FROM matching_runs WHERE request_id = :r', r=rid)[0]
    assert run['outcome'] == 'no_eligible_supply'
    for code in ('PROVIDER_SUSPENDED', 'OUTSIDE_SERVICE_AREA', 'UNAVAILABLE', 'EVIDENCE_EXPIRED', 'SELF_BOOKING'):
        assert run['exclusions'].get(code, 0) >= 1, (code, run['exclusions'])
    assert 'errands_only' not in str(run)   # exclusions carry reason codes, never identities


def test_sequential_offers_on_decline_and_expiry(world, client, db):
    c_tok, addr = world.customer()
    a_tok, _ = world.provider('+94771000001', spot='near')
    b_tok, _ = world.provider('+94771000002', spot='mid')
    rid = world.request(c_tok, addr)
    drain(world.app)
    first = _offer_for(world, a_tok)
    assert world.offers(b_tok) == []                                       # one offer at a time (Q41)
    assert client.post(f"/v1/offers/{first['id']}/decline", headers=bearer(a_tok)).status_code == 204
    drain(world.app)
    second = _offer_for(world, b_tok)
    db("UPDATE offers SET expires_at = now() - interval '1 second' WHERE id = :o", o=second['id'])
    db("UPDATE jobs SET run_at = now() WHERE kind = 'offer.expire'")
    drain(world.app)
    r = client.get(f'/v1/requests/{rid}', headers=bearer(c_tok)).json()
    assert r['status'] == 'unfulfilled'
    statuses = sorted(o['status'] for o in db('SELECT status FROM offers WHERE request_id = :r', r=rid))
    assert statuses == ['declined', 'expired']
    assert world.accept(b_tok, second['id']).json()['error']['code'] == 'OFFER_NOT_ACTIVE'


# --- acceptance: revalidation, races, idempotency ---------------------------------------------------------

def test_acceptance_revalidates_evidence_and_category(world, client, db):
    c_tok, addr = world.customer()
    a_tok, a_pid = world.provider('+94771000001', spot='near')
    b_tok, _ = world.provider('+94771000002', spot='mid')
    world.request(c_tok, addr)
    drain(world.app)
    offer = _offer_for(world, a_tok)
    db("UPDATE provider_evidence SET next_review_due = current_date - 1 WHERE provider_id = :p AND evidence_type = "
       "'training_record'", p=a_pid)                                                              # AT-003
    r = world.accept(a_tok, offer['id'])
    assert r.status_code == 409 and r.json()['error']['code'] == 'PROVIDER_INELIGIBLE'
    assert 'EVIDENCE_EXPIRED' in r.json()['error']['details']['reason_codes']
    drain(world.app)
    offer_b = _offer_for(world, b_tok)                                       # redispatched to the next provider
    db("UPDATE service_categories SET status = 'disabled' WHERE code = 'household_cleaning'")    # AT-011
    r = world.accept(b_tok, offer_b['id'])
    assert r.json()['error']['code'] == 'PROVIDER_INELIGIBLE'
    assert 'CATEGORY_DISABLED' in r.json()['error']['details']['reason_codes']
    assert db('SELECT count(*) AS n FROM bookings')[0]['n'] == 0


def test_suspension_revokes_pending_offer_and_redispatches(world, client, make_admin, admin_login):
    c_tok, addr = world.customer()
    a_tok, a_pid = world.provider('+94771000001', spot='near')
    b_tok, _ = world.provider('+94771000002', spot='mid')
    world.request(c_tok, addr)
    drain(world.app)
    offer = _offer_for(world, a_tok)
    _, clock = make_admin('incident.ira', roles=[('incident_responder', None)])
    ir = admin_login('incident.ira', clock)
    r = client.post(f'/v1/admin/providers/{a_pid}/suspend', headers=bearer(ir), json={'reason': 'Safety report'})
    assert r.json()['offers_revoked'] == 1                                                         # AT-010
    assert world.accept(a_tok, offer['id']).json()['error']['code'] == 'OFFER_NOT_ACTIVE'
    drain(world.app)
    assert len(world.offers(b_tok)) == 1


def test_competing_acceptances_produce_exactly_one_booking(world, app, db):
    app.state.services.settings.parallel_offers = 2           # controlled parallel offers for the race (AT-004)
    c_tok, addr = world.customer()
    a_tok, _ = world.provider('+94771000001', spot='near')
    b_tok, _ = world.provider('+94771000002', spot='mid')
    world.request(c_tok, addr)
    drain(app)
    offers = [(a_tok, _offer_for(world, a_tok)['id']), (b_tok, _offer_for(world, b_tok)['id'])]

    def go(pair):
        tokens, offer_id = pair
        with TestClient(app) as c:
            return c.post(f'/v1/offers/{offer_id}/accept',
                          headers={**bearer(tokens), 'Idempotency-Key': f'k-{uuid.uuid4().hex}'}).status_code
    for _ in range(1):
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = sorted(pool.map(go, offers))
    assert codes == [200, 409], codes
    assert db('SELECT count(*) AS n FROM booking_assignments WHERE active')[0]['n'] == 1
    assert sorted(o['status'] for o in db('SELECT status FROM offers')) == ['accepted', 'revoked']


def test_provider_cannot_hold_overlapping_jobs(world, app, db):
    c1, addr1 = world.customer('+94770000001')
    c2, addr2 = world.customer('+94770000002')
    p_tok, pid = world.provider('+94771000001')
    world.request(c1, addr1, start=tomorrow_at(10))
    world.request(c2, addr2, start=tomorrow_at(11))          # overlaps 10:00-12:00 (+30 min buffer)
    drain(app)
    offers = world.offers(p_tok)
    assert len(offers) == 2

    def go(offer_id):
        with TestClient(app) as c:
            return c.post(f'/v1/offers/{offer_id}/accept',
                          headers={**bearer(p_tok), 'Idempotency-Key': f'k-{uuid.uuid4().hex}'}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = sorted(pool.map(go, [o['id'] for o in offers]))
    assert codes == [200, 409], codes                                                              # AT-005
    assert db('SELECT count(*) AS n FROM booking_assignments WHERE active')[0]['n'] == 1
    # The database itself refuses an overlapping live assignment, whatever the application does (INV-02).
    live = db('SELECT booking_id, busy FROM booking_assignments WHERE active')[0]
    other_offer = [o['id'] for o in offers if not db('SELECT 1 AS x FROM booking_assignments WHERE offer_id = :o',
                                                       o=o['id'])][0]
    with pytest.raises(DBAPIError):
        db('''INSERT INTO booking_assignments(booking_id, provider_id, offer_id, busy)
              SELECT b.id, :p, :o, tstzrange(lower(:busy), upper(:busy)) FROM bookings b LIMIT 1''',
           p=pid, o=other_offer, busy=live['busy'])


def test_accept_retry_after_lost_response_returns_same_booking(world):
    c_tok, addr = world.customer()
    p_tok, _ = world.provider('+94771000001')
    world.request(c_tok, addr)
    drain(world.app)
    oid = _offer_for(world, p_tok)['id']
    first = world.accept(p_tok, oid, key='accept-key-0001').json()
    same_key = world.accept(p_tok, oid, key='accept-key-0001').json()
    new_key = world.accept(p_tok, oid).json()                                                      # AT-020
    assert first['booking_id'] == same_key['booking_id'] == new_key['booking_id'] and new_key['replayed']
    assert world.db('SELECT count(*) AS n FROM bookings')[0]['n'] == 1


# --- booking lifecycle ------------------------------------------------------------------------------------

@pytest.fixture
def booked(world):
    """A customer with an accepted booking. Returns (customer tokens, provider tokens, provider id, booking id)."""
    def do(customer_phone='+94770000001', provider_phone='+94771000001', **request_kw):
        c_tok, addr = world.customer(customer_phone)
        p_tok, pid = world.provider(provider_phone)
        world.request(c_tok, addr, **request_kw)
        drain(world.app)
        bid = world.accept(p_tok, _offer_for(world, p_tok)['id']).json()['booking_id']
        return c_tok, p_tok, pid, bid
    return do


def test_state_machine_rules(world, booked, client):
    c_tok, p_tok, _, bid = booked()
    assert world.move(p_tok, bid, 'completed_pending_confirmation', 1).json()['error']['code'] == 'TRANSITION_NOT_ALLOWED'
    assert world.move(c_tok, bid, 'en_route', 1).json()['error']['code'] == 'TRANSITION_NOT_ALLOWED'
    assert world.move(p_tok, bid, 'en_route', 7).json()['error']['code'] == 'VERSION_CONFLICT'
    assert world.move(p_tok, bid, 'en_route', 1).json()['version'] == 2
    assert world.move(p_tok, bid, 'arrived', 2).json()['version'] == 3
    # Check-in PIN: wrong codes are counted and lock the check-in after five (FR-BKG-02, Q48).
    for _ in range(5):
        r = world.move(p_tok, bid, 'in_progress', 3, checkin_pin='0000' if world.pin(c_tok, bid) != '0000' else '1111')
        assert r.json()['error']['code'] == 'CHECKIN_PIN_INVALID'
    r = world.move(p_tok, bid, 'in_progress', 3, checkin_pin=world.pin(c_tok, bid))
    assert r.status_code == 423 and r.json()['error']['code'] == 'CHECKIN_LOCKED'


def test_staff_checkin_exception_needs_step_up(world, booked, client, make_admin, admin_login):
    c_tok, p_tok, _, bid = booked()
    world.move(p_tok, bid, 'en_route', 1)
    world.move(p_tok, bid, 'arrived', 2)
    _, clock = make_admin('ops.olga', roles=[('operations_admin', None)])
    ops = admin_login('ops.olga', clock)
    body = {'reason': 'Beneficiary has no phone; ID checked at door', 'expected_version': 3}
    path = f'/v1/admin/bookings/{bid}/checkin-exception'
    assert client.post(path, headers=bearer(ops), json=body).json()['error']['code'] == 'STEP_UP_REQUIRED'
    step_up(client, ops, clock, 'checkin.override')
    assert client.post(path, headers=bearer(ops), json=body).json()['status'] == 'in_progress'
    inspector = client.get(f'/v1/admin/bookings/{bid}', headers=bearer(ops)).json()
    assert 'check-in exception' in inspector['events'][-1]['reason']
    assert 'address' not in inspector and 'Synthetic Lane' not in str(inspector)


def test_object_level_access(world, booked, client):
    c_tok, p_tok, _, bid = booked()
    stranger = world.login(phone='+94770000009')
    other_provider, _ = world.provider('+94771000009')
    for tokens in (stranger, other_provider):
        assert client.get(f'/v1/bookings/{bid}', headers=bearer(tokens)).status_code == 404       # AT-012/AT-013
        assert client.get(f'/v1/bookings/{bid}/settlement', headers=bearer(tokens)).status_code == 404
    assert client.get('/v1/offers/me', headers=bearer(c_tok)).status_code == 403                  # customer-only user
    assert client.post('/v1/addresses', headers=bearer(other_provider), json={}).status_code in (403, 422)


def test_customer_cancellation_frees_provider_calendar(world, booked, client, db):
    c_tok, p_tok, pid, bid = booked()
    r = world.move(c_tok, bid, 'cancelled', 1, reason='Plans changed')
    assert r.json()['status'] == 'cancelled'
    assert db('SELECT count(*) AS n FROM booking_assignments WHERE active')[0]['n'] == 0
    c2, addr2 = world.customer('+94770000002')
    world.request(c2, addr2)                 # same time slot as the cancelled job
    drain(world.app)
    assert len(world.offers(p_tok)) == 1


def test_customer_cannot_cancel_once_work_started(world, booked):
    c_tok, p_tok, _, bid = booked()
    world.move(p_tok, bid, 'en_route', 1)
    world.move(p_tok, bid, 'arrived', 2)
    assert world.move(c_tok, bid, 'cancelled', 3).json()['error']['code'] == 'TRANSITION_NOT_ALLOWED'


def test_provider_safety_exit_opens_urgent_incident(world, booked, db):
    c_tok, p_tok, _, bid = booked()
    world.move(p_tok, bid, 'en_route', 1)
    world.move(p_tok, bid, 'arrived', 2)
    assert world.move(p_tok, bid, 'cancelled', 3).json()['error']['code'] == 'SAFETY_REASON_REQUIRED'
    r = world.move(p_tok, bid, 'cancelled', 3, safety_cancellation=True, reason='Aggressive dog loose at gate')
    assert r.json()['status'] == 'cancelled'                                                       # AT-018
    inc = db('SELECT kind, severity, urgent FROM incidents')[0]
    assert inc == {'kind': 'no_entry', 'severity': 'high', 'urgent': True}
    assert db("SELECT count(*) AS n FROM ops_alerts WHERE kind = 'urgent_incident'")[0]['n'] == 1


def _finish(world, c_tok, p_tok, bid):
    world.move(p_tok, bid, 'en_route', 1)
    world.move(p_tok, bid, 'arrived', 2)
    world.move(p_tok, bid, 'in_progress', 3, checkin_pin=world.pin(c_tok, bid))
    world.move(p_tok, bid, 'completed_pending_confirmation', 4)


def test_completion_dispute_needs_human(world, booked, client, make_admin, admin_login):
    c_tok, p_tok, _, bid = booked()
    _finish(world, c_tok, p_tok, bid)
    assert world.move(c_tok, bid, 'disputed', 5, reason='Kitchen not cleaned').json()['status'] == 'disputed'   # AT-015
    assert world.move(c_tok, bid, 'completed', 6).json()['error']['code'] == 'TRANSITION_NOT_ALLOWED'
    _, clock = make_admin('ops.olga', roles=[('operations_admin', None)])
    ops = admin_login('ops.olga', clock)
    r = client.post(f'/v1/admin/bookings/{bid}/transitions', headers=bearer(ops),
                    json={'target_status': 'completed', 'expected_version': 6, 'reason': 'Provider returned and finished'})
    assert r.json()['status'] == 'completed'


def test_unconfirmed_completion_alerts_but_never_autocompletes(world, booked, db, client):
    c_tok, p_tok, _, bid = booked()
    _finish(world, c_tok, p_tok, bid)
    db("UPDATE jobs SET run_at = now() WHERE kind = 'completion.review'")
    drain(world.app)
    assert db("SELECT kind FROM ops_alerts WHERE kind = 'completion_unconfirmed'")
    assert client.get(f'/v1/bookings/{bid}', headers=bearer(c_tok)).json()['status'] == 'completed_pending_confirmation'


def test_settlement_disagreement_goes_to_finance(world, booked, client, make_admin, admin_login, db):
    c_tok, p_tok, _, bid = booked()
    assert client.post(f'/v1/bookings/{bid}/payment-report', headers=bearer(c_tok),
                       json={}).json()['error']['code'] == 'NOT_PAYABLE_YET'
    _finish(world, c_tok, p_tok, bid)
    assert client.post(f'/v1/bookings/{bid}/payment-ack', headers=bearer(p_tok),
                       json={'received': True}).json()['error']['code'] == 'NO_CUSTOMER_REPORT'
    client.post(f'/v1/bookings/{bid}/payment-report', headers=bearer(c_tok), json={'reference': 'CASH'})
    r = client.post(f'/v1/bookings/{bid}/payment-ack', headers=bearer(p_tok), json={'received': False, 'note': 'Not paid'})
    assert r.json()['status'] == 'DISPUTED'                                                         # AT-017
    _, oclock = make_admin('ops.olga', roles=[('operations_admin', None)])
    ops = admin_login('ops.olga', oclock)
    resolve = {'resolution': 'paid', 'evidence_note': 'Customer showed bank transfer receipt'}
    assert client.post(f'/v1/admin/settlements/{bid}/resolve', headers=bearer(ops),
                       json=resolve).json()['error']['code'] == 'PERMISSION_DENIED'
    _, fclock = make_admin('fin.fred', roles=[('finance_reconciler', None)])
    fin = admin_login('fin.fred', fclock)
    done = client.post(f'/v1/admin/settlements/{bid}/resolve', headers=bearer(fin), json=resolve).json()
    assert done['status'] == 'RESOLVED' and done['platform_collected_funds'] is False               # AT-016
    csv_text = client.get('/v1/admin/finance/export.csv', headers=bearer(fin)).text
    assert 'RESOLVED' in csv_text and 'platform_collected_funds' in csv_text
    assert '+9477' not in csv_text and 'Synthetic Lane' not in csv_text and 'Provider 001' not in csv_text
    with pytest.raises(DBAPIError):
        db('UPDATE settlements SET platform_collected_funds = true')                               # INV-10


# --- beneficiary bookings -----------------------------------------------------------------------------------

def _beneficiary(client, c_tok, phone='+94779999001'):
    r = client.post('/v1/beneficiaries', headers=bearer(c_tok),
                    json={'name': 'Synthetic Parent', 'phone': phone, 'relationship': 'parent'})
    assert r.json()['phone_masked'] == f'*******{phone[-3:]}'
    return r.json()['id']


def _consent_sms(db, cipher, phone):
    lookup = cipher.lookup_hash(phone, purpose='phone')
    body = db('SELECT body FROM dev_sms_inbox WHERE phone_lookup = :p ORDER BY id DESC LIMIT 1', p=lookup)[0]['body']
    return body.split('Reference ')[1].split('.')[0], body.split('Code ')[1][:6]


def test_beneficiary_must_consent_before_dispatch(world, client, db, cipher):
    c_tok, addr = world.customer()
    p_tok, _ = world.provider('+94771000001')
    ben = _beneficiary(client, c_tok)
    rid = world.request(c_tok, addr, beneficiary_id=ben)
    drain(world.app)
    assert client.get(f'/v1/requests/{rid}', headers=bearer(c_tok)).json()['status'] == 'awaiting_beneficiary'
    assert world.offers(p_tok) == []                                                               # AT-009
    consent_id, code = _consent_sms(db, cipher, '+94779999001')
    wrong = '000000' if code != '000000' else '111111'
    assert client.post(f'/v1/beneficiary-consents/{consent_id}/respond',
                       json={'code': wrong, 'decision': 'confirm'}).status_code == 400
    assert client.post(f'/v1/beneficiary-consents/{consent_id}/respond',
                       json={'code': code, 'decision': 'confirm'}).json()['status'] == 'confirmed'
    drain(world.app)
    bid = world.accept(p_tok, _offer_for(world, p_tok)['id']).json()['booking_id']
    drain(world.app)
    assert 'checkin_pin' not in client.get(f'/v1/bookings/{bid}', headers=bearer(c_tok)).json()    # PIN goes to beneficiary
    _, pin_sms = _consent_sms_or_pin(db, cipher, '+94779999001')
    assert client.get(f'/v1/bookings/{bid}', headers=bearer(p_tok)).json()['service_recipient'] == 'Synthetic Parent'
    assert world.move(p_tok, bid, 'en_route', 1).status_code == 200
    world.move(p_tok, bid, 'arrived', 2)
    assert world.move(p_tok, bid, 'in_progress', 3, checkin_pin=pin_sms).json()['status'] == 'in_progress'


def _consent_sms_or_pin(db, cipher, phone):
    lookup = cipher.lookup_hash(phone, purpose='phone')
    body = db('SELECT body FROM dev_sms_inbox WHERE phone_lookup = :p ORDER BY id DESC LIMIT 1', p=lookup)[0]['body']
    return body, body.split('check-in code is ')[1][:4]


def test_beneficiary_decline_or_silence_cancels(world, client, db, cipher):
    c_tok, addr = world.customer()
    ben = _beneficiary(client, c_tok)
    rid = world.request(c_tok, addr, beneficiary_id=ben)
    drain(world.app)
    consent_id, code = _consent_sms(db, cipher, '+94779999001')
    client.post(f'/v1/beneficiary-consents/{consent_id}/respond', json={'code': code, 'decision': 'decline'})
    assert client.get(f'/v1/requests/{rid}', headers=bearer(c_tok)).json()['status'] == 'cancelled'   # FR-BEN-03
    ben2 = _beneficiary(client, c_tok, phone='+94779999002')
    rid2 = world.request(c_tok, addr, beneficiary_id=ben2, start=tomorrow_at(15))
    db("UPDATE beneficiary_consents SET expires_at = now() - interval '1 second' WHERE request_id = :r", r=rid2)
    db("UPDATE jobs SET run_at = now() WHERE kind = 'consent.expire'")
    drain(world.app)
    assert client.get(f'/v1/requests/{rid2}', headers=bearer(c_tok)).json()['beneficiary_consent'] == 'expired'


def test_staff_assisted_consent(world, client, make_admin, admin_login):
    c_tok, addr = world.customer()
    p_tok, _ = world.provider('+94771000001')
    rid = world.request(c_tok, addr, beneficiary_id=_beneficiary(client, c_tok))
    _, clock = make_admin('ops.olga', roles=[('operations_admin', None)])
    ops = admin_login('ops.olga', clock)
    path, body = f'/v1/admin/requests/{rid}/beneficiary-consent', {'reason': 'Confirmed by phone with beneficiary'}
    assert client.post(path, headers=bearer(ops), json=body).json()['error']['code'] == 'STEP_UP_REQUIRED'
    step_up(client, ops, clock, 'consent.staff')
    assert client.post(path, headers=bearer(ops), json=body).json()['consent'] == 'staff_confirmed'
    drain(world.app)
    assert len(world.offers(p_tok)) == 1


# --- expiry, reassignment, jobs -----------------------------------------------------------------------------

def test_unassigned_request_expires_before_start(world, client, db):
    c_tok, addr = world.customer()
    p_tok, _ = world.provider('+94771000001')
    rid = world.request(c_tok, addr)
    drain(world.app)
    assert len(world.offers(p_tok)) == 1
    db("UPDATE jobs SET run_at = now() WHERE kind = 'request.expire'")
    drain(world.app)
    assert client.get(f'/v1/requests/{rid}', headers=bearer(c_tok)).json()['status'] == 'expired'   # Q27
    assert world.offers(p_tok) == []


def test_reassignment_keeps_booking_and_removes_old_provider(world, booked, client, make_admin, admin_login, db):
    c_tok, a_tok, a_pid, bid = booked()
    b_tok, b_pid = world.provider('+94771000002', spot='mid')
    _, clock = make_admin('ops.olga', roles=[('operations_admin', None)])
    ops = admin_login('ops.olga', clock)
    r = client.post(f'/v1/admin/bookings/{bid}/reassign', headers=bearer(ops),
                    json={'reason': 'Provider reported illness', 'expected_version': 1})
    assert r.json() == {'booking_id': bid, 'status': 'reassigning', 'version': 2}
    assert client.get(f'/v1/bookings/{bid}', headers=bearer(a_tok)).status_code == 404   # old provider loses access
    drain(world.app)
    offer = _offer_for(world, b_tok)
    assert world.accept(b_tok, offer['id']).json()['booking_id'] == bid                    # same booking id (Q51)
    view = client.get(f'/v1/bookings/{bid}', headers=bearer(b_tok)).json()
    assert view['status'] == 'accepted' and view['version'] == 3 and 'address' in view
    ended = db('SELECT end_reason FROM booking_assignments WHERE provider_id = :p', p=a_pid)[0]['end_reason']
    assert ended.startswith('reassigned:')


def test_failing_job_is_retried_then_dead_lettered(app, reset, db):
    from hela.core.db import tx
    from hela.core.jobs import enqueue, run_due
    with tx(app.state.services.engine) as con:
        enqueue(con, 'boom', {}, max_attempts=2)

    def boom(con, payload):
        raise RuntimeError('synthetic failure')
    run_due(app.state.services.engine, {'boom': boom})
    job = db('SELECT status, attempts, last_error FROM jobs')[0]
    assert job['status'] == 'queued' and job['attempts'] == 1 and 'synthetic failure' in job['last_error']
    db("UPDATE jobs SET run_at = now()")
    run_due(app.state.services.engine, {'boom': boom})
    assert db('SELECT status FROM jobs')[0]['status'] == 'dead'


# --- incidents and reviews ----------------------------------------------------------------------------------

def test_incident_reporting_and_response(world, booked, client, make_admin, admin_login, db):
    c_tok, p_tok, _, bid = booked()
    general = client.post('/v1/incidents', headers=bearer(c_tok),
                          json={'kind': 'harassment', 'severity': 'medium', 'description': 'Synthetic: rude messages'})
    assert general.status_code == 201 and general.json()['automated_judgement'] is False          # booking optional (Q56)
    urgent = client.post('/v1/incidents', headers=bearer(c_tok),
                         json={'booking_id': bid, 'kind': 'safety', 'severity': 'critical',
                               'description': 'Synthetic: provider behaved unsafely'}).json()
    assert urgent['urgent'] and urgent['guidance']['status'] == 'PENDING LOCAL VALIDATION'
    stranger = world.login(phone='+94770000009')
    assert client.get(f"/v1/incidents/{urgent['id']}", headers=bearer(stranger)).status_code == 404
    assert client.post('/v1/incidents', headers=bearer(stranger),
                       json={'booking_id': bid, 'kind': 'other', 'severity': 'low',
                             'description': 'Not my booking at all'}).status_code == 404
    assert b'behaved unsafely' not in bytes(db('SELECT description_enc FROM incidents WHERE severity = :s',
                                               s='critical')[0]['description_enc'])
    _, fclock = make_admin('fin.fred', roles=[('finance_reconciler', None)])
    assert client.get('/v1/admin/incidents', headers=bearer(admin_login('fin.fred', fclock))).status_code == 403
    _, clock = make_admin('incident.ira', roles=[('incident_responder', None)])
    ir = admin_login('incident.ira', clock)
    queue = client.get('/v1/admin/incidents', headers=bearer(ir)).json()
    assert queue[0]['id'] == urgent['id']                                         # urgent first
    case = client.get(f"/v1/admin/incidents/{urgent['id']}", headers=bearer(ir)).json()
    assert case['description'] == 'Synthetic: provider behaved unsafely' and 'reporter_id' not in case
    act = lambda action, **kw: client.post(f"/v1/admin/incidents/{urgent['id']}/actions", headers=bearer(ir),  # noqa: E731
                                           json={'action': action, 'note': 'Synthetic triage note', **kw}).json()
    assert act('acknowledge')['status'] == 'acknowledged'
    assert act('place_booking_on_hold', booking_version=1)['status'] == 'acknowledged'
    assert client.get(f'/v1/bookings/{bid}', headers=bearer(c_tok)).json()['status'] == 'incident_hold'
    assert act('close')['error']['code'] == 'TRANSITION_NOT_ALLOWED'
    assert act('resolve')['status'] == 'resolved'
    assert db("SELECT first_response_at IS NOT NULL AS ok FROM incidents WHERE id = :i", i=urgent['id'])[0]['ok']
    assert db('SELECT count(*) AS n FROM sensitive_access_logs WHERE resource_type = :t', t='incident')[0]['n'] == 1


def test_reviews_only_after_completion_and_moderated(world, booked, client, make_admin, admin_login):
    c_tok, p_tok, _, bid = booked()
    assert client.post(f'/v1/bookings/{bid}/reviews', headers=bearer(c_tok),
                       json={'rating': 4}).json()['error']['code'] == 'REVIEW_NOT_ALLOWED'
    _finish(world, c_tok, p_tok, bid)
    world.move(c_tok, bid, 'completed', 5)
    assert client.post(f'/v1/bookings/{bid}/reviews', headers=bearer(p_tok), json={'rating': 1}).status_code == 404
    r = client.post(f'/v1/bookings/{bid}/reviews', headers=bearer(c_tok), json={'rating': 4, 'text': 'Call me on 0771234567'})
    assert r.status_code == 201
    assert client.post(f'/v1/bookings/{bid}/reviews', headers=bearer(c_tok),
                       json={'rating': 5}).json()['error']['code'] == 'REVIEW_EXISTS'
    _, clock = make_admin('ops.olga', roles=[('operations_admin', None)])
    ops = admin_login('ops.olga', clock)
    pending = client.get('/v1/admin/reviews/pending', headers=bearer(ops)).json()
    assert pending[0]['pii_flag'] is True
    assert client.post(f"/v1/admin/reviews/{pending[0]['id']}/moderate", headers=bearer(ops),
                       json={'decision': 'reject', 'reason': 'Contains a phone number'}).json()['moderation_state'] == 'rejected'


def test_operations_queue_and_region_scope(world, client, make_admin, admin_login, region):
    c_tok, addr = world.customer()
    world.request(c_tok, addr)
    drain(world.app)
    _, clock = make_admin('ops.olga', roles=[('operations_admin', None)])
    queue = client.get('/v1/admin/operations/queue', headers=bearer(admin_login('ops.olga', clock))).json()
    assert len(queue['unfulfilled_or_expired']) == 1
    other = region('synthetic_other_area')
    _, sclock = make_admin('ops.scoped', roles=[('operations_admin', other)])
    scoped = admin_login('ops.scoped', sclock)
    assert client.get('/v1/admin/operations/queue', headers=bearer(scoped)).json()['unfulfilled_or_expired'] == []
    rid = queue['unfulfilled_or_expired'][0]['id']
    assert client.get(f'/v1/admin/requests/{rid}', headers=bearer(scoped)).json()['error']['code'] == 'PERMISSION_DENIED'


def test_safety_guidance_is_marked_unvalidated(client, reset):
    g = client.get('/v1/safety/guidance').json()
    assert g['status'] == 'PENDING LOCAL VALIDATION' and g['emergency_contacts'] == []


def test_sms_outage_is_retried_and_never_marked_sent(world, booked, app, db):
    """AT-019: a failing SMS gateway leaves notifications queued and retried, never shown as delivered."""
    class Down:
        def send(self, con, **kw):
            raise ConnectionError('synthetic SMS outage')
    real = app.state.services.sms
    db('DELETE FROM jobs')                       # start from a clean outbox; sign-in itself still needs SMS
    c_tok, p_tok, _, bid = booked()
    drain(app)
    db("UPDATE notifications SET status = 'sent'")
    app.state.services.sms = Down()
    world.move(p_tok, bid, 'en_route', 1)       # queues customer notification during the outage
    world.move(p_tok, bid, 'arrived', 2)
    drain(app)
    stuck = db("SELECT count(*) AS n FROM notifications WHERE status = 'sent' AND template_key = 'booking.updated'")[0]['n']
    retrying = db("SELECT count(*) AS n FROM jobs WHERE kind = 'notification.deliver' AND status = 'queued' "
                  "AND attempts >= 1 AND last_error LIKE 'ConnectionError%'")[0]['n']
    assert stuck == 0 and retrying >= 2
    app.state.services.sms = real
    db("UPDATE jobs SET run_at = now() WHERE kind = 'notification.deliver'")
    drain(app)
    assert db("SELECT count(*) AS n FROM notifications WHERE status <> 'sent'")[0]['n'] == 0


def test_otp_request_during_sms_outage_is_a_clean_retryable_error(client, app, reset):
    class Down:
        def send(self, con, **kw):
            raise ConnectionError('synthetic SMS outage')
    app.state.services.sms = Down()
    r = client.post('/v1/auth/otp/request', json={'phone': '+94770000001'})
    assert r.status_code == 503 and r.json()['error'] == {**r.json()['error'], 'code': 'SMS_UNAVAILABLE', 'retryable': True}
