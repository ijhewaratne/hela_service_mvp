"""Run one complete Hela Service demonstration against a temporary synthetic SQLite DB."""
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os

from fastapi.testclient import TestClient
from hela.api import create_app
from hela.db import now_utc

os.environ['HELA_DEMO_MODE']='1'

def call(client,method,path,actor,**kwargs):
    headers={'X-Demo-Actor':actor, 'Idempotency-Key':kwargs.pop('key','demo-key-00001')}
    response=client.request(method,path,headers=headers,**kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f'{method} {path}: HTTP {response.status_code}: {response.text}')
    return response.json() if response.content else None

def run():
    with TemporaryDirectory(prefix='hela_demo_') as directory:
        app=create_app(str(Path(directory)/'synthetic.sqlite'))
        with TestClient(app) as client:
            start=now_utc()+timedelta(days=2)
            req=call(client,'POST','/v1/requests','customer_asha',key='demo-request-01',json={
                'address_id':'addr_asha','category':'house_cleaning',
                'task_details':'Synthetic two-hour household cleaning',
                'start_at':start.isoformat(),
                'end_at':(start+timedelta(hours=2)).isoformat()})
            dispatch=call(client,'POST',f'/v1/admin/requests/{req["id"]}/dispatch','admin_ops')
            offer=dispatch['issued'][0]
            booking=call(client,'POST',f'/v1/offers/{offer["offer_id"]}/accept',
                         offer['provider_id'],key='demo-accept-01')
            bid=booking['id']; version=booking['version']
            for step,status in enumerate(['en_route','arrived','in_progress','completed_pending_confirmation']):
                booking=call(client,'POST',f'/v1/bookings/{bid}/transitions',offer['provider_id'],
                             key=f'demo-transition-{step:03d}',json={
                                 'target_status':status,'expected_version':version})
                version=booking['version']
            report=call(client,'POST',f'/v1/bookings/{bid}/settlement/customer-reported','customer_asha')
            confirmation=call(client,'POST',f'/v1/bookings/{bid}/settlement/provider-confirmed',offer['provider_id'])
            booking=call(client,'POST',f'/v1/bookings/{bid}/transitions','customer_asha',
                         key='demo-final-01',json={'target_status':'completed','expected_version':version})
            events=call(client,'GET',f'/v1/bookings/{bid}/events','customer_asha')
            return {
                'mode':'synthetic_only','category':req['category'],'price_lkr':req['estimate_lkr'],
                'offers_created':dispatch['offers_issued'], 'assigned_provider':offer['provider_id'],
                'booking_status':booking['status'],'booking_event_count':len(events),
                'offline_payment_status':confirmation['payment_status'],
                'platform_collected_funds':confirmation['platform_collected_funds'],
                'warnings':['Rates and actors are fictional.',
                            'No real provider verified and no money collected.',
                            'Do not expose demo actor headers publicly.']}

if __name__=='__main__':
    print(json.dumps(run(),indent=2))
