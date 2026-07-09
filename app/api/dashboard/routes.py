import uuid
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.security import get_password_hash, verify_password, create_access_token, generate_api_key
from app.database.session import get_db
from app.models.billing import BillingAccount, BillingTransaction
from app.models.key import APIKey
from app.models.org import Organization, OrganizationMember
from app.models.registry import ModelRegistry
from app.models.user import User
from app.models.usage import UsageLog
from app.services.models import ModelRegistryService

router = APIRouter()


# Schemas
class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    is_active: bool
    is_admin: bool

    class Config:
        from_attributes = True


class APIKeyCreateRequest(BaseModel):
    name: str = Field(..., max_length=255)
    daily_limit: Optional[int] = None
    monthly_limit: Optional[int] = None


class APIKeyCreateResponse(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    plain_key: str  # Only returned ONCE on creation
    created_at: datetime


class APIKeyListResponse(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    is_active: bool
    daily_limit: Optional[int]
    monthly_limit: Optional[int]
    created_at: datetime
    revoked_at: Optional[datetime]

    class Config:
        from_attributes = True


# Auth Endpoints
@router.post("/auth/register", response_model=UserResponse)
async def register(payload: UserRegister, db: AsyncSession = Depends(get_db)):
    """Register a new user, create an organization, and seed $10.00 free balance."""
    # Check if user already exists
    result = await db.execute(select(User).filter(User.email == payload.email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered."
        )

    # 1. Create User
    hashed_pwd = get_password_hash(payload.password)
    user = User(
        email=payload.email,
        hashed_password=hashed_pwd,
        is_active=True,
        is_admin=False
    )
    db.add(user)
    await db.flush()  # Populates user.id

    # 2. Create Organization
    org = Organization(
        name=f"{payload.email.split('@')[0]}'s Workspace",
        owner_id=user.id
    )
    db.add(org)
    await db.flush()

    # 3. Add to Organization Members
    member = OrganizationMember(
        organization_id=org.id,
        user_id=user.id,
        role="admin"
    )
    db.add(member)

    # 4. Initialize Billing Account with $10.00 Free Trial Credit
    billing = BillingAccount(
        organization_id=org.id,
        balance=10.000000,
        tier="free"
    )
    db.add(billing)
    await db.flush()

    # 5. Log seed transaction
    seed_tx = BillingTransaction(
        billing_account_id=billing.id,
        amount=10.000000,
        type="deposit",
        description="Free trial signup bonus credits."
    )
    db.add(seed_tx)

    await db.commit()
    await db.refresh(user)
    return user


@router.post("/auth/login", response_model=Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db)
):
    """Authenticate a dashboard user and return a JWT access token."""
    result = await db.execute(select(User).filter(User.email == form_data.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user account."
        )

    access_token = create_access_token(subject=user.id)
    return Token(access_token=access_token)


@router.get("/auth/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    """Get current authenticated user info."""
    return user


# API Keys Endpoints
@router.post("/api-keys", response_model=APIKeyCreateResponse)
async def create_key(
    payload: APIKeyCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new API Key for the user's organization."""
    # Find user's organization via membership
    result = await db.execute(
        select(Organization)
        .join(OrganizationMember)
        .filter(OrganizationMember.user_id == user.id)
    )
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User organization not found."
        )

    plain_key, prefix, hashed_key = generate_api_key()
    
    api_key = APIKey(
        organization_id=org.id,
        name=payload.name,
        prefix=prefix,
        hashed_key=hashed_key,
        is_active=True,
        daily_limit=payload.daily_limit,
        monthly_limit=payload.monthly_limit,
        permissions={"allowed_models": ["*"], "rate_limit_rpm": 200}
    )
    
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    return APIKeyCreateResponse(
        id=api_key.id,
        name=api_key.name,
        prefix=api_key.prefix,
        plain_key=plain_key,
        created_at=api_key.created_at
    )


@router.get("/api-keys", response_model=List[APIKeyListResponse])
async def list_keys(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List all API keys belonging to the user's organization."""
    result = await db.execute(
        select(APIKey)
        .join(Organization)
        .join(OrganizationMember)
        .filter(OrganizationMember.user_id == user.id, APIKey.revoked_at == None)
        .order_by(APIKey.created_at.desc())
    )
    return list(result.scalars().all())


@router.delete("/api-keys/{key_id}")
async def revoke_key(
    key_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Revoke (delete) a specific API Key."""
    result = await db.execute(
        select(APIKey)
        .join(Organization)
        .join(OrganizationMember)
        .filter(OrganizationMember.user_id == user.id, APIKey.id == key_id)
    )
    api_key = result.scalar_one_or_none()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API Key not found or access denied."
        )

    api_key.is_active = False
    api_key.revoked_at = datetime.now(timezone.utc)
    await db.commit()
    return {"detail": "API Key revoked successfully."}


# Billing Endpoints
@router.get("/billing/balance")
async def get_balance(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Retrieve current organization credit balance and subscription tier."""
    result = await db.execute(
        select(BillingAccount)
        .join(Organization)
        .join(OrganizationMember)
        .filter(OrganizationMember.user_id == user.id)
    )
    billing = result.scalar_one_or_none()
    if not billing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Billing account not found."
        )
    return {
        "balance": float(billing.balance),
        "currency": billing.currency,
        "tier": billing.tier,
        "stripe_customer_id": billing.stripe_customer_id
    }


@router.get("/billing/transactions")
async def get_transactions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """View transaction ledger history for the organization."""
    result = await db.execute(
        select(BillingTransaction)
        .join(BillingAccount)
        .join(Organization)
        .join(OrganizationMember)
        .filter(OrganizationMember.user_id == user.id)
        .order_by(BillingTransaction.created_at.desc())
    )
    txs = result.scalars().all()
    return [
        {
            "id": t.id,
            "amount": float(t.amount),
            "type": t.type,
            "description": t.description,
            "created_at": t.created_at
        }
        for t in txs
    ]


@router.post("/billing/deposit")
async def simulated_deposit(
    amount: Decimal = Query(Decimal("20.00"), ge=Decimal("1.00")),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Simulate a credit card deposit top-up (adds balance)."""
    result = await db.execute(
        select(BillingAccount)
        .join(Organization)
        .join(OrganizationMember)
        .filter(OrganizationMember.user_id == user.id)
    )
    billing = result.scalar_one_or_none()
    if not billing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Billing account not found."
        )

    billing.balance += amount
    
    tx = BillingTransaction(
        billing_account_id=billing.id,
        amount=amount,
        type="deposit",
        description=f"Simulated deposit top-up via dashboard."
    )
    db.add(tx)
    await db.commit()

    return {"detail": f"Successfully deposited ${amount:.2f}.", "new_balance": float(billing.balance)}


# Usage Analytics Endpoints
@router.get("/analytics")
async def get_analytics(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get aggregated token usage volume, costs, and request counts."""
    # Find user's organization via membership
    result = await db.execute(
        select(Organization.id)
        .join(OrganizationMember)
        .filter(OrganizationMember.user_id == user.id)
    )
    org_id = result.scalar_one_or_none()
    if not org_id:
        return {"error": "Organization not found."}

    # Sum of tokens & cost
    totals = await db.execute(
        select(
            func.sum(UsageLog.prompt_tokens).label("prompt"),
            func.sum(UsageLog.completion_tokens).label("completion"),
            func.sum(UsageLog.cost).label("cost"),
            func.count(UsageLog.id).label("requests"),
            func.avg(UsageLog.latency_ms).label("latency")
        )
        .filter(UsageLog.organization_id == org_id)
    )
    metrics = totals.first()

    # Model breakdowns
    breakdowns_res = await db.execute(
        select(
            UsageLog.model,
            func.sum(UsageLog.total_tokens).label("tokens"),
            func.sum(UsageLog.cost).label("cost"),
            func.count(UsageLog.id).label("requests")
        )
        .filter(UsageLog.organization_id == org_id)
        .group_by(UsageLog.model)
    )
    breakdowns = [
        {"model": b.model, "tokens": b.tokens, "cost": float(b.cost or 0), "requests": b.requests}
        for b in breakdowns_res.all()
    ]

    return {
        "summary": {
            "total_prompt_tokens": int(metrics.prompt or 0),
            "total_completion_tokens": int(metrics.completion or 0),
            "total_tokens": int((metrics.prompt or 0) + (metrics.completion or 0)),
            "total_cost_usd": float(metrics.cost or 0),
            "total_requests": int(metrics.requests or 0),
            "average_latency_ms": float(metrics.latency or 0)
        },
        "by_model": breakdowns
    }


# Request History
@router.get("/requests")
async def get_request_history(
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """View historical requests log for user's organization."""
    result = await db.execute(
        select(UsageLog)
        .join(Organization)
        .join(OrganizationMember)
        .filter(OrganizationMember.user_id == user.id)
        .order_by(UsageLog.created_at.desc())
        .limit(limit)
    )
    logs = result.scalars().all()
    return [
        {
            "id": log.id,
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
            "created_at": log.created_at
        }
        for log in logs
    ]


# Models list
@router.get("/models")
async def get_dashboard_models(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List active models available to use in this gateway (dashboard view)."""
    db_models = await ModelRegistryService.get_active_models(db)
    return [
        {
            "id": model.name,
            "provider": model.provider,
            "context_length": model.context_length,
            "prompt_price_per_1m": float(model.prompt_token_price),
            "completion_price_per_1m": float(model.completion_token_price),
            "capabilities": model.capabilities
        }
        for model in db_models
    ]
