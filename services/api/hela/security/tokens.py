"""Short-lived signed access tokens. Every request also checks the server-side session (revocation)."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt

Audience = Literal['hela-app', 'hela-admin']
ALGORITHM = 'HS256'


def issue_access_token(*, secret: str, issuer: str, audience: Audience, subject: str, session_id: str,
                       minutes: int) -> str:
    now = datetime.now(UTC)
    claims = {'iss': issuer, 'aud': audience, 'sub': subject, 'sid': session_id, 'iat': now,
              'nbf': now, 'exp': now + timedelta(minutes=minutes), 'jti': uuid.uuid4().hex}
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


def decode_access_token(token: str, *, secret: str, issuer: str, audience: Audience) -> dict:
    """Raises jwt.InvalidTokenError on any signature, expiry, issuer or audience problem."""
    return jwt.decode(token, secret, algorithms=[ALGORITHM], issuer=issuer, audience=audience,
                      options={'require': ['exp', 'iat', 'nbf', 'iss', 'aud', 'sub', 'sid']})
