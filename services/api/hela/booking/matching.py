"""Eligibility-first matching and sequential dispatch (spec §8, FR-MAT-01..08, Q40–Q45).

Hard gates run first and fail closed; ranking only orders providers who passed every gate. Each run is
recorded with config version, candidate counts, exclusion reason counts and the ranked list (FR-MAT-03).
The weights are the spec's PROPOSED starting hypothesis, not tuned production values.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from statistics import median

from sqlalchemy import Connection

from ..audit import service as audit
from ..core.config import Settings
from ..core.db import many, one, run
from ..core.jobs import enqueue
from ..notifications.outbox import notify
from ..verification.eligibility import provider_category_gaps
from .availability import has_overlap, schedule_slack_minutes

WEIGHTS = {'distance': 0.45, 'schedule_slack': 0.25, 'decline': 0.20, 'fairness': 0.10}
COLD_START_OFFERS = 5


def distance_band(km: float) -> str:
    return '<2 km' if km < 2 else '2-5 km' if km < 5 else '5-10 km' if km < 10 else '10+ km'


def _history(con: Connection, provider_id: str) -> tuple[float, int]:
    """30-day decline rate (expired offers excluded: the provider may simply have been offline) and assignments."""
    h = one(con, '''SELECT count(*) FILTER (WHERE status = 'declined') AS declined,
                           count(*) FILTER (WHERE status = 'accepted') AS accepted
                    FROM offers WHERE provider_id = :p AND created_at > now() - interval '30 days' ''', p=provider_id)
    answered = h['declined'] + h['accepted']
    decline = 0.5 if answered < COLD_START_OFFERS else h['declined'] / answered  # neutral cold start (Q45)
    assigned = one(con, '''SELECT count(*) AS n FROM booking_assignments
                           WHERE provider_id = :p AND started_at > now() - interval '30 days' ''', p=provider_id)['n']
    return decline, assigned


def hard_gates(con: Connection, s: Settings, req: dict, provider_id: str, *, distance_km: float | None,
               radius_km: float | None) -> tuple[list[str], int | None]:
    """All mandatory checks for one provider and request (§8.1). Returns (reason codes, schedule slack)."""
    reasons = list(provider_category_gaps(con, provider_id, req['category_code']))
    if provider_id == str(req['customer_id']):
        reasons.append('SELF_BOOKING')                       # Q12: never receive your own booking
    if distance_km is None or radius_km is None:
        reasons.append('NO_SERVICE_AREA')
    elif distance_km > radius_km:
        reasons.append('OUTSIDE_SERVICE_AREA')
    slack = schedule_slack_minutes(con, provider_id, req['start_at'], req['end_at'], s.local_timezone)
    if slack is None:
        reasons.append('UNAVAILABLE')
    if has_overlap(con, provider_id, req['start_at'], req['end_at'], s.travel_buffer_minutes):
        reasons.append('CONFLICTING_ASSIGNMENT')
    return reasons, slack


def _candidates(con: Connection, req: dict) -> list[dict]:
    """Coarse geographic candidates: providers with any approval row for the category and a service area."""
    return many(con, '''SELECT a.provider_id, ST_Distance(sa.base_point, addr.match_point) / 1000.0 AS distance_km,
                               least(sa.radius_km, p.max_radius_km)::float AS radius_km
                        FROM provider_category_approvals a
                        JOIN service_requests r ON r.id = :r
                        JOIN customer_addresses addr ON addr.id = r.address_id
                        JOIN category_policies p ON p.id = r.policy_id
                        LEFT JOIN provider_service_areas sa ON sa.provider_id = a.provider_id
                        WHERE a.category_code = r.category_code
                        ORDER BY a.provider_id''', r=req['id'])


def run_matching(con: Connection, s: Settings, req: dict) -> dict:
    already = {str(r['provider_id']) for r in many(con, 'SELECT provider_id FROM offers WHERE request_id = :r',
                                                   r=req['id'])}
    exclusions: Counter = Counter()
    eligible = []
    candidates = _candidates(con, req)
    for c in candidates:
        pid = str(c['provider_id'])
        if pid in already:
            exclusions['ALREADY_OFFERED'] += 1
            continue
        reasons, slack = hard_gates(con, s, req, pid, distance_km=c['distance_km'], radius_km=c['radius_km'])
        if reasons:
            for code in reasons:
                exclusions[code.split(':')[0]] += 1   # non-sensitive codes only; no provider identities
            continue
        decline, assigned = _history(con, pid)
        eligible.append({'provider_id': pid, 'distance_km': round(c['distance_km'], 2), 'radius_km': c['radius_km'],
                         'slack_minutes': slack, 'decline': decline, 'assigned_30d': assigned})
    cohort_median = median([e['assigned_30d'] for e in eligible]) if eligible else 0
    for e in eligible:
        parts = {'distance': min(e['distance_km'] / max(e['radius_km'], 0.01), 1.0),
                 'schedule_slack': 1.0 - min(e['slack_minutes'] / 720.0, 1.0),
                 'decline': e['decline'],
                 'fairness': min(e['assigned_30d'] / (2 * cohort_median + 1), 1.0)}
        e['components'] = {k: round(v, 4) for k, v in parts.items()}
        e['rank_cost'] = round(sum(WEIGHTS[k] * v for k, v in parts.items()), 5)
    # Deterministic tie-break on provider id (documented, FR-MAT-03).
    eligible.sort(key=lambda e: (e['rank_cost'], e['provider_id']))
    return {'candidate_count': len(candidates), 'eligible': eligible, 'exclusions': dict(exclusions)}


def dispatch_round(con: Connection, s: Settings, request_id: str, *, actor: str = 'dispatcher') -> dict:
    """One dispatch step for a request. Idempotent: does nothing while live offers exist."""
    req = one(con, 'SELECT * FROM service_requests WHERE id = :id FOR UPDATE', id=request_id)
    if not req or req['status'] not in ('requested', 'offering'):
        return {'outcome': 'skipped', 'status': req['status'] if req else None}
    booking = one(con, 'SELECT status FROM bookings WHERE request_id = :id', id=request_id)
    if booking and booking['status'] != 'reassigning':
        return {'outcome': 'skipped', 'status': req['status']}
    run(con, '''UPDATE offers SET status = 'expired', responded_at = now()
                WHERE request_id = :r AND status = 'pending' AND expires_at <= now()''', r=request_id)
    if one(con, "SELECT 1 AS ok FROM offers WHERE request_id = :r AND status = 'pending' LIMIT 1", r=request_id):
        return {'outcome': 'offers_live', 'status': req['status']}
    from .requests import set_request_status
    if req['dispatch_round'] >= s.max_dispatch_rounds:
        return _unfulfilled(con, req, actor, 'MAX_ROUNDS_REACHED')
    result = run_matching(con, s, req)
    chosen = result['eligible'][:s.parallel_offers]
    round_no = req['dispatch_round'] + 1
    mr = one(con, '''INSERT INTO matching_runs(request_id, round, config_version, candidate_count, eligible_count,
                         exclusions, ranked, outcome)
                     VALUES (:r, :n, :v, :c, :e, CAST(:x AS jsonb), CAST(:rk AS jsonb), :o) RETURNING id''',
             r=request_id, n=round_no, v=s.matching_config_version, c=result['candidate_count'],
             e=len(result['eligible']), x=json.dumps(result['exclusions']),
             rk=json.dumps([{k: e[k] for k in ('provider_id', 'rank_cost', 'components')} for e in result['eligible']]),
             o='offered' if chosen else 'no_eligible_supply')
    run(con, 'UPDATE service_requests SET dispatch_round = :n, updated_at = now() WHERE id = :id', n=round_no,
        id=request_id)
    if not chosen:
        # FR-MAT-08: honest unfulfilled state; never fabricate availability or silently widen the radius.
        return _unfulfilled(con, req, actor, 'NO_ELIGIBLE_SUPPLY', matching_run_id=str(mr['id']))
    expires = datetime.now(UTC) + timedelta(seconds=s.offer_ttl_seconds)
    for e in chosen:
        offer = one(con, '''INSERT INTO offers(request_id, provider_id, matching_run_id, expires_at, distance_band)
                            VALUES (:r, :p, :m, :x, :b) RETURNING id''', r=request_id, p=e['provider_id'], m=mr['id'],
                    x=expires, b=distance_band(e['distance_km']))
        oid = str(offer['id'])
        enqueue(con, 'offer.expire', {'offer_id': oid}, run_at=expires, dedupe_key=f'offer-expire:{oid}')
        notify(con, recipient_type='user', recipient_id=e['provider_id'], template_key='offer.new',
               dedupe_key=f'offer:{oid}', params={'minutes': s.offer_ttl_seconds // 60})
    if req['status'] == 'requested':
        set_request_status(con, request_id, 'requested', 'offering', 'system', actor)
    audit.record(con, actor_type='system', actor_id=actor, action='request.dispatched', entity_type='service_request',
                 entity_id=request_id, details={'round': round_no, 'offers': len(chosen),
                                                'eligible': len(result['eligible'])})
    return {'outcome': 'offered', 'round': round_no, 'offers': len(chosen), 'matching_run_id': str(mr['id'])}


def _unfulfilled(con: Connection, req: dict, actor: str, reason: str, **extra) -> dict:
    from .requests import set_request_status
    rid = str(req['id'])
    booking = one(con, 'SELECT id FROM bookings WHERE request_id = :id', id=rid)
    if booking:
        # A reassignment that found nobody: keep the booking visible to operations, do not fake a provider.
        run(con, '''INSERT INTO ops_alerts(kind, ref_type, ref_id, severity) VALUES ('reassignment_failed', 'booking',
                    :b, 'urgent') ON CONFLICT DO NOTHING''', b=booking['id'])
        return {'outcome': 'reassignment_failed', 'reason': reason, **extra}
    set_request_status(con, rid, req['status'], 'unfulfilled', 'system', actor, reason)
    notify(con, recipient_type='user', recipient_id=str(req['customer_id']), template_key='request.unfulfilled',
           dedupe_key=f'unfulfilled:{rid}')
    audit.record(con, actor_type='system', actor_id=actor, action='request.unfulfilled', entity_type='service_request',
                 entity_id=rid, details={'reason': reason})
    return {'outcome': 'unfulfilled', 'reason': reason, **extra}
