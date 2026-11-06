from datetime import timedelta
import pytest
from fastapi.testclient import TestClient
from hela.api import create_app
from hela.db import now_utc

@pytest.fixture
def app(tmp_path,monkeypatch):
    monkeypatch.setenv('HELA_DEMO_MODE','1')
    return create_app(str(tmp_path/'hela.sqlite'))

@pytest.fixture
def client(app):
    return TestClient(app)

@pytest.fixture
def future_slot():
    start=now_utc()+timedelta(days=2)
    return start, start+timedelta(hours=2)

@pytest.fixture
def make_request(client,future_slot):
    def create(actor='customer_asha',key='request-key-001',category='house_cleaning',address=None,offset_days=0):
        s,e=future_slot
        s=s+timedelta(days=offset_days)
        e=e+timedelta(days=offset_days)
        resp=client.post('/v1/requests',headers={'X-Demo-Actor':actor,'Idempotency-Key':key},json={
            'address_id':address or ('addr_nimal' if actor=='customer_nimal' else 'addr_asha'),
            'category':category, 'task_details':'Synthetic cleaning of two rooms',
            'start_at':s.isoformat(),'end_at':e.isoformat()})
        assert resp.status_code==201,resp.text
        return resp.json()
    return create

@pytest.fixture
def dispatched(client,make_request):
    r=make_request()
    response=client.post('/v1/admin/requests/'+r['id']+'/dispatch',headers={'X-Demo-Actor':'admin_ops'})
    assert response.status_code==200,response.text
    return r,response.json()

@pytest.fixture
def accepted(client,dispatched):
    req, result=dispatched
    assert result['offers_issued']==2
    chosen=result['issued'][0]
    resp=client.post('/v1/offers/'+chosen['offer_id']+'/accept',headers={
        'X-Demo-Actor':chosen['provider_id'],'Idempotency-Key':'accept-key-001'})
    assert resp.status_code==200,resp.text
    return req,chosen,resp.json()
