import uuid
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


class BaseBillingService(ABC):
    """
    Abstract Base Class for Billing / Payment Integration.
    Defines methods for managing external customer accounts, charging invoices,
    subscribing to monthly plans, and handling payment provider webhooks.
    """

    @abstractmethod
    async def create_customer(self, organization_id: uuid.UUID, email: str) -> str:
        """
        Create a customer account in the payment processor.
        Returns:
            str: The external payment processor customer ID (e.g. Stripe Customer ID).
        """
        pass

    @abstractmethod
    async def create_subscription(self, customer_id: str, plan_id: str) -> Dict[str, Any]:
        """
        Subscribe a customer to a monthly recurring plan.
        Returns:
            Dict[str, Any]: Subscription details.
        """
        pass

    @abstractmethod
    async def charge_invoice(self, customer_id: str, amount_usd: float, description: str) -> str:
        """
        Create a one-off charge / invoice for the customer.
        Returns:
            str: The external invoice or charge ID.
        """
        pass

    @abstractmethod
    async def handle_webhook(self, payload: Any, signature: Optional[str] = None) -> bool:
        """
        Process incoming payment processor webhook events (e.g., payment succeeded, sub cancelled).
        Returns:
            bool: True if processed successfully.
        """
        pass
