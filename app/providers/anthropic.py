import json
import time
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional
import httpx
from fastapi import HTTPException

from app.providers.base import BaseProvider, ProviderContext
from app.schemas.openai import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionStreamChoice,
    ChatCompletionStreamResponse,
    ChoiceDelta,
    ChoiceMessage,
    EmbeddingsRequest,
    EmbeddingsResponse,
    UsageInfo,
)


class AnthropicProvider(BaseProvider):
    """Provider for Anthropic API."""

    def __init__(self, base_url: str = "https://api.anthropic.com/v1"):
        self.base_url = base_url

    def _translate_request(self, request: ChatCompletionRequest, provider_model: str) -> Dict[str, Any]:
        """Translate OpenAI request payload to Anthropic messages payload."""
        anthropic_data: Dict[str, Any] = {
            "model": provider_model,
            "max_tokens": request.max_tokens or 4096,  # Anthropic requires max_tokens
        }

        if request.temperature is not None:
            # Anthropic temp is 0.0 to 1.0, OpenAI is 0.0 to 2.0. Scale or cap.
            anthropic_data["temperature"] = min(max(request.temperature / 2.0, 0.0), 1.0)
        
        if request.top_p is not None:
            anthropic_data["top_p"] = request.top_p

        # Extract system prompt and convert messages
        system_prompt = ""
        anthropic_messages = []

        # Track tool blocks and tool results
        for msg in request.messages:
            if msg.role == "system":
                # Combine system prompts
                if system_prompt:
                    system_prompt += "\n" + str(msg.content)
                else:
                    system_prompt = str(msg.content)
            elif msg.role == "user":
                anthropic_messages.append({
                    "role": "user",
                    "content": self._convert_content_to_anthropic(msg.content)
                })
            elif msg.role == "assistant":
                content_blocks = []
                if msg.content:
                    content_blocks.append({"type": "text", "text": msg.content})
                
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        func_name = tc.get("function", {}).get("name")
                        func_args_str = tc.get("function", {}).get("arguments", "{}")
                        try:
                            func_args = json.loads(func_args_str)
                        except Exception:
                            func_args = {}
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tc.get("id"),
                            "name": func_name,
                            "input": func_args
                        })
                
                anthropic_messages.append({
                    "role": "assistant",
                    "content": content_blocks
                })
            elif msg.role == "tool":
                # A tool response in Anthropic is a tool_result within a user message.
                # If the previous message was already a user message with tool results, we can append to it.
                # Otherwise, we create a new user message.
                tool_content = msg.content or ""
                # Parse JSON if possible to make it cleaner, or keep as string
                tool_result_block = {
                    "type": "tool_result",
                    "tool_use_id": msg.tool_call_id,
                    "content": tool_content
                }
                
                if anthropic_messages and anthropic_messages[-1]["role"] == "user" and isinstance(anthropic_messages[-1]["content"], list):
                    anthropic_messages[-1]["content"].append(tool_result_block)
                else:
                    anthropic_messages.append({
                        "role": "user",
                        "content": [tool_result_block]
                    })

        if system_prompt:
            anthropic_data["system"] = system_prompt
        
        anthropic_data["messages"] = anthropic_messages

        # Translate tools
        if request.tools:
            anthropic_tools = []
            for tool in request.tools:
                if tool.get("type") == "function":
                    func = tool.get("function", {})
                    anthropic_tools.append({
                        "name": func.get("name"),
                        "description": func.get("description", ""),
                        "input_schema": func.get("parameters", {"type": "object", "properties": {}})
                    })
            if anthropic_tools:
                anthropic_data["tools"] = anthropic_tools

            # Handle tool_choice
            if request.tool_choice:
                if isinstance(request.tool_choice, str):
                    if request.tool_choice == "auto":
                        anthropic_data["tool_choice"] = {"type": "auto"}
                    elif request.tool_choice == "required":
                        anthropic_data["tool_choice"] = {"type": "any"}
                elif isinstance(request.tool_choice, dict):
                    func_name = request.tool_choice.get("function", {}).get("name")
                    if func_name:
                        anthropic_data["tool_choice"] = {"type": "tool", "name": func_name}

        return anthropic_data

    def _convert_content_to_anthropic(self, content: Any) -> Any:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # Translate image blocks if any (e.g. OpenAI GPT-4 Vision format to Anthropic)
            translated = []
            for item in content:
                if not isinstance(item, dict):
                    translated.append(item)
                    continue
                if item.get("type") == "text":
                    translated.append({"type": "text", "text": item.get("text")})
                elif item.get("type") == "image_url":
                    img_url = item.get("image_url", {}).get("url", "")
                    if img_url.startswith("data:image/"):
                        # Extract base64 and media type: "data:image/jpeg;base64,/9j/4AA..."
                        try:
                            prefix, base64_data = img_url.split(";base64,")
                            media_type = prefix.split("data:")[1]
                            translated.append({
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": base64_data
                                }
                            })
                        except Exception:
                            # Skip if parsing base64 image URL fails
                            continue
            return translated
        return content

    def _translate_response(self, anthropic_res: Dict[str, Any], gateway_model: str) -> ChatCompletionResponse:
        """Translate Anthropic Messages response to OpenAI ChatCompletionResponse."""
        content_text = ""
        tool_calls = []
        
        for block in anthropic_res.get("content", []):
            if block.get("type") == "text":
                content_text += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id"),
                    "type": "function",
                    "function": {
                        "name": block.get("name"),
                        "arguments": json.dumps(block.get("input", {}))
                    }
                })

        # Map stop reason
        stop_reason = anthropic_res.get("stop_reason")
        finish_reason = "stop"
        if stop_reason == "end_turn":
            finish_reason = "stop"
        elif stop_reason == "max_tokens":
            finish_reason = "length"
        elif stop_reason == "tool_use":
            finish_reason = "tool_calls"

        usage = anthropic_res.get("usage", {})
        prompt_tokens = usage.get("input_tokens", 0)
        completion_tokens = usage.get("output_tokens", 0)
        total_tokens = prompt_tokens + completion_tokens

        choice_message = ChoiceMessage(
            role="assistant",
            content=content_text if content_text else None,
            tool_calls=tool_calls if tool_calls else None
        )

        choice = ChatCompletionChoice(
            index=0,
            message=choice_message,
            finish_reason=finish_reason
        )

        return ChatCompletionResponse(
            id=anthropic_res.get("id", f"chatcmpl-{uuid.uuid4()}"),
            object="chat.completion",
            created=int(time.time()),
            model=gateway_model,
            choices=[choice],
            usage=UsageInfo(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens
            )
        )

    async def chat_completion(
        self, request: ChatCompletionRequest, ctx: ProviderContext
    ) -> ChatCompletionResponse:
        url = f"{self.base_url}/messages"
        headers = {
            "x-api-key": ctx.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        data = self._translate_request(request, ctx.provider_model_name)

        async with httpx.AsyncClient(timeout=ctx.timeout) as client:
            try:
                response = await client.post(url, headers=headers, json=data)
                if response.status_code != 200:
                    raise HTTPException(
                        status_code=response.status_code,
                        detail=f"Anthropic error: {response.text}",
                    )
                return self._translate_response(response.json(), request.model)
            except httpx.RequestError as e:
                raise HTTPException(
                    status_code=502,
                    detail=f"Failed to connect to Anthropic: {str(e)}",
                )

    async def chat_completion_stream(
        self, request: ChatCompletionRequest, ctx: ProviderContext
    ) -> AsyncGenerator[ChatCompletionStreamResponse, None]:
        url = f"{self.base_url}/messages"
        headers = {
            "x-api-key": ctx.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        data = self._translate_request(request, ctx.provider_model_name)
        data["stream"] = True

        client = httpx.AsyncClient(timeout=ctx.timeout)
        try:
            req = client.build_request("POST", url, headers=headers, json=data)
            response = await client.send(req, stream=True)

            if response.status_code != 200:
                await response.aread()
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Anthropic streaming error: {response.text}",
                )

            # Local state variables to construct proper OpenAI SSE chunks
            msg_id = f"chatcmpl-{uuid.uuid4()}"
            prompt_tokens = 0
            completion_tokens = 0
            
            # Active tool calls being streamed
            tool_calls_stream: Dict[int, Dict[str, Any]] = {}

            event_type = ""
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data_str = line[5:].strip()
                    try:
                        event_data = json.loads(data_str)
                    except Exception:
                        continue

                    # Handle Anthropic Stream Events
                    if event_type == "message_start":
                        msg_id = event_data.get("message", {}).get("id", msg_id)
                        prompt_tokens = event_data.get("message", {}).get("usage", {}).get("input_tokens", 0)
                        
                        # Yield first chunk with role
                        yield ChatCompletionStreamResponse(
                            id=msg_id,
                            model=request.model,
                            choices=[
                                ChatCompletionStreamChoice(
                                    index=0,
                                    delta=ChoiceDelta(role="assistant")
                                )
                            ],
                            created=int(time.time())
                        )
                    
                    elif event_type == "content_block_start":
                        index = event_data.get("index", 0)
                        block = event_data.get("content_block", {})
                        if block.get("type") == "tool_use":
                            tool_calls_stream[index] = {
                                "id": block.get("id"),
                                "name": block.get("name"),
                                "input_accumulated": ""
                            }
                            # Send initial tool call chunk
                            yield ChatCompletionStreamResponse(
                                id=msg_id,
                                model=request.model,
                                choices=[
                                    ChatCompletionStreamChoice(
                                        index=0,
                                        delta=ChoiceDelta(
                                            tool_calls=[{
                                                "index": index,
                                                "id": block.get("id"),
                                                "type": "function",
                                                "function": {"name": block.get("name"), "arguments": ""}
                                            }]
                                        )
                                    )
                                ],
                                created=int(time.time())
                            )
                    
                    elif event_type == "content_block_delta":
                        index = event_data.get("index", 0)
                        delta = event_data.get("delta", {})
                        
                        if delta.get("type") == "text_delta":
                            text_chunk = delta.get("text", "")
                            yield ChatCompletionStreamResponse(
                                id=msg_id,
                                model=request.model,
                                choices=[
                                    ChatCompletionStreamChoice(
                                        index=0,
                                        delta=ChoiceDelta(content=text_chunk)
                                    )
                                ],
                                created=int(time.time())
                            )
                        elif delta.get("type") == "input_json_delta":
                            json_chunk = delta.get("partial_json", "")
                            if index in tool_calls_stream:
                                tool_calls_stream[index]["input_accumulated"] += json_chunk
                                yield ChatCompletionStreamResponse(
                                    id=msg_id,
                                    model=request.model,
                                    choices=[
                                        ChatCompletionStreamChoice(
                                            index=0,
                                            delta=ChoiceDelta(
                                                tool_calls=[{
                                                    "index": index,
                                                    "function": {"arguments": json_chunk}
                                                }]
                                            )
                                        )
                                    ],
                                    created=int(time.time())
                                )
                    
                    elif event_type == "message_delta":
                        usage = event_data.get("usage", {})
                        completion_tokens = usage.get("output_tokens", completion_tokens)
                        
                        stop_reason = event_data.get("delta", {}).get("stop_reason")
                        finish_reason = "stop"
                        if stop_reason == "end_turn":
                            finish_reason = "stop"
                        elif stop_reason == "max_tokens":
                            finish_reason = "length"
                        elif stop_reason == "tool_use":
                            finish_reason = "tool_calls"
                            
                        # Yield final choices stop chunk
                        yield ChatCompletionStreamResponse(
                            id=msg_id,
                            model=request.model,
                            choices=[
                                ChatCompletionStreamChoice(
                                    index=0,
                                    delta=ChoiceDelta(),
                                    finish_reason=finish_reason
                                )
                            ],
                            created=int(time.time())
                        )
                        
                        # Yield final usage chunk
                        yield ChatCompletionStreamResponse(
                            id=msg_id,
                            model=request.model,
                            choices=[],
                            usage=UsageInfo(
                                prompt_tokens=prompt_tokens,
                                completion_tokens=completion_tokens,
                                total_tokens=prompt_tokens + completion_tokens
                            ),
                            created=int(time.time())
                        )
                        
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to connect to Anthropic stream: {str(e)}",
            )
        finally:
            await client.aclose()

    async def embeddings(
        self, request: EmbeddingsRequest, ctx: ProviderContext
    ) -> EmbeddingsResponse:
        raise HTTPException(
            status_code=501,
            detail="Anthropic provider does not support text embeddings.",
        )
