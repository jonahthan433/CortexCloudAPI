import asyncio
import logging
import uuid
from decimal import Decimal

from sqlalchemy import select
from app.core.security import get_password_hash, generate_api_key
from app.database.session import AsyncSessionLocal
from app.models.user import User
from app.models.org import Organization, OrganizationMember
from app.models.key import APIKey
from app.models.registry import ModelRegistry
from app.models.billing import BillingAccount

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cortexcloud.seed")


async def seed():
    logger.info("Connecting to database to seed dev environment...")
    async with AsyncSessionLocal() as db:
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
                name="gemini-2.5-flash",
                provider="gemini",
                provider_model_name="gemini-2.5-flash",
                context_length=1000000,
                prompt_token_price=2.0000,
                completion_token_price=6.0000,
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
            # Check if model already exists
            res = await db.execute(select(ModelRegistry).filter(ModelRegistry.name == m.name))
            if not res.scalar_one_or_none():
                db.add(m)
                logger.info(f"Seeding model: {m.name}")

        # 2. Seed Admin User
        admin_email = "admin@cortexcloud.ai"
        res = await db.execute(select(User).filter(User.email == admin_email))
        admin = res.scalar_one_or_none()
        if not admin:
            admin = User(
                email=admin_email,
                hashed_password=get_password_hash("adminpassword"),
                is_active=True,
                is_admin=True
            )
            db.add(admin)
            logger.info("Seeding admin user...")
            await db.flush()

        # 3. Seed Developer User
        dev_email = "developer@example.com"
        res = await db.execute(select(User).filter(User.email == dev_email))
        dev = res.scalar_one_or_none()
        if not dev:
            dev = User(
                email=dev_email,
                hashed_password=get_password_hash("devpassword"),
                is_active=True,
                is_admin=False
            )
            db.add(dev)
            logger.info("Seeding developer user...")
            await db.flush()

        # 4. Create Organization
        res = await db.execute(select(Organization).filter(Organization.owner_id == dev.id))
        org = res.scalar_one_or_none()
        if not org:
            org = Organization(
                name="CortexCloud Dev Org",
                owner_id=dev.id
            )
            db.add(org)
            logger.info("Creating developer organization...")
            await db.flush()

            # Add member association
            member = OrganizationMember(
                organization_id=org.id,
                user_id=dev.id,
                role="admin"
            )
            db.add(member)

            # Create Billing Account
            billing = BillingAccount(
                organization_id=org.id,
                balance=1000.00,
                currency="USD",
                tier="pay_as_you_go"
            )
            db.add(billing)
            logger.info("Creating billing account with $1000 balance...")

        # 5. Create default API Key
        res = await db.execute(select(APIKey).filter(APIKey.organization_id == org.id))
        api_key = res.scalar_one_or_none()
        if not api_key:
            plain_key, prefix, hashed_key = generate_api_key()
            api_key = APIKey(
                organization_id=org.id,
                name="Developer Default Key",
                prefix=prefix,
                hashed_key=hashed_key,
                is_active=True,
                permissions={"allowed_models": ["*"], "rate_limit_rpm": 200}
            )
            db.add(api_key)
            logger.info("Generating default developer API key...")
            await db.flush()
            print("\n" + "="*50)
            print(f"DEV WORKSPACE SEED SUCCESSFUL!")
            print(f"Admin User Email: {admin_email}")
            print("Admin User Password: adminpassword")
            print(f"Developer User Email: {dev_email}")
            print("Developer User Password: devpassword")
            print(f"Generated API Key: {plain_key}")
            print("="*50 + "\n")
        else:
            print("\nWorkspace already seeded.")

        await db.commit()
    logger.info("Seeding complete.")


if __name__ == "__main__":
    asyncio.run(seed())
