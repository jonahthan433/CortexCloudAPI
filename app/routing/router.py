import asyncio
import time
import logging
from typing import AsyncGenerator, Dict, Optional, Tuple, Type, Any
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.registry import ModelRegistry
from app.providers import (
    BaseProvider,
    ProviderContext,
    OpenAIProvider,
    AnthropicProvider,
    GeminiProvider,
    GroqProvider,
)
from app.schemas.openai import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionStreamResponse,
    EmbeddingsRequest,
    EmbeddingsResponse,
)
from app.services.models import ModelRegistryService

logger = logging.getLogger("cortexcloud.routing.router")


class ModelRouter:
    """
    Routing engine responsible for dispatching requests to AI providers.
    Handles retries, latency measurement, and failover/fallback mechanisms.
    """

    PROVIDER_MAP: Dict[str, Type[BaseProvider]] = {
        "openai": OpenAIProvider,
        "anthropic": AnthropicProvider,
        "gemini": GeminiProvider,
        "groq": GroqProvider,
    }

    def __init__(self, db: AsyncSession):
        self.db = db

    def _get_provider_api_key(self, provider: str) -> str:
        """Resolve the API key for a provider from global settings."""
        key_map = {
            "openai": settings.OPENAI_API_KEY,
            "anthropic": settings.ANTHROPIC_API_KEY,
            "gemini": settings.GEMINI_API_KEY,
            "groq": settings.GROQ_API_KEY,
        }
        
        api_key = key_map.get(provider.lower())
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"API key for provider '{provider}' is not configured on the gateway.",
            )
        return api_key

    def _get_provider(self, provider_name: str) -> BaseProvider:
        """Get the concrete provider instance."""
        provider_class = self.PROVIDER_MAP.get(provider_name.lower())
        if not provider_class:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Provider '{provider_name}' is not supported by the gateway.",
            )
        return provider_class()

    async def route_chat_completion(
        self, request: ChatCompletionRequest, correlation_id: str
    ) -> Tuple[ChatCompletionResponse, ModelRegistry, float]:
        """
        Routes chat completion requests with retries, latency tracking, and fallback.
        Returns:
            Tuple[ChatCompletionResponse, ModelRegistry, float]: (response, routed_model, latency_ms)
        """
        # 1. Look up primary model in registry
        model_entry = await ModelRegistryService.get_model_by_name(self.db, request.model)
        if not model_entry:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Requested model '{request.model}' is not registered or active.",
            )

        try:
            # 2. Try completing request with retry logic
            response, latency_ms = await self._execute_with_retry(
                self._execute_chat_completion, request, model_entry, correlation_id
            )
            return response, model_entry, latency_ms
        except HTTPException as primary_err:
            # 3. Check for configured fallback model if primary fails with 429 or 5xx
            fallback_name = model_entry.capabilities.get("fallback_model")
            if fallback_name and primary_err.status_code in (429, 500, 502, 503, 504):
                logger.info(
                    f"Primary model '{request.model}' failed with status {primary_err.status_code} "
                    f"on correlation ID {correlation_id}. Initiating fallback to '{fallback_name}'..."
                )
                fallback_entry = await ModelRegistryService.get_model_by_name(self.db, fallback_name)
                if fallback_entry:
                    try:
                        # Attempt to complete with the fallback model
                        response, latency_ms = await self._execute_with_retry(
                            self._execute_chat_completion, request, fallback_entry, correlation_id
                        )
                        return response, fallback_entry, latency_ms
                    except Exception as fallback_err:
                        logger.error(
                            f"Fallback model '{fallback_name}' also failed on correlation ID {correlation_id}: {str(fallback_err)}"
                        )
                        raise HTTPException(
                            status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"Primary model '{request.model}' failed (HTTP {primary_err.status_code}). Fallback '{fallback_name}' also failed: {str(fallback_err)}",
                        )
            raise primary_err

    async def route_chat_completion_stream(
        self, request: ChatCompletionRequest, correlation_id: str
    ) -> Tuple[AsyncGenerator[ChatCompletionStreamResponse, None], ModelRegistry]:
        """
        Routes chat completion streams to the appropriate provider.
        Does not support intermediate buffering retries due to streaming nature,
        but attempts immediate fallback lookup if connection setup fails.
        """
        model_entry = await ModelRegistryService.get_model_by_name(self.db, request.model)
        if not model_entry:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Requested model '{request.model}' is not registered or active.",
            )

        try:
            stream = await self._execute_chat_completion_stream(request, model_entry, correlation_id)
            return stream, model_entry
        except HTTPException as primary_err:
            # Immediate failover before streaming starts
            fallback_name = model_entry.capabilities.get("fallback_model")
            if fallback_name and primary_err.status_code in (429, 500, 502, 503, 504):
                logger.info(
                    f"Primary stream '{request.model}' failed with status {primary_err.status_code} "
                    f"on correlation ID {correlation_id}. Initiating fallback to '{fallback_name}'..."
                )
                fallback_entry = await ModelRegistryService.get_model_by_name(self.db, fallback_name)
                if fallback_entry:
                    try:
                        stream = await self._execute_chat_completion_stream(request, fallback_entry, correlation_id)
                        return stream, fallback_entry
                    except Exception as fallback_err:
                        logger.error(
                            f"Fallback stream '{fallback_name}' also failed on correlation ID {correlation_id}: {str(fallback_err)}"
                        )
                        raise HTTPException(
                            status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"Primary stream '{request.model}' failed (HTTP {primary_err.status_code}). Fallback '{fallback_name}' also failed: {str(fallback_err)}",
                        )
            raise primary_err

    async def route_embeddings(
        self, request: EmbeddingsRequest, correlation_id: str
    ) -> Tuple[EmbeddingsResponse, ModelRegistry, float]:
        """
        Routes embedding requests.
        Returns:
            Tuple[EmbeddingsResponse, ModelRegistry, float]: (response, routed_model, latency_ms)
        """
        model_entry = await ModelRegistryService.get_model_by_name(self.db, request.model)
        if not model_entry:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Requested embedding model '{request.model}' is not registered or active.",
            )

        response, latency_ms = await self._execute_with_retry(
            self._execute_embeddings, request, model_entry, correlation_id
        )
        return response, model_entry, latency_ms

    # Execution wrapper functions
    async def _execute_chat_completion(
        self, request: ChatCompletionRequest, model: ModelRegistry, correlation_id: str
    ) -> ChatCompletionResponse:
        provider = self._get_provider(model.provider)
        api_key = self._get_provider_api_key(model.provider)
        
        ctx = ProviderContext(
            api_key=api_key,
            provider_model_name=model.provider_model_name,
            correlation_id=correlation_id,
        )
        return await provider.chat_completion(request, ctx)

    async def _execute_chat_completion_stream(
        self, request: ChatCompletionRequest, model: ModelRegistry, correlation_id: str
    ) -> AsyncGenerator[ChatCompletionStreamResponse, None]:
        provider = self._get_provider(model.provider)
        api_key = self._get_provider_api_key(model.provider)
        
        ctx = ProviderContext(
            api_key=api_key,
            provider_model_name=model.provider_model_name,
            correlation_id=correlation_id,
        )
        return provider.chat_completion_stream(request, ctx)

    async def _execute_embeddings(
        self, request: EmbeddingsRequest, model: ModelRegistry, correlation_id: str
    ) -> EmbeddingsResponse:
        provider = self._get_provider(model.provider)
        api_key = self._get_provider_api_key(model.provider)
        
        ctx = ProviderContext(
            api_key=api_key,
            provider_model_name=model.provider_model_name,
            correlation_id=correlation_id,
        )
        return await provider.embeddings(request, ctx)

    # Retry Policy Engine
    async def _execute_with_retry(
        self, func, request, model_entry, correlation_id, max_retries: int = 3, backoff_factor: float = 0.5
    ) -> Tuple[Any, float]:
        """
        Executes a gateway function with exponential backoff on retryable HTTP errors.
        Returns:
            Tuple[Any, float]: (result, total_latency_ms)
        """
        last_exception = None
        start_time = time.perf_counter()
        
        for attempt in range(max_retries):
            try:
                result = await func(request, model_entry, correlation_id)
                latency_ms = (time.perf_counter() - start_time) * 1000.0
                return result, latency_ms
            except HTTPException as e:
                last_exception = e
                # Retry on rate limiting (429) or upstream server issues (5xx)
                if e.status_code in (429, 500, 502, 503, 504):
                    if attempt < max_retries - 1:
                        sleep_time = backoff_factor * (2 ** attempt)
                        logger.warning(
                            f"HTTP {e.status_code} from provider '{model_entry.provider}' for model '{model_entry.name}' "
                            f"on correlation ID {correlation_id}. Retrying attempt {attempt + 1}/{max_retries} in {sleep_time:.2f}s..."
                        )
                        await asyncio.sleep(sleep_time)
                        continue
                raise e
            except Exception as e:
                # Wrap unexpected request network anomalies
                last_exception = HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Upstream provider connection error: {str(e)}",
                )
                if attempt < max_retries - 1:
                    sleep_time = backoff_factor * (2 ** attempt)
                    logger.warning(
                        f"Unexpected error from provider '{model_entry.provider}' for model '{model_entry.name}' "
                        f"on correlation ID {correlation_id}: {str(e)}. Retrying attempt {attempt + 1}/{max_retries} in {sleep_time:.2f}s..."
                    )
                    await asyncio.sleep(sleep_time)
                    continue
                raise last_exception

        raise last_exception
