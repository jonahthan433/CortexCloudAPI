import json
from typing import AsyncGenerator
import httpx
from fastapi import HTTPException

from app.providers.base import BaseProvider, ProviderContext
from app.schemas.openai import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionStreamResponse,
    EmbeddingsRequest,
    EmbeddingsResponse,
)


class OpenAIProvider(BaseProvider):
    """Provider for OpenAI API."""

    def __init__(self, base_url: str = "https://api.openai.com/v1"):
        self.base_url = base_url

    async def chat_completion(
        self, request: ChatCompletionRequest, ctx: ProviderContext
    ) -> ChatCompletionResponse:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {ctx.api_key}",
            "Content-Type": "application/json",
            "X-Correlation-ID": ctx.correlation_id,
        }

        # Adapt request body: override the model name to the provider's model name
        data = request.model_dump(exclude_none=True)
        data["model"] = ctx.provider_model_name

        async with httpx.AsyncClient(timeout=ctx.timeout) as client:
            try:
                response = await client.post(url, headers=headers, json=data)
                if response.status_code != 200:
                    raise HTTPException(
                        status_code=response.status_code,
                        detail=f"OpenAI error: {response.text}",
                    )
                return ChatCompletionResponse.model_validate(response.json())
            except httpx.RequestError as e:
                raise HTTPException(
                    status_code=502,
                    detail=f"Failed to connect to OpenAI: {str(e)}",
                )

    async def chat_completion_stream(
        self, request: ChatCompletionRequest, ctx: ProviderContext
    ) -> AsyncGenerator[ChatCompletionStreamResponse, None]:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {ctx.api_key}",
            "Content-Type": "application/json",
            "X-Correlation-ID": ctx.correlation_id,
        }

        data = request.model_dump(exclude_none=True)
        data["model"] = ctx.provider_model_name
        data["stream"] = True
        # Ensure we request token usage if supported by upstream
        if "stream_options" not in data:
            data["stream_options"] = {"include_usage": True}

        client = httpx.AsyncClient(timeout=ctx.timeout)
        try:
            # Create a request object to use with client.send
            req = client.build_request("POST", url, headers=headers, json=data)
            response = await client.send(req, stream=True)

            if response.status_code != 200:
                await response.aread()  # Consume response body to prevent leak
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"OpenAI streaming error: {response.text}",
                )

            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                if line.startswith("data: "):
                    content = line[6:].strip()
                    if content == "[DONE]":
                        break
                    try:
                        chunk_dict = json.loads(content)
                        # Patch model name in chunk to gateway alias
                        if "model" in chunk_dict:
                            chunk_dict["model"] = request.model
                        yield ChatCompletionStreamResponse.model_validate(chunk_dict)
                    except Exception:
                        # Ignore malformed JSON chunks from upstream
                        continue
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to connect to OpenAI stream: {str(e)}",
            )
        finally:
            await client.aclose()

    async def embeddings(
        self, request: EmbeddingsRequest, ctx: ProviderContext
    ) -> EmbeddingsResponse:
        url = f"{self.base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {ctx.api_key}",
            "Content-Type": "application/json",
            "X-Correlation-ID": ctx.correlation_id,
        }

        data = request.model_dump(exclude_none=True)
        data["model"] = ctx.provider_model_name

        async with httpx.AsyncClient(timeout=ctx.timeout) as client:
            try:
                response = await client.post(url, headers=headers, json=data)
                if response.status_code != 200:
                    raise HTTPException(
                        status_code=response.status_code,
                        detail=f"OpenAI error: {response.text}",
                    )
                return EmbeddingsResponse.model_validate(response.json())
            except httpx.RequestError as e:
                raise HTTPException(
                    status_code=502,
                    detail=f"Failed to connect to OpenAI: {str(e)}",
                )
