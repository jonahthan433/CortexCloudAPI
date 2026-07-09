import logging
import uuid
from decimal import Decimal
from typing import Optional
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import BillingAccount, BillingTransaction
from app.models.registry import ModelRegistry
from app.models.usage import UsageLog
from app.services.models import ModelRegistryService

logger = logging.getLogger("cortexcloud.services.usage")


class UsageMeteringService:
    """
    Handles calculating cost, logging API usage metrics,
    and charging organization billing balances atomically.
    """

    @staticmethod
    async def record_usage(
        db: AsyncSession,
        organization_id: uuid.UUID,
        api_key_id: Optional[uuid.UUID],
        correlation_id: str,
        model_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: int,
        status_code: int,
        request_path: str,
        client_ip: str,
        error_message: Optional[str] = None
    ) -> UsageLog:
        """
        Record API usage, calculate cost, charge the organization balance,
        and generate a ledger transaction. Runs atomically.
        """
        # 1. Fetch model pricing
        model_entry = await ModelRegistryService.get_model_by_name(db, model_name)
        
        prompt_price_per_1m = Decimal("0.0")
        completion_price_per_1m = Decimal("0.0")
        provider = "unknown"
        
        if model_entry:
            prompt_price_per_1m = Decimal(str(model_entry.prompt_token_price))
            completion_price_per_1m = Decimal(str(model_entry.completion_token_price))
            provider = model_entry.provider

        # 2. Calculate cost (price per 1M tokens)
        cost = Decimal("0.0")
        if prompt_tokens > 0:
            cost += (Decimal(prompt_tokens) * prompt_price_per_1m) / Decimal("1000000.0")
        if completion_tokens > 0:
            cost += (Decimal(completion_tokens) * completion_price_per_1m) / Decimal("1000000.0")

        # Round cost to 6 decimal places
        cost = cost.quantize(Decimal("1.000000"))

        # 3. Create usage log entry
        usage_log = UsageLog(
            organization_id=organization_id,
            api_key_id=api_key_id,
            correlation_id=uuid.UUID(correlation_id),
            provider=provider,
            model=model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cost=float(cost),
            latency_ms=latency_ms,
            status_code=status_code,
            error_message=error_message,
            request_path=request_path,
            client_ip=client_ip,
        )
        db.add(usage_log)

        # 4. Process billing charge (if cost is non-zero and status was successful)
        if cost > 0 and status_code == 200:
            # Query billing account with row-level write lock
            result = await db.execute(
                select(BillingAccount)
                .filter(BillingAccount.organization_id == organization_id)
                .with_for_update()
            )
            billing_account = result.scalar_one_or_none()

            if billing_account:
                # Deduct cost from balance
                old_balance = Decimal(str(billing_account.balance))
                new_balance = old_balance - cost
                billing_account.balance = float(new_balance)
                
                # Create a ledger transaction
                transaction = BillingTransaction(
                    billing_account_id=billing_account.id,
                    amount=float(-cost),
                    type="usage_charge",
                    description=f"Usage charge for {prompt_tokens} prompt + {completion_tokens} completion tokens on model '{model_name}'.",
                )
                db.add(transaction)
                logger.info(f"Charged org {organization_id} cost ${cost} for key {api_key_id}")
            else:
                logger.warning(f"Billing account not found for organization {organization_id}. Usage logged but not charged.")

        await db.commit()
        return usage_log
