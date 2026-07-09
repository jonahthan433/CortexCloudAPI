"""
x402 payment-gated API routes.

These mirror the existing /v1/ routes but:
  - No API key authentication required (payment replaces auth)
  - Mounted at /x402/v1/ prefix
  - Protected by PaymentMiddlewareASGI (x402 payment required)
  - No internal usage/billing tracking (x402 payment IS the billing)
"""

import json
import time
import uuid
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.routing.router import ModelRouter
from app.schemas.openai import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    EmbeddingsRequest,
    EmbeddingsResponse,
    ModelListResponse,
    ModelObject,
)
from app.services.models import ModelRegistryService
from app.usage.tokenizer import count_messages_tokens, count_tokens

logger = logging.getLogger("cortexcloud.x402.routes")

router = APIRouter()


# --------------------------------------------------------------------------
# Streaming wrapper (x402 — no internal billing, payment handles it)
# --------------------------------------------------------------------------

async def _x402_stream_wrapper(
    stream_generator,
    request: ChatCompletionRequest,
    correlation_id: str,
    start_time: float,
    routed_model_name: str,
):
    """Stream wrapper for x402 payment-gated completions."""
    try:
        async for chunk in stream_generator:
            yield f"data: {chunk.model_dump_json(exclude_none=True)}\n\n"

        yield "data: [DONE]\n\n"

        latency_ms = int((time.perf_counter() - start_time) * 1000.0)
        logger.info(
            f"x402 stream completed: model={routed_model_name} "
            f"correlation_id={correlation_id} latency_ms={latency_ms}"
        )

    except Exception as e:
        latency_ms = int((time.perf_counter() - start_time) * 1000.0)
        logger.error(
            f"x402 stream error: model={routed_model_name} "
            f"correlation_id={correlation_id} latency_ms={latency_ms} error={e}"
        )

        err_response = {
            "error": {
                "message": f"Gateway error: {str(e)}",
                "type": "api_error",
                "code": 500,
            }
        }
        yield f"data: {json.dumps(err_response)}\n\n"
        yield "data: [DONE]\n\n"


# --------------------------------------------------------------------------
# Chat Completions (x402 payment-gated)
# --------------------------------------------------------------------------

@router.post("/chat/completions")
@router.post("/responses")
async def x402_chat_completions(
    request: ChatCompletionRequest,
    req_http: Request,
    db: AsyncSession = Depends(get_db),
    x_correlation_id: Optional[str] = Header(None),
):
    """
    OpenAI-compatible chat completion endpoint (x402 payment-gated).
    No API key required — payment via x402 protocol replaces authentication.
    """
    correlation_id = x_correlation_id or str(uuid.uuid4())
    start_time = time.perf_counter()

    router_engine = ModelRouter(db)

    # Streaming
    if request.stream:
        stream_generator, routed_model = await router_engine.route_chat_completion_stream(
            request, correlation_id
        )
        return StreamingResponse(
            _x402_stream_wrapper(
                stream_generator=stream_generator,
                request=request,
                correlation_id=correlation_id,
                start_time=start_time,
                routed_model_name=routed_model.name,
            ),
            media_type="text/event-stream",
        )

    # Non-streaming
    try:
        response, routed_model, latency_ms = await router_engine.route_chat_completion(
            request, correlation_id
        )
        response.model = request.model

        logger.info(
            f"x402 completion: model={routed_model.name} "
            f"correlation_id={correlation_id} latency_ms={int(latency_ms)} "
            f"tokens={response.usage.prompt_tokens}+{response.usage.completion_tokens}"
        )

        return response

    except HTTPException as http_err:
        raise http_err

    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"CortexCloud router error: {str(exc)}",
        )


# --------------------------------------------------------------------------
# Embeddings (x402 payment-gated)
# --------------------------------------------------------------------------

@router.post("/embeddings", response_model=EmbeddingsResponse)
async def x402_embeddings(
    request: EmbeddingsRequest,
    req_http: Request,
    db: AsyncSession = Depends(get_db),
    x_correlation_id: Optional[str] = Header(None),
):
    """
    OpenAI-compatible text embeddings endpoint (x402 payment-gated).
    No API key required — payment via x402 protocol replaces authentication.
    """
    correlation_id = x_correlation_id or str(uuid.uuid4())
    start_time = time.perf_counter()

    router_engine = ModelRouter(db)

    try:
        response, routed_model, latency_ms = await router_engine.route_embeddings(
            request, correlation_id
        )

        prompt_tokens = response.usage.prompt_tokens
        if prompt_tokens == 0:
            if isinstance(request.input, str):
                prompt_tokens = count_tokens(request.input, request.model)
            elif isinstance(request.input, list):
                prompt_tokens = sum(count_tokens(str(item), request.model) for item in request.input)

        response.model = request.model
        response.usage.prompt_tokens = prompt_tokens
        response.usage.total_tokens = prompt_tokens

        logger.info(
            f"x402 embedding: model={routed_model.name} "
            f"correlation_id={correlation_id} latency_ms={int(latency_ms)} "
            f"tokens={prompt_tokens}"
        )

        return response

    except HTTPException as http_err:
        raise http_err

    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"CortexCloud embeddings error: {str(exc)}",
        )


# --------------------------------------------------------------------------
# Models (x402 — free, no payment required)
# --------------------------------------------------------------------------

@router.get("/models", response_model=ModelListResponse)
async def x402_list_models(
    db: AsyncSession = Depends(get_db),
) -> ModelListResponse:
    """
    List available models on the CortexCloud gateway.
    Free endpoint — no payment or API key required.
    """
    db_models = await ModelRegistryService.get_active_models(db)

    models_data = [
        ModelObject(
            id=model.name,
            created=int(model.created_at.timestamp()) if model.created_at else 1718000000,
            owned_by=model.provider,
        )
        for model in db_models
    ]

    return ModelListResponse(data=models_data)
