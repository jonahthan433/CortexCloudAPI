import asyncio
import pytest
from decimal import Decimal
from typing import AsyncGenerator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.security import hash_api_key, get_password_hash
from app.database.session import Base
from app.models.user import User
from app.models.org import Organization, OrganizationMember
from app.models.key import APIKey
from app.models.registry import ModelRegistry
from app.models.billing import BillingAccount, BillingTransaction

# Use the active database URL for testing, but we'll clean up tables
test_engine = create_async_engine(settings.DATABASE_URL, echo=False)
TestSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

# Set mock API keys for testing to ensure provider validation passes
settings.OPENAI_API_KEY = "test-openai-key"
settings.ANTHROPIC_API_KEY = "test-anthropic-key"
settings.GEMINI_API_KEY = "test-gemini-key"
settings.GROQ_API_KEY = "test-groq-key"


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
async def setup_db():
    # Make sure all tables are created
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
         await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture(autouse=True)
async def clean_database():
    """Truncate all tables before each test to guarantee test isolation."""
    async with TestSessionLocal() as session:
        # Disable foreign keys check in postgres to truncate cleanly
        await session.execute(text("TRUNCATE TABLE billing_transactions, usage_logs, api_keys, organization_members, billing_accounts, organizations, users, models CASCADE;"))
        await session.commit()


@pytest.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@pytest.fixture
async def seed_data(db: AsyncSession):
    """Seed default models, a test user, an organization, and an API key."""
    # 1. Seed Models
    models = [
        ModelRegistry(
            name="gpt-4o",
            provider="openai",
            provider_model_name="gpt-4o",
            context_length=128000,
            prompt_token_price=5.0000,
            completion_token_price=15.0000,
            capabilities={"vision": True, "tool_calling": True, "streaming": True, "fallback_model": "claude-3-5-sonnet"}
        ),
        ModelRegistry(
            name="claude-3-5-sonnet",
            provider="anthropic",
            provider_model_name="claude-3-5-sonnet-20240620",
            context_length=200000,
            prompt_token_price=3.0000,
            completion_token_price=15.0000,
            capabilities={"vision": True, "tool_calling": True, "streaming": True}
        ),
        ModelRegistry(
            name="gemini-1.5-pro",
            provider="gemini",
            provider_model_name="gemini-1.5-pro",
            context_length=1000000,
            prompt_token_price=7.0000,
            completion_token_price=21.0000,
            capabilities={"vision": True, "tool_calling": True, "streaming": True}
        ),
        ModelRegistry(
            name="text-embedding-3-small",
            provider="openai",
            provider_model_name="text-embedding-3-small",
            context_length=8191,
            prompt_token_price=0.0200,
            completion_token_price=0.0000,
            capabilities={"vision": False, "tool_calling": False, "streaming": False}
        )
    ]
    for m in models:
        db.add(m)

    # 2. Seed Admin User
    admin = User(
        email="admin@cortexcloud.ai",
        hashed_password=get_password_hash("adminpassword"),
        is_active=True,
        is_admin=True
    )
    db.add(admin)
    await db.flush()

    # 3. Seed Regular User
    user = User(
        email="developer@example.com",
        hashed_password=get_password_hash("devpassword"),
        is_active=True,
        is_admin=False
    )
    db.add(user)
    await db.flush()

    # 4. Create Organization
    org = Organization(
        name="Test Dev Org",
        owner_id=user.id
    )
    db.add(org)
    await db.flush()

    # Add member association
    member = OrganizationMember(
        organization_id=org.id,
        user_id=user.id,
        role="admin"
    )
    db.add(member)

    # 5. Create Billing Account (with $50.00 credit)
    billing = BillingAccount(
        organization_id=org.id,
        balance=50.0000,
        tier="pay_as_you_go"
    )
    db.add(billing)
    await db.flush()

    # Log deposit transaction
    deposit_tx = BillingTransaction(
        billing_account_id=billing.id,
        amount=50.0000,
        type="deposit",
        description="Initial deposit."
    )
    db.add(deposit_tx)

    # 6. Seed API Key
    # Plain: cx-live-testkey12345
    plain_key = "cx-live-testkey12345"
    hashed_key = hash_api_key(plain_key)
    api_key = APIKey(
        organization_id=org.id,
        name="Development Key",
        prefix="cx-live-testkey",
        hashed_key=hashed_key,
        is_active=True,
        permissions={"allowed_models": ["*"], "rate_limit_rpm": 60}
    )
    db.add(api_key)
    
    await db.commit()

    return {
        "user_id": user.id,
        "org_id": org.id,
        "api_key_id": api_key.id,
        "plain_key": plain_key,
        "admin_id": admin.id
    }
