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
    EmbeddingObject,
    EmbeddingsRequest,
    EmbeddingsResponse,
    UsageInfo,
)


class GeminiProvider(BaseProvider):
    """Provider for Google Gemini API."""

    def __init__(self, base_url: str = "https://generativelanguage.googleapis.com/v1beta"):
        self.base_url = base_url

    def _translate_request(self, request: ChatCompletionRequest) -> Dict[str, Any]:
        """Translate OpenAI ChatCompletionRequest to Gemini generateContent format."""
        gemini_data: Dict[str, Any] = {}
        
        # Generation config mapping
        gen_config = {}
        if request.temperature is not None:
            gen_config["temperature"] = request.temperature
        if request.top_p is not None:
            gen_config["topP"] = request.top_p
        if request.max_tokens is not None:
            gen_config["maxOutputTokens"] = request.max_tokens
        
        # Enable JSON mode if requested
        if request.response_format and request.response_format.type == "json_object":
            gen_config["responseMimeType"] = "application/json"

        if gen_config:
            gemini_data["generationConfig"] = gen_config

        # Extract system prompt and convert messages
        contents = []
        system_instruction_parts = []

        for msg in request.messages:
            if msg.role == "system":
                system_instruction_parts.append({"text": str(msg.content)})
            else:
                role = "user" if msg.role in ("user", "tool") else "model"
                parts = []

                if msg.role == "tool":
                    # Tool response in Gemini is a functionResponse part
                    # We wrap the content in a dict
                    try:
                        resp_data = json.loads(msg.content or "{}")
                    except Exception:
                        resp_data = {"result": msg.content}
                    parts.append({
                        "functionResponse": {
                            "name": msg.name or "",
                            "response": resp_data
                        }
                    })
                elif msg.tool_calls:
                    # Assistant sending tool calls is a functionCall part
                    for tc in msg.tool_calls:
                        func_name = tc.get("function", {}).get("name")
                        func_args_str = tc.get("function", {}).get("arguments", "{}")
                        try:
                            func_args = json.loads(func_args_str)
                        except Exception:
                            func_args = {}
                        parts.append({
                            "functionCall": {
                                "name": func_name,
                                "args": func_args
                            }
                        })
                else:
                    # Translate content (supports multimodal image inputs)
                    parts = self._convert_content_to_gemini(msg.content)

                contents.append({
                    "role": role,
                    "parts": parts
                })

        gemini_data["contents"] = contents

        if system_instruction_parts:
            gemini_data["systemInstruction"] = {
                "parts": system_instruction_parts
            }

        # Translate tools
        if request.tools:
            function_declarations = []
            for tool in request.tools:
                if tool.get("type") == "function":
                    func = tool.get("function", {})
                    # Gemini parameters map directly to JSON Schema format
                    function_declarations.append({
                        "name": func.get("name"),
                        "description": func.get("description", ""),
                        "parameters": func.get("parameters", {"type": "OBJECT", "properties": {}})
                    })
            if function_declarations:
                gemini_data["tools"] = [{"functionDeclarations": function_declarations}]

        return gemini_data

    def _convert_content_to_gemini(self, content: Any) -> List[Dict[str, Any]]:
        if isinstance(content, str):
            return [{"text": content}]
        if isinstance(content, list):
            parts = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text":
                    parts.append({"text": item.get("text")})
                elif item.get("type") == "image_url":
                    img_url = item.get("image_url", {}).get("url", "")
                    if img_url.startswith("data:image/"):
                        try:
                            prefix, base64_data = img_url.split(";base64,")
                            mime_type = prefix.split("data:")[1]
                            parts.append({
                                "inlineData": {
                                    "mimeType": mime_type,
                                    "data": base64_data
                                }
                            })
                        except Exception:
                            continue
            return parts
        return []

    def _translate_response(self, gemini_res: Dict[str, Any], gateway_model: str) -> ChatCompletionResponse:
        """Translate Gemini response to OpenAI format."""
        candidates = gemini_res.get("candidates", [])
        content_text = ""
        tool_calls = []
        finish_reason = "stop"

        if candidates:
            cand = candidates[0]
            content = cand.get("content", {})
            for part in content.get("parts", []):
                if "text" in part:
                    content_text += part["text"]
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    tool_calls.append({
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": fc.get("name"),
                            "arguments": json.dumps(fc.get("args", {}))
                        }
                    })

            gemini_reason = cand.get("finishReason")
            if gemini_reason == "STOP":
                finish_reason = "stop"
            elif gemini_reason == "MAX_TOKENS":
                finish_reason = "length"
            elif gemini_reason == "SAFETY":
                finish_reason = "content_filter"

            if tool_calls:
                finish_reason = "tool_calls"

        usage_meta = gemini_res.get("usageMetadata", {})
        prompt_tokens = usage_meta.get("promptTokenCount", 0)
        completion_tokens = usage_meta.get("candidatesTokenCount", 0)
        total_tokens = usage_meta.get("totalTokenCount", prompt_tokens + completion_tokens)

        choice = ChatCompletionChoice(
            index=0,
            message=ChoiceMessage(
                role="assistant",
                content=content_text if content_text else None,
                tool_calls=tool_calls if tool_calls else None
            ),
            finish_reason=finish_reason
        )

        return ChatCompletionResponse(
            id=f"chatcmpl-{uuid.uuid4()}",
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
        url = f"{self.base_url}/models/{ctx.provider_model_name}:generateContent"
        headers = {
            "x-goog-api-key": ctx.api_key,
            "Content-Type": "application/json",
        }

        data = self._translate_request(request)

        async with httpx.AsyncClient(timeout=ctx.timeout) as client:
            try:
                response = await client.post(url, headers=headers, json=data)
                if response.status_code != 200:
                    raise HTTPException(
                        status_code=response.status_code,
                        detail=f"Gemini error: {response.text}",
                    )
                return self._translate_response(response.json(), request.model)
            except httpx.RequestError as e:
                raise HTTPException(
                    status_code=502,
                    detail=f"Failed to connect to Gemini: {str(e)}",
                )

    async def chat_completion_stream(
        self, request: ChatCompletionRequest, ctx: ProviderContext
    ) -> AsyncGenerator[ChatCompletionStreamResponse, None]:
        url = f"{self.base_url}/models/{ctx.provider_model_name}:streamGenerateContent"
        headers = {
            "x-goog-api-key": ctx.api_key,
            "Content-Type": "application/json",
        }

        data = self._translate_request(request)

        client = httpx.AsyncClient(timeout=ctx.timeout)
        try:
            req = client.build_request("POST", url, headers=headers, json=data)
            response = await client.send(req, stream=True)

            if response.status_code != 200:
                await response.aread()
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Gemini streaming error: {response.text}",
                )

            msg_id = f"chatcmpl-{uuid.uuid4()}"
            prompt_tokens = 0
            completion_tokens = 0
            
            # Send initial assistant role chunk
            yield ChatCompletionStreamResponse(
                id=msg_id,
                model=request.model,
                choices=[ChatCompletionStreamChoice(index=0, delta=ChoiceDelta(role="assistant"))],
                created=int(time.time())
            )

            # Gemini stream is a JSON stream, yielding either SSE-like lines or direct JSON chunks.
            # In the REST API, streamGenerateContent yields a SSE stream where data: is a candidate chunk.
            # We process data lines:
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                
                # Check for standard server-sent-event format
                data_str = line
                if line.startswith("data: "):
                    data_str = line[6:].strip()
                elif line.startswith("[") or line.startswith(","):
                    # Sometimes wrapped in a JSON list (Gemini JSON stream chunk)
                    data_str = line.strip("[], \t")
                    if not data_str:
                        continue

                try:
                    chunk = json.loads(data_str)
                except Exception:
                    continue

                candidates = chunk.get("candidates", [])
                usage_meta = chunk.get("usageMetadata", {})
                
                if usage_meta:
                    prompt_tokens = usage_meta.get("promptTokenCount", prompt_tokens)
                    completion_tokens = usage_meta.get("candidatesTokenCount", completion_tokens)

                if candidates:
                    cand = candidates[0]
                    content = cand.get("content", {})
                    finish_reason = None
                    
                    gemini_reason = cand.get("finishReason")
                    if gemini_reason == "STOP":
                        finish_reason = "stop"
                    elif gemini_reason == "MAX_TOKENS":
                        finish_reason = "length"
                    
                    for part in content.get("parts", []):
                        if "text" in part:
                            text_chunk = part["text"]
                            yield ChatCompletionStreamResponse(
                                id=msg_id,
                                model=request.model,
                                choices=[
                                    ChatCompletionStreamChoice(
                                        index=0,
                                        delta=ChoiceDelta(content=text_chunk),
                                        finish_reason=finish_reason
                                    )
                                ],
                                created=int(time.time())
                            )
                        elif "functionCall" in part:
                            fc = part["functionCall"]
                            tool_id = f"call_{uuid.uuid4().hex[:8]}"
                            yield ChatCompletionStreamResponse(
                                id=msg_id,
                                model=request.model,
                                choices=[
                                    ChatCompletionStreamChoice(
                                        index=0,
                                        delta=ChoiceDelta(
                                            tool_calls=[{
                                                "index": 0,
                                                "id": tool_id,
                                                "type": "function",
                                                "function": {
                                                    "name": fc.get("name"),
                                                    "arguments": json.dumps(fc.get("args", {}))
                                                }
                                            }]
                                        ),
                                        finish_reason="tool_calls"
                                    )
                                ],
                                created=int(time.time())
                            )
            
            # Yield final token usage chunk
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
                detail=f"Failed to connect to Gemini stream: {str(e)}",
            )
        finally:
            await client.aclose()

    async def embeddings(
        self, request: EmbeddingsRequest, ctx: ProviderContext
    ) -> EmbeddingsResponse:
        url = f"{self.base_url}/models/{ctx.provider_model_name}:embedContent"
        headers = {
            "x-goog-api-key": ctx.api_key,
            "Content-Type": "application/json",
        }

        # Convert input
        contents = []
        if isinstance(request.input, str):
            contents.append({"parts": [{"text": request.input}]})
        elif isinstance(request.input, list):
            for item in request.input:
                if isinstance(item, str):
                    contents.append({"parts": [{"text": item}]})
                else:
                    raise HTTPException(
                        status_code=400,
                        detail="Gemini embedding only supports string or list of string inputs.",
                    )

        # Gemini embedContent supports single embedding. For batching, it is :batchEmbedContents
        # For simplicity, we implement single embedding.
        if len(contents) > 1:
            # Batch embedding
            url = f"{self.base_url}/models/{ctx.provider_model_name}:batchEmbedContents"
            requests = [
                {"model": f"models/{ctx.provider_model_name}", "content": c}
                for c in contents
            ]
            data = {"requests": requests}
        else:
            data = {
                "model": f"models/{ctx.provider_model_name}",
                "content": contents[0]
            }

        async with httpx.AsyncClient(timeout=ctx.timeout) as client:
            try:
                response = await client.post(url, headers=headers, json=data)
                if response.status_code != 200:
                    raise HTTPException(
                        status_code=response.status_code,
                        detail=f"Gemini embedding error: {response.text}",
                    )
                
                resp_json = response.json()
                
                # Format into OpenAI structure
                data_list = []
                if "embeddings" in resp_json:
                    # Batch result
                    for idx, emb in enumerate(resp_json["embeddings"]):
                        data_list.append(
                            EmbeddingObject(
                                index=idx,
                                embedding=emb.get("values", [])
                            )
                        )
                else:
                    # Single result
                    emb = resp_json.get("embedding", {})
                    data_list.append(
                        EmbeddingObject(
                            index=0,
                            embedding=emb.get("values", [])
                        )
                    )

                return EmbeddingsResponse(
                    object="list",
                    data=data_list,
                    model=request.model,
                    usage=UsageInfo(prompt_tokens=0, completion_tokens=0, total_tokens=0)
                )
            except httpx.RequestError as e:
                raise HTTPException(
                    status_code=502,
                    detail=f"Failed to connect to Gemini: {str(e)}",
                )
