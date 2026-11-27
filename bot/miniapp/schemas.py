"""Pydantic schemas for Mini App Admin API requests, synced with just1knode v2 contract."""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

_TG_FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,256}$")


class Verify2FAPayload(BaseModel):
    code: str = Field(..., min_length=1, max_length=32)
    setup_token: str | None = Field(default=None, max_length=512)


class TopupPayload(BaseModel):
    amount_rub: int = Field(..., ge=20, le=50000)
    idempotency_key: str | None = Field(default=None, max_length=64)


class StandardQuotePayload(BaseModel):
    device_limit: int = Field(..., ge=2, le=10)
    duration_days: int = Field(..., ge=7, le=90)

    @field_validator("duration_days")
    @classmethod
    def check_duration(cls, value: int) -> int:
        if value not in {7, 30, 90}:
            raise ValueError("duration_days must be 7, 30, or 90")
        return value


class StandardCheckoutPayload(StandardQuotePayload):
    use_balance: bool = True
    idempotency_key: str | None = Field(default=None, max_length=64)


class StandardDowngradePayload(BaseModel):
    target_device_limit: int = Field(..., ge=2, le=10)


class WiCheckoutPayload(BaseModel):
    action: str = Field(..., pattern=r"^(buy|renew|add_slot|topup)$")
    device_limit: int | None = Field(default=None, ge=1, le=3)
    gb: int | None = Field(default=None, ge=10, le=50)
    use_balance: bool = True
    idempotency_key: str | None = Field(default=None, max_length=64)


class WiDowngradePayload(BaseModel):
    target_device_limit: int = Field(..., ge=1, le=2)


class AutoRenewPayload(BaseModel):
    scope: str = Field(..., pattern=r"^(standard|wi)$")
    enabled: bool


class DevicePriorityPayload(BaseModel):
    priority: int = Field(..., ge=-1, le=100)
    replace_hwid: str | None = Field(default=None, max_length=128)


class AddNodePayload(BaseModel):
    api_url: str = Field(..., min_length=8, max_length=500)
    api_key: str = Field(..., min_length=8, max_length=512)
    name: str = Field(default="", max_length=255)
    country_flag: str | None = Field(default=None, max_length=10)


class IncyGlobalSettingsPayload(BaseModel):
    profile_title: str | None = Field(default=None, max_length=120)
    announce: str | None = Field(default=None, max_length=300)
    announce_url: str | None = Field(default=None, max_length=500)
    profile_show_devices: bool | None = None
    profile_update_interval: int | None = Field(default=None, ge=1, le=720)
    support_url: str | None = Field(default=None, max_length=500)
    web_page_url: str | None = Field(default=None, max_length=500)
    sort_order: Literal["none", "ping", "name"] | None = None
    hide_url: bool | None = None
    hide_check: bool | None = None
    fragmentation_enabled: bool | None = None
    fragmentation_packets: str | None = Field(default=None, max_length=64)
    fragmentation_length: str | None = Field(default=None, max_length=32)
    fragmentation_interval: str | None = Field(default=None, max_length=32)
    no_limit_mode: Literal["off", "all", "xhttp"] | None = None
    dns_resolve_enabled: bool | None = None
    dns_resolve_domain: str | None = Field(default=None, max_length=253)
    dns_resolve_ip: str | None = Field(default=None, max_length=64)
    per_app_proxy_enabled: bool | None = None
    per_app_proxy_mode: Literal["bypass", "proxy"] | None = None
    per_app_proxy_list: str | None = Field(default=None, max_length=2000)
    cdn_label_mode: Literal["badge", "name", "none"] | None = None
    cdn_yandex_label: str | None = Field(default=None, max_length=32)
    cdn_turboflare_label: str | None = Field(default=None, max_length=32)
    mtproto_proxy_url: str | None = Field(default=None, max_length=500)

    @field_validator("mtproto_proxy_url")
    @classmethod
    def check_mtproto_proxy_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        if not clean:
            return ""
        lowered = clean.lower()
        if not (lowered.startswith("https://t.me/proxy?") or lowered.startswith("tg://proxy?")):
            raise ValueError(
                "Ссылка прокси должна начинаться с https://t.me/proxy? или tg://proxy?"
            )
        return clean


