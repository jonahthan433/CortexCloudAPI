import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.completions import router as completions_router
from app.api.v1.models import router as models_router
from app.api.dashboard.routes import router as dashboard_router
from app.admin.routes import router as admin_router
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
app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Production-ready OpenAI-compatible AI gateway and routing layer.",
    version="1.0.0",
    lifespan=lifespan,
)

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
app.include_router(models_router, prefix="/v1", tags=["OpenAI Compatible Registry"])

# 2. Dashboard developer REST APIs under /v1/dashboard prefix
app.include_router(dashboard_router, prefix="/v1/dashboard", tags=["Dashboard Developers API"])

# 3. Administration REST APIs under /v1/admin prefix
app.include_router(admin_router, prefix="/v1/admin", tags=["Gateway Administration API"])
