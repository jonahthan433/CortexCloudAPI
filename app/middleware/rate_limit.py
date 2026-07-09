import time
import secrets
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import verify_api_key
from app.core.redis import get_redis_client
from app.database.session import get_db
from app.models.key import APIKey
from app.models.usage import UsageLog

logger = logging.getLogger("cortexcloud.middleware.rate_limit")


class RateLimiter:
    """
    Redis-backed rate limiting engine.
    Supports sliding-window minute limits (IP & API Key),
    and Daily/Monthly usage caps with database-primed fallbacks.
    """

    @staticmethod
    async def is_rate_limited(key: str, limit: int, window_seconds: int) -> bool:
        """Enforces a sliding window rate limit using Redis."""
        try:
            redis = get_redis_client()
            current_time = time.time()
            clear_before = current_time - window_seconds
            # Use random suffix to prevent key conflicts under heavy concurrency in the same millisecond
            member_id = f"{current_time}:{secrets.token_hex(4)}"

            # Use sorted sets for precise sliding window rate limiting
            async with redis.pipeline(transaction=True) as pipe:
                # 1. Remove old requests outside the sliding window
                pipe.zremrangebyscore(key, 0, clear_before)
                # 2. Count remaining requests in the window
                pipe.zcard(key)
                # 3. Add current request
                pipe.zadd(key, {member_id: current_time})
                # 4. Set expiry to clean up inactive keys
                pipe.expire(key, window_seconds + 5)
                
                results = await pipe.execute()
                count = results[1]  # ZCARD result

            return count >= limit
        except Exception as e:
            logger.error(f"Redis rate limiter failed for key '{key}': {str(e)}. Fail-open allowed request.")
            return False

    @staticmethod
    async def enforce_ip_limit(request: Request, limit: int = 60, window: int = 60):
        """Limit request rates based on client IP. Used on public auth/dashboard endpoints."""
        client_ip = request.client.host if request.client else "127.0.0.1"
        redis_key = f"ratelimit:ip:{client_ip}"
        
        limited = await RateLimiter.is_rate_limited(redis_key, limit, window)
        if limited:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="IP rate limit exceeded. Please try again later.",
            )

    @staticmethod
    async def check_api_key_limits(
        request: Request,
        api_key: APIKey = Depends(verify_api_key),
        db: AsyncSession = Depends(get_db)
    ) -> APIKey:
        """
        FastAPI dependency enforcing minute rate limits, daily limits,
        and monthly limits for the authenticated API key.
        """
        redis = get_redis_client()
        key_id = str(api_key.id)

        # 1. Minute Rate Limit (e.g. Default 200 requests/minute, customizable in permissions)
        minute_limit = api_key.permissions.get("rate_limit_rpm", 200)
        redis_key = f"ratelimit:key:{key_id}:minute"
        
        limited = await RateLimiter.is_rate_limited(redis_key, minute_limit, 60)
        if limited:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="API key minute rate limit exceeded.",
            )

        # 2. Daily Limits
        if api_key.daily_limit is not None:
            daily_key = f"ratelimit:key:{key_id}:daily"
            try:
                current_daily = await redis.get(daily_key)

                if current_daily is None:
                    # Cache miss/eviction: Query database for today's logs to prime Redis
                    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                    result = await db.execute(
                        select(func.count(UsageLog.id))
                        .filter(UsageLog.api_key_id == api_key.id, UsageLog.created_at >= today_start)
                    )
                    db_count = result.scalar() or 0
                    current_daily = db_count
                    
                    # Set TTL to expire at the end of the day
                    seconds_to_midnight = int((datetime.now(timezone.utc).replace(hour=23, minute=59, second=59) - datetime.now(timezone.utc)).total_seconds())
                    await redis.set(daily_key, db_count, ex=max(seconds_to_midnight, 60))
                else:
                    current_daily = int(current_daily)

                if current_daily >= api_key.daily_limit:
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail="API key daily request limit reached.",
                    )
                
                # Increment daily request count in Redis
                await redis.incr(daily_key)
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Redis daily limit check failed for key '{key_id}': {str(e)}. Falling back to database check.")
                # Database fallback for daily limits
                today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                result = await db.execute(
                    select(func.count(UsageLog.id))
                    .filter(UsageLog.api_key_id == api_key.id, UsageLog.created_at >= today_start)
                )
                db_count = result.scalar() or 0
                if db_count >= api_key.daily_limit:
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail="API key daily request limit reached.",
                    )

        # 3. Monthly Limits
        if api_key.monthly_limit is not None:
            monthly_key = f"ratelimit:key:{key_id}:monthly"
            try:
                current_monthly = await redis.get(monthly_key)

                if current_monthly is None:
                    # Prime from database: count requests for the current calendar month
                    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                    result = await db.execute(
                        select(func.count(UsageLog.id))
                        .filter(UsageLog.api_key_id == api_key.id, UsageLog.created_at >= month_start)
                    )
                    db_count = result.scalar() or 0
                    current_monthly = db_count
                    
                    # Set TTL to 30 days or remaining days in month
                    await redis.set(monthly_key, db_count, ex=30 * 86400)
                else:
                    current_monthly = int(current_monthly)

                if current_monthly >= api_key.monthly_limit:
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail="API key monthly request limit reached.",
                    )
                
                # Increment monthly count
                await redis.incr(monthly_key)
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Redis monthly limit check failed for key '{key_id}': {str(e)}. Falling back to database check.")
                # Database fallback for monthly limits
                month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                result = await db.execute(
                    select(func.count(UsageLog.id))
                    .filter(UsageLog.api_key_id == api_key.id, UsageLog.created_at >= month_start)
                )
                db_count = result.scalar() or 0
                if db_count >= api_key.monthly_limit:
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail="API key monthly request limit reached.",
                    )

        return api_key