class UpdateNodePayload(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    country_flag: str | None = Field(default=None, max_length=10)
    api_url: str | None = Field(default=None, min_length=1, max_length=500)
    api_key: str | None = Field(default=None, min_length=1, max_length=512)
    in_standard: bool | None = None
    in_white_internet: bool | None = None
    vless_name: str | None = Field(default=None, max_length=120)
    server_description: str | None = Field(default=None, max_length=30)
    wi_yandex_name: str | None = Field(default=None, max_length=120)
    wi_yandex_badge: str | None = Field(default=None, max_length=30)
    wi_turboflare_name: str | None = Field(default=None, max_length=120)
    wi_turboflare_badge: str | None = Field(default=None, max_length=30)


class ToggleNodePayload(BaseModel):
    is_active: bool | None = None


_ALLOWED_BROADCAST_AUDIENCES = frozenset({
    "all",
    "active",
    "active_standard",
    "active_wi",
    "expiring_3d",
    "expired",
    "no_sub",
    "never",
    "test",
})


def _validate_audience(value: str) -> str:
    clean = value.strip()
    if clean in _ALLOWED_BROADCAST_AUDIENCES:
        return clean
    raise ValueError("Недопустимая аудитория")


class AdminUserAdjustPayload(BaseModel):
    action: Literal[
        "balance",
        "forgive_debt",
        "grant_standard",
        "grant_wi",
        "wi_traffic",
        "restrictions",
    ]
    amount_rub: int | None = Field(default=None, ge=-100000, le=100000)
    days: int | None = Field(default=None, ge=-3650, le=3650)
    device_limit: int | None = Field(default=None, ge=1, le=20)
    traffic_action: Literal["add_gb", "reset_used", "set_base_gb"] | None = None
    gb: int | None = Field(default=None, ge=1, le=5000)
    financial_hold: bool | None = None
    topup_blocked: bool | None = None
    reactivate_wi: bool = False
    notify_user: bool | None = None
    send_note_to_user: bool = False
    note: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def check_action_fields(self) -> "AdminUserAdjustPayload":
        if self.action == "balance":
            if self.amount_rub is None or self.amount_rub == 0:
                raise ValueError("amount_rub must be non-zero for balance adjustment")
        elif self.action == "forgive_debt":
            if self.amount_rub is not None and self.amount_rub <= 0:
                raise ValueError("amount_rub must be positive for forgive_debt")
        elif self.action == "grant_standard":
            if self.days is None:
                raise ValueError("days is required for grant_standard")
            if self.days == 0 and self.device_limit is None:
                raise ValueError("Укажите дни или лимит устройств")
            if self.device_limit is not None and not (2 <= self.device_limit <= 10):
                raise ValueError("device_limit must be between 2 and 10 for standard")
        elif self.action == "grant_wi":
            if self.days is None:
                raise ValueError("days is required for grant_wi")
            if self.days == 0 and self.device_limit is None:
                raise ValueError("Укажите дни или лимит устройств")
            if self.device_limit is not None and not (1 <= self.device_limit <= 3):
                raise ValueError("device_limit must be between 1 and 3 for wi")
        elif self.action == "wi_traffic":
            if self.traffic_action is None:
                raise ValueError("traffic_action is required for wi_traffic")
            if self.traffic_action in ("add_gb", "set_base_gb") and (self.gb is None or self.gb < 1):
                raise ValueError("gb (>= 1) is required for add_gb/set_base_gb")
        elif self.action == "restrictions":
            if (
                self.financial_hold is None
                and self.topup_blocked is None
                and not self.reactivate_wi
            ):
                raise ValueError("financial_hold, topup_blocked or reactivate_wi is required for restrictions")
        return self


class NodeLifecyclePayload(BaseModel):
    lifecycle_status: Literal["active", "decommissioning"]


class AdminUserOptionalNotifyPayload(BaseModel):
    notify_user: bool = False


class AdminDirectMessagePayload(BaseModel):
    text: str = Field(..., min_length=1, max_length=4096)


class BroadcastPreviewPayload(BaseModel):
    target_audience: str = Field(..., min_length=1, max_length=64)
    service_scope: Literal["standard", "white_internet"] | None = None

    @field_validator("target_audience")
    @classmethod
    def check_audience(cls, value: str) -> str:
        return _validate_audience(value)


class BroadcastStartPayload(BaseModel):
    target_audience: str = Field(..., min_length=1, max_length=64)
    text: str = Field(..., min_length=1, max_length=4096)
    media_url: str | None = Field(default=None, max_length=500)

    @field_validator("target_audience")
    @classmethod
    def check_audience(cls, value: str) -> str:
        return _validate_audience(value)

    @field_validator("media_url")
    @classmethod
    def check_media_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        if not clean:
            return None
        lowered = clean.lower()
        if lowered.startswith("http://"):
            raise ValueError("URL изображения должен использовать HTTPS")
        if lowered.startswith("https://"):
            if any(ch.isspace() for ch in clean):
                raise ValueError("Некорректный HTTPS URL изображения")
            parsed = urlparse(clean)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError("Некорректный HTTPS URL изображения")
            return clean
        if _TG_FILE_ID_RE.fullmatch(clean):
            return clean
        raise ValueError("Укажите корректный HTTPS URL или Telegram file_id изображения")


class MassBonusPayload(BaseModel):
    target_audience: str = Field(..., min_length=1, max_length=64)
    amount_rub: int = Field(..., ge=1, le=50000)
    reason: str = Field(default="", max_length=200)
    notify_users: bool = True

    @field_validator("target_audience")
    @classmethod
    def check_audience(cls, value: str) -> str:
        return _validate_audience(value)


class MassExtendPayload(BaseModel):
    service_scope: Literal["standard", "white_internet"]
    target_audience: str = Field(..., min_length=1, max_length=64)
    days: int = Field(..., ge=1, le=365)
    reason: str = Field(default="", max_length=200)
    notify_users: bool = True

    @field_validator("target_audience")
    @classmethod
    def check_audience(cls, value: str) -> str:
        return _validate_audience(value)


class PaymentsMaintenancePayload(BaseModel):
    enabled: bool

