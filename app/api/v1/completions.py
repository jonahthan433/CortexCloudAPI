import json
import time
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import verify_api_key
from app.database.session import get_db, AsyncSessionLocal
from app.models.key import APIKey
from app.middleware.rate_limit import RateLimiter
from app.routing.router import ModelRouter
from app.schemas.openai import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    EmbeddingsRequest,
    EmbeddingsResponse,
)
from app.services.usage import UsageMeteringService
from app.usage.tokenizer import count_messages_tokens, count_tokens

router = APIRouter()


async def stream_completion_wrapper(
    stream_generator,
    request: ChatCompletionRequest,
    api_key: APIKey,
    correlation_id: str,
    client_ip: str,
    start_time: float,
    request_path: str,
    routed_model_name: str
):
    """
    Wrapper for streaming completions.
    Buffers completion chunks, yields SSE compliant output,
    and commits usage/billing data atomically after stream closes.
    """
    accumulated_content = ""
    prompt_tokens = count_messages_tokens(request.messages, request.model)
    completion_tokens = 0
    provider_reported_usage = None

    try:
        async for chunk in stream_generator:
            yield f"data: {chunk.model_dump_json(exclude_none=True)}\n\n"
            
            # Accumulate content to count tokens locally if not reported
            if chunk.choices:
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    accumulated_content += delta.content

            if chunk.usage:
                provider_reported_usage = chunk.usage

        yield "data: [DONE]\n\n"

        # Resolve usage tokens
        p_tokens = prompt_tokens
        c_tokens = 0
        
        if provider_reported_usage:
            p_tokens = provider_reported_usage.prompt_tokens
            c_tokens = provider_reported_usage.completion_tokens
        else:
            c_tokens = count_tokens(accumulated_content, routed_model_name)

        latency_ms = int((time.perf_counter() - start_time) * 1000.0)

        # Log usage to DB
        async with AsyncSessionLocal() as db:
            await UsageMeteringService.record_usage(
                db=db,
                organization_id=api_key.organization_id,
                api_key_id=api_key.id,
                correlation_id=correlation_id,
                model_name=routed_model_name,
                prompt_tokens=p_tokens,
                completion_tokens=c_tokens,
                latency_ms=latency_ms,
                status_code=200,
                request_path=request_path,
                client_ip=client_ip,
            )

    except Exception as e:
        latency_ms = int((time.perf_counter() - start_time) * 1000.0)
        # Log failure to usage logs
        async with AsyncSessionLocal() as db:
            await UsageMeteringService.record_usage(
                db=db,
                organization_id=api_key.organization_id,
                api_key_id=api_key.id,
                correlation_id=correlation_id,
                model_name=routed_model_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=0,
                latency_ms=latency_ms,
                status_code=500,
                request_path=request_path,
                client_ip=client_ip,
                error_message=str(e),
            )
        
        # Yield OpenAI formatted error chunk
        err_response = {
            "error": {
                "message": f"Gateway error: {str(e)}",
                "type": "api_error",
                "code": 500,
            }
        }
        yield f"data: {json.dumps(err_response)}\n\n"
        yield "data: [DONE]\n\n"


