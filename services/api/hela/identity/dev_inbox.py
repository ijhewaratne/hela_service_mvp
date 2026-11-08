"""Sandbox SMS inbox (Q8). Mounted only when env == 'dev' AND HELA_DEV_INBOX_TOKEN is set."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from pydantic import Field

from ..core.db import one, tx
from ..core.errors import ApiError
from ..core.schema import Input
from ..core.services import Services, services
from ..security.crypto import constant_time_equal
from .routes import normalize_phone

router = APIRouter(prefix='/v1/dev', tags=['dev-only'], include_in_schema=False)


class InboxQuery(Input):
    phone: str = Field(min_length=8, max_length=24)


# POST with a JSON body so the phone number never appears in URLs or access logs.
@router.post('/sms-inbox/latest')
def latest_message(query: InboxQuery, x_dev_inbox_token: str | None = Header(None), svc: Services = Depends(services)):
    expected = svc.settings.dev_inbox_token
    if svc.settings.env != 'dev' or not expected or not x_dev_inbox_token \
            or not constant_time_equal(x_dev_inbox_token, expected):
        raise ApiError(404, 'NOT_FOUND', 'error.not_found')
    try:
        lookup = svc.cipher.lookup_candidates(normalize_phone(query.phone), purpose='phone')
    except ValueError:
        raise ApiError(404, 'NOT_FOUND', 'error.not_found') from None
    with tx(svc.engine) as con:
        row = one(con, '''SELECT body, created_at FROM dev_sms_inbox WHERE phone_lookup = ANY(:p)
                          AND created_at > now() - interval '10 minutes' ORDER BY id DESC LIMIT 1''', p=lookup)
    if not row:
        raise ApiError(404, 'NOT_FOUND', 'error.not_found')
    return row
