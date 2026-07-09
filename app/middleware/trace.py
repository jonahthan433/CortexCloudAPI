import logging
import time
import uuid
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.logging import correlation_id_ctx

logger = logging.getLogger("cortexcloud.middleware.trace")


class TracingMiddleware(BaseHTTPMiddleware):
    """
    Middleware that captures or generates correlation IDs, binds them
    to log contexts, measures latency, and formats unhandled server exceptions.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Extract correlation ID from headers or generate a new one
        correlation_id = request.headers.get("X-Correlation-ID") or request.headers.get("x-correlation-id")
        if not correlation_id:
            correlation_id = str(uuid.uuid4())

        # Bind correlation ID to contextvar for the duration of this request
        token = correlation_id_ctx.set(correlation_id)
        
        start_time = time.perf_counter()
        logger.info(f"Incoming request: {request.method} {request.url.path} from {request.client.host if request.client else 'unknown'}")

        try:
            response = await call_next(request)
            
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            logger.info(f"Outgoing response: {request.method} {request.url.path} - Status: {response.status_code} - Latency: {latency_ms:.2f}ms")
            
            # Inject correlation ID in response header
            response.headers["X-Correlation-ID"] = correlation_id
            return response
            
        except Exception as exc:
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            logger.exception(f"Unhandled gateway exception on {request.method} {request.url.path} - Latency: {latency_ms:.2f}ms - Error: {str(exc)}")
            
            # Return a clean production-ready JSON error response
            err_response = {
                "error": {
                    "message": "Internal Server Error. Please contact support with this Correlation ID.",
                    "type": "api_error",
                    "code": status.HTTP_500_INTERNAL_SERVER_ERROR,
                    "correlation_id": correlation_id
                }
            }
            
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content=err_response,
                headers={"X-Correlation-ID": correlation_id}
            )
            
        finally:
            # Clear contextvar token
            correlation_id_ctx.reset(token)