@router.post("/chat/completions")
@router.post("/responses")
async def chat_completions(
    request: ChatCompletionRequest,
    req_http: Request,
    db: AsyncSession = Depends(get_db),
    api_key: APIKey = Depends(RateLimiter.check_api_key_limits),
    x_correlation_id: Optional[str] = Header(None)
):
    """
    OpenAI-compatible chat completion endpoint.
    Supports standard HTTP response and streaming SSE.
    """
    correlation_id = x_correlation_id or str(uuid.uuid4())
    client_ip = req_http.client.host if req_http.client else "127.0.0.1"
    start_time = time.perf_counter()
    request_path = req_http.url.path

    router_engine = ModelRouter(db)

    # 1. Handle Streaming
    if request.stream:
        stream_generator, routed_model = await router_engine.route_chat_completion_stream(
            request, correlation_id
        )
        return StreamingResponse(
            stream_completion_wrapper(
                stream_generator=stream_generator,
                request=request,
                api_key=api_key,
                correlation_id=correlation_id,
                client_ip=client_ip,
                start_time=start_time,
                request_path=request_path,
                routed_model_name=routed_model.name
            ),
            media_type="text/event-stream",
        )

    # 2. Handle Non-Streaming
    try:
        response, routed_model, latency_ms = await router_engine.route_chat_completion(
            request, correlation_id
        )
        
        # Override returned model to what gateway client requested
        response.model = request.model

        # Log usage to DB and charge balance
        await UsageMeteringService.record_usage(
            db=db,
            organization_id=api_key.organization_id,
            api_key_id=api_key.id,
            correlation_id=correlation_id,
            model_name=routed_model.name,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            latency_ms=int(latency_ms),
            status_code=200,
            request_path=request_path,
            client_ip=client_ip,
        )
        
        return response

    except HTTPException as http_err:
        # Log HTTP failures
        latency_ms = int((time.perf_counter() - start_time) * 1000.0)
        prompt_tokens = count_messages_tokens(request.messages, request.model)
        await UsageMeteringService.record_usage(
            db=db,
            organization_id=api_key.organization_id,
            api_key_id=api_key.id,
            correlation_id=correlation_id,
            model_name=request.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=0,
            latency_ms=latency_ms,
            status_code=http_err.status_code,
            request_path=request_path,
            client_ip=client_ip,
            error_message=http_err.detail,
        )
        raise http_err

    except Exception as exc:
        latency_ms = int((time.perf_counter() - start_time) * 1000.0)
        prompt_tokens = count_messages_tokens(request.messages, request.model)
        await UsageMeteringService.record_usage(
            db=db,
            organization_id=api_key.organization_id,
            api_key_id=api_key.id,
            correlation_id=correlation_id,
            model_name=request.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=0,
            latency_ms=latency_ms,
            status_code=500,
            request_path=request_path,
            client_ip=client_ip,
            error_message=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"CortexCloud router error: {str(exc)}",
        )


@router.post("/embeddings", response_model=EmbeddingsResponse)
async def embeddings(
    request: EmbeddingsRequest,
    req_http: Request,
    db: AsyncSession = Depends(get_db),
    api_key: APIKey = Depends(RateLimiter.check_api_key_limits),
    x_correlation_id: Optional[str] = Header(None)
):
    """
    OpenAI-compatible text embeddings endpoint.
    """
    correlation_id = x_correlation_id or str(uuid.uuid4())
    client_ip = req_http.client.host if req_http.client else "127.0.0.1"
    start_time = time.perf_counter()
    request_path = req_http.url.path

    router_engine = ModelRouter(db)

    try:
        response, routed_model, latency_ms = await router_engine.route_embeddings(
            request, correlation_id
        )

        # Estimate prompt tokens if not provided by upstream embeddings response
        prompt_tokens = response.usage.prompt_tokens
        if prompt_tokens == 0:
            if isinstance(request.input, str):
                prompt_tokens = count_tokens(request.input, request.model)
            elif isinstance(request.input, list):
                prompt_tokens = sum(count_tokens(str(item), request.model) for item in request.input)

        response.model = request.model
        response.usage.prompt_tokens = prompt_tokens
        response.usage.total_tokens = prompt_tokens

        # Log usage to DB and charge balance
        await UsageMeteringService.record_usage(
            db=db,
            organization_id=api_key.organization_id,
            api_key_id=api_key.id,
            correlation_id=correlation_id,
            model_name=routed_model.name,
            prompt_tokens=prompt_tokens,
            completion_tokens=0,
            latency_ms=int(latency_ms),
            status_code=200,
            request_path=request_path,
            client_ip=client_ip,
        )

        return response

    except HTTPException as http_err:
        latency_ms = int((time.perf_counter() - start_time) * 1000.0)
        await UsageMeteringService.record_usage(
            db=db,
            organization_id=api_key.organization_id,
            api_key_id=api_key.id,
            correlation_id=correlation_id,
            model_name=request.model,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=latency_ms,
            status_code=http_err.status_code,
            request_path=request_path,
            client_ip=client_ip,
            error_message=http_err.detail,
        )
        raise http_err

    except Exception as exc:
        latency_ms = int((time.perf_counter() - start_time) * 1000.0)
        await UsageMeteringService.record_usage(
            db=db,
            organization_id=api_key.organization_id,
            api_key_id=api_key.id,
            correlation_id=correlation_id,
            model_name=request.model,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=latency_ms,
            status_code=500,
            request_path=request_path,
            client_ip=client_ip,
            error_message=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"CortexCloud embeddings error: {str(exc)}",
        )
