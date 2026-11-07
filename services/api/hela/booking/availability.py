"""Provider service area, weekly hours (Asia/Colombo) and blackouts (P04, Q43), plus the schedule checks
matching uses. Times are stored as UTC instants; weekly hours are local wall-clock times."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import Field, model_validator
from sqlalchemy import Connection

from ..audit import service as audit
from ..authz.deps import UserPrincipal, current_user
from ..core.db import many, one, run, tx
from ..core.schema import Input
from ..core.services import Services, services
from ..customers.routes import coarse

router = APIRouter(prefix='/v1/provider', tags=['provider-availability'])


class ServiceAreaIn(Input):
    lat: float = Field(ge=5.5, le=10.0)
    lon: float = Field(ge=79.4, le=82.0)
    radius_km: float = Field(gt=0, le=25)


class Slot(Input):
    weekday: int = Field(ge=0, le=6)
    start_local: time
    end_local: time

    @model_validator(mode='after')
    def ordered(self):
        if self.end_local <= self.start_local:
            raise ValueError('end_local must be after start_local (no overnight slots)')
        return self


class WeeklyIn(Input):
    slots: list[Slot] = Field(max_length=28)


class BlackoutIn(Input):
    starts_at: datetime
    ends_at: datetime

    @model_validator(mode='after')
    def ordered(self):
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ValueError('Timestamps need a timezone offset')
        if self.ends_at <= self.starts_at:
            raise ValueError('ends_at must be after starts_at')
        return self


def provider(user: UserPrincipal = Depends(current_user)) -> UserPrincipal:
    user.require_persona('provider')
    return user


@router.put('/service-area')
def set_service_area(body: ServiceAreaIn, user: UserPrincipal = Depends(provider), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        run(con, '''INSERT INTO provider_service_areas(provider_id, base_point, radius_km)
                    VALUES (:p, ST_MakePoint(:lon, :lat)::geography, :r)
                    ON CONFLICT (provider_id) DO UPDATE SET base_point = EXCLUDED.base_point,
                        radius_km = EXCLUDED.radius_km, updated_at = now()''',
            p=user.user_id, lon=coarse(body.lon), lat=coarse(body.lat), r=body.radius_km)
        audit.record(con, actor_type='user', actor_id=user.user_id, action='provider.service_area_set',
                     entity_type='provider', entity_id=user.user_id, details={'radius_km': body.radius_km})
    return {'radius_km': body.radius_km}


@router.put('/availability')
def set_weekly(body: WeeklyIn, user: UserPrincipal = Depends(provider), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        run(con, 'DELETE FROM provider_weekly_availability WHERE provider_id = :p', p=user.user_id)
        for s in body.slots:
            run(con, '''INSERT INTO provider_weekly_availability(provider_id, weekday, start_local, end_local)
                        VALUES (:p, :d, :s, :e)''', p=user.user_id, d=s.weekday, s=s.start_local, e=s.end_local)
        audit.record(con, actor_type='user', actor_id=user.user_id, action='provider.availability_set',
                     entity_type='provider', entity_id=user.user_id, details={'slots': len(body.slots)})
    return {'slots': len(body.slots)}


@router.post('/blackouts', status_code=201)
def add_blackout(body: BlackoutIn, user: UserPrincipal = Depends(provider), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        row = one(con, '''INSERT INTO provider_blackouts(provider_id, period) VALUES (:p, tstzrange(:s, :e, '[)'))
                          RETURNING id''', p=user.user_id, s=body.starts_at, e=body.ends_at)
    return {'id': str(row['id'])}


@router.get('/availability')
def get_availability(user: UserPrincipal = Depends(provider), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        slots = many(con, '''SELECT weekday, start_local, end_local FROM provider_weekly_availability
                             WHERE provider_id = :p ORDER BY weekday, start_local''', p=user.user_id)
        blackouts = many(con, '''SELECT id, lower(period) AS starts_at, upper(period) AS ends_at FROM provider_blackouts
                                 WHERE provider_id = :p AND upper(period) > now() ORDER BY lower(period)''',
                         p=user.user_id)
        area = one(con, 'SELECT radius_km FROM provider_service_areas WHERE provider_id = :p', p=user.user_id)
    return {'timezone': svc.settings.local_timezone, 'slots': slots,
            'blackouts': [{**b, 'id': str(b['id'])} for b in blackouts],
            'service_radius_km': float(area['radius_km']) if area else None}


# --- checks used by matching and acceptance -----------------------------------------------------------
# Loaders take many providers at once; the rule itself (slack_from) is pure and shared by both paths.

def load_schedules(con: Connection, provider_ids: list[str], start: datetime, end: datetime,
                   tz: str) -> dict[str, list[dict] | None]:
    """Weekly slots on the job's local weekday per provider; None for a provider with an overlapping blackout."""
    if not provider_ids:
        return {}
    weekday = start.astimezone(ZoneInfo(tz)).weekday()
    out: dict[str, list[dict] | None] = {pid: [] for pid in provider_ids}
    for r in many(con, '''SELECT provider_id, start_local, end_local FROM provider_weekly_availability
                           WHERE provider_id = ANY(CAST(:ids AS uuid[])) AND weekday = :d''', ids=provider_ids, d=weekday):
        out[str(r['provider_id'])].append(r)
    for r in many(con, '''SELECT DISTINCT provider_id FROM provider_blackouts WHERE provider_id = ANY(CAST(:ids AS uuid[]))
                           AND period && tstzrange(:s, :e, '[)')''', ids=provider_ids, s=start, e=end):
        out[str(r['provider_id'])] = None
    return out


def slack_from(slots: list[dict] | None, start: datetime, end: datetime, tz: str) -> int | None:
    """Minutes of weekly-slot margin around the window, or None if no single slot covers it (or a blackout
    overlaps). Windows that cross or end at local midnight are never covered (no overnight work in R1)."""
    zone = ZoneInfo(tz)
    ls, le = start.astimezone(zone), end.astimezone(zone)
    if slots is None or ls.date() != le.date():
        return None
    best = None
    day = datetime.combine(ls.date(), time(0), zone)
    for slot in slots:
        if slot['start_local'] <= ls.time() and slot['end_local'] >= le.time():
            slack = (ls - (day + _td(slot['start_local']))) + ((day + _td(slot['end_local'])) - le)
            minutes = int(slack.total_seconds() // 60)
            best = minutes if best is None else max(best, minutes)
    return best


def _td(t: time) -> timedelta:
    return timedelta(hours=t.hour, minutes=t.minute, seconds=t.second)


def busy_providers(con: Connection, provider_ids: list[str], start: datetime, end: datetime,
                   buffer_minutes: int) -> set[str]:
    """Providers holding a live assignment that overlaps [start, end + buffer)."""
    if not provider_ids:
        return set()
    return {str(r['provider_id']) for r in many(con, '''SELECT DISTINCT provider_id FROM booking_assignments
                WHERE provider_id = ANY(CAST(:ids AS uuid[])) AND active AND busy && tstzrange(:s, :e, '[)')''',
                ids=provider_ids, s=start, e=end + timedelta(minutes=buffer_minutes))}
