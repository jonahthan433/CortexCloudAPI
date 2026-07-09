import uuid
from datetime import datetime
from typing import List, Optional, TYPE_CHECKING
from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base

if TYPE_CHECKING:
    from app.models.org import Organization


class BillingAccount(Base):
    __tablename__ = "billing_accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    balance: Mapped[float] = mapped_column(Numeric(12, 6), default=0.000000, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="USD", nullable=False)
    tier: Mapped[str] = mapped_column(String(50), default="free", nullable=False)  # "free", "pay_as_you_go", "subscription"
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization", back_populates="billing_account")
    transactions: Mapped[List["BillingTransaction"]] = relationship(
        "BillingTransaction", back_populates="billing_account", cascade="all, delete-orphan"
    )


class BillingTransaction(Base):
    __tablename__ = "billing_transactions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    billing_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("billing_accounts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    amount: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False)  # positive for deposit, negative for charge
    type: Mapped[str] = mapped_column(String(50), nullable=False)  # "deposit", "usage_charge", "refund"
    description: Mapped[str] = mapped_column(String(555), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    billing_account: Mapped["BillingAccount"] = relationship("BillingAccount", back_populates="transactions")
