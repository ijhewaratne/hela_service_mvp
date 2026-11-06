"""Customer addresses and beneficiaries (FR-LOC-01..03, FR-BEN-01, Q17, Q20). Exact location and contact
details are encrypted; only the owner sees them, masked where possible."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from ..audit import service as audit
from ..authz.deps import UserPrincipal, current_user
from ..core.db import many, one, run, tx
from ..core.errors import ApiError
from ..core.services import Services, services
from ..identity.routes import normalize_phone

router = APIRouter(prefix='/v1', tags=['customer'])

MATCH_GRID_DEGREES = 0.005  # ~550 m: enough for distance bands and region checks, too coarse to locate a home


def coarse(value: float) -> float:
    return round(round(value / MATCH_GRID_DEGREES) * MATCH_GRID_DEGREES, 6)


class AddressIn(BaseModel):
    label: str = Field(min_length=1, max_length=40)
    address_text: str = Field(min_length=5, max_length=300)
    lat: float = Field(ge=5.5, le=10.0)    # Sri Lanka bounding box; region check follows
    lon: float = Field(ge=79.4, le=82.0)
    pin_confirmed: bool

    @field_validator('pin_confirmed')
    @classmethod
    def must_confirm(cls, v: bool) -> bool:
        if not v:
            raise ValueError('The map pin must be confirmed by the customer')
        return v


class BeneficiaryIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    phone: str = Field(min_length=8, max_length=24)
    relationship: str = Field(min_length=2, max_length=40)

    @field_validator('phone')
    @classmethod
    def _phone(cls, v: str) -> str:
        return normalize_phone(v)


def customer(user: UserPrincipal = Depends(current_user)) -> UserPrincipal:
    user.require_persona('customer')
    return user


@router.post('/addresses', status_code=201)
def add_address(body: AddressIn, user: UserPrincipal = Depends(customer), svc: Services = Depends(services)):
    lat, lon = coarse(body.lat), coarse(body.lon)
    with tx(svc.engine) as con:
        region = one(con, '''SELECT id, name FROM service_regions
                             WHERE status = 'enabled' AND ST_Covers(boundary, ST_MakePoint(:lon, :lat)::geography)
                             LIMIT 1''', lat=lat, lon=lon)
        if not region:
            # FR-LOC-02 / AT-029: refuse clearly rather than accept an address we cannot serve.
            raise ApiError(422, 'UNSUPPORTED_LOCALITY', 'customer.unsupported_locality')
        row = one(con, '''INSERT INTO customer_addresses(owner_id, label, address_enc, pin_enc, match_point,
                                                         region_id, area_label)
                          VALUES (:o, :l, :a, :p, ST_MakePoint(:lon, :lat)::geography, :r, :area) RETURNING id''',
                  o=user.user_id, l=body.label,
                  a=svc.cipher.encrypt(body.address_text, context='customer_addresses.address'),
                  p=svc.cipher.encrypt(f'{body.lat:.6f},{body.lon:.6f}', context='customer_addresses.pin'),
                  lon=lon, lat=lat, r=region['id'], area=region['name'])
        audit.record(con, actor_type='user', actor_id=user.user_id, action='address.added',
                     entity_type='customer_address', entity_id=str(row['id']))
    return {'id': str(row['id']), 'label': body.label, 'area_label': region['name']}


@router.get('/addresses')
def list_addresses(user: UserPrincipal = Depends(customer), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        rows = many(con, '''SELECT id, label, address_enc, area_label, created_at FROM customer_addresses
                            WHERE owner_id = :o AND archived_at IS NULL ORDER BY created_at''', o=user.user_id)
    return [{'id': str(r['id']), 'label': r['label'], 'area_label': r['area_label'],
             'address_text': svc.cipher.decrypt(r['address_enc'], context='customer_addresses.address')}
            for r in rows]


@router.delete('/addresses/{address_id}', status_code=204)
def archive_address(address_id: UUID, user: UserPrincipal = Depends(customer), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        # Archived, not deleted: past bookings still reference it (retention per DEC-09).
        if not run(con, '''UPDATE customer_addresses SET archived_at = now()
                           WHERE id = :id AND owner_id = :o AND archived_at IS NULL''', id=address_id, o=user.user_id):
            raise ApiError(404, 'NOT_FOUND', 'error.not_found')
        audit.record(con, actor_type='user', actor_id=user.user_id, action='address.archived',
                     entity_type='customer_address', entity_id=str(address_id))


@router.post('/beneficiaries', status_code=201)
def add_beneficiary(body: BeneficiaryIn, user: UserPrincipal = Depends(customer), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        row = one(con, '''INSERT INTO beneficiaries(owner_id, name_enc, phone_enc, phone_last3, relationship)
                          VALUES (:o, :n, :p, :l3, :r) RETURNING id''', o=user.user_id,
                  n=svc.cipher.encrypt(body.name, context='beneficiaries.name'),
                  p=svc.cipher.encrypt(body.phone, context='beneficiaries.phone'), l3=body.phone[-3:],
                  r=body.relationship)
        audit.record(con, actor_type='user', actor_id=user.user_id, action='beneficiary.added',
                     entity_type='beneficiary', entity_id=str(row['id']))
    return {'id': str(row['id']), 'phone_masked': f'*******{body.phone[-3:]}', 'relationship': body.relationship}


@router.get('/beneficiaries')
def list_beneficiaries(user: UserPrincipal = Depends(customer), svc: Services = Depends(services)):
    with tx(svc.engine) as con:
        rows = many(con, '''SELECT id, name_enc, phone_last3, relationship FROM beneficiaries
                            WHERE owner_id = :o AND archived_at IS NULL ORDER BY created_at''', o=user.user_id)
    # Q20: after entry the booker sees only the last three digits of the beneficiary's number.
    return [{'id': str(r['id']), 'name': svc.cipher.decrypt(r['name_enc'], context='beneficiaries.name'),
             'phone_masked': f"*******{r['phone_last3']}", 'relationship': r['relationship']} for r in rows]
