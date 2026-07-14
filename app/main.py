import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app.api.v1.completions import router as completions_router
from app.api.v1.models import router as models_router
from app.api.dashboard.routes import router as dashboard_router
from app.admin.routes import router as admin_router
from app.activity import router as activity_router
from app.pricing_route import router as pricing_router
from app.core.config import settings
from app.core.logging import setup_logging
from app.core.redis import get_redis_client, close_redis

logger = logging.getLogger("cortexcloud.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Setup structured logging
    setup_logging()
    logger.info("Initializing CortexCloud API Gateway...")

    # 2. Pre-warm tiktoken tokenizers
    from app.usage.tokenizer import pre_warm_tokenizers
    pre_warm_tokenizers()

    # 3. Setup Redis client connection pool
    try:
        redis_client = get_redis_client()
        await redis_client.ping()
        logger.info("Connected to Redis successfully.")
    except Exception as e:
        logger.error(f"Failed to connect to Redis on startup: {str(e)}")

    yield

    # 3. Cleanup on shutdown
    logger.info("Shutting down CortexCloud API Gateway...")
    await close_redis()
    logger.info("Redis connection closed.")


# Initialize FastAPI
CORTEXCLOUD_HOME_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<title>CortexCloud API</title>
<link rel="icon" href="/favicon.ico" />
<link rel="shortcut icon" href="/favicon.ico" />
<meta property="og:title" content="CortexCloud API" />
<meta property="og:description" content="Production-ready OpenAI-compatible AI gateway with x402 payment-gated routes. Agents pay per call in USDC on Base." />
<meta property="og:image" content="https://api.cortexcloud.org/favicon.ico" />
<meta property="og:type" content="website" />
<meta name="twitter:card" content="summary_large_image" />
<meta name="twitter:image" content="https://api.cortexcloud.org/favicon.ico" />
</head><body style="margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#0b1020;color:#e5e7eb;font-family:system-ui,sans-serif">
<div style="max-width:560px;padding:40px;text-align:center">
<img src="/favicon.ico" alt="CortexCloud" style="width:96px;height:96px;border-radius:16px" />
<h1 style="font-size:28px;margin:20px 0 8px">CortexCloud <span style="background:linear-gradient(90deg,#22d3ee,#a855f7);-webkit-background-clip:text;background-clip:text;color:transparent">API</span></h1>
<p style="color:#9ca3af;line-height:1.6">Agent-native AI inference &amp; data gateway. Pay per call in USDC on Base via x402 -- no API key, no subscription.</p>
<p style="color:#9ca3af">Docs: <code style="background:#111827;padding:2px 6px;border-radius:6px;color:#22d3ee">POST /x402/v1/chat/completions</code> · Models: <a href="/x402/v1/models" style="color:#22d3ee">/x402/v1/models</a></p>
</div></body></html>"""

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Production-ready OpenAI-compatible AI gateway and routing layer.",
    version="1.0.0",
    lifespan=lifespan,
)

# x402 discovery: serve our curated OpenAPI spec at /openapi.json (overrides the
# auto-generated one) so x402scan / @agentcash/discovery can verify ownership
# (info.contact.email) and enumerate the payable x402 endpoints.
import json as _json

with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "openapi.json")) as _f:
    _OPENAPI_SPEC = _json.load(_f)

app.openapi = lambda: _OPENAPI_SPEC


# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure Request Correlation Tracing Middleware
from app.middleware.trace import TracingMiddleware
app.add_middleware(TracingMiddleware)


# Health Check
@app.get("/health", status_code=status.HTTP_200_OK, tags=["System"])
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Serve the CortexCloud logo as the favicon (replaces SVG placeholder)."""
    from fastapi.responses import FileResponse
    return FileResponse(
        "static/favicon.jpg",
        media_type="image/jpeg",
        filename="favicon.jpg",
    )


@app.get("/", include_in_schema=False)
async def home():
    """Branded BlockRun-style landing page (static/index.html)."""
    from fastapi.responses import FileResponse
    return FileResponse("/opt/CortexCloudAPI/static/index.html")


async def health_check():
    """Health check endpoint to verify database and caching layers."""
    redis_status = "unhealthy"
    try:
        redis_client = get_redis_client()
        await redis_client.ping()
        redis_status = "healthy"
    except Exception:
        pass

    return {
        "status": "healthy" if redis_status == "healthy" else "degraded",
        "redis": redis_status,
        "gateway": "running"
    }


# Include APIRouters
# 1. OpenAI-compatible endpoints under /v1 prefix
app.include_router(completions_router, prefix="/v1", tags=["OpenAI Compatible Gateway"])
app.include_router(activity_router, prefix="/x402/v1", tags=["Activity Feed"])
app.include_router(pricing_router, prefix="/x402/v1", tags=["Pricing"])
app.include_router(models_router, prefix="/v1", tags=["OpenAI Compatible Registry"])

# 2. Dashboard developer REST APIs under /v1/dashboard prefix
app.include_router(dashboard_router, prefix="/v1/dashboard", tags=["Dashboard Developers API"])

# 3. Administration REST APIs under /v1/admin prefix
app.include_router(admin_router, prefix="/v1/admin", tags=["Gateway Administration API"])

# 4. x402 Payment-Gated Routes (conditionally loaded)
_x402_active = False
if settings.X402_ENABLED and settings.WALLET_ADDRESS:
    try:
        from app.x402.routes import router as x402_router
        from app.middleware.x402 import X402PaymentMiddleware

        # Include x402 routes at /x402/v1 prefix
        app.include_router(x402_router, prefix="/x402/v1", tags=["x402 Payment Gateway"])

        # Include Bazaar discovery + MCP routes at /x402/v1
        from app.x402.bazaar_routes import router as bazaar_router

        app.include_router(bazaar_router, prefix="/x402/v1", tags=["Bazaar Discovery"])

        # Read-only monitoring/status endpoint (free)
        from app.x402.status_routes import router as status_router
        app.include_router(status_router, prefix="/x402/v1", tags=["x402 Status"])

        # Phase B: data marketplace endpoints (x402-gated via middleware)
        from app.x402.data_routes import router as data_router
        app.include_router(data_router, prefix="/x402/v1", tags=["x402 Data Marketplace"])

        # Phase B extension: on-chain Base data (public RPC, keyless)
        from app.x402.onchain_routes import router as onchain_router
        app.include_router(onchain_router, prefix="/x402/v1", tags=["x402 On-Chain Base"])

        # Add per-IP rate limiting for the x402 gateway (before payment middleware)
        from app.middleware.x402_rate_limit import X402RateLimitMiddleware
        app.add_middleware(X402RateLimitMiddleware)

        # Add custom x402 Payment Middleware
        app.add_middleware(X402PaymentMiddleware)

        _x402_active = True
        logger.info("x402 payment gateway middleware and router enabled")

    except Exception as e:
        logger.error(f"Failed to initialize x402 payment gateway: {e}")
elif settings.X402_ENABLED and not settings.WALLET_ADDRESS:
    logger.warning("x402 is enabled but WALLET_ADDRESS is not set — payment routes disabled")



# .well-known discovery endpoint for x402
@app.get("/.well-known/x402.json", tags=["x402 Discovery"])
async def x402_discovery():
    """x402 protocol discovery endpoint. Returns payment configuration metadata."""
    if not _x402_active:
        return {
            "x402": False,
            "message": "x402 payment gateway is not enabled on this instance.",
        }

    return {
        "x402": True,
        "version": 2,
        "facilitator": settings.X402_FACILITATOR_URL,
        "network": settings.X402_NETWORK,
        "wallet": settings.WALLET_ADDRESS,
        "endpoints": {
            "chat_completions": "/x402/v1/chat/completions",
            "embeddings": "/x402/v1/embeddings",
            "models": "/x402/v1/models",
        },
        "docs": "https://github.com/jonahthan433/CortexCloudAPI",
    }

