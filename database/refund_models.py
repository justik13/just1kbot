"""Durable provider-refund outbox models.

Kept in a focused module so refund delivery has an explicit lifecycle instead of
being overloaded onto payment-creation commands.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from config.enums import ProviderRefundOperationStatus
from database.models import Base, sql_enum_in
from utils.datetime_helpers import now_utc


class ProviderRefundOperation(Base):
    __tablename__ = "provider_refund_operations"
    __table_args__ = (
        CheckConstraint(
            sql_enum_in("status", ProviderRefundOperationStatus),
            name="ck_provider_refund_operations_status",
        ),
        CheckConstraint(
            "provider_status IS NULL OR "
            "provider_status IN ('pending','succeeded','canceled')",
            name="ck_provider_refund_operations_provider_status",
        ),
        CheckConstraint(
            "amount > 0 AND amount = trunc(amount)",
            name="ck_provider_refund_operations_whole_amount",
        ),
        CheckConstraint(
            "currency = 'RUB'",
            name="ck_provider_refund_operations_currency_rub",
        ),
        CheckConstraint(
            "attempts >= 0 AND max_attempts > 0",
            name="ck_provider_refund_operations_attempts",
        ),
        Index(
            "ix_provider_refund_operations_claim",
            "next_attempt_at",
            "id",
            postgresql_where=text("status IN ('pending','retry')"),
        ),
        Index(
            "ix_provider_refund_operations_lease",
            "locked_at",
            postgresql_where=text("status='processing'"),
        ),
        Index(
            "uq_provider_refund_operations_active_payment",
            "payment_id",
            unique=True,
            postgresql_where=text("status IN ('pending','processing','retry')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    operation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, default=uuid.uuid4
    )
    payment_id: Mapped[int] = mapped_column(
        ForeignKey("payments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    reservation_id: Mapped[int] = mapped_column(
        ForeignKey("account_balance_reservations.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="RUB", server_default=text("'RUB'")
    )
    provider_payment_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_refund_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True
    )
    provider_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default=text("'pending'")
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=12, server_default=text("12")
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=text("now()")
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(100))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error: Mapped[str | None] = mapped_column(Text)
    requested_by_admin_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=now_utc,
        onupdate=now_utc,
        server_default=text("now()"),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
