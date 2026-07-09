import logging
from typing import Any, List, Union
import tiktoken

logger = logging.getLogger("cortexcloud.usage.tokenizer")


def count_tokens(text: str, model_name: str = "gpt-4") -> int:
    """
    Count the number of tokens in a string using tiktoken.
    Falls back to cl100k_base (used by GPT-4 and Claude) if model encoding is unknown.
    """
    if not text:
        return 0
        
    try:
        # Resolve encoding by model name alias
        if "claude" in model_name.lower():
            encoding = tiktoken.get_encoding("cl100k_base")
        elif "gemini" in model_name.lower():
            encoding = tiktoken.get_encoding("cl100k_base")
        else:
            try:
                encoding = tiktoken.encoding_for_model(model_name)
            except KeyError:
                encoding = tiktoken.get_encoding("cl100k_base")
                
        return len(encoding.encode(text))
    except Exception as e:
        logger.warning(f"Failed to count tokens for model '{model_name}': {str(e)}")
        # Fallback to rough character-based estimation: ~4 chars per token
        return len(text) // 4


def count_messages_tokens(messages: List[Any], model_name: str = "gpt-4") -> int:
    """
    Estimate total tokens in a list of chat completion messages.
    """
    total = 0
    for msg in messages:
        # Handle dict or Pydantic model
        role = getattr(msg, "role", None) or msg.get("role", "")
        content = getattr(msg, "content", None) or msg.get("content", "")
        
        total += count_tokens(str(role), model_name)
        
        if isinstance(content, str):
            total += count_tokens(content, model_name)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        total += count_tokens(part.get("text", ""), model_name)
                    # For images, count as a fixed token overhead (e.g. 85 tokens per image for simplicity)
                    elif part.get("type") == "image_url":
                        total += 85
    # Add overhead tokens for chat formatting
    total += 3
    return total


def pre_warm_tokenizers() -> None:
    """Pre-warm tiktoken tokenizers on startup to avoid blocking I/O in requests."""
    try:
        tiktoken.get_encoding("cl100k_base")
        tiktoken.get_encoding("o200k_base")
        logger.info("Pre-warmed tokenizers: cl100k_base and o200k_base cached in memory.")
    except Exception as e:
        logger.warning(f"Failed to pre-warm tokenizers: {str(e)}")
