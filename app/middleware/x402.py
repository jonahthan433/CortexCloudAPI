import base64
import json
import logging
from typing import Optional

import httpx
from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.core.config import settings
from app.x402.pricing import ROUTE_PRICING, ROUTE_DESCRIPTIONS, usd_to_usdc_atomic

# Per-route JSON input/output schemas for the x402 v2 Bazaar discovery
# extension. Required by x402scan's validator (SCHEMA_INPUT_MISSING is a hard
# error if the 402 body lacks an input schema).
BAZAAR_SCHEMAS = {
    "/x402/v1/chat/completions": {
        "input": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model id, e.g. gemini/gemini-2.0-flash"},
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string", "enum": ["system", "user", "assistant", "tool"]},
                            "content": {"type": "string"}
                        },
                        "required": ["role", "content"]
                    }
                },
                "stream": {"type": "boolean"},
                "temperature": {"type": "number"},
                "max_tokens": {"type": "integer"}
            },
            "required": ["model", "messages"]
        },
        "output": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "object": {"type": "string"},
                "choices": {"type": "array"},
                "usage": {"type": "object"}
            }
        }
    },
    "/x402/v1/embeddings": {
        "input": {
            "type": "object",
            "properties": {
                "input": {"type": "string", "description": "Text to embed"},
                "model": {"type": "string", "description": "Embedding model id, e.g. gemini/text-embedding-004"}
            },
            "required": ["input"]
        },
        "output": {
            "type": "object",
            "properties": {
                "object": {"type": "string"},
                "data": {"type": "array"},
                "model": {"type": "string"}
            }
        }
    },
}


# Path-only views of ROUTE_PRICING / ROUTE_DESCRIPTIONS so the paywall
# matches by PATH and fires before method routing (x402scan probes
# HEAD/GET/POST/etc, so protected routes must never return 405).
_PATH_PRICING = {
    (k.split(" ", 1)[1] if " " in k else k): v for k, v in ROUTE_PRICING.items()
}
_PATH_DESCRIPTIONS = {
    (k.split(" ", 1)[1] if " " in k else k): v for k, v in ROUTE_DESCRIPTIONS.items()
}

logger = logging.getLogger("cortexcloud.middleware.x402")


