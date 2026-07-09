"""
x402 middleware factory — builds the PaymentMiddlewareASGI configuration.

Wires up:
  1. HTTPFacilitatorClient → external facilitator for verify/settle
  2. ExactEvmServerScheme → registered for the configured network
  3. Route configs with pricing from pricing.py
  4. Bazaar discovery extension metadata for x402scan discoverability
"""

import logging
from typing import Optional

from app.core.config import settings
from app.x402.pricing import ROUTE_PRICING, ROUTE_DESCRIPTIONS

logger = logging.getLogger("cortexcloud.x402")


def build_x402_middleware_config() -> Optional[tuple]:
    """
    Build x402 middleware components.

    Returns:
        Tuple of (routes_dict, x402ResourceServer) ready to pass to
        PaymentMiddlewareASGI, or None if x402 is disabled / misconfigured.
    """
    if not settings.X402_ENABLED:
        logger.info("x402 payments disabled (X402_ENABLED=false)")
        return None

    if not settings.WALLET_ADDRESS:
        logger.warning(
            "x402 payments disabled: WALLET_ADDRESS not set. "
            "Set WALLET_ADDRESS in .env to enable crypto payments."
        )
        return None

    try:
        from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
        from x402.http.types import RouteConfig
        from x402.mechanisms.evm.exact import ExactEvmServerScheme
        from x402.server import x402ResourceServer
    except ImportError as e:
        logger.error(
            f"x402 package not installed: {e}. "
            "Run: pip install 'x402[fastapi]'"
        )
        return None

    # 1. Create facilitator client
    facilitator = HTTPFacilitatorClient(
        FacilitatorConfig(url=settings.X402_FACILITATOR_URL)
    )

    # 2. Create resource server and register EVM scheme
    server = x402ResourceServer(facilitator)
    server.register(settings.X402_NETWORK, ExactEvmServerScheme())

    # 3. Build route configs from pricing
    routes: dict[str, RouteConfig] = {}

    for route_key, price in ROUTE_PRICING.items():
        # Skip free routes — no payment needed
        if price == "$0.00":
            continue

        description = ROUTE_DESCRIPTIONS.get(route_key, "CortexCloud AI API endpoint")

        # Build Bazaar discovery extension metadata
        bazaar_extensions = _build_bazaar_extension(route_key, description)

        routes[route_key] = RouteConfig(
            accepts=[
                PaymentOption(
                    scheme="exact",
                    pay_to=settings.WALLET_ADDRESS,
                    price=price,
                    network=settings.X402_NETWORK,
                ),
            ],
            mime_type="application/json",
            description=description,
            service_name="CortexCloud API",
            tags=["ai", "llm", "openai-compatible", "gateway"],
            extensions=bazaar_extensions,
        )

    logger.info(
        f"x402 payment middleware configured: "
        f"{len(routes)} paid routes, "
        f"network={settings.X402_NETWORK}, "
        f"facilitator={settings.X402_FACILITATOR_URL}, "
        f"wallet={settings.WALLET_ADDRESS[:10]}..."
    )

    return routes, server


def _build_bazaar_extension(route_key: str, description: str) -> dict:
    """Build Bazaar discovery extension metadata for a route."""
    try:
        from x402.extensions.bazaar import declare_discovery_extension, OutputConfig
    except ImportError:
        # Bazaar extension not available — return empty
        return {}

    method, path = route_key.split(" ", 1)

    ext = {}
    if "chat/completions" in path or "responses" in path:
        ext = declare_discovery_extension(
            input={
                "model": "gemini-2.5-flash",
                "messages": [{"role": "user", "content": "Hello"}],
            },
            input_schema={
                "type": "object",
                "properties": {
                    "model": {
                        "type": "string",
                        "description": "Model ID to use for the completion",
                    },
                    "messages": {
                        "type": "array",
                        "description": "Array of message objects with role and content",
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {"type": "string", "enum": ["system", "user", "assistant"]},
                                "content": {"type": "string"},
                            },
                        },
                    },
                    "stream": {
                        "type": "boolean",
                        "description": "Whether to stream the response",
                    },
                    "temperature": {
                        "type": "number",
                        "description": "Sampling temperature (0-2)",
                    },
                    "max_tokens": {
                        "type": "integer",
                        "description": "Maximum tokens to generate",
                    },
                },
                "required": ["model", "messages"],
            },
            body_type="json",
            output=OutputConfig(
                example={
                    "id": "chatcmpl-abc123",
                    "object": "chat.completion",
                    "model": "gemini-2.5-flash",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "Hello! How can I help you?"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
                },
                schema={
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "object": {"type": "string"},
                        "model": {"type": "string"},
                        "choices": {"type": "array"},
                        "usage": {"type": "object"},
                    },
                },
            ),
        )
    elif "embeddings" in path:
        ext = declare_discovery_extension(
            input={
                "model": "text-embedding-3-small",
                "input": "Hello world",
            },
            input_schema={
                "type": "object",
                "properties": {
                    "model": {"type": "string", "description": "Embedding model ID"},
                    "input": {
                        "type": ["string", "array"],
                        "description": "Text or array of texts to embed",
                    },
                },
                "required": ["model", "input"],
            },
            body_type="json",
            output=OutputConfig(
                example={
                    "object": "list",
                    "data": [{"object": "embedding", "index": 0, "embedding": [0.0023, -0.009]}],
                    "model": "text-embedding-3-small",
                    "usage": {"prompt_tokens": 2, "total_tokens": 2},
                },
            ),
        )

    # Set method manually as declare_discovery_extension has a bug where it doesn't output method inside the input dictionary
    if ext and "bazaar" in ext and "info" in ext["bazaar"] and "input" in ext["bazaar"]["info"]:
        ext["bazaar"]["info"]["input"]["method"] = method

    return ext

