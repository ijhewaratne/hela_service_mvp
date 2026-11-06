from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from fastapi.testclient import TestClient
from hela.db import now_utc,iso

A='X-Demo-Actor'

def admin(): return {A:'admin_ops'}
def provider(name): return {A:name,'Idempotency-Key':'test-accept-0001'}

def test_startup_requires_explicit_local_demo_mode(tmp_path,monkeypatch):
    from hela.api import create_app
    import pytest
    monkeypatch.delenv('HELA_DEMO_MODE',raising=False)
    with pytest.raises(RuntimeError,match='Refusing to start'):
        create_app(str(tmp_path/'should-not-create.sqlite'))


def test_catalog_filters_disabled_categories(client):
    data=client.get('/v1/categories').json()
    assert {x['code'] for x in data} == {'house_cleaning','errands'}
    assert client.get('/health').json()['deployable'] is False


def test_request_hard_gates_category_and_ownership(client,make_request,future_slot):
    s,e=future_slot
    body={'address_id':'addr_asha','category':'child_supervision',
          'task_details':'Unsafe category must be blocked','start_at':s.isoformat(),
          'end_at':e.isoformat()}
    resp=client.post('/v1/requests',headers={A:'customer_asha','Idempotency-Key':'child-block-01'},json=body)
    assert resp.status_code==422
    body['category']='house_cleaning'
    resp=client.post('/v1/requests',headers={A:'customer_nimal','Idempotency-Key':'foreign-addr-01'},json=body)
    assert resp.status_code==404
    assert client.post('/v1/requests',headers={A:'provider_alpha','Idempotency-Key':'provider-key-01'},json=body).status_code==403


def test_request_idempotency_same_key_conflicting_payload(client,make_request,future_slot):
    first=make_request()
    second=make_request()
    assert first['id']==second['id']
    s,e=future_slot
    bad=client.post('/v1/requests',headers={A:'customer_asha','Idempotency-Key':'request-key-001'},json={
        'address_id':'addr_asha','category':'house_cleaning','task_details':'Different details entirely',
        'start_at':s.isoformat(),'end_at':e.isoformat()})
    assert bad.status_code==409


def test_matching_excludes_unverified_suspended_expired_far(client,dispatched):
    r,result=dispatched
    assert result['status']=='offering'
    selected={x['provider_id'] for x in result['issued']}
    assert selected <= {'provider_alpha','provider_beta','provider_gamma'}
    counts=result['excluded_counts']
    assert counts['category_not_approved'] >= 1
    assert counts['inactive_or_suspended'] >= 1
    assert counts['credentials_expired_or_insufficient'] >= 1
    assert counts['outside_service_radius'] >= 1
    for pid in ('provider_unverified','provider_suspended','provider_expired','provider_far'):
        assert client.get('/v1/offers/me',headers={A:pid}).json() == []
    log=client.get('/v1/admin/requests/'+r['id']+'/matching-decisions',headers=admin())
    assert log.status_code==200
    assert len(log.json())==1


def test_dispatch_private_info_and_role_gate(client,dispatched):
    req,result=dispatched
    assert client.post('/v1/admin/requests/'+req['id']+'/dispatch',headers={A:'customer_asha'}).status_code==403
    for choice in result['issued']:
        offers=client.get('/v1/offers/me',headers={A:choice['provider_id']}).json()
        assert len(offers)==1
        assert 'task_details' not in offers[0]
        assert 'address_id' not in offers[0]
        assert 'lat' not in offers[0]


def test_offer_owner_only_idempotent_acceptance_and_one_booking(client,dispatched):
    req,result=dispatched
    first,second=result['issued']
    forbidden=client.post('/v1/offers/'+first['offer_id']+'/accept',headers=provider('provider_unverified'))
    assert forbidden.status_code==404
    good=client.post('/v1/offers/'+first['offer_id']+'/accept',headers=provider(first['provider_id']))
    assert good.status_code==200
    replay=client.post('/v1/offers/'+first['offer_id']+'/accept',headers=provider(first['provider_id']))
    assert replay.status_code==200
    assert replay.json()['id']==good.json()['id']
    loser=client.post('/v1/offers/'+second['offer_id']+'/accept',headers=provider(second['provider_id']))
    assert loser.status_code==409
    with client.app.state.db.connection() as con:
        assert con.execute('SELECT COUNT(*) FROM bookings WHERE request_id=?',(req['id'],)).fetchone()[0]==1


