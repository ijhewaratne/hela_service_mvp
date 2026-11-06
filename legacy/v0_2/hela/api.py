"""DEMO ONLY: all actors are synthetic. Never expose to the public internet."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Literal
import json
import os
import re
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from .db import DemoDatabase, iso, now_utc
from .domain import (Conflict, Missing, Denied, add_audit, accept_offer,
                     check_booking_access, dispatch, get_request, transition)

class ServiceRequestInput(BaseModel):
    address_id: str = Field(min_length=3,max_length=100)
    category: str = Field(min_length=3,max_length=100)
    task_details: str = Field(min_length=10,max_length=1000)
    start_at: datetime
    end_at: datetime

    @model_validator(mode='after')
    def check_times(self):
        if self.start_at.tzinfo is None or self.end_at.tzinfo is None:
            raise ValueError('Timestamps require timezone offsets (UTC preferred)')
        duration = self.end_at-self.start_at
        if duration <= timedelta(0) or duration > timedelta(hours=8):
            raise ValueError('Duration must be greater than zero and no more than eight hours')
        if self.start_at < now_utc()+timedelta(minutes=5):
            raise ValueError('Service must start at least five minutes in the future')
        if self.start_at > now_utc()+timedelta(days=60):
            raise ValueError('Service must be within 60 days')
        return self

class TransitionInput(BaseModel):
    target_status: Literal['en_route','arrived','in_progress','completed_pending_confirmation',
                           'completed','disputed','cancelled','incident_hold','cancelled_after_review']
    expected_version: int = Field(ge=1)
    reason: str = Field(default='',max_length=500)

class IncidentInput(BaseModel):
    kind: Literal['service_quality','safety','payment_dispute','other']
    severity: Literal['low','medium','high','critical']
    description: str = Field(min_length=15,max_length=3000)

class ApprovalInput(BaseModel):
    category: str
    decision: Literal['approved','denied','revoked']
    reason: str = Field(min_length=5,max_length=500)

class DemoActor(BaseModel):
    id: str
    role: str


def uuid():
    return str(uuid4())

def digest(body: ServiceRequestInput):
    return sha256(json.dumps(body.model_dump(mode='json'),sort_keys=True).encode()).hexdigest()

def db_of(request: Request):
    return request.app.state.db

def actor_of(request: Request, x_demo_actor: str | None = Header(None)) -> DemoActor:
    if not x_demo_actor:
        raise HTTPException(401,'This demo requires X-Demo-Actor (synthetic accounts only)')
    with db_of(request).connection() as con:
        a=con.execute('SELECT * FROM actors WHERE id=?',(x_demo_actor,)).fetchone()
    if not a or not a['active']:
        raise HTTPException(401,'Unknown synthetic actor')
    return DemoActor(id=a['id'], role=a['role'])

def only(actor: DemoActor, *roles):
    if actor.role not in roles:
        raise HTTPException(403,'Role not permitted')

def idem(key):
    if not key or not re.fullmatch(r'[A-Za-z0-9._:-]{8,80}',key):
        raise HTTPException(400,'Idempotency-Key must be 8–80 safe characters')


def create_app(db_path: str | None = None) -> FastAPI:
    if os.getenv('HELA_DEMO_MODE') != '1':
        raise RuntimeError('Refusing to start: set HELA_DEMO_MODE=1 for local synthetic testing only. No production authentication exists.')
    db=DemoDatabase(db_path or os.getenv('HELA_DB_PATH','/tmp/hela_service_synthetic.sqlite'))
    db.initialize()
    app=FastAPI(title='Hela Service — SYNTHETIC DEMO ONLY',version='0.2.0',
                description='Independent proof of concept. No production auth, payments, real providers, GPS tracking or safeguarding integration. NEVER deploy publicly.')
    app.state.db=db

    @app.exception_handler(Conflict)
    async def conflict_handler(req: Request, exc: Conflict):
        return JSONResponse({'detail':str(exc)},status_code=409)

    @app.exception_handler(Missing)
    async def missing_handler(req: Request, exc: Missing):
        return JSONResponse({'detail':str(exc)},status_code=404)

    @app.exception_handler(Denied)
    async def denied_handler(req: Request, exc: Denied):
        return JSONResponse({'detail':str(exc)},status_code=403)

    @app.get('/health')
    def health():
        return {'status':'ok','environment':'synthetic_demo','deployable':False}

    @app.get('/v1/categories')
    def categories():
        with db.connection() as con:
            return [dict(r) for r in con.execute('''SELECT code, display_name, hourly_rate_lkr
                          FROM categories WHERE enabled=1 ORDER BY code''')]

    @app.get('/v1/addresses/me')
    def my_addresses(actor: DemoActor = Depends(actor_of)):
        only(actor,'customer')
        with db.connection() as con:
            return [dict(r) for r in con.execute('SELECT id,label FROM addresses WHERE owner_id=?',(actor.id,))]

    @app.post('/v1/requests',status_code=201)
    def create_request(data: ServiceRequestInput, request: Request,
                       idempotency_key: str | None = Header(None,alias='Idempotency-Key'),
                       actor: DemoActor = Depends(actor_of)):
        only(actor,'customer'); idem(idempotency_key)
        payload_hash=digest(data)
        with db.connection(write=True) as con:
            old=con.execute('SELECT * FROM requests WHERE booker_id=? AND idempotency_key=?',
                            (actor.id,idempotency_key)).fetchone()
            if old:
                if old['payload_hash'] != payload_hash:
                    raise Conflict('Idempotency key reused with different request body')
                return dict(old)
            addr=con.execute('SELECT * FROM addresses WHERE id=? AND owner_id=?',
                             (data.address_id,actor.id)).fetchone()
            if not addr:
                raise Missing('Household address not found')
            category=con.execute('SELECT * FROM categories WHERE code=? AND enabled=1',
                                 (data.category,)).fetchone()
            if not category:
                raise HTTPException(422,'Service category disabled or unsupported')
            # Pricing policy is illustrative. No platform payments or commission collection.
            start=iso(data.start_at); end=iso(data.end_at)
            hours=(data.end_at-data.start_at).total_seconds()/3600
            estimate=round(category['hourly_rate_lkr']*hours)
            rid=uuid()
            con.execute('''INSERT INTO requests(id,booker_id,address_id,category,task_details,
                           starts_at,ends_at,status,estimate_lkr,pricing_policy_version,
                           idempotency_key,payload_hash,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (rid,actor.id,data.address_id,data.category,data.task_details,
                         start,end,'requested',estimate,'demo-v1',idempotency_key,payload_hash,iso(now_utc())))
            add_audit(con,actor.id,'request.created',rid,{'category':data.category})
            return dict(con.execute('SELECT * FROM requests WHERE id=?',(rid,)).fetchone())

    @app.get('/v1/requests/{request_id}')
    def request_detail(request_id: str,actor: DemoActor=Depends(actor_of)):
        with db.connection() as con:
            r=get_request(con,request_id)
            if actor.role!='admin' and actor.id!=r['booker_id']:
                raise Missing('Request not found')
            return {k:r[k] for k in ('id','category','status','starts_at','ends_at',
                                    'task_details','estimate_lkr','pricing_policy_version')}

    @app.post('/v1/admin/requests/{request_id}/dispatch')
    def dispatch_request(request_id: str, actor: DemoActor=Depends(actor_of)):
        only(actor,'admin')
        with db.connection(write=True) as con:
            return dispatch(con,request_id,actor.id)

    @app.get('/v1/offers/me')
    def my_offers(actor: DemoActor=Depends(actor_of)):
        only(actor,'provider')
        with db.connection() as con:
            rows=con.execute('''SELECT o.id,o.request_id,o.status,o.expires_at,o.match_score,
                r.category,r.starts_at,r.ends_at FROM offers o JOIN requests r ON r.id=o.request_id
                WHERE o.provider_id=? AND o.status='pending' AND o.expires_at>?
                ORDER BY o.created_at DESC''',(actor.id,iso(now_utc()))).fetchall()
            return [dict(r) for r in rows]  # Address and private task_details intentionally withheld.

    @app.post('/v1/offers/{offer_id}/accept')
    def accept(offer_id: str, idempotency_key: str | None = Header(None,alias='Idempotency-Key'),
               actor: DemoActor=Depends(actor_of)):
        only(actor,'provider'); idem(idempotency_key)
        with db.connection(write=True) as con:
            return accept_offer(con,offer_id,actor.id)

    @app.post('/v1/offers/{offer_id}/decline',status_code=204)
    def decline(offer_id: str, actor: DemoActor=Depends(actor_of)):
        only(actor,'provider')
        with db.connection(write=True) as con:
            off=con.execute('SELECT * FROM offers WHERE id=? AND provider_id=?',(offer_id,actor.id)).fetchone()
            if not off:
                raise Missing('Offer not found')
            if off['status']!='pending':
                raise Conflict('Offer no longer active')
            con.execute("UPDATE offers SET status='declined' WHERE id=?",(offer_id,))
            add_audit(con,actor.id,'offer.declined',offer_id)
        return None

    @app.get('/v1/bookings/{booking_id}')
    def booking_detail(booking_id: str,actor: DemoActor=Depends(actor_of)):
        with db.connection() as con:
            return dict(check_booking_access(con,booking_id,actor.id,actor.role))

    @app.get('/v1/bookings/{booking_id}/events')
    def booking_history(booking_id: str,actor: DemoActor=Depends(actor_of)):
        with db.connection() as con:
            check_booking_access(con,booking_id,actor.id,actor.role)
            return [dict(r) for r in con.execute('''SELECT actor_id,from_status,to_status,details,created_at
                   FROM booking_events WHERE booking_id=? ORDER BY id''',(booking_id,))]

    @app.post('/v1/bookings/{booking_id}/transitions')
    def change_booking(booking_id: str,data: TransitionInput,
                       idempotency_key: str | None = Header(None,alias='Idempotency-Key'),
                       actor: DemoActor=Depends(actor_of)):
        idem(idempotency_key)
        with db.connection(write=True) as con:
            check_booking_access(con,booking_id,actor.id,actor.role)
            scope=f'booking.transition:{booking_id}'
            request_hash=sha256(json.dumps(data.model_dump(),sort_keys=True).encode()).hexdigest()
            prev=con.execute('''SELECT * FROM action_idempotency
                     WHERE actor_id=? AND scope=? AND key=?''',
                     (actor.id,scope,idempotency_key)).fetchone()
            if prev:
                if prev['request_hash']!=request_hash:
                    raise Conflict('Idempotency key reused with different transition')
                return json.loads(prev['response_json'])
            updated=transition(con,booking_id,actor.id,actor.role,data.target_status,
                               data.expected_version,data.reason)
            con.execute('''INSERT INTO action_idempotency VALUES(?,?,?,?,?,?)''',
                        (actor.id,scope,idempotency_key,request_hash,
                         json.dumps(updated,sort_keys=True),iso(now_utc())))
            return updated

    @app.get('/v1/bookings/{booking_id}/settlement')
    def settlement(booking_id: str,actor: DemoActor=Depends(actor_of)):
        with db.connection() as con:
            check_booking_access(con,booking_id,actor.id,actor.role)
            row=con.execute('SELECT * FROM settlement_records WHERE booking_id=?',(booking_id,)).fetchone()
            return dict(row)  # Status tracks reported offline payment, NOT platform cash receipt.

    @app.post('/v1/bookings/{booking_id}/settlement/customer-reported')
    def customer_report(booking_id: str,actor: DemoActor=Depends(actor_of)):
        only(actor,'customer')
        with db.connection(write=True) as con:
            book=check_booking_access(con,booking_id,actor.id,actor.role)
            if book['booker_id']!=actor.id:
                raise Missing('Booking not found')
            if book['status'] not in ('completed_pending_confirmation','completed'):
                raise Conflict('Service has not reached completion stage')
            row=con.execute('SELECT * FROM settlement_records WHERE booking_id=?',(booking_id,)).fetchone()
            if row['payment_status'] not in ('UNPAID','CUSTOMER_REPORTED_PAID'):
                raise Conflict('Payment status cannot be changed')
            con.execute("UPDATE settlement_records SET payment_status='CUSTOMER_REPORTED_PAID',updated_at=? WHERE booking_id=?",
                        (iso(now_utc()),booking_id))
            add_audit(con,actor.id,'settlement.customer_reported',booking_id)
            return {'payment_status':'CUSTOMER_REPORTED_PAID','platform_collected_funds':False}

    @app.post('/v1/bookings/{booking_id}/settlement/provider-confirmed')
    def provider_confirm(booking_id: str,actor: DemoActor=Depends(actor_of)):
        only(actor,'provider')
        with db.connection(write=True) as con:
            book=check_booking_access(con,booking_id,actor.id,actor.role)
            if book['provider_id']!=actor.id:
                raise Missing('Booking not found')
            row=con.execute('SELECT * FROM settlement_records WHERE booking_id=?',(booking_id,)).fetchone()
            if row['payment_status'] not in ('CUSTOMER_REPORTED_PAID','PROVIDER_CONFIRMED'):
                raise Conflict('Customer has not reported offline payment')
            con.execute("UPDATE settlement_records SET payment_status='PROVIDER_CONFIRMED',updated_at=? WHERE booking_id=?",
                        (iso(now_utc()),booking_id))
            add_audit(con,actor.id,'settlement.provider_confirmed',booking_id)
            return {'payment_status':'PROVIDER_CONFIRMED','platform_collected_funds':False}

    @app.post('/v1/bookings/{booking_id}/incidents',status_code=201)
    def report_incident(booking_id: str,data: IncidentInput,actor: DemoActor=Depends(actor_of)):
        with db.connection(write=True) as con:
            check_booking_access(con,booking_id,actor.id,actor.role)
            incident_id=uuid()
            con.execute('''INSERT INTO incidents VALUES(?,?,?,?,?,?,?,?)''',
                        (incident_id,booking_id,actor.id,data.severity,data.kind,data.description,'open',iso(now_utc())))
            add_audit(con,actor.id,'incident.created',incident_id,
                      {'booking_id':booking_id,'severity':data.severity})
            return {'id':incident_id,'booking_id':booking_id,'status':'open',
                    'manual_response_required':True}

    @app.post('/v1/admin/providers/{provider_id}/category-approvals')
    def approve_category(provider_id: str,data: ApprovalInput,actor: DemoActor=Depends(actor_of)):
        only(actor,'admin')
        with db.connection(write=True) as con:
            skill=con.execute('SELECT * FROM provider_skills WHERE provider_id=? AND category=?',
                              (provider_id,data.category)).fetchone()
            if not skill:
                raise Missing('Provider category not found')
            if data.decision=='approved':
                # An admin cannot bypass missing/expired evidence with a blind toggle.
                if not skill['credentials_expires_at'] or skill['credentials_expires_at']<=iso(now_utc()):
                    raise Conflict('Credential evidence must be valid before approval')
                cat=con.execute('SELECT safeguards_required FROM categories WHERE code=?',
                                (data.category,)).fetchone()
                if cat['safeguards_required'] and not skill['safeguards_verified']:
                    raise Conflict('Safeguard verification required')
            con.execute('UPDATE provider_skills SET status=? WHERE provider_id=? AND category=?',
                        (data.decision,provider_id,data.category))
            add_audit(con,actor.id,'provider.category_decision',provider_id,
                      {'category':data.category,'decision':data.decision,'reason':data.reason})
            return {'provider_id':provider_id,'category':data.category,'decision':data.decision}

    @app.get('/v1/admin/requests/{request_id}/matching-decisions')
    def match_logs(request_id: str,actor: DemoActor=Depends(actor_of)):
        only(actor,'admin')
        with db.connection() as con:
            get_request(con,request_id)
            return [dict(r) for r in con.execute('''SELECT id,summary_json,created_at FROM match_decisions
                          WHERE request_id=? ORDER BY id''',(request_id,))]

    @app.get('/v1/admin/audit')
    def audit(actor: DemoActor=Depends(actor_of)):
        only(actor,'admin')
        with db.connection() as con:
            return [dict(r) for r in con.execute('''SELECT * FROM audit_events ORDER BY id DESC LIMIT 100''')]

    return app
