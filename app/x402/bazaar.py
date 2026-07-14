"""Bazaar discovery metadata for CortexCloud x402 routes.

Builds per-route discovery extensions (input/output schemas + descriptions)
using the official x402 Python SDK's declare_discovery_extension, and an MCP
discovery extension so agent frameworks can consume the gateway directly.

No on-chain transaction is performed by this module — it only advertises
how agents should call the routes (payment happens client-side via x402).
"""

from typing import Any, Dict

from x402.extensions.bazaar import (
    OutputConfig,
    declare_discovery_extension,
    declare_mcp_discovery_extension,
    DeclareMcpDiscoveryConfig,
)

from app.core.config import settings

BASE = settings.X402_RESOURCE_BASE  # https://cortexcloud.org


# ---------------------------------------------------------------------------
# Chat completions route discovery
# ---------------------------------------------------------------------------
def chat_completions_discovery() -> Dict[str, Any]:
    return declare_discovery_extension(
        input={
            "model": "openai/gpt-4o-mini",
            "messages": [{"role": "user", "content": "Hello"}],
        },
        input_schema={
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Model ID (gateway alias), e.g. 'gemini/gemini-1.5-pro', 'groq/llama-3.1-8b', 'nvidia/llama-3.1-8b-instruct', or any OpenRouter model.",
                },
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {"type": "string", "enum": ["system", "user", "assistant"]},
                            "content": {"type": "string"},
                        },
                        "required": ["role", "content"],
                    },
                    "description": "Conversation messages.",
                },
                "stream": {"type": "boolean", "description": "Stream SSE chunks."},
                "temperature": {"type": "number"},
                "max_tokens": {"type": "integer"},
            },
            "required": ["model", "messages"],
        },
        body_type="json",
        output=OutputConfig(
            example={
                "id": "chatcmpl-xxx",
                "object": "chat.completion",
                "model": "openai/gpt-4o-mini",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello! How can I help?"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 9, "completion_tokens": 10, "total_tokens": 19},
            }
        ),
    )


def embeddings_discovery() -> Dict[str, Any]:
    return declare_discovery_extension(
        input={"input": "Text to embed", "model": "text-embedding-3-small"},
        input_schema={
            "type": "object",
            "properties": {
                "input": {
                    "type": "string",
                    "description": "Text (or list of strings) to embed.",
                },
                "model": {"type": "string", "description": "Embedding model ID."},
            },
            "required": ["input"],
        },
        body_type="json",
        output=OutputConfig(
            example={
                "object": "list",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.01, -0.02, 0.03]}],
                "model": "text-embedding-3-small",
                "usage": {"prompt_tokens": 4, "total_tokens": 4},
            }
        ),
    )


def models_discovery() -> Dict[str, Any]:
    return declare_discovery_extension(
        input={},
        input_schema={"type": "object", "properties": {}},
        output=OutputConfig(
            example={
                "object": "list",
                "data": [
                    {"id": "gemini/gemini-1.5-pro", "created": 1718000000, "owned_by": "gemini"}
                ],
            }
        ),
    )


# ---------------------------------------------------------------------------
# MCP discovery (so MCP-capable agent frameworks can list + call tools)
# ---------------------------------------------------------------------------
def chat_mcp_discovery() -> Dict[str, Any]:
    return declare_mcp_discovery_extension(
        DeclareMcpDiscoveryConfig(
            tool_name="chat_completions",
            description="OpenAI-compatible chat completion via CortexCloud x402 gateway. Client pays per call in USDC on Base via x402.",
            input_schema={
                "type": "object",
                "properties": {
                    "model": {"type": "string"},
                    "messages": {"type": "array", "items": {"type": "object"}},
                    "stream": {"type": "boolean"},
                },
                "required": ["model", "messages"],
            },
            transport="sse",
            example={
                "endpoint": f"{BASE}/x402/v1/chat/completions",
                "method": "POST",
                "payment": "x402 USDC on Base (eip155:8453)",
            },
        )
    )


def embeddings_mcp_discovery() -> Dict[str, Any]:
    return declare_mcp_discovery_extension(
        DeclareMcpDiscoveryConfig(
            tool_name="embeddings",
            description="OpenAI-compatible text embeddings via CortexCloud x402 gateway. Client pays per call in USDC on Base via x402.",
            input_schema={
                "type": "object",
                "properties": {
                    "input": {"type": "string"},
                    "model": {"type": "string"},
                },
                "required": ["input"],
            },
            transport="sse",
            example={
                "endpoint": f"{BASE}/x402/v1/embeddings",
                "method": "POST",
                "payment": "x402 USDC on Base (eip155:8453)",
            },
        )
    )


def build_discovery_doc() -> Dict[str, Any]:
    """Consolidated Bazaar discovery document for all routes."""
    return {
        "version": "1.0",
        "gateway": "CortexCloud",
        "description": "Pay-per-call AI inference gateway for autonomous agents. No signup, no API key. Pay per request in USDC on Base via x402.",
        "payment": {
            "scheme": "exact",
            "network": settings.X402_NETWORK,
            "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "payTo": settings.WALLET_ADDRESS,
            "facilitator": settings.X402_FACILITATOR_URL,
        },
        "routes": {
            "POST /x402/v1/chat/completions": {
                "description": "OpenAI-compatible chat completions.",
                "price_usd": "0.005",
                "extensions": chat_completions_discovery(),
            },
            "POST /x402/v1/responses": {
                "description": "OpenAI-compatible chat completions (alias).",
                "price_usd": "0.005",
                "extensions": chat_completions_discovery(),
            },
            "POST /x402/v1/embeddings": {
                "description": "OpenAI-compatible text embeddings.",
                "price_usd": "0.001",
                "extensions": embeddings_discovery(),
            },
            "GET /x402/v1/models": {
                "description": "List available models (free).",
                "price_usd": "0.00",
                "extensions": models_discovery(),
            },
        },
        "mcp": {
            "transport": "sse",
            "endpoint": f"{BASE}/x402/v1/mcp",
            "tools": [
                {"tool_name": "chat_completions", "extensions": chat_mcp_discovery()},
                {"tool_name": "embeddings", "extensions": embeddings_mcp_discovery()},
            ],
        },
    }
