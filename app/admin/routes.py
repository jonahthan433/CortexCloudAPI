import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_admin
from app.database.session import get_db
from app.models.billing import BillingTransaction
from app.models.key import APIKey
from app.models.registry import ModelRegistry
from app.models.user import User
from app.models.usage import UsageLog
from app.models.org import Organization
from app.services.models import ModelRegistryService

router = APIRouter()


# Pydantic schemas for Admin operations
class ModelCreate(BaseModel):
    name: str = Field(..., description="Gateway alias name, e.g. gpt-4o")
    provider: str = Field(..., description="Provider, e.g. openai")
    provider_model_name: str = Field(..., description="Upstream provider model name")
    context_length: int = Field(..., ge=1)
    prompt_token_price: float = Field(..., ge=0.0, description="Price per 1M prompt tokens")
    completion_token_price: float = Field(..., ge=0.0, description="Price per 1M completion tokens")
    capabilities: Dict[str, Any] = Field(default_factory=dict, description="Model capability flags")
    is_active: bool = True


class ModelRegistryResponse(BaseModel):
    id: uuid.UUID
    name: str
    provider: str
    provider_model_name: str
    context_length: int
    prompt_token_price: float
    completion_token_price: float
    is_active: bool
    capabilities: Dict[str, Any]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class UserAdminResponse(BaseModel):
    id: uuid.UUID
    email: str
    is_active: bool
    is_admin: bool
    created_at: datetime


class APIKeyAdminResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    prefix: str
    is_active: bool
    created_at: datetime


# Routes
@router.post("/models", response_model=ModelRegistryResponse)
async def admin_create_model(
    payload: ModelCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """Register a new AI model in the system. Requires Admin auth."""
    # Check if model name already exists
    existing = await ModelRegistryService.get_model_by_name(db, payload.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Model name '{payload.name}' already exists in registry."
        )

    model = await ModelRegistryService.create_model(
        db=db,
        name=payload.name,
        provider=payload.provider,
        provider_model_name=payload.provider_model_name,
        context_length=payload.context_length,
        prompt_token_price=payload.prompt_token_price,
        completion_token_price=payload.completion_token_price,
        capabilities=payload.capabilities,
        is_active=payload.is_active
    )
    return model


@router.patch("/models/{model_id}/toggle", response_model=ModelRegistryResponse)
async def admin_toggle_model(
    model_id: uuid.UUID,
    is_active: bool = Query(..., description="Active status to set"),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """Enable or disable a model by its registry ID. Requires Admin auth."""
    model = await ModelRegistryService.toggle_model_active(db, model_id, is_active)
    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model not found in registry."
        )
    return model


@router.get("/users", response_model=List[UserAdminResponse])
async def admin_view_users(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """View list of all registered dashboard users. Requires Admin auth."""
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return list(result.scalars().all())


@router.get("/api-keys", response_model=List[APIKeyAdminResponse])
async def admin_view_keys(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """View all API keys in the gateway. Requires Admin auth."""
    result = await db.execute(select(APIKey).order_by(APIKey.created_at.desc()))
    return list(result.scalars().all())


@router.get("/usage")
async def admin_view_usage(
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """View recent API request logs. Requires Admin auth."""
    result = await db.execute(
        select(UsageLog).order_by(UsageLog.created_at.desc()).limit(limit)
    )
    logs = result.scalars().all()
    
    return [
        {
            "id": log.id,
            "organization_id": log.organization_id,
            "api_key_id": log.api_key_id,
            "correlation_id": log.correlation_id,
            "provider": log.provider,
            "model": log.model,
            "prompt_tokens": log.prompt_tokens,
            "completion_tokens": log.completion_tokens,
            "total_tokens": log.total_tokens,
            "cost": float(log.cost),
            "latency_ms": log.latency_ms,
            "status_code": log.status_code,
            "error_message": log.error_message,
            "request_path": log.request_path,
            "client_ip": log.client_ip,
            "created_at": log.created_at,
        }
        for log in logs
    ]


@router.get("/revenue")
async def admin_view_revenue(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """View gateway financial metrics (total charges and transaction ledger totals). Requires Admin auth."""
    # Sum up all usage_charge transactions
    result = await db.execute(
        select(func.sum(BillingTransaction.amount))
        .filter(BillingTransaction.type == "usage_charge")
    )
    total_debits = result.scalar() or 0.0
    
    # Sum up deposits
    result = await db.execute(
        select(func.sum(BillingTransaction.amount))
        .filter(BillingTransaction.type == "deposit")
    )
    total_deposits = result.scalar() or 0.0

    return {
        "revenue_usd": abs(float(total_debits)),  # Total charges is our revenue
        "deposits_usd": float(total_deposits),
        "ledger_balance_usd": float(total_deposits + total_debits),
    }


@router.get("/provider-health")
async def admin_provider_health(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """
    Calculate upstream provider health dynamically by evaluating success/failure
    rates in the last 15 minutes of requests.
    """
    time_window = datetime.now(timezone.utc) - timedelta(minutes=15)
    providers = ["openai", "anthropic", "gemini", "groq"]
    
    # Query aggregated stats (totals and errors) in a single round-trip
    result = await db.execute(
        select(
            UsageLog.provider,
            func.count(UsageLog.id).label("total"),
            func.sum(case((UsageLog.status_code >= 500, 1), else_=0)).label("failed")
        )
        .filter(UsageLog.created_at >= time_window)
        .group_by(UsageLog.provider)
    )
    
    stats = {row.provider.lower(): (row.total, int(row.failed or 0)) for row in result.all()}
    health_report = {}

    for provider in providers:
        total, failed = stats.get(provider.lower(), (0, 0))
        
        if total == 0:
            status_str = "inactive"
            error_rate = 0.0
        else:
            error_rate = failed / total
            if error_rate < 0.05:
                status_str = "healthy"
            elif error_rate < 0.15:
                status_str = "degraded"
            else:
                status_str = "unhealthy"

        health_report[provider] = {
            "status": status_str,
            "requests_last_15m": total,
            "errors_last_15m": failed,
            "error_rate": float(f"{error_rate:.4f}")
        }

    return health_report
