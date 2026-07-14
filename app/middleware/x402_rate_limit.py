"""
Per-IP rate limiting for the x402 payment gateway.

Goal: protect the paid endpoints from abuse / probe floods without throttling
legitimate agents. Uses the existing Redis sliding-window RateLimiter. Runs as
a BaseHTTPMiddleware placed BEFORE X402PaymentMiddleware so it governs the
inbound request rate on the /x402/v1 prefix. Fails open on Redis errors.
"""
import logging
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.middleware.rate_limit import RateLimiter

logger = logging.getLogger("cortexcloud.middleware.x402_ratelimit")

X402_IP_LIMIT = 60          # requests per window
X402_IP_WINDOW = 60         # seconds


class X402RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        # Only govern the x402 gateway surface.
        if not path.startswith("/x402/v1"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "127.0.0.1"
        redis_key = f"ratelimit:x402:ip:{client_ip}"
        try:
            limited = await RateLimiter.is_rate_limited(
                redis_key, X402_IP_LIMIT, X402_IP_WINDOW
            )
        except Exception as e:
            logger.warning(f"x402 rate-limit check failed (fail-open): {e}")
            limited = False

        if limited:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=429,
                content={"error": "x402 gateway rate limit exceeded. Slow down and retry."},
            )

        return await call_next(request)
