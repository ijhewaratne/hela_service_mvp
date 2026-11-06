"""Eligibility-first matching and auditable booking state machine (independent Hela design)."""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone, timedelta
from math import asin, cos, radians, sin, sqrt
import json
from uuid import uuid4
from .db import iso, now_utc

ACTIVE_BOOKING_STATUSES = (
    'accepted','en_route','arrived','in_progress','completed_pending_confirmation',
    'disputed','incident_hold'
)

class Conflict(Exception):
    pass

class Missing(Exception):
    pass

class Denied(Exception):
    pass

def uid():
    return str(uuid4())

def haversine_km(a_lat, a_lon, b_lat, b_lon):
    """Straight-line proximity proxy, NOT true road distance or a route ETA."""
    p1, p2 = radians(a_lat), radians(b_lat)
    dp = radians(b_lat - a_lat)
    dl = radians(b_lon - a_lon)
    k = sin(dp/2)**2 + cos(p1)*cos(p2)*sin(dl/2)**2
    return 6371.0088 * 2 * asin(min(1.0, sqrt(k)))

def add_audit(con, actor, action, entity_id, details=None):
    con.execute('INSERT INTO audit_events(actor_id,action,entity_id,details_json,created_at) VALUES(?,?,?,?,?)',
                (actor, action, entity_id, json.dumps(details or {}, sort_keys=True), iso(now_utc())))

def booking_event(con, booking_id, actor, old, new, details=None):
    con.execute('''INSERT INTO booking_events(booking_id,actor_id,from_status,to_status,details,created_at)
                   VALUES(?,?,?,?,?,?)''',
                (booking_id,actor,old,new,json.dumps(details or {}, sort_keys=True),iso(now_utc())))

def get_request(con, request_id):
    request = con.execute('''SELECT r.*, a.lat AS address_lat, a.lon AS address_lon,
                            c.enabled AS category_enabled, c.safeguards_required
                            FROM requests r JOIN addresses a ON a.id=r.address_id
                            JOIN categories c ON c.code=r.category WHERE r.id=?''',
                          (request_id,)).fetchone()
    if request is None:
        raise Missing('Request does not exist')
    return request

def eligible_candidates(con, req):
    """Fail-closed filters; normalized illustrative score is NOT PickMe's dispatch algorithm."""
    all_providers = con.execute('''SELECT p.*, a.active AS actor_active FROM providers p
                                   JOIN actors a ON a.id=p.actor_id''').fetchall()
    excluded = Counter()
    eligible = []
    start = req['starts_at']; end = req['ends_at']
    for p in all_providers:
        pid = p['actor_id']
        reason = None
        if not p['actor_active'] or p['status'] != 'active':
            reason = 'inactive_or_suspended'
        skill = con.execute('SELECT * FROM provider_skills WHERE provider_id=? AND category=?',
                            (pid,req['category'])).fetchone()
        if reason is None and (skill is None or skill['status'] != 'approved'):
            reason = 'category_not_approved'
        if reason is None and (not skill['credentials_expires_at'] or skill['credentials_expires_at'] <= iso(now_utc())
                               or skill['credentials_expires_at'] <= end):
            reason = 'credentials_expired_or_insufficient'
        if reason is None and req['safeguards_required'] and not skill['safeguards_verified']:
            reason = 'missing_safeguard_checks'
        distance = haversine_km(p['lat'],p['lon'],req['address_lat'],req['address_lon'])
        if reason is None and distance > p['service_radius_km']:
            reason = 'outside_service_radius'
        slots = con.execute('''SELECT starts_at,ends_at FROM provider_availability
                               WHERE provider_id=? AND starts_at<=? AND ends_at>=?''',
                            (pid,start,end)).fetchall()
        if reason is None and not slots:
            reason = 'unavailable'
        if reason is None:
            bind = ','.join('?' for _ in ACTIVE_BOOKING_STATUSES)
            overlap = con.execute(f'''SELECT 1 FROM bookings b JOIN requests r ON r.id=b.request_id
                 WHERE b.provider_id=? AND b.status IN ({bind}) AND r.starts_at<? AND r.ends_at>? LIMIT 1''',
                 (pid,*ACTIVE_BOOKING_STATUSES,end,start)).fetchone()
            if overlap:
                reason = 'conflicting_assignment'
        if reason:
            excluded[reason] += 1
            continue
        # Use nearest open availability interval to measure scheduling flexibility.
        slack_hours = max((datetime.fromisoformat(start) - datetime.fromisoformat(s['starts_at'])).total_seconds()/3600
                          + (datetime.fromisoformat(s['ends_at']) - datetime.fromisoformat(end)).total_seconds()/3600
                          for s in slots)
        slack_penalty = 1.0-min(slack_hours/12.0,1.0)
        distance_penalty = min(distance/max(p['service_radius_km'],0.01),1.0)
        score = (0.45 * distance_penalty + 0.25 * slack_penalty +
                 0.20 * max(0.0,min(p['recent_decline_rate'],1.0)) +
                 0.10 * min(p['recent_assignments']/10.0,1.0))
        eligible.append({'provider_id':pid, 'score':round(score,5),
                         'approx_straight_line_km':round(distance,2)})
    eligible.sort(key=lambda x:(x['score'],x['provider_id']))
    return eligible, dict(excluded)

