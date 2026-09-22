# backend/core/redis_client.py

"""
Redis connection factory (Day 80).

A thin wrapper so nothing else in the codebase imports the redis
package directly - if the client library or connection details ever
change, this is the one place to touch. decode_responses=True so every
caller works with plain str, matching the rest of the codebase (no
callers should ever see bytes).
"""

import redis

from core.config import settings


def make_redis_client(url: str = None) -> redis.Redis:
    return redis.Redis.from_url(url or settings.redis_url, decode_responses=True)
