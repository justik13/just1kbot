from datetime import datetime, date
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

from utils.datetime_helpers import now_utc
from utils.encryption import EncryptedString


API_OPERATION_TYPES = (
    "create_peer",
    "update_peer",
    "delete_peer",
)

API_OPERATION_STATUSES = (
    "pending",
    "processing",
    "retry",
    "succeeded",
    "dead",
    "cancelled",
)


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
    referral_days: Mapped[int] = mapped_column(Integer, default=0)

    last_payment_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    is_bot_blocked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
        cascade="all, delete-orphan",
    )

    current_tariff = relationship(
        "Tariff",
        foreign_keys=[current_tariff_id],
    )


class VPNProfile(Base):
    __tablename__ = "vpn_profiles"

    __table_args__ = (
        CheckConstraint(
            "provisioning_status IN ('pending_create', 'active', 'pending_update', "
            "'deleting', 'create_failed', 'update_failed', 'delete_failed')",
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

    protocol: Mapped[str] = mapped_column(String(50), default="amneziawg2")
    max_clients: Mapped[int] = mapped_column(Integer, default=50)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Tariff(Base):
    __tablename__ = "tariffs"

    __table_args__ = (
        UniqueConstraint(
            "device_limit",
            "duration_days",
            name="uq_tariffs_device_limit_duration_days",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    device_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    price_rub: Mapped[int] = mapped_column(Integer, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Payment(Base):
    __tablename__ = "payments"

    __table_args__ = (
        Index(
            "uq_payments_external_id_not_null",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
        Index("ix_payments_status_created_at", "status", "created_at"),
        Index("ix_payments_tariff_status", "tariff_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    tariff_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tariffs.id", ondelete="RESTRICT"),
        nullable=False,
    )

    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(20), nullable=False)

    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    manual_review_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    snapshot_duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot_device_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    snapshot_currency: Mapped[str | None] = mapped_column(String(20), nullable=True)

    referral_user_bonus_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    referral_referrer_bonus_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )

    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    payment_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String(50), nullable=True)

    user = relationship("User", back_populates="payments")
    tariff = relationship("Tariff")

    events = relationship(
        "PaymentEvent",
        back_populates="payment",
        cascade="all, delete-orphan",
    )


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
        index=True,
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
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_created_at_desc", "created_at", postgresql_ops={"created_at": "DESC"}),
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


class PendingAPIDeletion(Base):
    __tablename__ = "pending_api_deletions"

    __table_args__ = (
        Index(
            "ix_pending_api_deletions_attempts",
            "attempts",
            "created_at",
            postgresql_where=text("attempts < 10"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    server_name: Mapped[str] = mapped_column(String(255), nullable=False)
    api_url: Mapped[str] = mapped_column(String(500), nullable=False)
    api_key: Mapped[str] = mapped_column(EncryptedString(critical=True), nullable=False)

    peer_id: Mapped[str] = mapped_column(String(255), nullable=False)
    client_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(50), nullable=True)

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class APIOperation(Base):
    """Durable command record for future Amnezia API workers.

    ``payload`` must contain only non-secret operation parameters. API credentials
    are kept separately in the encrypted snapshot column.
    """

    __tablename__ = "api_operations"

    __table_args__ = (
        CheckConstraint(
            "operation_type IN ('create_peer', 'update_peer', 'delete_peer')",
            name="ck_api_operations_operation_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'retry', 'succeeded', "
            "'dead', 'cancelled')",
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

    __table_args__ = (
        Index("ix_hub_messages_chat_id", "chat_id"),
    )

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
