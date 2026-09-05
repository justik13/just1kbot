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
    REFERRAL_USER_BONUS = "referral_user_bonus"
    REFERRAL_REFERRER_BONUS = "referral_referrer_bonus"
    REFERRAL_REVERSAL = "referral_reversal"
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


class ServerLifecycleStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DECOMMISSIONING = "DECOMMISSIONING"
    DECOMMISSIONED = "DECOMMISSIONED"
    ARCHIVED = "ARCHIVED"


class PaymentProviderOperationStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    RETRY = "retry"
    SUCCEEDED = "succeeded"
    DEAD = "dead"
    CANCELLED = "cancelled"


# Backward-compatible alias for payment outbox queue status
PaymentQueueStatus = PaymentProviderOperationStatus


class VPNProvisioningStatus(StrEnum):
    PENDING_CREATE = "pending_create"
    ACTIVE = "active"
    PENDING_UPDATE = "pending_update"
    DELETING = "deleting"
    CREATE_FAILED = "create_failed"
    CREATE_CLEANUP_PENDING = "create_cleanup_pending"
    UPDATE_FAILED = "update_failed"
    DELETE_FAILED = "delete_failed"


class AdminAuditAction(StrEnum):
    ACCOUNT_PURCHASE_SETTLED = "ACCOUNT_PURCHASE_SETTLED"
    ACCOUNT_TARIFF_CHANGE_SETTLED = "ACCOUNT_TARIFF_CHANGE_SETTLED"
    ADD_SERVER = "ADD_SERVER"
    ADD_TARIFF = "ADD_TARIFF"
    ADMIN_BALANCE_DEDUCT = "ADMIN_BALANCE_DEDUCT"
    ADMIN_BALANCE_TOPUP = "ADMIN_BALANCE_TOPUP"
    ADMIN_DEVICE_DELETE = "ADMIN_DEVICE_DELETE"
    ADMIN_DIRECT_MESSAGE = "ADMIN_DIRECT_MESSAGE"
    ADMIN_DIRECT_MESSAGE_SENT = "ADMIN_DIRECT_MESSAGE_SENT"
    ADMIN_SUB_CHANGE = "ADMIN_SUB_CHANGE"
    ADMIN_SUB_EXTEND = "ADMIN_SUB_EXTEND"
    ADMIN_SUB_GRANT = "ADMIN_SUB_GRANT"
    ADMIN_SUB_REDUCE = "ADMIN_SUB_REDUCE"
    BALANCE_REFUND_REQUESTED = "BALANCE_REFUND_REQUESTED"
    BAN = "BAN"
    BAN_USER = "BAN_USER"
    BROADCAST = "BROADCAST"
    BROADCAST_COMPLETED = "BROADCAST_COMPLETED"
    CHANGE_TARIFF = "CHANGE_TARIFF"
    CLEANUP_DEVICE_DELETE = "CLEANUP_DEVICE_DELETE"
    DEDUCT_USER_BALANCE = "DEDUCT_USER_BALANCE"
    DELETE_DEVICE = "DELETE_DEVICE"
    DELETE_SERVER = "DELETE_SERVER"
    DELETE_TARIFF = "DELETE_TARIFF"
    DEVICE_CREATE = "DEVICE_CREATE"
    DEVICE_CREATED = "DEVICE_CREATED"
    DEVICE_CREATE_BLOCKED = "DEVICE_CREATE_BLOCKED"
    DEVICE_DELETE = "DEVICE_DELETE"
    DEVICE_DELETED = "DEVICE_DELETED"
    DEVICE_RENAME = "DEVICE_RENAME"
    EDIT_SERVER = "EDIT_SERVER"
    EDIT_TARIFF = "EDIT_TARIFF"
    EXTEND = "EXTEND"
    GRANT = "GRANT"
    MANUAL_GRANT = "MANUAL_GRANT"
    MASS_BONUS_GRANTED = "MASS_BONUS_GRANTED"
    PAID_AFTER_CANCEL = "PAID_AFTER_CANCEL"
    PAYMENT_CANCELLED = "PAYMENT_CANCELLED"
    PAYMENT_CANCEL_AFTER_COMPLETED = "PAYMENT_CANCEL_AFTER_COMPLETED"
    PAYMENT_CHARGEBACK = "PAYMENT_CHARGEBACK"
    PAYMENT_DISPUTE_MANUAL_REVIEW = "PAYMENT_DISPUTE_MANUAL_REVIEW"
    PAYMENT_DISPUTE_OPENED = "PAYMENT_DISPUTE_OPENED"
    PAYMENT_DISPUTE_RESOLVED = "PAYMENT_DISPUTE_RESOLVED"
    PAYMENT_QUEUE_MANUAL_RETRY = "PAYMENT_QUEUE_MANUAL_RETRY"
    PAYMENT_SUCCESS = "PAYMENT_SUCCESS"
    REDUCE = "REDUCE"
    REFERRAL_ATTACHED = "REFERRAL_ATTACHED"
    REFERRAL_BONUS_GRANTED = "REFERRAL_BONUS_GRANTED"
    TOGGLE_MAINTENANCE = "TOGGLE_MAINTENANCE"
    TOGGLE_SERVER = "TOGGLE_SERVER"
    UNBAN = "UNBAN"
    UNBAN_USER = "UNBAN_USER"
    USER_REGISTER = "USER_REGISTER"
    USER_RESTORED = "USER_RESTORED"
    WELCOME_BONUS_GRANTED = "WELCOME_BONUS_GRANTED"
    WHITE_INTERNET_GRANT_TRIAL = "WHITE_INTERNET_GRANT_TRIAL"
    WHITE_INTERNET_RESET_TRIAL = "WHITE_INTERNET_RESET_TRIAL"


class ServiceType(StrEnum):
    AWG = "awg"
    WHITE_INTERNET = "white_internet"


class WhiteInternetStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    EXHAUSTED = "EXHAUSTED"
    EXPIRED = "EXPIRED"
    DISABLED = "DISABLED"


class WhiteInternetProvisioningStatus(StrEnum):
    PENDING_CREATE = "PENDING_CREATE"
    ACTIVE = "ACTIVE"
    PENDING_UPDATE = "PENDING_UPDATE"
    PENDING_DELETE = "PENDING_DELETE"
    SYNCED_INACTIVE = "SYNCED_INACTIVE"
    FAILED = "FAILED"


class WhiteInternetGrantType(StrEnum):
    BASE = "BASE"
    TOPUP = "TOPUP"


__all__ = [
    "AccountLedgerEntryType",
    "AccountReservationStatus",
    "AccountReservationType",
    "AdminAuditAction",
    "ApiOperationStatus",
    "ApiOperationType",
    "EntitlementEntryType",
    "PaidValueEntryType",
    "PaymentCheckoutStatus",
    "PaymentDisputeStatus",
    "PaymentFulfillmentStatus",
    "PaymentProviderOperationStatus",
    "PaymentProviderStatus",
    "PaymentQueueStatus",
    "PaymentReconciliationStatus",
    "ProviderRefundOperationStatus",
    "ServerHealthState",
    "ServerLifecycleStatus",
    "ServiceType",
    "TariffQuoteOperation",
    "TariffQuoteStatus",
    "VPNProvisioningStatus",
    "WebhookInboxStatus",
    "WhiteInternetGrantType",
    "WhiteInternetProvisioningStatus",
    "WhiteInternetStatus",
]
