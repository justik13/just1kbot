import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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

from config.constants import VLESS_PROTOCOL
from config.enums import (
    AccountLedgerEntryType,
    OrderStatus,
    OrderServiceType,
    ServerHealthState,
    ServerLifecycleStatus,
    WebhookInboxStatus,
)
from database.sql_helpers import sql_enum_in
from utils.datetime_helpers import now_utc
from utils.encryption import EncryptedString


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    __table_args__ = (
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

    referred_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)

    last_payment_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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

    total_traffic_bytes: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )
    total_wi_traffic_bytes: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )
    monthly_vless_bytes: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )
    monthly_wi_bytes: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )
    traffic_cycle: Mapped[str | None] = mapped_column(
        String(7), nullable=True
    )

    orders = relationship(
        "Order",
        back_populates="user",
    )

    vless_subscription = relationship(
        "VlessSubscription",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )


class Server(Base):
    __tablename__ = "servers"

    __table_args__ = (
        UniqueConstraint("api_url", name="uq_servers_api_url"),
        CheckConstraint(
            sql_enum_in("lifecycle_status", ServerLifecycleStatus),
            name="ck_servers_lifecycle_status",
        ),
        CheckConstraint(
            "role IN ('ru_hub', 'eu_hub', 'exit_node')",
            name="ck_servers_role",
        ),
        Index("ix_servers_lifecycle_status", "lifecycle_status"),
        Index("ix_servers_role", "role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(
        String(30), nullable=False, default="ru_hub", server_default="ru_hub"
    )
    country_flag: Mapped[str | None] = mapped_column(String(10), nullable=True)

    api_url: Mapped[str] = mapped_column(String(500), nullable=False)
    api_key: Mapped[str] = mapped_column(EncryptedString(critical=True), nullable=False)

    protocol: Mapped[str] = mapped_column(
        String(50), nullable=False, default=VLESS_PROTOCOL, server_default=VLESS_PROTOCOL
    )
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
        Index(
            "uq_orders_user_idempotency_key",
            "user_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
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
        String(30), nullable=False, default="standard", server_default=text("'standard'")
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
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
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
    ledger_entries = relationship("AccountLedgerEntry", back_populates="order")


class AccountLedgerEntry(Base):
    """Append-only account history split into 'real' (YooKassa) and 'bonus' (internal) buckets."""

    __tablename__ = "account_ledger_entries"
    __table_args__ = (
        CheckConstraint(
            sql_enum_in("entry_type", AccountLedgerEntryType),
            name="ck_account_ledger_entry_type",
        ),
        CheckConstraint(
            "bucket IN ('real', 'bonus')",
            name="ck_account_ledger_bucket",
        ),
        CheckConstraint("currency = 'RUB'", name="ck_account_ledger_currency_rub"),
        CheckConstraint(
            "amount <> 0 AND amount = trunc(amount)",
            name="ck_account_ledger_whole_nonzero_amount",
        ),
        CheckConstraint(
            "(entry_type = 'payment_credit' AND bucket = 'real' AND amount > 0) OR "
            "(entry_type = 'purchase_debit' AND amount < 0 AND order_id IS NOT NULL) OR "
            "(entry_type = 'refund_debit' AND amount < 0 AND order_id IS NOT NULL AND bucket = 'real') OR "
            "(entry_type = 'admin_adjustment' AND bucket = 'bonus')",
            name="ck_account_ledger_entry_shape",
        ),
        Index(
            "ix_account_ledger_user_history",
            "user_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_account_ledger_user_bucket",
            "user_id",
            "bucket",
        ),
        Index(
            "uq_account_ledger_order_debit",
            "order_id",
            "bucket",
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
    bucket: Mapped[str] = mapped_column(
        String(10), nullable=False, default="real", server_default=text("'real'")
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="RUB", server_default=text("'RUB'")
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=text("now()")
    )

    order = relationship("Order", back_populates="ledger_entries", foreign_keys=[order_id])


class WebhookInbox(Base):
    __tablename__ = "webhook_inbox"
    __table_args__ = (
        UniqueConstraint("provider", "event_key", name="uq_webhook_inbox_provider_event_key"),
        CheckConstraint(sql_enum_in("status", WebhookInboxStatus), name="ck_webhook_inbox_status"),
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
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


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
            "AND traffic_uplink_bytes >= 0 AND traffic_downlink_bytes >= 0 "
            "AND traffic_overage_bytes >= 0",
            name="ck_white_internet_subscriptions_traffic_nonnegative",
        ),
        CheckConstraint(
            "device_limit >= 1 AND device_limit <= 3",
            name="ck_white_internet_subscriptions_device_limit",
        ),
        CheckConstraint(
            "pending_device_limit IS NULL OR (pending_device_limit >= 1 AND pending_device_limit <= 3)",
            name="ck_wi_subs_pending_device_limit",
        ),
        Index(
            "uq_white_internet_live_user",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'ACTIVE', 'EXHAUSTED')"),
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
    node_traffic_snapshots: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    device_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    pending_device_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pending_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    auto_renew_enabled: Mapped[bool] = mapped_column(
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
    node_sync_state: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    pending_revoked_uuids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)

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


class VlessSubscription(Base):
    """VLESS TLS subscription for standard access with INCY HWID device quota tracking."""

    __tablename__ = "vless_subscriptions"

    __table_args__ = (
        Index(
            "ix_vless_subscriptions_pending_revoked",
            "id",
            postgresql_where=text("pending_revoked_uuids != '[]'::jsonb"),
        ),
        Index(
            "ix_vless_subscriptions_expires_at",
            "expires_at",
            postgresql_where=text("expires_at IS NOT NULL"),
        ),
    )

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
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    device_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    downgrade_cooldown_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    auto_renew_enabled: Mapped[bool] = mapped_column(
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
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )

    pending_revoked_uuids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    traffic_down: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    traffic_up: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    node_traffic_snapshots: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
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

    user = relationship("User", back_populates="vless_subscription")


class UserDevice(Base):
    """Unified HWID device registry for Standard and White Internet subscriptions."""

    __tablename__ = "user_devices"

    __table_args__ = (
        UniqueConstraint("user_id", "hwid", name="uq_user_devices_user_hwid"),
        Index("ix_user_devices_user_priority", "user_id", "bi_priority", "first_seen_at"),
        Index("ix_user_devices_user_std_priority", "user_id", "std_priority", "first_seen_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    hwid: Mapped[str] = mapped_column(String(128), nullable=False)
    device_uuid: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        unique=True,
        index=True,
        default=lambda: str(uuid.uuid4()),
    )
    device_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(64), nullable=True)
    os_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    std_priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    bi_priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=text("now()")
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=text("now()")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=text("now()")
    )

    user = relationship("User", foreign_keys=[user_id])


class DeviceRegistrationEvent(Base):
    """Rolling 24-hour HWID registration history for daily churn-limit enforcement."""

    __tablename__ = "device_registration_events"

    __table_args__ = (
        Index("ix_device_reg_events_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    hwid: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=now_utc, server_default=text("now()")
    )


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
    success_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    fail_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    last_processed_id: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))

    target_audience: Mapped[str] = mapped_column(
        String(64), default="all", server_default=text("'all'")
    )

    broadcast_text: Mapped[str] = mapped_column(Text, nullable=False)
    media_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_type: Mapped[str] = mapped_column(String(50), nullable=False)
    label: Mapped[str] = mapped_column(String(50), nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), default="in_progress", server_default=text("'in_progress'"), index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
        server_default=text("now()"),
    )