def dispatch(con, request_id, actor, *, offer_limit=2, offer_minutes=15):
    req = get_request(con,request_id)
    if not req['category_enabled']:
        raise Conflict('Service category is not enabled')
    if req['status'] in ('cancelled','accepted'):
        raise Conflict('Request cannot be dispatched from current state')
    # A previously dispatched offer can expire and be replaced.
    con.execute("UPDATE offers SET status='expired' WHERE request_id=? AND status='pending' AND expires_at<=?",
                (request_id,iso(now_utc())))
    currently_pending = con.execute('''SELECT o.* FROM offers o WHERE request_id=? AND status='pending'
                                       ORDER BY match_score,provider_id''',(request_id,)).fetchall()
    if currently_pending:
        return {'request_id':request_id,'status':'offering','offers_issued':0,
                'pending_offers':len(currently_pending),'reason':'active_offers_exist'}
    candidates,excluded = eligible_candidates(con,req)
    used = {x['provider_id'] for x in con.execute('SELECT provider_id FROM offers WHERE request_id=?',(request_id,))}
    selected = [c for c in candidates if c['provider_id'] not in used][:offer_limit]
    issued = []
    for chosen in selected:
        offer_id = uid()
        con.execute('''INSERT INTO offers VALUES(?,?,?,?,?,?,?)''',
                    (offer_id,request_id,chosen['provider_id'],'pending',
                     iso(now_utc()+timedelta(minutes=offer_minutes)),chosen['score'],iso(now_utc())))
        issued.append({'offer_id':offer_id,'provider_id':chosen['provider_id'],
                       'score':chosen['score']})
    new_status = 'offering' if issued else 'unfulfilled'
    con.execute('UPDATE requests SET status=? WHERE id=?',(new_status,request_id))
    con.execute('INSERT INTO match_decisions(request_id,summary_json,created_at) VALUES(?,?,?)',
                (request_id,json.dumps({'strategy':'synthetic-demo-v1','eligible_count':len(candidates),
                                       'exclusions':excluded,'selected':issued},sort_keys=True),iso(now_utc())))
    add_audit(con,actor,'request.dispatched',request_id,{'issued_count':len(issued),'status':new_status})
    return {'request_id':request_id,'status':new_status,'offers_issued':len(issued),
            'pending_offers':len(issued),'excluded_counts':excluded,'issued':issued}

