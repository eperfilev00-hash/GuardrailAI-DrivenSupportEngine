"""Redis client for caching and session management."""

import json
from typing import Optional

import redis.asyncio as redis

from app.config import settings


class RedisClient:
    """Async Redis client wrapper."""

    def __init__(self):
        self.client: redis.Redis | None = None

    async def connect(self):
        """Establish Redis connection."""
        self.client = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )

    async def close(self):
        """Close Redis connection."""
        if self.client:
            await self.client.close()

    async def get(self, key: str) -> Optional[str]:
        """Get value from cache."""
        if self.client:
            return await self.client.get(key)
        return None

    async def set(self, key: str, value: str, expire: int = 3600):
        """Set value in cache with expiration."""
        if self.client:
            await self.client.setex(key, expire, value)

    async def get_json(self, key: str) -> Optional[dict]:
        """Get JSON value from cache."""
        data = await self.get(key)
        return json.loads(data) if data else None

    async def set_json(self, key: str, value: dict, expire: int = 3600):
        """Set JSON value in cache."""
        await self.set(key, json.dumps(value), expire)

    async def delete(self, key: str) -> bool:
        """Delete key from cache."""
        if self.client:
            return await self.client.delete(key) > 0
        return False


# Global Redis client
redis_client = RedisClient()