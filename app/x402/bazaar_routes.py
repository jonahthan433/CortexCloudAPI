"""Bazaar discovery + minimal MCP SSE routes for CortexCloud x402 gateway.

- GET  /x402/v1/.well-known/bazaar  -> consolidated Bazaar discovery doc
- GET  /x402/v1/mcp                 -> MCP SSE endpoint (initialize / tools/list /
                                       tools/call). tools/call returns the x402
                                       endpoint + payment requirements so the agent
                                       completes the per-call USDC payment client-side.
"""

import json
import logging
from typing import AsyncIterable

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse

from app.x402.bazaar import build_discovery_doc, chat_mcp_discovery, embeddings_mcp_discovery
from app.core.config import settings

logger = logging.getLogger("cortexcloud.x402.bazaar")

router = APIRouter()

BASE = settings.X402_RESOURCE_BASE


@router.get("/.well-known/bazaar", tags=["Bazaar Discovery"])
async def bazaar_discovery() -> JSONResponse:
    """Bazaar discovery document — consumed by Coinbase Bazaar & agent frameworks."""
    return JSONResponse(content=build_discovery_doc())


@router.get("/mcp", tags=["MCP"])
async def mcp_sse(request: Request) -> StreamingResponse:
    """Minimal MCP server over SSE. Advertises tools; tools/call returns the
    x402 endpoint + payment requirements (agent pays client-side)."""

    async def event_stream() -> AsyncIterable[str]:
        # MCP initialize
        yield _sse({"jsonrpc": "2.0", "id": 1, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "CortexCloud", "version": "1.0"},
        }})

        # tools/list
        tools = [
            {
                "name": "chat_completions",
                "description": "OpenAI-compatible chat completion via CortexCloud x402 gateway (pay per call, USDC on Base).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "model": {"type": "string"},
                        "messages": {"type": "array", "items": {"type": "object"}},
                        "stream": {"type": "boolean"},
                    },
                    "required": ["model", "messages"],
                },
            },
            {
                "name": "embeddings",
                "description": "OpenAI-compatible text embeddings via CortexCloud x402 gateway (pay per call, USDC on Base).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "input": {"type": "string"},
                        "model": {"type": "string"},
                    },
                    "required": ["input"],
                },
            },
        ]
        yield _sse({"jsonrpc": "2.0", "id": 2, "result": {"tools": tools}})

        # Handle incoming client messages (initialize / tools/list / tools/call)
        async for line in request.stream():
            line = line.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            try:
                msg = json.loads(payload)
            except Exception:
                continue
            method = msg.get("method")
            mid = msg.get("id")
            if method == "tools/call":
                name = msg.get("params", {}).get("name")
                if name == "chat_completions":
                    result = {
                        "endpoint": f"{BASE}/x402/v1/chat/completions",
                        "method": "POST",
                        "payment": {
                            "protocol": "x402",
                            "scheme": "exact",
                            "network": settings.X402_NETWORK,
                            "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                            "payTo": settings.WALLET_ADDRESS,
                            "maxAmountRequired": "5000",
                        },
                        "note": "Send an x402 PaymentRequirements challenge; pay in USDC on Base, then POST your request with the X-PAYMENT header.",
                    }
                elif name == "embeddings":
                    result = {
                        "endpoint": f"{BASE}/x402/v1/embeddings",
                        "method": "POST",
                        "payment": {
                            "protocol": "x402",
                            "scheme": "exact",
                            "network": settings.X402_NETWORK,
                            "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
                            "payTo": settings.WALLET_ADDRESS,
                            "maxAmountRequired": "1000",
                        },
                        "note": "Pay per call in USDC on Base via x402, then POST with X-PAYMENT header.",
                    }
                else:
                    result = {"error": f"unknown tool {name}"}
                yield _sse({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": json.dumps(result)}]}})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"