def test_competing_accepts_are_serialized(app,dispatched):
    req,result=dispatched
    a,b=result['issued']
    def try_accept(offer):
        with TestClient(app) as c:
            res=c.post('/v1/offers/'+offer['offer_id']+'/accept',headers={
                A:offer['provider_id'],'Idempotency-Key':'parallel-attempt-01'})
            return res.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(try_accept,(a,b)))
    assert sorted(results)==[200,409]
    with app.state.db.connection() as con:
        assert con.execute('SELECT COUNT(*) FROM bookings WHERE request_id=?',(req['id'],)).fetchone()[0]==1


def test_revoke_after_dispatch_blocks_acceptance(client,dispatched):
    req,result=dispatched
    choice=result['issued'][0]
    res=client.post('/v1/admin/providers/'+choice['provider_id']+'/category-approvals',
                    headers=admin(),json={'category':'house_cleaning','decision':'revoked',
                                          'reason':'Synthetic revocation during offer'})
    assert res.status_code==200
    fail=client.post('/v1/offers/'+choice['offer_id']+'/accept',headers=provider(choice['provider_id']))
    assert fail.status_code==409


def test_expired_offer_is_rejected(app,client,dispatched):
    _,result=dispatched
    choice=result['issued'][0]
    with app.state.db.connection(write=True) as con:
        con.execute('UPDATE offers SET expires_at=? WHERE id=?',
                    (iso(now_utc()-timedelta(seconds=1)),choice['offer_id']))
    fail=client.post('/v1/offers/'+choice['offer_id']+'/accept',headers=provider(choice['provider_id']))
    assert fail.status_code==409


def test_double_booking_provider_blocked_on_overlapping_request(client,accepted,make_request):
    req,chosen,booking=accepted
    other=make_request(actor='customer_nimal',key='second-customer-01')
    response=client.post('/v1/admin/requests/'+other['id']+'/dispatch',headers=admin())
    assert response.status_code==200
    offered={x['provider_id'] for x in response.json()['issued']}
    assert chosen['provider_id'] not in offered


def test_booking_state_machine_version_and_permissions(client,accepted):
    req,chosen,booking=accepted
    path='/v1/bookings/'+booking['id']
    assert client.get(path,headers={A:'customer_nimal'}).status_code==404
    assert client.get(path+'/events',headers={A:'customer_nimal'}).status_code==404
    assert client.get('/v1/requests/'+req['id'],headers={A:'customer_nimal'}).status_code==404
    bad=client.post(path+'/transitions',headers={A:'customer_asha','Idempotency-Key':'transition-01'},
                    json={'target_status':'en_route','expected_version':1})
    assert bad.status_code==409
    version=1
    for i,status in enumerate(['en_route','arrived','in_progress','completed_pending_confirmation']):
        result=client.post(path+'/transitions',headers={A:chosen['provider_id'],'Idempotency-Key':f'transition-p-{i:03d}'},
                           json={'target_status':status,'expected_version':version})
        assert result.status_code==200,result.text
        version=result.json()['version']
    stale=client.post(path+'/transitions',headers={A:'customer_asha','Idempotency-Key':'transition-stale-1'},
                     json={'target_status':'completed','expected_version':1})
    assert stale.status_code==409
    final=client.post(path+'/transitions',headers={A:'customer_asha','Idempotency-Key':'transition-final-1'},
                      json={'target_status':'completed','expected_version':version})
    assert final.status_code==200,final.text
    ev=client.get(path+'/events',headers={A:'customer_asha'}).json()
    assert [x['to_status'] for x in ev]==[
        'accepted','en_route','arrived','in_progress','completed_pending_confirmation','completed']


