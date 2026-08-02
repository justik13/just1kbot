"""Manual payment-dispute lifecycle for balance top-ups."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from database.models import Base
from utils.datetime_helpers import now_utc


class PaymentDispute(Base):
    __tablename__ = "payment_disputes"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open','won_by_merchant','lost_by_merchant','manual_review')",
            name="ck_payment_disputes_status",
        ),
        CheckConstraint(
            "amount > 0 AND amount = trunc(amount)",
            name="ck_payment_disputes_whole_amount",
        ),
        CheckConstraint(
            "currency = 'RUB'",
            name="ck_payment_disputes_currency_rub",
        ),
        CheckConstraint(
            "(status IN ('open','manual_review') AND resolved_at IS NULL) OR "
            "(status IN ('won_by_merchant','lost_by_merchant') "
            "AND resolved_at IS NOT NULL)",
            name="ck_payment_disputes_resolution_shape",
        ),
        CheckConstraint(
            "status <> 'lost_by_merchant' OR chargeback_entry_id IS NOT NULL",
            name="ck_payment_disputes_lost_has_debit",
        ),
        Index("ix_payment_disputes_payment_status", "payment_id", "status"),
        Index("ix_payment_disputes_user_status", "user_id", "status"),
        Index(
            "uq_payment_disputes_active_payment",
            "payment_id",
            unique=True,
            postgresql_where=text("status IN ('open','manual_review')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, default=uuid.uuid4
    )
    payment_id: Mapped[int] = mapped_column(
        ForeignKey("payments.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    provider_case_id: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="open", server_default=text("'open'")
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="RUB", server_default=text("'RUB'")
    )
    disputed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reservation_id: Mapped[int | None] = mapped_column(
        ForeignKey("account_balance_reservations.id", ondelete="RESTRICT"),
        unique=True,
    )
    chargeback_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("account_ledger_entries.id", ondelete="RESTRICT"),
        unique=True,
    )
    note: Mapped[str | None] = mapped_column(Text)
    created_by_admin_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    resolved_by_admin_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc,
        server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc,
        onupdate=now_utc, server_default=text("now()")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
