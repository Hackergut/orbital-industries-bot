"""Simple Redis cache wrapper for LLM and form mapping caching."""
import hashlib
import json
import logging
import os

try:
    import redis
except ImportError:
    redis = None

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "3600"))


class _Cache:
    def __init__(self):
        self._client = None
        if redis is None:
            logger.warning("redis package not installed; caching disabled")
            return
        try:
            self._client = redis.from_url(REDIS_URL, decode_responses=True)
            self._client.ping()
            logger.info("Redis cache connected")
        except Exception as e:
            logger.warning("Redis unavailable (%s); caching disabled", e)
            self._client = None

    def _key(self, prefix: str, data: dict) -> str:
        raw = json.dumps(data, sort_keys=True, default=str)
        return f"orbital:{prefix}:{hashlib.sha256(raw.encode()).hexdigest()[:16]}"

    def get(self, prefix: str, data: dict):
        if not self._client:
            return None
        try:
            key = self._key(prefix, data)
            val = self._client.get(key)
            if val:
                return json.loads(val)
        except Exception:
            pass
        return None

    def set(self, prefix: str, data: dict, value, ttl: int = None):
        if not self._client:
            return
        try:
            key = self._key(prefix, data)
            self._client.setex(key, ttl or CACHE_TTL_SECONDS, json.dumps(value, default=str))
        except Exception:
            pass

    def clear_prefix(self, prefix: str):
        if not self._client:
            return
        try:
            for key in self._client.scan_iter(match=f"orbital:{prefix}:*"):
                self._client.delete(key)
        except Exception:
            pass


cache = _Cache()
