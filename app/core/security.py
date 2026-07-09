import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Tuple, Union
import jwt
import bcrypt

from app.core.config import settings


def get_password_hash(password: str) -> str:
    """Hash a password using bcrypt."""
    pwd_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode('utf-8')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a bcrypt hash."""
    pwd_bytes = plain_password.encode('utf-8')
    hashed_bytes = hashed_password.encode('utf-8')
    try:
        return bcrypt.checkpw(pwd_bytes, hashed_bytes)
    except Exception:
        return False


def create_access_token(subject: Union[str, Any], expires_delta: Union[timedelta, None] = None) -> str:
    """Create a signed JWT access token for dashboard users."""
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode = {"exp": expire, "sub": str(subject)}
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> Union[str, None]:
    """Decode a JWT access token and return the subject (user ID) if valid."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        return payload.get("sub")
    except Exception:
        return None


def generate_api_key() -> Tuple[str, str, str]:
    """
    Generate a new API key.
    
    Returns:
        Tuple[str, str, str]: (plain_key, prefix, hashed_key)
        - plain_key: The full key to display once to the user, e.g. "cx-live-abc123xyz789..."
        - prefix: The key prefix for display, e.g. "cx-live-abc123xy"
        - hashed_key: The secure SHA256 hash of the plain key to store in the database.
    """
    # 32 bytes of secure random bytes, represented as a hex string (64 characters)
    random_part = secrets.token_hex(32)
    plain_key = f"cx-live-{random_part}"
    
    # Prefix is "cx-live-" plus first 8 characters of the random part (16 total chars)
    prefix = f"cx-live-{random_part[:8]}"
    
    # Hash plain_key with HMAC-SHA256 using API_KEY_SALT
    hashed_key = hmac.new(
        settings.API_KEY_SALT.encode(),
        plain_key.encode(),
        hashlib.sha256
    ).hexdigest()
    
    return plain_key, prefix, hashed_key


def hash_api_key(plain_key: str) -> str:
    """Hash an API key using HMAC-SHA256."""
    return hmac.new(
        settings.API_KEY_SALT.encode(),
        plain_key.encode(),
        hashlib.sha256
    ).hexdigest()
