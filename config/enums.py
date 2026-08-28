"""Single Source of Truth for system domain enums and state machines.

This module sits at Level 0 in the application architecture with zero internal dependencies.
All domain statuses, entry types, and operation kinds are defined here as standard StrEnums.
"""
from enum import StrEnum


class PaymentProviderStatus(StrEnum):
    NOT_CREATED = "not_created"
    CREATING = "creating"
    PENDING = "pending"
    WAITING_FOR_CAPTURE = "waiting_for_capture"
    SUCCEEDED = "succeeded"
    CANCELED = "canceled"
    REFUNDED = "refunded"
    UNKNOWN = "unknown"
    MANUAL_REVIEW = "manual_review"


class PaymentFulfillmentStatus(StrEnum):
    NOT_READY = "not_ready"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REVERSED = "reversed"
    MANUAL_REVIEW = "manual_review"


class PaymentCheckoutStatus(StrEnum):
    ACTIVE = "active"
    ABANDONED = "abandoned"


class PaymentReconciliationStatus(StrEnum):
    OK = "ok"
    REQUIRED = "required"
    MISMATCH = "mismatch"
    MANUAL_REVIEW = "manual_review"


class PaymentDisputeStatus(StrEnum):
    OPEN = "open"
    WON_BY_MERCHANT = "won_by_merchant"
    LOST_BY_MERCHANT = "lost_by_merchant"
    MANUAL_REVIEW = "manual_review"


class ProviderRefundOperationStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    RETRY = "retry"
    COMPLETED = "completed"
    FAILED = "failed"


class TariffQuoteStatus(StrEnum):
    ACTIVE = "active"
    CONSUMED = "consumed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    MANUAL_REVIEW = "manual_review"


class TariffQuoteOperation(StrEnum):
    PURCHASE = "purchase"
    RENEW = "renew"
    CHANGE = "change"


class AccountLedgerEntryType(StrEnum):
    PAYMENT_CREDIT = "payment_credit"
    PURCHASE_DEBIT = "purchase_debit"
    PURCHASE_REVERSAL = "purchase_reversal"
    REFUND_DEBIT = "refund_debit"
    CHARGEBACK_DEBIT = "chargeback_debit"
    ADMIN_ADJUSTMENT = "admin_adjustment"


class AccountReservationType(StrEnum):
    REFUND = "refund"
    DISPUTE = "dispute"


class AccountReservationStatus(StrEnum):
    ACTIVE = "active"
    RELEASED = "released"
    CONSUMED = "consumed"


class PaidValueEntryType(StrEnum):
    ACCOUNT_PURCHASE = "account_purchase"
    TARIFF_CONVERSION = "tariff_conversion"
    MANUAL_ADJUSTMENT = "manual_adjustment"


class EntitlementEntryType(StrEnum):
    ACCOUNT_PURCHASE_GRANT = "account_purchase_grant"
    MANUAL_GRANT = "manual_grant"
    TARIFF_CHANGE = "tariff_change"


class ApiOperationType(StrEnum):
    CREATE_PEER = "create_peer"
    UPDATE_PEER = "update_peer"
    DELETE_PEER = "delete_peer"


class ApiOperationStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    RETRY = "retry"
    SUCCEEDED = "succeeded"
    DEAD = "dead"
    CANCELLED = "cancelled"


class WebhookInboxStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    RETRY = "retry"
    SUCCEEDED = "succeeded"
    DEAD = "dead"


class ServerHealthState(StrEnum):
    ONLINE = "ONLINE"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    PROBLEM = "PROBLEM"
    AUTO_DISABLED = "AUTO_DISABLED"
    MANUAL_DISABLED = "MANUAL_DISABLED"


__all__ = [
    "AccountLedgerEntryType",
    "AccountReservationStatus",
    "AccountReservationType",
    "ApiOperationStatus",
    "ApiOperationType",
    "EntitlementEntryType",
    "PaidValueEntryType",
    "PaymentCheckoutStatus",
    "PaymentDisputeStatus",
    "PaymentFulfillmentStatus",
    "PaymentProviderStatus",
    "PaymentReconciliationStatus",
    "ProviderRefundOperationStatus",
    "ServerHealthState",
    "TariffQuoteOperation",
    "TariffQuoteStatus",
    "WebhookInboxStatus",
]
