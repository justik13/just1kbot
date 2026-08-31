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
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

from config.constants import AMNEZIA_PROTOCOL
from config.enums import (
    AccountLedgerEntryType,
    AccountReservationStatus,
    AccountReservationType,
    ApiOperationStatus,
    ApiOperationType,
    EntitlementEntryType,
    PaidValueEntryType,
    PaymentCheckoutStatus,
    PaymentDisputeStatus,
    PaymentFulfillmentStatus,
    PaymentProviderOperationStatus,
    PaymentProviderStatus,
    PaymentQueueStatus,
    PaymentReconciliationStatus,
    ServerHealthState,
    TariffQuoteOperation,
    TariffQuoteStatus,
    VPNProvisioningStatus,
    WebhookInboxStatus,
)
from database.sql_helpers import sql_enum_in
from utils.datetime_helpers import now_utc
from utils.encryption import EncryptedString


API_OPERATION_TYPES = tuple(s.value for s in ApiOperationType)
API_OPERATION_STATUSES = tuple(s.value for s in ApiOperationStatus)
PAYMENT_PROVIDER_STATUSES = tuple(s.value for s in PaymentProviderStatus)
PAYMENT_FULFILLMENT_STATUSES = tuple(s.value for s in PaymentFulfillmentStatus)
PAYMENT_RECONCILIATION_STATUSES = tuple(s.value for s in PaymentReconciliationStatus)
PAYMENT_PROVIDER_OPERATION_STATUSES = tuple(s.value for s in PaymentProviderOperationStatus)
PAYMENT_QUEUE_STATUSES = tuple(s.value for s in PaymentQueueStatus)
ACCOUNT_LEDGER_ENTRY_TYPES = tuple(s.value for s in AccountLedgerEntryType)
ACCOUNT_RESERVATION_TYPES = tuple(s.value for s in AccountReservationType)
ACCOUNT_RESERVATION_STATUSES = tuple(s.value for s in AccountReservationStatus)
PAID_VALUE_ENTRY_TYPES = tuple(s.value for s in PaidValueEntryType)
ENTITLEMENT_ENTRY_TYPES = tuple(s.value for s in EntitlementEntryType)
TARIFF_QUOTE_OPERATIONS = tuple(s.value for s in TariffQuoteOperation)
TARIFF_QUOTE_STATUSES = tuple(s.value for s in TariffQuoteStatus)
VPN_PROVISIONING_STATUSES = tuple(s.value for s in VPNProvisioningStatus)
WEBHOOK_INBOX_STATUSES = tuple(s.value for s in WebhookInboxStatus)
PAYMENT_DISPUTE_STATUSES = tuple(s.value for s in PaymentDisputeStatus)
PAYMENT_CHECKOUT_STATUSES = tuple(s.value for s in PaymentCheckoutStatus)


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
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)

    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    subscription_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    financial_block_reason: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )

    notification_retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_notification_attempt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    notified_3d: Mapped[bool] = mapped_column(Boolean, default=False)
    notified_1d: Mapped[bool] = mapped_column(Boolean, default=False)
    notified_2h: Mapped[bool] = mapped_column(Boolean, default=False)
    notified_expired: Mapped[bool] = mapped_column(Boolean, default=False)
    notified_grace_12h: Mapped[bool] = mapped_column(Boolean, default=False)

    device_creations_today: Mapped[int] = mapped_column(Integer, default=0)
    last_creation_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    profiles = relationship(
        "VPNProfile",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    payments = relationship(
        "Payment",
        back_populates="user",
    )

    current_tariff = relationship(
        "Tariff",
        foreign_keys=[current_tariff_id],
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
            "uq_vpn_profiles_server_peer_id_not_null", "server_id", "peer_id",
            unique=True, postgresql_where=text("peer_id IS NOT NULL"),
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
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    country_flag: Mapped[str | None] = mapped_column(String(10), nullable=True)

    api_url: Mapped[str] = mapped_column(String(500), nullable=False)
    api_key: Mapped[str] = mapped_column(EncryptedString(critical=True), nullable=False)

    protocol: Mapped[str] = mapped_column(String(50), default=AMNEZIA_PROTOCOL)
    max_clients: Mapped[int] = mapped_column(Integer, default=50)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    disabled_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_successful_check: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    health_state: Mapped[str] = mapped_column(
        String(30), default=ServerHealthState.ONLINE, server_default=ServerHealthState.ONLINE
    )
    problem_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_fails: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    consecutive_successes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    recovery_notice_sent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    last_alert_sent_state: Mapped[str | None] = mapped_column(String(30), nullable=True)

    capabilities: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
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


class TariffVersion(Base):
    __tablename__ = "tariff_versions"
    __table_args__ = (
        UniqueConstraint("tariff_id", "version_number", name="uq_tariff_versions_number"),
        CheckConstraint("duration_hours > 0", name="ck_tariff_versions_duration_positive"),
        CheckConstraint("device_limit > 0", name="ck_tariff_versions_device_limit_positive"),
        CheckConstraint("price_rub > 0", name="ck_tariff_versions_price_positive"),
        CheckConstraint("currency = 'RUB'", name="ck_tariff_versions_currency_rub"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tariff_id: Mapped[int] = mapped_column(ForeignKey("tariffs.id", ondelete="RESTRICT"), index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name_snapshot: Mapped[str] = mapped_column(String(100), nullable=False)
    duration_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    device_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    price_rub: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="RUB", server_default=text("'RUB'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc,
        server_default=text("now()")
    )
    tariff = relationship("Tariff", foreign_keys=[tariff_id])

    @property
    def duration_days(self) -> int:
        return self.duration_hours // 24


class TariffQuote(Base):
    __tablename__ = "tariff_quotes"
    __table_args__ = (
        CheckConstraint(sql_enum_in("operation_type", TariffQuoteOperation), name="ck_tariff_quotes_operation"),
        CheckConstraint(sql_enum_in("status", TariffQuoteStatus), name="ck_tariff_quotes_status"),
        CheckConstraint("currency = 'RUB'", name="ck_tariff_quotes_currency_rub"),
        CheckConstraint("current_paid_hours >= 0 AND bonus_hours >= 0 AND resulting_paid_hours >= 0 AND resulting_bonus_hours >= 0", name="ck_tariff_quotes_hours_nonnegative"),
        CheckConstraint("current_paid_value_rub >= 0 AND amount_due_rub >= 0 AND resulting_paid_value_rub >= 0 AND rounding_loss_value_rub >= 0", name="ck_tariff_quotes_values_nonnegative"),
        CheckConstraint("rounding_loss_hours >= 0 AND rounding_loss_hours < 1", name="ck_tariff_quotes_rounding_loss"),
        CheckConstraint("resulting_paid_value_rub <= current_paid_value_rub + amount_due_rub", name="ck_tariff_quotes_value_invariant"),
        CheckConstraint("operation_type <> 'change' OR (source_tariff_version_id IS NOT NULL AND target_tariff_version_id IS NOT NULL AND source_tariff_version_id <> target_tariff_version_id AND balance_as_of IS NOT NULL AND source_subscription_end IS NOT NULL AND source_balance_fingerprint IS NOT NULL AND source_entitlement_entry_ids IS NOT NULL AND source_ledger_entry_ids IS NOT NULL)", name="ck_tariff_quotes_change_source_snapshot"),
        CheckConstraint("source_balance_fingerprint IS NULL OR source_balance_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_tariff_quotes_fingerprint"),
        CheckConstraint("(status = 'consumed' AND consumed_at IS NOT NULL AND manual_review_at IS NULL) OR (status = 'manual_review' AND manual_review_at IS NOT NULL) OR (status IN ('active','expired','cancelled') AND consumed_at IS NULL AND manual_review_at IS NULL)", name="ck_tariff_quotes_lifecycle_timestamps"),
        CheckConstraint("service_type IN ('awg', 'white_internet')", name="ck_tariff_quotes_service_type"),
        Index("uq_tariff_quotes_active_change_user", "user_id", unique=True, postgresql_where=text("operation_type='change' AND status='active'")),
        Index("uq_tariff_quotes_active_checkout", "user_id", "service_type", "target_tariff_version_id", unique=True, postgresql_where=text("status='active' AND operation_type IN ('purchase','renew')")),
        Index(
            "ix_tariff_quotes_consumed_journal",
            text("consumed_at DESC NULLS LAST"),
            text("created_at DESC"),
            postgresql_where=text("status = 'consumed'"),
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    service_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="awg", server_default="awg"
    )
    operation_type: Mapped[str] = mapped_column(String(20))
    source_tariff_version_id: Mapped[int | None] = mapped_column(ForeignKey("tariff_versions.id", ondelete="RESTRICT"))
    target_tariff_version_id: Mapped[int] = mapped_column(ForeignKey("tariff_versions.id", ondelete="RESTRICT"))
    current_paid_hours: Mapped[int] = mapped_column(Integer)
    current_paid_value_rub: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    bonus_hours: Mapped[int] = mapped_column(Integer)
    amount_due_rub: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    resulting_paid_hours: Mapped[int] = mapped_column(Integer)
    resulting_paid_value_rub: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    resulting_bonus_hours: Mapped[int] = mapped_column(Integer)
    rounding_loss_hours: Mapped[Decimal] = mapped_column(Numeric(18, 12))
    rounding_loss_value_rub: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    status: Mapped[str] = mapped_column(String(20), default="active")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    manual_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    diagnostic_reason: Mapped[str | None] = mapped_column(String(255))
    balance_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_subscription_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_balance_fingerprint: Mapped[str | None] = mapped_column(String(64))
    source_entitlement_entry_ids: Mapped[list | None] = mapped_column(JSONB)
    source_ledger_entry_ids: Mapped[list | None] = mapped_column(JSONB)
    purchase_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    user = relationship("User", foreign_keys=[user_id])
    target_tariff_version = relationship("TariffVersion", foreign_keys=[target_tariff_version_id])
    source_tariff_version = relationship("TariffVersion", foreign_keys=[source_tariff_version_id])


class PaidValueLedgerEntry(Base):
    """Append-only paid subscription value created only from account purchases."""

    __tablename__ = "paid_value_ledger"
    __table_args__ = (
        CheckConstraint(
            sql_enum_in("entry_type", PaidValueEntryType),
            name="ck_paid_value_ledger_entry_type",
        ),
        CheckConstraint("currency = 'RUB'", name="ck_paid_value_ledger_currency_rub"),
        CheckConstraint(
            "paid_value_rub_delta <> 'NaN'::numeric",
            name="ck_paid_value_ledger_finite_value",
        ),
        CheckConstraint(
            "entry_type <> 'account_purchase' OR "
            "(quote_id IS NOT NULL AND paid_hours_delta > 0 "
            "AND paid_value_rub_delta > 0)",
            name="ck_paid_value_account_purchase_shape",
        ),
        CheckConstraint(
            "entry_type <> 'tariff_conversion' OR quote_id IS NOT NULL",
            name="ck_paid_value_conversion_shape",
        ),
        Index(
            "uq_paid_value_account_purchase",
            "quote_id",
            unique=True,
            postgresql_where=text("entry_type='account_purchase'"),
        ),
        Index(
            "uq_paid_value_conversion_quote",
            "quote_id",
            unique=True,
            postgresql_where=text("entry_type='tariff_conversion'"),
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[str] = mapped_column(String(100), nullable=False)
    entry_type: Mapped[str] = mapped_column(String(40), nullable=False)
    paid_hours_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    paid_value_rub_delta: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="RUB", server_default=text("'RUB'")
    )
    tariff_version_id: Mapped[int] = mapped_column(
        ForeignKey("tariff_versions.id", ondelete="RESTRICT"), nullable=False
    )
    quote_id: Mapped[int | None] = mapped_column(
        ForeignKey("tariff_quotes.id", ondelete="RESTRICT"), index=True
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=text("now()")
    )


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
    user_cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    manual_review_reason: Mapped[str | None] = mapped_column(String(255))
    topup_context: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, onupdate=now_utc,
        server_default=text("now()")
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
    events = relationship(
        "PaymentEvent", back_populates="payment", cascade="all, delete-orphan"
    )


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
            "AND payment_id IS NOT NULL AND quote_id IS NULL "
            "AND reversal_of_id IS NULL) OR "
            "(entry_type = 'purchase_debit' AND amount < 0 "
            "AND payment_id IS NULL AND quote_id IS NOT NULL "
            "AND reversal_of_id IS NULL) OR "
            "(entry_type = 'purchase_reversal' AND amount > 0 "
            "AND payment_id IS NULL AND quote_id IS NOT NULL "
            "AND reversal_of_id IS NOT NULL) OR "
            "(entry_type IN ('refund_debit','chargeback_debit') "
            "AND amount < 0 AND payment_id IS NOT NULL "
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
            postgresql_where=text("entry_type='payment_credit'"),
        ),
        Index(
            "uq_account_ledger_purchase_debit",
            "quote_id",
            unique=True,
            postgresql_where=text("entry_type='purchase_debit'"),
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
            postgresql_where=text(
                "entry_type IN ('refund_debit','chargeback_debit')"
            ),
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
    quote_id: Mapped[int | None] = mapped_column(
        ForeignKey("tariff_quotes.id", ondelete="RESTRICT"), nullable=True
    )
    reversal_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("account_ledger_entries.id", ondelete="RESTRICT"), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(160), nullable=False, unique=True
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=text("now()")
    )


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
    idempotency_key: Mapped[str] = mapped_column(
        String(180), nullable=False, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=text("now()")
    )


class AccountBalanceReservation(Base):
    """Spend hold used while an external refund or dispute is unresolved."""

    __tablename__ = "account_balance_reservations"
    __table_args__ = (
        CheckConstraint(
            sql_enum_in("reservation_type", AccountReservationType),
            name="ck_account_reservations_type",
        ),
        CheckConstraint(
            sql_enum_in("status", AccountReservationStatus),
            name="ck_account_reservations_status",
        ),
        CheckConstraint(
            "amount > 0 AND amount = trunc(amount)",
            name="ck_account_reservations_whole_positive_amount",
        ),
        CheckConstraint("currency = 'RUB'", name="ck_account_reservations_currency_rub"),
        CheckConstraint(
            "(status = 'active' AND resolved_at IS NULL) OR "
            "(status IN ('released','consumed') AND resolved_at IS NOT NULL)",
            name="ck_account_reservations_lifecycle",
        ),
        Index(
            "ix_account_reservations_active_user",
            "user_id",
            "id",
            postgresql_where=text("status='active'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    payment_id: Mapped[int] = mapped_column(
        ForeignKey("payments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    reservation_type: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="RUB", server_default=text("'RUB'")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default=text("'active'")
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(180), nullable=False, unique=True
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=text("now()")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaymentProviderOperation(Base):
    __tablename__ = "payment_provider_operations"
    __table_args__ = (
        CheckConstraint("operation_type IN ('create_payment','reconcile_payment')", name="ck_payment_provider_operations_type"),
        CheckConstraint(sql_enum_in("status", PaymentProviderOperationStatus), name="ck_payment_provider_operations_status"),
        Index("ix_payment_provider_operations_claim", "next_attempt_at", "id", postgresql_where=text("status IN ('pending','retry')")),
        Index("ix_payment_provider_operations_lease", "locked_at", postgresql_where=text("status = 'processing'")),
        Index("uq_payment_provider_create", "payment_id", unique=True, postgresql_where=text("operation_type='create_payment'")),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id", ondelete="RESTRICT"), index=True)
    operation_type: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default=text("'pending'"))
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, default=12, server_default=text("12"))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(100))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookInbox(Base):
    __tablename__ = "webhook_inbox"
    __table_args__ = (
        UniqueConstraint("provider", "event_key", name="uq_webhook_inbox_provider_event_key"),
        CheckConstraint(sql_enum_in("status", WebhookInboxStatus), name="ck_webhook_inbox_status"),
        Index("ix_webhook_inbox_claim", "next_attempt_at", "id", postgresql_where=text("status IN ('pending','retry')")),
        Index("ix_webhook_inbox_lease", "locked_at", postgresql_where=text("status = 'processing'")),
        Index("ix_webhook_inbox_retention", "received_at", "id", postgresql_where=text("status IN ('succeeded', 'dead')")),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider: Mapped[str] = mapped_column(String(30))
    event_key: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(100))
    provider_object_id: Mapped[str] = mapped_column(String(255))
    payment_external_id: Mapped[str | None] = mapped_column(String(255), index=True)
    public_order_id: Mapped[str | None] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, default=30, server_default=text("30"))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(100))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    last_error: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EntitlementEntry(Base):
    __tablename__ = "entitlement_entries"
    __table_args__ = (
        UniqueConstraint(
            "beneficiary_user_id", "source_type", "source_id", "entry_type",
            name="uq_entitlement_entries_source",
        ),
        CheckConstraint(
            sql_enum_in("entry_type", EntitlementEntryType),
            name="ck_entitlement_entries_type",
        ),
        CheckConstraint(
            "(entry_type IN ('account_purchase_grant','referral_user_bonus',"
            "'referral_referrer_bonus','manual_grant') AND days_delta > 0 "
            "AND reversed_entry_id IS NULL "
            "AND (hours_delta IS NULL OR hours_delta = days_delta * 24)) OR "
            "(entry_type = 'tariff_change' AND source_type = 'quote' "
            "AND days_delta = 0 AND hours_delta > 0 "
            "AND reversed_entry_id IS NULL) OR "
            "(entry_type = 'referral_reversal' AND days_delta < 0 "
            "AND reversed_entry_id IS NOT NULL "
            "AND (hours_delta IS NULL OR hours_delta = days_delta * 24))",
            name="ck_entitlement_entries_shape",
        ),
        Index(
            "ix_entitlement_entries_user_history",
            "beneficiary_user_id", "created_at", "id",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    beneficiary_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(30), nullable=False)
    source_id: Mapped[str] = mapped_column(String(100), nullable=False)
    entry_type: Mapped[str] = mapped_column(String(40), nullable=False)
    days_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    hours_delta: Mapped[int | None] = mapped_column(Integer)
    device_limit_snapshot: Mapped[int | None] = mapped_column(Integer)
    tariff_id_snapshot: Mapped[int | None] = mapped_column(Integer)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    reversed_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("entitlement_entries.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=text("now()")
    )


class PaymentRefund(Base):
    __tablename__ = "payment_refunds"
    __table_args__ = (CheckConstraint("provider_status IN ('pending','succeeded','canceled')", name="ck_payment_refunds_provider_status"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id", ondelete="RESTRICT"), nullable=False, index=True)
    provider_refund_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_status: Mapped[str] = mapped_column(String(20), nullable=False)
    event_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaymentEvent(Base):
    __tablename__ = "payment_events"

    __table_args__ = (
        Index("ix_payment_events_payment_created", "payment_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    payment_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("payments.id", ondelete="CASCADE"),
        nullable=False,
    )

    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    payment = relationship("Payment", back_populates="events")


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
        Index("ix_broadcast_in_progress", "status", "created_at", postgresql_where=text("status = 'in_progress'")),
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
    "🛠 Бот находится на техническом обслуживании. "
    "Пожалуйста, попробуйте позже."
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
            "traffic_limit_bytes >= 0 AND traffic_used_bytes >= 0 "
            "AND traffic_uplink_bytes >= 0 AND traffic_downlink_bytes >= 0 "
            "AND last_uplink_snapshot >= 0 AND last_downlink_snapshot >= 0 "
            "AND traffic_overage_bytes >= 0",
            name="ck_white_internet_subscriptions_traffic_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    origin_node_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("servers.id", ondelete="RESTRICT"),
        nullable=False,
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

    traffic_limit_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=53687091200, server_default=text("53687091200")
    )
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
    grants = relationship(
        "WhiteInternetQuotaGrant",
        back_populates="subscription",
        cascade="all, delete-orphan",
    )
    traffic_events = relationship(
        "WhiteInternetTrafficEvent",
        back_populates="subscription",
    )


class WhiteInternetQuotaGrant(Base):
    """Single Source of Truth (SSOT) for remaining White Internet quota grants."""

    __tablename__ = "white_internet_quota_grants"

    __table_args__ = (
        CheckConstraint(
            "grant_type IN ('BASE', 'TOPUP')",
            name="ck_white_internet_quota_grants_grant_type",
        ),
        CheckConstraint(
            "bytes_granted > 0",
            name="ck_white_internet_quota_grants_bytes_granted_positive",
        ),
        CheckConstraint(
            "bytes_remaining >= 0",
            name="ck_white_internet_quota_grants_bytes_remaining_nonnegative",
        ),
        CheckConstraint(
            "bytes_remaining <= bytes_granted",
            name="ck_white_internet_quota_grants_bytes_remaining_le_granted",
        ),
        CheckConstraint(
            "price_rub >= 0",
            name="ck_white_internet_quota_grants_price_nonnegative",
        ),
        UniqueConstraint(
            "subscription_id",
            "quote_id",
            "grant_type",
            name="uq_white_internet_quota_grants_sub_quote_type",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    subscription_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("white_internet_subscriptions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    grant_type: Mapped[str] = mapped_column(String(20), nullable=False)
    bytes_granted: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bytes_remaining: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price_rub: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00"), server_default=text("0.00")
    )
    quote_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tariff_quotes.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=text("now()")
    )

    subscription = relationship("WhiteInternetSubscription", back_populates="grants")
    quote = relationship("TariffQuote", foreign_keys=[quote_id])


class WhiteInternetTrafficEvent(Base):
    """Immutable append-only audit ledger for accounted White Internet traffic snapshots."""

    __tablename__ = "white_internet_traffic_events"

    __table_args__ = (
        CheckConstraint(
            "delta_uplink >= 0 AND delta_downlink >= 0",
            name="ck_white_internet_traffic_events_deltas_nonnegative",
        ),
        CheckConstraint(
            "allocated_bytes >= 0 AND overage_bytes >= 0",
            name="ck_white_internet_traffic_events_alloc_nonnegative",
        ),
        CheckConstraint(
            "snapshot_uplink_after >= snapshot_uplink_before OR snapshot_uplink_before = 0",
            name="ck_white_internet_traffic_events_uplink_monotonic",
        ),
        CheckConstraint(
            "snapshot_downlink_after >= snapshot_downlink_before OR snapshot_downlink_before = 0",
            name="ck_white_internet_traffic_events_downlink_monotonic",
        ),
        CheckConstraint(
            "allocated_bytes + overage_bytes = delta_uplink + delta_downlink",
            name="ck_white_internet_traffic_events_conservation",
        ),
        CheckConstraint(
            "delta_uplink = snapshot_uplink_after - snapshot_uplink_before",
            name="ck_white_internet_traffic_events_delta_uplink_arithmetic",
        ),
        CheckConstraint(
            "delta_downlink = snapshot_downlink_after - snapshot_downlink_before",
            name="ck_white_internet_traffic_events_delta_downlink_arithmetic",
        ),
        CheckConstraint(
            "allocated_bytes <= delta_uplink + delta_downlink",
            name="ck_white_internet_traffic_events_allocated_le_delta",
        ),
        CheckConstraint(
            "overage_bytes <= delta_uplink + delta_downlink",
            name="ck_white_internet_traffic_events_overage_le_delta",
        ),
        UniqueConstraint(
            "subscription_id",
            "node_epoch",
            "snapshot_uplink_after",
            "snapshot_downlink_after",
            name="uq_white_internet_traffic_event_snapshot",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    subscription_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("white_internet_subscriptions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    node_epoch: Mapped[str] = mapped_column(String(64), nullable=False)
    node_boot_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    node_starttime: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    snapshot_uplink_before: Mapped[int] = mapped_column(BigInteger, nullable=False)
    snapshot_uplink_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    snapshot_downlink_before: Mapped[int] = mapped_column(BigInteger, nullable=False)
    snapshot_downlink_after: Mapped[int] = mapped_column(BigInteger, nullable=False)

    delta_uplink: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delta_downlink: Mapped[int] = mapped_column(BigInteger, nullable=False)
    allocated_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    overage_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=text("now()")
    )

    subscription = relationship("WhiteInternetSubscription", back_populates="traffic_events")
