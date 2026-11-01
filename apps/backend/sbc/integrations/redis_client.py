from __future__ import annotations

from functools import lru_cache

import redis

from sbc.config import get_settings


@lru_cache
def get_redis() -> redis.Redis | None:
    url = get_settings().redis_url
    if not url:
        return None
    return redis.Redis.from_url(url, decode_responses=True, socket_timeout=5)


def redis_ok() -> bool:
    r = get_redis()
    if r is None:
        return False
    try:
        return bool(r.ping())
    except redis.RedisError:
        return False
