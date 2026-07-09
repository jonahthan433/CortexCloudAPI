import hmac
import hashlib
from datetime import datetime, timezone
from typing import Optional
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.core.security import decode_access_token, hash_api_key
from app.models.user import User
from app.models.key import APIKey
from app.models.org import Organization
from app.models.billing import BillingAccount

# OAuth2 scheme for dashboard JWT auth
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/dashboard/auth/login", auto_error=False)

# API Key header extraction for gateway endpoints
api_key_header = APIKeyHeader(name="Authorization", auto_error=False)


async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
) -> User:
    """FastAPI dependency to retrieve the current logged in user from JWT."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise credentials_exception

    # Token can be prefixed with "Bearer " if parsed from header manually
    if token.lower().startswith("bearer "):
        token = token[7:]

    user_id = decode_access_token(token)
    if user_id is None:
        raise credentials_exception

    result = await db.execute(select(User).filter(User.id == user_id, User.is_active == True))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception
    return user


async def get_current_admin(
    current_user: User = Depends(get_current_user)
) -> User:
    """Dependency to check if the current user is a system admin."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The user does not have enough privileges",
        )
    return current_user


async def verify_api_key(
    authorization: Optional[str] = Security(api_key_header),
    db: AsyncSession = Depends(get_db)
) -> APIKey:
    """
    Verify the Bearer API Key sent in the Authorization header.
    Returns the database APIKey model with loaded organization relationships.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization Header. Provide Bearer API key.",
        )

    # Parse Bearer token
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format. Must be 'Bearer <key>'.",
        )
    
    plain_key = parts[1]
    
    # Hash the provided plain key
    hashed = hash_api_key(plain_key)

    # Query the active API Key
    result = await db.execute(
        select(APIKey)
        .filter(APIKey.hashed_key == hashed, APIKey.is_active == True)
    )
    api_key = result.scalar_one_or_none()

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or inactive API Key.",
        )

    # Check key expiration
    if api_key.expires_at and api_key.expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key has expired.",
        )

    # Check key revocation
    if api_key.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key has been revoked.",
        )

    # Check organization billing balance is positive
    billing_res = await db.execute(
        select(BillingAccount).filter(BillingAccount.organization_id == api_key.organization_id)
    )
    billing = billing_res.scalar_one_or_none()
    if not billing or billing.balance <= 0:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Billing balance is exhausted or negative. Please top up your account.",
        )

    return api_key
