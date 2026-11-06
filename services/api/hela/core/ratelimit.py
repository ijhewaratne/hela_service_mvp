"""Fixed-window throttling in Redis. Fails closed: if Redis is unreachable, throttled actions are refused."""
from __future__ import annotations

import redis

from .errors import ApiError


def hit(client: redis.Redis, bucket: str, *, limit: int, window_seconds: int) -> None:
    key = f'rl:{bucket}'
    try:
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds, nx=True)
        count, _ = pipe.execute()
    except redis.RedisError:
        raise ApiError(503, 'DEPENDENCY_UNAVAILABLE', 'error.dependency_unavailable', retryable=True) from None
    if count > limit:
        raise ApiError(429, 'RATE_LIMITED', 'error.rate_limited', retryable=True)
