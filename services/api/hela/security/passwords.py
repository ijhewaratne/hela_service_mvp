"""Admin password hashing (argon2id) and TOTP verification with replay protection (Q10)."""
from __future__ import annotations

import time

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # argon2id with library-recommended parameters
# Verified against when the username is unknown, so response time does not reveal which usernames exist.
_DUMMY_HASH = _hasher.hash('not-a-real-password-used-for-timing-only')

MIN_PASSWORD_LENGTH = 14


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f'Admin passwords must be at least {MIN_PASSWORD_LENGTH} characters')
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash or _DUMMY_HASH, password) and stored_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def new_totp_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name='Hela Service Admin (dev)')


def matching_totp_step(secret: str, code: str, *, at: float | None = None, window: int = 1) -> int | None:
    """Return the 30-second time step the code matches (for replay tracking), or None."""
    if not code.isdigit() or len(code) != 6:
        return None
    totp = pyotp.TOTP(secret)
    now = at if at is not None else time.time()
    for offset in range(-window, window + 1):
        t = now + offset * totp.interval
        if pyotp.utils.strings_equal(totp.at(t), code):
            return int(t // totp.interval)
    return None
