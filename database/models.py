import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

from config.constants import AMNEZIA_PROTOCOL
from config.enums import (
    AccountLedgerEntryType,
    ApiOperationStatus,
    ApiOperationType,
    OrderStatus,
    OrderServiceType,
    PaymentCheckoutStatus,
    PaymentFulfillmentStatus,
    PaymentProviderStatus,
    PaymentReconciliationStatus,
    ServerHealthState,
    ServerLifecycleStatus,
    VPNProvisioningStatus,
    WebhookInboxStatus,
)
from database.sql_helpers import sql_enum_in
from utils.datetime_helpers import now_utc
from utils.encryption import EncryptedString


API_OPERATION_TYPES = tuple(s.value for s in ApiOperationType)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    __table_args__ = (
        Index(
            "ix_users_active_subscription",
            "subscription_end",
            postgresql_where=text("is_deleted = false AND subscription_end IS NOT NULL"),
        ),
        Index(
            "ix_users_banned",
            "telegram_id",
            postgresql_where=text("is_banned = true AND is_deleted = false"),
        ),
        Index(
            "ix_users_expiring_subscription",
            "subscription_end",
            "telegram_id",
            postgresql_where=text(
                """
                is_deleted = false
                AND is_bot_blocked = false
                AND is_banned = false
                AND subscription_end IS NOT NULL
                AND (notified_3d = false OR notified_1d = false OR notified_2h = false)
                """
            ),
        ),
        Index(
            "ix_users_expired_grace_notify",
            "subscription_end",
            "telegram_id",
            postgresql_where=text(
                """
                is_deleted = false
                AND is_bot_blocked = false
                AND subscription_end IS NOT NULL
                AND (notified_expired = false OR notified_grace_12h = false)
                """
            ),
        ),
        Index(
            "ix_users_paginated",
            "created_at",
            "id",
            postgresql_where=text("is_deleted = false"),
            postgresql_ops={"created_at": "DESC", "id": "DESC"},
        ),
        Index(
            "ix_users_username_trgm",
            "username",
            postgresql_using="gin",
            postgresql_ops={"username": "gin_trgm_ops"},
        ),
        Index(
            "ix_users_username_lower",
            text("lower(username)"),
            postgresql_where=text("username IS NOT NULL AND is_deleted = false"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)

    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    subscription_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_trial_reset_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    device_limit: Mapped[int] = mapped_column(Integer, default=0)

    current_tariff_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("tariffs.id", ondelete="SET NULL"),
        nullable=True,
    )

    referred_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)

    last_payment_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    is_bot_blocked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    financial_hold: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    topup_blocked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    financial_block_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    notification_retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_notification_attempt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    notified_3d: Mapped[bool] = mapped_column(Boolean, default=False)
    notified_1d: Mapped[bool] = mapped_column(Boolean, default=False)
    notified_2h: Mapped[bool] = mapped_column(Boolean, default=False)
    notified_expired: Mapped[bool] = mapped_column(Boolean, default=False)
    notified_grace_12h: Mapped[bool] = mapped_column(Boolean, default=False)

    device_creations_today: Mapped[int] = mapped_column(Integer, default=0)
    last_creation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    total_traffic_bytes: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )
    total_wi_traffic_bytes: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )
    monthly_awg_bytes: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )
    monthly_wi_bytes: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )
    traffic_cycle: Mapped[str | None] = mapped_column(
        String(7), nullable=True
    )
    archived_device_traffic: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'")
    )

    profiles = relationship(
        "VPNProfile",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    payments = relationship(
        "Payment",
        back_populates="user",
    )

    orders = relationship(
        "Order",
        back_populates="user",
    )

    current_tariff = relationship(
        "Tariff",
        foreign_keys=[current_tariff_id],
    )

    vless_subscription = relationship(
        "VlessSubscription",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )


class VPNProfile(Base):
    __tablename__ = "vpn_profiles"

    __table_args__ = (
        CheckConstraint(
            sql_enum_in("provisioning_status", VPNProvisioningStatus),
            name="ck_vpn_profiles_provisioning_status",
        ),
        CheckConstraint("desired_version > 0", name="ck_vpn_profiles_desired_version_positive"),
        Index(
            "uq_vpn_profiles_server_peer_id_not_null",
            "server_id",
            "peer_id",
            unique=True,
            postgresql_where=text("peer_id IS NOT NULL"),
        ),
        Index(
            "uq_vpn_profiles_user_server_device_name",
            "user_id",
            "server_id",
            text("lower(device_name)"),
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    server_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("servers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    device_name: Mapped[str] = mapped_column(String(255), nullable=False)
    peer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    raw_config: Mapped[str | None] = mapped_column(EncryptedString(critical=True), nullable=True)

    traffic_down: Mapped[int] = mapped_column(BigInteger, default=0)
    traffic_up: Mapped[int] = mapped_column(BigInteger, default=0)
    raw_last_down: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )
    raw_last_up: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )

    last_connected: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    provisioning_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="active", server_default=text("'active'")
    )
    desired_is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    actual_is_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    desired_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    desired_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    user = relationship("User", back_populates="profiles")
    server = relationship("Server")


class Server(Base):
    __tablename__ = "servers"

    __table_args__ = (
        UniqueConstraint("api_url", name="uq_servers_api_url"),
        CheckConstraint(
            sql_enum_in("lifecycle_status", ServerLifecycleStatus),
            name="ck_servers_lifecycle_status",
        ),
        Index("ix_servers_lifecycle_status", "lifecycle_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    country_flag: Mapped[str | None] = mapped_column(String(10), nullable=True)

    api_url: Mapped[str] = mapped_column(String(500), nullable=False)
    api_key: Mapped[str] = mapped_column(EncryptedString(critical=True), nullable=False)

    protocol: Mapped[str] = mapped_column(
        String(50), nullable=False, default=AMNEZIA_PROTOCOL, server_default=AMNEZIA_PROTOCOL
    )
    max_clients: Mapped[int] = mapped_column(Integer, default=50)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    disabled_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_successful_check: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    health_state: Mapped[str] = mapped_column(
        String(30), default=ServerHealthState.ONLINE, server_default=ServerHealthState.ONLINE
    )
    lifecycle_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=ServerLifecycleStatus.ACTIVE,
        server_default=ServerLifecycleStatus.ACTIVE,
    )
    problem_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_fails: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    consecutive_successes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    recovery_notice_sent: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    last_alert_sent_state: Mapped[str | None] = mapped_column(String(30), nullable=True)

    capabilities: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    extra_data: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    xray_instance_epoch: Mapped[str | None] = mapped_column(String(64), nullable=True)
    xray_instance_boot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    xray_instance_starttime: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Tariff(Base):
    __tablename__ = "tariffs"

    __table_args__ = (
        UniqueConstraint(
            "service_type",
            "device_limit",
            "duration_days",
            name="uq_tariffs_service_device_duration",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    service_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="awg", server_default="awg"
    )
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    device_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    price_rub: Mapped[int] = mapped_column(Integer, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Payment(Base):
    """YooKassa balance top-up tracked through provider and account-ledger state."""

    __tablename__ = "payments"

    __table_args__ = (
        Index(
            "uq_payments_external_id_not_null",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
        Index(
            "uq_payments_public_order_id_not_null",
            "public_order_id",
            unique=True,
            postgresql_where=text("public_order_id IS NOT NULL"),
        ),
        Index(
            "uq_payments_provider_idempotency_key_not_null",
            "provider_idempotency_key",
            unique=True,
            postgresql_where=text("provider_idempotency_key IS NOT NULL"),
        ),
        Index(
            "uq_payments_visible_topup_user",
            "user_id",
            unique=True,
            postgresql_where=text(
                "ui_visible=true AND checkout_status='active' "
                "AND provider_status NOT IN ('succeeded','canceled','refunded')"
            ),
        ),
        Index("ix_payments_user_created", "user_id", "created_at"),
        Index(
            "ix_payments_attention",
            "created_at",
            postgresql_where=text(
                "reconciliation_status IN ('required','mismatch','manual_review')"
            ),
        ),
        Index(
            "ix_payments_referral_bonus_unprocessed",
            "created_at",
            postgresql_where=text(
                "provider_status = 'succeeded' "
                "AND fulfillment_status = 'succeeded' "
                "AND NOT (COALESCE(topup_context, '{}'::jsonb) @> '{\"referral_bonus_processed\": true}'::jsonb)"
            ),
        ),
        Index(
            "ix_payments_recovery_pending",
            "created_at",
            postgresql_where=text(
                "external_id IS NOT NULL AND provider_status IN ('creating', 'pending', 'waiting_for_capture', 'unknown')"
            ),
        ),
        Index(
            "ix_payments_recovery_unfulfilled",
            "created_at",
            postgresql_where=text(
                "provider_status = 'succeeded' AND provider_confirmed_at IS NOT NULL AND fulfillment_status NOT IN ('succeeded', 'reversed', 'manual_review')"
            ),
        ),
        Index(
            "ix_payments_auto_fulfill_retry",
            "created_at",
            postgresql_where=text(
                "provider_status = 'succeeded' "
                "AND provider_confirmed_at IS NOT NULL "
                "AND fulfillment_status = 'succeeded' "
                "AND topup_context ? 'auto_fulfill_action' "
                "AND topup_context->>'auto_fulfill_status' = 'failed'"
            ),
        ),
        CheckConstraint(
            sql_enum_in("provider_status", PaymentProviderStatus),
            name="ck_payments_provider_status",
        ),
        CheckConstraint(
            sql_enum_in("fulfillment_status", PaymentFulfillmentStatus),
            name="ck_payments_fulfillment_status",
        ),
        CheckConstraint(
            sql_enum_in("reconciliation_status", PaymentReconciliationStatus),
            name="ck_payments_reconciliation_status",
        ),
        CheckConstraint(
            sql_enum_in("checkout_status", PaymentCheckoutStatus),
            name="ck_payments_checkout_status",
        ),
        CheckConstraint(
            "currency = 'RUB' AND amount > 0 AND amount = trunc(amount)",
            name="ck_payments_topup_money",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="RUB", server_default=text("'RUB'")
    )
    public_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="creating", server_default=text("'creating'")
    )
    fulfillment_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="not_ready", server_default=text("'not_ready'")
    )
    reconciliation_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="ok", server_default=text("'ok'")
    )
    checkout_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default=text("'active'")
    )
    ui_visible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    user_cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    manual_review_reason: Mapped[str | None] = mapped_column(String(255))
    topup_context: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
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
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    credited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    credit_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payment_url_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_last_error_code: Mapped[str | None] = mapped_column(String(100))
    provider_last_error: Mapped[str | None] = mapped_column(Text)
    fulfillment_last_error_code: Mapped[str | None] = mapped_column(String(100))
    fulfillment_last_error: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(String(255), index=True)
    payment_url: Mapped[str | None] = mapped_column(String(1000))
    payment_method: Mapped[str | None] = mapped_column(String(50))

    user = relationship("User", back_populates="payments")


class Order(Base):
    """Clean commercial order entity for simple billing."""

    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_user_created", "user_id", "created_at"),
        Index(
            "ix_orders_external_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
        Index("ix_orders_status", "status"),
        CheckConstraint(
            sql_enum_in("status", OrderStatus),
            name="ck_orders_status",
        ),
        CheckConstraint(
            sql_enum_in("service_type", OrderServiceType),
            name="ck_orders_service_type",
        ),
        CheckConstraint(
            "amount_rub >= 0 AND amount_rub = trunc(amount_rub)",
            name="ck_orders_amount_rub",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    service_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="awg", server_default=text("'awg'")
    )
    tariff_id: Mapped[int | None] = mapped_column(
        ForeignKey("tariffs.id", ondelete="SET NULL"), nullable=True
    )
    amount_rub: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    duration_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    traffic_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    device_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payment_method: Mapped[str] = mapped_column(
        String(30), nullable=False, default="yookassa", server_default=text("'yookassa'")
    )
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payment_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default=text("'pending'")
    )
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=text("now()")
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user = relationship("User", back_populates="orders", foreign_keys=[user_id])
    tariff = relationship("Tariff", foreign_keys=[tariff_id])
    ledger_entries = relationship("AccountLedgerEntry", back_populates="order")


class AccountLedgerEntry(Base):
    """Append-only real-money account history.

    The signed sum is the user's accounting position.  A negative position is
    exposed as debt, never as a spendable negative balance.
    """

    __tablename__ = "account_ledger_entries"
    __table_args__ = (
        CheckConstraint(
            sql_enum_in("entry_type", AccountLedgerEntryType),
            name="ck_account_ledger_entry_type",
        ),
        CheckConstraint("currency = 'RUB'", name="ck_account_ledger_currency_rub"),
        CheckConstraint(
            "amount <> 0 AND amount = trunc(amount)",
            name="ck_account_ledger_whole_nonzero_amount",
        ),
        CheckConstraint(
            "(entry_type = 'payment_credit' AND amount > 0 "
            "AND (payment_id IS NOT NULL OR order_id IS NOT NULL) AND quote_id IS NULL "
            "AND reversal_of_id IS NULL) OR "
            "(entry_type = 'purchase_debit' AND amount < 0 "
            "AND payment_id IS NULL AND (quote_id IS NOT NULL OR order_id IS NOT NULL) "
            "AND reversal_of_id IS NULL) OR "
            "(entry_type = 'purchase_reversal' AND amount > 0 "
            "AND payment_id IS NULL AND (quote_id IS NOT NULL OR order_id IS NOT NULL) "
            "AND reversal_of_id IS NOT NULL) OR "
            "(entry_type IN ('refund_debit','chargeback_debit') "
            "AND amount < 0 AND (payment_id IS NOT NULL OR order_id IS NOT NULL) "
            "AND quote_id IS NULL AND reversal_of_id IS NULL) OR "
            "(entry_type = 'admin_adjustment' AND payment_id IS NULL "
            "AND quote_id IS NULL AND reversal_of_id IS NULL)",
            name="ck_account_ledger_entry_shape",
        ),
        Index(
            "ix_account_ledger_user_history",
            "user_id",
            "created_at",
            "id",
        ),
        Index(
            "uq_account_ledger_payment_credit",
            "payment_id",
            unique=True,
            postgresql_where=text("entry_type='payment_credit' AND payment_id IS NOT NULL"),
        ),
        Index(
            "uq_account_ledger_purchase_debit",
            "quote_id",
            unique=True,
            postgresql_where=text("entry_type='purchase_debit' AND quote_id IS NOT NULL"),
        ),
        Index(
            "uq_account_ledger_order_debit",
            "order_id",
            unique=True,
            postgresql_where=text("entry_type='purchase_debit' AND order_id IS NOT NULL"),
        ),
        Index(
            "uq_account_ledger_order_credit",
            "order_id",
            unique=True,
            postgresql_where=text("entry_type='payment_credit' AND order_id IS NOT NULL"),
        ),
        Index(
            "uq_account_ledger_reversal",
            "reversal_of_id",
            unique=True,
            postgresql_where=text("entry_type='purchase_reversal'"),
        ),
        Index(
            "ix_account_ledger_payment_debits",
            "payment_id",
            postgresql_where=text("entry_type IN ('refund_debit','chargeback_debit')"),
        ),
        Index(
            "ix_account_ledger_order_id",
            "order_id",
            postgresql_where=text("order_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    entry_type: Mapped[str] = mapped_column(String(30), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="RUB", server_default=text("'RUB'")
    )
    payment_id: Mapped[int | None] = mapped_column(
        ForeignKey("payments.id", ondelete="RESTRICT"), nullable=True
    )
    quote_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=True,
    )
    reversal_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("account_ledger_entries.id", ondelete="RESTRICT"), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=text("now()")
    )

    order = relationship("Order", back_populates="ledger_entries", foreign_keys=[order_id])


class AccountLedgerAllocation(Base):
    """Append-only FIFO attribution of account credits to purchase debits."""

    __tablename__ = "account_ledger_allocations"
    __table_args__ = (
        CheckConstraint(
            "amount > 0 AND amount = trunc(amount)",
            name="ck_account_allocations_whole_positive_amount",
        ),
        UniqueConstraint(
            "credit_entry_id",
            "debit_entry_id",
            name="uq_account_allocations_credit_debit",
        ),
        Index("ix_account_allocations_credit", "credit_entry_id", "id"),
        Index("ix_account_allocations_debit", "debit_entry_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    credit_entry_id: Mapped[int] = mapped_column(
        ForeignKey("account_ledger_entries.id", ondelete="RESTRICT"), nullable=False
    )
    debit_entry_id: Mapped[int] = mapped_column(
        ForeignKey("account_ledger_entries.id", ondelete="RESTRICT"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=text("now()")
    )


class WebhookInbox(Base):
    __tablename__ = "webhook_inbox"
    __table_args__ = (
        UniqueConstraint("provider", "event_key", name="uq_webhook_inbox_provider_event_key"),
        CheckConstraint(sql_enum_in("status", WebhookInboxStatus), name="ck_webhook_inbox_status"),
        Index(
            "ix_webhook_inbox_claim",
            "next_attempt_at",
            "id",
            postgresql_where=text("status IN ('pending','retry')"),
        ),
        Index(
            "ix_webhook_inbox_lease", "locked_at", postgresql_where=text("status = 'processing'")
        ),
        Index(
            "ix_webhook_inbox_retention",
            "received_at",
            "id",
            postgresql_where=text("status IN ('succeeded', 'dead')"),
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider: Mapped[str] = mapped_column(String(30))
    event_key: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(100))
    provider_object_id: Mapped[str] = mapped_column(String(255))
    payment_external_id: Mapped[str | None] = mapped_column(String(255), index=True)
    public_order_id: Mapped[str | None] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default=text("'pending'")
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, default=30, server_default=text("30"))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(100))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_logs"

    __table_args__ = (
        Index("ix_audit_logs_created_at_desc", "created_at", postgresql_ops={"created_at": "DESC"}),
        Index(
            "ix_audit_logs_target",
            func.lower(text("target_type")),
            text("target_id"),
            text("created_at DESC"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    admin_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)

    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    details: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class BroadcastProgress(Base):
    __tablename__ = "broadcast_progress"

    __table_args__ = (
        Index(
            "ix_broadcast_in_progress",
            "status",
            "created_at",
            postgresql_where=text("status = 'in_progress'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    admin_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    total_count: Mapped[int] = mapped_column(Integer, nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    last_processed_id: Mapped[int] = mapped_column(BigInteger, default=0)

    target_audience: Mapped[str] = mapped_column(String(20), default="all")

    broadcast_text: Mapped[str] = mapped_column(Text, nullable=False)
    media_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_type: Mapped[str] = mapped_column(String(50), nullable=False)
    label: Mapped[str] = mapped_column(String(50), nullable=False)

    status: Mapped[str] = mapped_column(String(20), default="in_progress", index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )


class APIOperation(Base):
    """Durable command record for future Amnezia API workers.

    ``payload`` must contain only non-secret operation parameters. API credentials
    are kept separately in the encrypted snapshot column.
    """

    __tablename__ = "api_operations"

    __table_args__ = (
        CheckConstraint(
            sql_enum_in("operation_type", ApiOperationType),
            name="ck_api_operations_operation_type",
        ),
        CheckConstraint(
            sql_enum_in("status", ApiOperationStatus),
            name="ck_api_operations_status",
        ),
        CheckConstraint(
            "attempts >= 0",
            name="ck_api_operations_attempts_nonnegative",
        ),
        CheckConstraint(
            "max_attempts > 0",
            name="ck_api_operations_max_attempts_positive",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_api_operations_idempotency_key",
        ),
        Index(
            "ix_api_operations_claim",
            "status",
            "next_attempt_at",
            "created_at",
            postgresql_where=text("status IN ('pending', 'retry')"),
        ),
        Index(
            "ix_api_operations_processing_lock",
            "locked_at",
            postgresql_where=text("status = 'processing'"),
        ),
        Index("ix_api_operations_server_id", "server_id"),
        Index("ix_api_operations_profile_id", "profile_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default=text("'pending'")
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)

    server_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("servers.id", ondelete="SET NULL"), nullable=True
    )
    profile_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("vpn_profiles.id", ondelete="SET NULL"), nullable=True
    )

    server_name_snapshot: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_url_snapshot: Mapped[str | None] = mapped_column(String(500), nullable=True)
    api_key_snapshot: Mapped[str | None] = mapped_column(
        EncryptedString(critical=True), nullable=True
    )

    peer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10, server_default=text("10")
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=text("now()")
    )

    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

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
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


DEFAULT_MAINTENANCE_MESSAGE = (
    "🛠 Бот находится на техническом обслуживании. Пожалуйста, попробуйте позже."
)


class MaintenanceMode(Base):
    __tablename__ = "maintenance_mode"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )


class HubMessage(Base):
    __tablename__ = "hub_messages"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Durable marker for Telegram message-effect screens: lets render_hub
    # restore the "clean hub on next navigation" invariant after restart.
    is_effect_message: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )


class WhiteInternetSubscription(Base):
    """White Internet (Белый Интернет) subscription lifecycle and node state."""

    __tablename__ = "white_internet_subscriptions"

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'ACTIVE', 'EXHAUSTED', 'EXPIRED', 'DISABLED')",
            name="ck_white_internet_subscriptions_status",
        ),
        CheckConstraint(
            "provisioning_status IN ('PENDING_CREATE', 'ACTIVE', 'PENDING_UPDATE', 'PENDING_DELETE', 'SYNCED_INACTIVE', 'FAILED')",
            name="ck_white_internet_subscriptions_provisioning_status",
        ),
        CheckConstraint(
            "base_traffic_bytes >= 0 AND extra_traffic_bytes >= 0 AND traffic_used_bytes >= 0 "
            "AND traffic_uplink_bytes >= 0 AND traffic_downlink_bytes >= 0",
            name="ck_white_internet_subscriptions_traffic_nonnegative",
        ),
        CheckConstraint(
            "device_limit >= 1 AND device_limit <= 3",
            name="ck_white_internet_subscriptions_device_limit",
        ),
        Index(
            "uq_white_internet_live_user",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'ACTIVE', 'EXHAUSTED')"),
        ),
        Index(
            "ix_white_internet_subscriptions_active_hwids",
            "active_hwids",
            postgresql_using="gin",
        ),
        Index(
            "ix_wi_subs_expiring_notify",
            "expires_at",
            "user_id",
            postgresql_where=text(
                "status IN ('ACTIVE', 'EXHAUSTED', 'EXPIRED') AND (notified_3d = false OR notified_1d = false OR notified_2h = false OR notified_expired = false)"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    origin_node_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("servers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING", server_default=text("'PENDING'"), index=True
    )
    status_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    base_traffic_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=53687091200, server_default=text("53687091200")
    )
    extra_traffic_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )

    @hybrid_property
    def traffic_limit_bytes(self) -> int:
        return (self.base_traffic_bytes or 0) + (self.extra_traffic_bytes or 0)

    @traffic_limit_bytes.setter
    def traffic_limit_bytes(self, value: int) -> None:
        self.base_traffic_bytes = int(value)
        self.extra_traffic_bytes = 0

    @traffic_limit_bytes.expression
    def traffic_limit_bytes(cls):
        return cls.base_traffic_bytes + cls.extra_traffic_bytes

    traffic_used_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    traffic_uplink_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    traffic_downlink_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    traffic_overage_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    last_uplink_snapshot: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    last_downlink_snapshot: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )

    traffic_stats_epoch: Mapped[str | None] = mapped_column(String(64), nullable=True)

    active_hwids: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, default=dict, server_default=text("'{}'::jsonb")
    )
    device_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    last_device_reset_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_trial: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    provisioning_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="PENDING_CREATE",
        server_default=text("'PENDING_CREATE'"),
    )
    desired_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    actual_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_reconciled_node_epoch: Mapped[str | None] = mapped_column(String(64), nullable=True)

    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Two-phase trial reset: True means the row must be hard-deleted by the
    # reconciliation worker only after the node confirms the client disabled
    # (SYNCED_INACTIVE with matching versions). Never delete rows that still
    # have an unconfirmed presence on the node — that would orphan credentials.
    pending_hard_delete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    notified_3d: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    notified_1d: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    notified_2h: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    notified_expired: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    notified_90p: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

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

    user = relationship("User", foreign_keys=[user_id])
    origin_node = relationship("Server", foreign_keys=[origin_node_id])


class WhiteInternetOrphanCleanup(Base):
    """Durable outbox for disabling a client UUID on a former origin node.

    Inserted in the same transaction as a renew migration that moves a
    subscription to a new origin. The reconciliation worker claims pending rows
    (FOR UPDATE SKIP LOCKED) and converges the old node to disabled, so a lost
    fire-and-forget task or a process restart can never silently orphan an
    active credential on the old node.
    """

    __tablename__ = "white_internet_orphan_cleanups"

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'done')",
            name="ck_white_internet_orphan_cleanups_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    server_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("servers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    client_uuid: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    desired_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default=text("'pending'")
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

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

    server = relationship("Server", foreign_keys=[server_id])


class AdminOperationIdempotency(Base):
    """Stores deterministic idempotency tokens for admin UI mutations with 7-day TTL."""

    __tablename__ = "admin_operation_idempotency"

    op_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    admin_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )


class VlessSubscription(Base):
    """VLESS TLS subscription for standard access with INCY HWID device quota tracking."""

    __tablename__ = "vless_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    active_hwids: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, default=dict, server_default=text("'{}'::jsonb")
    )

    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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

    user = relationship("User", back_populates="vless_subscription")