class X402PaymentMiddleware(BaseHTTPMiddleware):
    """
    Custom FastAPI Middleware to enforce the x402 payment protocol.
    Challenges requests to protected routes with HTTP 402 if unpaid,
    and validates paid requests using Coinbase's CDP facilitator.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Check if x402 is enabled and merchant wallet is configured
        if not settings.X402_ENABLED or not settings.WALLET_ADDRESS:
            return await call_next(request)

        path = request.url.path
        method = request.method
        route_key = f"{method} {path}"

        # Match by PATH only (method-agnostic paywall). x402scan probes with
        # HEAD/GET/POST/etc; the paywall must fire before method routing so
        # protected routes never return 405. Free routes ($0.00) pass through.
        price_str = _PATH_PRICING.get(path)
        if price_str is None:
            return await call_next(request)

        # Free routes do not require payment (e.g. models list)
        if price_str == "$0.00":
            return await call_next(request)

        # 1. Allow bypass if the request contains a valid API key (Bearer auth)
        # This keeps the existing non-anonymous API key clients working on /v1/ routes
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer ") and not path.startswith("/x402/"):
            # The verify_api_key dependency in route handlers will do the actual validation.
            # We just let it pass this middleware layer.
            return await call_next(request)

        # 2. Get route payment requirements
        price_atomic = usd_to_usdc_atomic(price_str)
        description = _PATH_DESCRIPTIONS.get(path, "CortexCloud API resource access")
        
        # Build the exact x402 v2 PaymentRequirements challenge. This shape is
        # validated by x402scan's PaymentRequiredV2Schema + requires an
        # extensions.bazaar.schema input (SCHEMA_INPUT_MISSING is a hard error).
        resource_url = f"{settings.X402_RESOURCE_BASE}{path}"
        schemas = BAZAAR_SCHEMAS.get(path, {
            "input": {"type": "object", "properties": {}},
            "output": {"type": "object", "properties": {}},
        })
        requirements = {
            "x402Version": 2,
            "resource": {
                "url": resource_url,
                "description": description,
                "mimeType": "application/json",
            },
            "accepts": [
                {
                    "scheme": "exact",
                    "network": settings.X402_NETWORK,
                    "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                    "amount": str(price_atomic),
                    "payTo": settings.WALLET_ADDRESS,
                    "maxTimeoutSeconds": 60,
                    "extra": {
                        "name": "USD Coin",
                        "version": "2",
                    },
                }
            ],
            "extensions": {
                "bazaar": {
                    "schema": {
                        "properties": {
                            "input": {"properties": {"body": schemas["input"]}},
                            "output": {"properties": {"example": schemas["output"]}},
                        }
                    }
                }
            },
        }

        # 3. Check for payment header.
        # x402 SDK v2 clients send "PAYMENT-SIGNATURE"; legacy/agent clients
        # may send "X-PAYMENT". Accept either.
        x_payment = request.headers.get("X-PAYMENT") or request.headers.get(
            "PAYMENT-SIGNATURE"
        )
        if not x_payment:
            logger.info(f"x402: Payment required for {route_key}. Returning 402 challenge.")
            return JSONResponse(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                content=requirements,
                headers={
                    "PAYMENT-REQUIRED": base64.b64encode(json.dumps(requirements).encode()).decode()
                }
            )

        # 4. Present header -> verify and settle via CDP facilitator
        try:
            # Base64-decode the payload
            payload_bytes = base64.b64decode(x_payment)
            payment_payload = json.loads(payload_bytes.decode("utf-8"))
        except Exception as e:
            logger.warning(f"x402: Failed to decode X-PAYMENT header for {route_key}: {e}")
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "Invalid X-PAYMENT header format. Must be base64-encoded JSON PaymentPayload."}
            )

        # Build validation payload for the CDP facilitator.
        # Use the challenge version (v2) so v2 payment payloads validate correctly.
        challenge_version = requirements.get("x402Version", 1)
        validation_body = {
            "x402Version": challenge_version,
            "paymentPayload": payment_payload,
            "paymentRequirements": requirements["accepts"][0]
        }

        verify_url = f"{settings.X402_FACILITATOR_URL}/verify"
        settle_url = f"{settings.X402_FACILITATOR_URL}/settle"

        # Build facilitator auth headers.
        # CDP v2 facilitator requires per-request ES256-signed JWTs via
        # cdp.x402.create_cdp_auth_headers. Fall back to a static Bearer token
        # if only X402_FACILITATOR_API_KEY is set (legacy/compat path).
        facilitator_headers = {"Content-Type": "application/json"}
        _cid = getattr(settings, "X402_FACILITATOR_API_KEY_ID", None)
        _csec = getattr(settings, "X402_FACILITATOR_API_KEY_SECRET", None)
        _cdp_headers = {}
        if _cid and _csec:
            try:
                from cdp.x402.x402 import create_cdp_auth_headers
                _cdp_headers = create_cdp_auth_headers(_cid, _csec)()
            except Exception as exc:
                logger.error(f"x402: failed building CDP facilitator headers: {exc}")
        elif getattr(settings, "X402_FACILITATOR_API_KEY", None):
            facilitator_headers["Authorization"] = (
                f"Bearer {settings.X402_FACILITATOR_API_KEY}"
            )

        async with httpx.AsyncClient(timeout=10.0) as client:
            # A. Call /verify endpoint
            logger.info(f"x402: Verifying transaction signature via CDP facilitator: {verify_url}")
            try:
                verify_res = await client.post(verify_url, json=validation_body, headers={**facilitator_headers, **_cdp_headers.get("verify", {})})
                if verify_res.status_code != 200:
                    logger.warning(f"x402: Facilitator verification failed with status {verify_res.status_code}: {verify_res.text}")
                    return JSONResponse(
                        status_code=status.HTTP_402_PAYMENT_REQUIRED,
                        content={"error": f"Facilitator verification failed: {verify_res.text}"}
                    )
                
                verify_data = verify_res.json()
                # Check for isValid or success flags
                is_valid = verify_data.get("isValid", False) or verify_data.get("success", False)
                if not is_valid:
                    logger.warning(f"x402: Facilitator marked payment payload as invalid: {verify_data}")
                    return JSONResponse(
                        status_code=status.HTTP_402_PAYMENT_REQUIRED,
                        content={"error": "Payment signature is invalid.", "details": verify_data}
                    )
            except Exception as e:
                logger.error(f"x402: Exception during facilitator verification: {e}")
                return JSONResponse(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    content={"error": "CDP facilitator verification network error."}
                )

            # B. Call /settle endpoint
            logger.info(f"x402: Settling transaction on Base chain via CDP facilitator: {settle_url}")
            try:
                settle_res = await client.post(settle_url, json=validation_body, headers={**facilitator_headers, **_cdp_headers.get("settle", {})})
                if settle_res.status_code != 200:
                    logger.warning(f"x402: Facilitator settlement failed with status {settle_res.status_code}: {settle_res.text}")
                    return JSONResponse(
                        status_code=status.HTTP_402_PAYMENT_REQUIRED,
                        content={"error": f"Facilitator settlement failed: {settle_res.text}"}
                    )
                
                settle_data = settle_res.json()
                is_settled = settle_data.get("success", False)
                if not is_settled:
                    logger.warning(f"x402: Facilitator marked settlement as unsuccessful: {settle_data}")
                    return JSONResponse(
                        status_code=status.HTTP_402_PAYMENT_REQUIRED,
                        content={"error": "Payment settlement failed.", "details": settle_data}
                    )
            except Exception as e:
                logger.error(f"x402: Exception during facilitator settlement: {e}")
                return JSONResponse(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    content={"error": "CDP facilitator settlement network error."}
                )

        # 5. Success -> forward request and add X-PAYMENT-RESPONSE header
        logger.info(f"x402: Payment verified and settled successfully for {route_key}.")
        response = await call_next(request)
        
        # Add success payload to response headers
        success_payload = json.dumps({"success": True, "details": settle_data})
        response.headers["X-PAYMENT-RESPONSE"] = base64.b64encode(success_payload.encode()).decode()
        return response
