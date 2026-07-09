import uuid
from typing import Any, Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.registry import ModelRegistry


class ModelRegistryService:
    @staticmethod
    async def get_active_models(db: AsyncSession) -> List[ModelRegistry]:
        """Retrieve all active models registered in the gateway."""
        result = await db.execute(
            select(ModelRegistry)
            .filter(ModelRegistry.is_active == True)
            .order_by(ModelRegistry.name)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_model_by_name(db: AsyncSession, name: str) -> Optional[ModelRegistry]:
        """Retrieve a registered model by its gateway alias."""
        result = await db.execute(
            select(ModelRegistry).filter(ModelRegistry.name == name)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def create_model(
        db: AsyncSession,
        name: str,
        provider: str,
        provider_model_name: str,
        context_length: int,
        prompt_token_price: float,
        completion_token_price: float,
        capabilities: Dict[str, Any],
        is_active: bool = True
    ) -> ModelRegistry:
        """Create and register a new model in the system."""
        model = ModelRegistry(
            name=name,
            provider=provider,
            provider_model_name=provider_model_name,
            context_length=context_length,
            prompt_token_price=prompt_token_price,
            completion_token_price=completion_token_price,
            capabilities=capabilities,
            is_active=is_active,
        )
        db.add(model)
        await db.commit()
        await db.refresh(model)
        return model

    @staticmethod
    async def toggle_model_active(db: AsyncSession, model_id: uuid.UUID, is_active: bool) -> Optional[ModelRegistry]:
        """Enable or disable a model by its registry ID."""
        result = await db.execute(
            select(ModelRegistry).filter(ModelRegistry.id == model_id)
        )
        model = result.scalar_one_or_none()
        if model:
            model.is_active = is_active
            await db.commit()
            await db.refresh(model)
        return model
