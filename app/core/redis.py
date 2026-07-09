from typing import Optional
import redis.asyncio as redis

from app.core.config import settings

# Global async Redis client
redis_client: Optional[redis.Redis] = None


def get_redis_client() -> redis.Redis:
    """Get or initialize the global async Redis client."""
    global redis_client
    if redis_client is None:
        redis_client = redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=50,  # Pool sizing for gateway scale
        )
    return redis_client


async def close_redis() -> None:
    """Close the global async Redis connection pool."""
    global redis_client
    if redis_client is not None:
        await redis_client.aclose()
        redis_client = None
