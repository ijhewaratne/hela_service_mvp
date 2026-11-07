"""Short-lived signed access tokens. Every request also checks the server-side session (revocation).

Tokens are signed with Ed25519 (EdDSA) and name their signing key in the `kid` header, so the signing key can be
rotated: the new key signs, the previous public key keeps verifying until the last token it signed has expired.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt

from .keys import SigningKeys

Audience = Literal['hela-app', 'hela-admin']
ALGORITHM = 'EdDSA'


def issue_access_token(*, keys: SigningKeys, issuer: str, audience: Audience, subject: str, session_id: str,
                       minutes: int) -> str:
    now = datetime.now(UTC)
    claims = {'iss': issuer, 'aud': audience, 'sub': subject, 'sid': session_id, 'iat': now,
              'nbf': now, 'exp': now + timedelta(minutes=minutes), 'jti': uuid.uuid4().hex}
    return jwt.encode(claims, keys.private, algorithm=ALGORITHM, headers={'kid': keys.active})


def decode_access_token(token: str, *, keys: SigningKeys, issuer: str, audience: Audience) -> dict:
    """Raises jwt.InvalidTokenError on any signature, key id, expiry, issuer or audience problem."""
    header = jwt.get_unverified_header(token)
    if header.get('alg') != ALGORITHM:
        raise jwt.InvalidAlgorithmError('Unexpected token algorithm')
    kid = header.get('kid')
    public = keys.public.get(kid) if isinstance(kid, str) else None
    if public is None:
        raise jwt.InvalidTokenError('Unknown signing key')
    return jwt.decode(token, public, algorithms=[ALGORITHM], issuer=issuer, audience=audience,
                      options={'require': ['exp', 'iat', 'nbf', 'iss', 'aud', 'sub', 'sid']})
