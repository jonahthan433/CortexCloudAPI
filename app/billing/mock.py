import uuid
from typing import Any, Dict, Optional

from app.billing.base import BaseBillingService


class MockBillingService(BaseBillingService):
    """
    Mock implementation of BaseBillingService for local execution
    and testing without external APIs (like Stripe).
    """

    async def create_customer(self, organization_id: uuid.UUID, email: str) -> str:
        # Returns a mock customer ID
        mock_id = f"cus_mock_{uuid.uuid4().hex[:12]}"
        return mock_id

    async def create_subscription(self, customer_id: str, plan_id: str) -> Dict[str, Any]:
        # Returns mock subscription payload
        sub_id = f"sub_mock_{uuid.uuid4().hex[:12]}"
        return {
            "subscription_id": sub_id,
            "customer_id": customer_id,
            "plan_id": plan_id,
            "status": "active",
            "current_period_end": int(time_time_mock := 1718000000 + 30 * 86400)
        }

    async def charge_invoice(self, customer_id: str, amount_usd: float, description: str) -> str:
        # Returns mock charge ID
        charge_id = f"ch_mock_{uuid.uuid4().hex[:12]}"
        return charge_id

    async def handle_webhook(self, payload: Any, signature: Optional[str] = None) -> bool:
        # Silently approve all mock webhooks
        return True
