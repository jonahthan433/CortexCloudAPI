from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field


# Chat completions request schemas
class ChatCompletionMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: Optional[Union[str, List[Dict[str, Any]]]] = None
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None


class ResponseFormat(BaseModel):
    type: Literal["text", "json_object"]


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[ChatCompletionMessage]
    temperature: Optional[float] = Field(default=1.0, ge=0.0, le=2.0)
    top_p: Optional[float] = Field(default=1.0, ge=0.0, le=1.0)
    n: Optional[int] = Field(default=1, ge=1)
    stream: Optional[bool] = Field(default=False)
    stop: Optional[Union[str, List[str]]] = None
    max_tokens: Optional[int] = Field(default=None, ge=1)
    presence_penalty: Optional[float] = Field(default=0.0, ge=-2.0, le=2.0)
    frequency_penalty: Optional[float] = Field(default=0.0, ge=-2.0, le=2.0)
    user: Optional[str] = None
    response_format: Optional[ResponseFormat] = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None


# Chat completions response schemas
class UsageInfo(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChoiceMessage(BaseModel):
    role: str = "assistant"
    content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None


class ChatCompletionChoice(BaseModel):
    index: int
    message: ChoiceMessage
    finish_reason: Optional[Literal["stop", "length", "tool_calls", "content_filter"]] = None


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatCompletionChoice]
    usage: UsageInfo
    system_fingerprint: Optional[str] = None


# Chat completions streaming response schemas
class ChoiceDelta(BaseModel):
    role: Optional[str] = None
    content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None


class ChatCompletionStreamChoice(BaseModel):
    index: int
    delta: ChoiceDelta
    finish_reason: Optional[Literal["stop", "length", "tool_calls", "content_filter"]] = None


class ChatCompletionStreamResponse(BaseModel):
    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: List[ChatCompletionStreamChoice]
    usage: Optional[UsageInfo] = None


# Embeddings request schemas
class EmbeddingsRequest(BaseModel):
    model: str
    input: Union[str, List[str], List[int], List[List[int]]]
    encoding_format: Optional[Literal["float", "base64"]] = "float"
    user: Optional[str] = None


class EmbeddingObject(BaseModel):
    object: str = "embedding"
    index: int
    embedding: List[float]


class EmbeddingsResponse(BaseModel):
    object: str = "list"
    data: List[EmbeddingObject]
    model: str
    usage: UsageInfo


# Model Registry schemas
class ModelObject(BaseModel):
    id: str
    object: Literal["model"] = "model"
    created: int = 1718000000  # Default epoch
    owned_by: str = "cortexcloud"


class ModelListResponse(BaseModel):
    object: Literal["list"] = "list"
    data: List[ModelObject]

