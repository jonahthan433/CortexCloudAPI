import uuid
from datetime import datetime
from typing import Any, Dict
from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, JSON, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base


class ModelRegistry(Base):
    __tablename__ = "models"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)  # gateway model name, e.g., "gpt-4o"
    provider: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g., "openai", "anthropic", "gemini"
    provider_model_name: Mapped[str] = mapped_column(String(255), nullable=False)  # upstream model identifier
    context_length: Mapped[int] = mapped_column(Integer, nullable=False)
    
    # Pricing fields representing USD cost per 1,000,000 tokens
    prompt_token_price: Mapped[float] = mapped_column(Numeric(10, 4), default=0.0000, nullable=False)
    completion_token_price: Mapped[float] = mapped_column(Numeric(10, 4), default=0.0000, nullable=False)
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    
    # Store capability flags: {"vision": True, "tool_calling": True, "reasoning": False, "streaming": True}
    capabilities: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
