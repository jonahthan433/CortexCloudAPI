"""
Read-only monitoring / status endpoint for the x402 gateway.
Free (no payment required). Useful for healthchecks, dashboards, and
x402scan-style liveness probes. Exposes request/payment tallies kept in
Redis plus gateway + wallet + provider-health signals.
"""
import time
import logging
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database.session import get_db
from app.core.redis import get_redis_client
from app.services.models import ModelRegistryService

logger = logging.getLogger("cortexcloud.x402.status")
router = APIRouter()


@router.get("/status")
async def x402_status(db: AsyncSession = Depends(get_db)):
    """Public, free gateway status + lightweight metrics."""
    started = time.perf_counter()
    redis_status = "unhealthy"
    model_count = -1
    payment_tally = None
    try:
        rc = get_redis_client()
        await rc.ping()
        redis_status = "healthy"
        # Aggregate x402 payment tallies recorded by the middleware.
        keys = await rc.keys("x402:pay:*")
        total = 0
        for k in keys:
            try:
                total += int(await rc.get(k) or 0)
            except Exception:
                pass
        payment_tally = {"settled_payments": total, "keys": len(keys)}
    except Exception as e:
        logger.warning(f"status: redis/tally check failed: {e}")

    try:
        models = await ModelRegistryService.get_active_models(db)
        model_count = len(models)
    except Exception as e:
        logger.warning(f"status: model count failed: {e}")

    return JSONResponse({
        "gateway": "cortexcloud-x402",
        "status": "ok" if redis_status == "healthy" else "degraded",
        "x402_enabled": bool(settings.X402_ENABLED and settings.WALLET_ADDRESS),
        "network": settings.X402_NETWORK,
        "merchant_wallet": settings.WALLET_ADDRESS,
        "models_available": model_count,
        "redis": redis_status,
        "payment_tally": payment_tally,
        "latency_ms": int((time.perf_counter() - started) * 1000),
    })
