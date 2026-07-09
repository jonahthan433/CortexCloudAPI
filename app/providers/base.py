from abc import ABC, abstractmethod
from typing import AsyncGenerator, Optional
from pydantic import BaseModel

from app.schemas.openai import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionStreamResponse,
    EmbeddingsRequest,
    EmbeddingsResponse,
)


class ProviderContext(BaseModel):
    """Context holding details about the specific execution of a request."""
    api_key: str
    provider_model_name: str
    correlation_id: str
    timeout: float = 30.0


class BaseProvider(ABC):
    """Abstract Base Class that all concrete AI Gateway providers must implement."""

    @abstractmethod
    async def chat_completion(
        self, request: ChatCompletionRequest, ctx: ProviderContext
    ) -> ChatCompletionResponse:
        """Send a non-streaming chat completion request to the provider."""
        pass

    @abstractmethod
    async def chat_completion_stream(
        self, request: ChatCompletionRequest, ctx: ProviderContext
    ) -> AsyncGenerator[ChatCompletionStreamResponse, None]:
        """Send a streaming chat completion request to the provider yielding SSE chunks."""
        pass

    @abstractmethod
    async def embeddings(
        self, request: EmbeddingsRequest, ctx: ProviderContext
    ) -> EmbeddingsResponse:
        """Send an embedding creation request to the provider."""
        pass