def test_offline_settlement_does_not_claim_collected_funds(client,accepted):
    _,chosen,booking=accepted
    bid=booking['id']
    before=client.post(f'/v1/bookings/{bid}/settlement/customer-reported',headers={A:'customer_asha'})
    assert before.status_code==409
    version=1
    for i,status in enumerate(['en_route','arrived','in_progress','completed_pending_confirmation']):
        res=client.post(f'/v1/bookings/{bid}/transitions',headers={A:chosen['provider_id'],
                          'Idempotency-Key':f'payment-transition-{i:03d}'},
                          json={'target_status':status,'expected_version':version})
        assert res.status_code==200,res.text
        version=res.json()['version']
    assert client.post(f'/v1/bookings/{bid}/settlement/provider-confirmed',
                       headers={A:chosen['provider_id']}).status_code==409
    report=client.post(f'/v1/bookings/{bid}/settlement/customer-reported',headers={A:'customer_asha'})
    assert report.status_code==200
    assert report.json()['platform_collected_funds'] is False
    confirm=client.post(f'/v1/bookings/{bid}/settlement/provider-confirmed',headers={A:chosen['provider_id']})
    assert confirm.status_code==200
    assert confirm.json()['platform_collected_funds'] is False
    summary=client.get(f'/v1/bookings/{bid}/settlement',headers={A:'customer_asha'}).json()
    assert summary['payment_status']=='PROVIDER_CONFIRMED'
    assert summary['commission_due_lkr']==0  # Await commercial policy signoff.


def test_incident_reporting_requires_booking_access(client,accepted):
    _,chosen,book=accepted
    body={'kind':'safety','severity':'high','description':'Synthetic safety concern for testing only'}
    denied=client.post(f'/v1/bookings/{book["id"]}/incidents',headers={A:'customer_nimal'},json=body)
    assert denied.status_code==404
    created=client.post(f'/v1/bookings/{book["id"]}/incidents',headers={A:'customer_asha'},json=body)
    assert created.status_code==201
    assert created.json()['manual_response_required'] is True
    assert client.get('/v1/admin/audit',headers={A:'customer_asha'}).status_code==403


def test_empty_matching_is_unfulfilled(client,make_request,app):
    req=make_request()
    with app.state.db.connection(write=True) as con:
        con.execute("UPDATE providers SET status='suspended'")
    res=client.post('/v1/admin/requests/'+req['id']+'/dispatch',headers=admin())
    assert res.status_code==200
    assert res.json()['status']=='unfulfilled'
    with app.state.db.connection() as con:
        assert con.execute('SELECT COUNT(*) FROM bookings').fetchone()[0]==0


def test_transition_retry_is_idempotent_and_modified_replay_rejected(client,accepted):
    _, chosen, booking=accepted
    url=f'/v1/bookings/{booking["id"]}/transitions'
    headers={A:chosen['provider_id'],'Idempotency-Key':'same-transition-key-001'}
    body={'target_status':'en_route','expected_version':1}
    one=client.post(url,headers=headers,json=body)
    assert one.status_code==200
    replay=client.post(url,headers=headers,json=body)
    assert replay.status_code==200
    assert replay.json()==one.json()
    changed=client.post(url,headers=headers,json={'target_status':'arrived','expected_version':1})
    assert changed.status_code==409
    events=client.get(f'/v1/bookings/{booking["id"]}/events',headers={A:chosen['provider_id']}).json()
    assert len(events)==2  # accepted, en_route: never duplicate transition events


def test_admin_must_give_reason_for_sensitive_transition(client,accepted):
    _, chosen, booking=accepted
    url=f'/v1/bookings/{booking["id"]}/transitions'
    reject=client.post(url,headers={A:'admin_ops','Idempotency-Key':'admin-cancel-01'},
                       json={'target_status':'cancelled','expected_version':1})
    assert reject.status_code==409
    okay=client.post(url,headers={A:'admin_ops','Idempotency-Key':'admin-cancel-02'},
                     json={'target_status':'cancelled','expected_version':1,
                           'reason':'Synthetic cancellation recorded by staff'})
    assert okay.status_code==200
    assert okay.json()['status']=='cancelled'
