from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import verify_api_key
from app.database.session import get_db
from app.models.key import APIKey
from app.schemas.openai import ModelListResponse, ModelObject
from app.services.models import ModelRegistryService

router = APIRouter()


@router.get("/models", response_model=ModelListResponse)
async def list_models(
    db: AsyncSession = Depends(get_db),
    api_key: APIKey = Depends(verify_api_key)
) -> ModelListResponse:
    """
    List all models currently available in the gateway registry.
    Requires Bearer API Key authentication.
    """
    db_models = await ModelRegistryService.get_active_models(db)
    
    models_data = [
        ModelObject(
            id=model.name,
            created=int(model.created_at.timestamp()) if model.created_at else 1718000000,
            owned_by=model.provider
        )
        for model in db_models
    ]
    
    return ModelListResponse(data=models_data)