def accept_offer(con, offer_id, provider_id):
    offer = con.execute('SELECT * FROM offers WHERE id=?',(offer_id,)).fetchone()
    if offer is None or offer['provider_id'] != provider_id:
        raise Missing('Offer not found')
    if offer['status'] == 'accepted':
        previous = con.execute('SELECT * FROM bookings WHERE offer_id=?',(offer_id,)).fetchone()
        if previous:
            return dict(previous)
    if offer['status'] != 'pending' or offer['expires_at'] <= iso(now_utc()):
        raise Conflict('Offer is no longer active')
    req = get_request(con,offer['request_id'])
    if req['status'] != 'offering':
        raise Conflict('Request no longer accepting offers')
    if con.execute('SELECT 1 FROM bookings WHERE request_id=?',(req['id'],)).fetchone():
        raise Conflict('Booking has already been assigned')
    candidates,_ = eligible_candidates(con,req)
    if provider_id not in {c['provider_id'] for c in candidates}:
        raise Conflict('Provider eligibility changed; offer cannot be accepted')
    booking_id = uid(); timestamp=iso(now_utc())
    con.execute('UPDATE offers SET status=? WHERE id=?',('accepted',offer_id))
    con.execute("UPDATE offers SET status='revoked' WHERE request_id=? AND id<>? AND status='pending'",
                (req['id'],offer_id))
    con.execute('''INSERT INTO bookings VALUES(?,?,?,?,?,?,?,?)''',
                (booking_id,req['id'],provider_id,offer_id,'accepted',1,timestamp,timestamp))
    con.execute("UPDATE requests SET status='accepted' WHERE id=?",(req['id'],))
    con.execute('UPDATE providers SET recent_assignments=recent_assignments+1 WHERE actor_id=?',
                (provider_id,))
    con.execute('''INSERT INTO settlement_records(booking_id,agreed_amount_lkr,commission_due_lkr,payment_status,updated_at)
                   VALUES(?,?,?,?,?)''',(booking_id,req['estimate_lkr'],0,'UNPAID',timestamp))
    booking_event(con,booking_id,provider_id,None,'accepted',{'offer_id':offer_id})
    add_audit(con,provider_id,'offer.accepted',offer_id,{'booking_id':booking_id})
    return dict(con.execute('SELECT * FROM bookings WHERE id=?',(booking_id,)).fetchone())

# Allowed transitions and roles; deliberately limited until business policies are finalized.
TRANSITIONS = {
    ('accepted','en_route'):{'provider'},
    ('en_route','arrived'):{'provider'},
    ('arrived','in_progress'):{'provider'},
    ('in_progress','completed_pending_confirmation'):{'provider'},
    ('completed_pending_confirmation','completed'):{'customer','admin'},
    ('completed_pending_confirmation','disputed'):{'customer','admin'},
    ('disputed','completed'):{'admin'},
    ('accepted','cancelled'):{'customer','admin'},
    ('en_route','cancelled'):{'admin'},
    ('arrived','cancelled'):{'admin'},
    ('in_progress','incident_hold'):{'admin'},
    ('incident_hold','disputed'):{'admin'},
    ('disputed','cancelled_after_review'):{'admin'},
}

def check_booking_access(con, booking_id, actor, role):
    book = con.execute('''SELECT b.*,r.booker_id,r.starts_at,r.ends_at,r.category
                          FROM bookings b JOIN requests r ON r.id=b.request_id
                          WHERE b.id=?''',(booking_id,)).fetchone()
    if not book or (role!='admin' and actor not in (book['booker_id'],book['provider_id'])):
        raise Missing('Booking not found')
    return book

def transition(con, booking_id, actor, role, target, expected_version, reason=''):
    book=check_booking_access(con,booking_id,actor,role)
    if book['version'] != expected_version:
        raise Conflict('Booking was changed by another action (version mismatch)')
    if role not in TRANSITIONS.get((book['status'],target),set()):
        raise Conflict('Transition is not allowed')
    if role=='provider' and actor!=book['provider_id']:
        raise Denied('Only assigned provider may perform this transition')
    if role=='customer' and actor!=book['booker_id']:
        raise Denied('Only booking customer may perform this transition')
    if role=='admin' and target in ('cancelled','cancelled_after_review','completed','incident_hold','disputed') and len(reason.strip())<5:
        raise Conflict('Admin intervention needs an auditable reason (5+ characters)')
    con.execute('''UPDATE bookings SET status=?,version=version+1,updated_at=?
                   WHERE id=? AND version=?''',(target,iso(now_utc()),booking_id,expected_version))
    if target=='cancelled':
        con.execute("UPDATE requests SET status='cancelled' WHERE id=?",(book['request_id'],))
    booking_event(con,booking_id,actor,book['status'],target,{'reason':reason})
    add_audit(con,actor,'booking.transition',booking_id,{'from':book['status'],'to':target,'reason':reason})
    return dict(con.execute('SELECT * FROM bookings WHERE id=?',(booking_id,)).fetchone())
