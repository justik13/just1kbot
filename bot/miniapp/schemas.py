"""Pydantic schemas for Mini App Admin API requests, synced with just1knode v2 contract."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from utils.security import normalize_public_domain


def _validate_fqdn(value: str) -> str:
    cleaned = normalize_public_domain(value, require_fqdn=True)
    if not cleaned:
        raise ValueError("SNI domain must be a valid public FQDN")
    return cleaned


class Verify2FAPayload(BaseModel):
    code: str = Field(..., min_length=1, max_length=32)
    setup_token: str | None = Field(default=None, max_length=512)


class TopupPayload(BaseModel):
    amount_rub: int = Field(..., ge=20, le=50000)


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


class StandardDowngradePayload(BaseModel):
    target_device_limit: int = Field(..., ge=2, le=10)


class WiCheckoutPayload(BaseModel):
    action: str = Field(..., pattern=r"^(buy|renew|add_slot|topup)$")
    device_limit: int = Field(default=1, ge=1, le=3)
    gb: int | None = Field(default=None, ge=10, le=50)
    use_balance: bool = True


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


class UpdateNodePayload(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    country_flag: str | None = Field(default=None, max_length=10)
    api_url: str | None = Field(default=None, min_length=1, max_length=255)
    api_key: str | None = Field(default=None, min_length=1, max_length=255)
    in_standard: bool | None = None
    in_white_internet: bool | None = None
    std_sni: str | None = Field(default=None, max_length=253)
    vless_name: str | None = Field(default=None, max_length=120)
    server_description: str | None = Field(default=None, max_length=30)
    wi_yandex_name: str | None = Field(default=None, max_length=120)
    wi_yandex_badge: str | None = Field(default=None, max_length=30)
    wi_turboflare_name: str | None = Field(default=None, max_length=120)
    wi_turboflare_badge: str | None = Field(default=None, max_length=30)

    @field_validator("std_sni")
    @classmethod
    def check_std_sni(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip().lower()
        if not clean:
            return ""
        return _validate_fqdn(clean)


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
    action: Literal["balance", "grant_standard", "grant_wi", "wi_traffic", "restrictions"]
    amount_rub: int | None = Field(default=None, ge=-100000, le=100000)
    days: int | None = Field(default=None, ge=-3650, le=3650)
    device_limit: int | None = Field(default=None, ge=1, le=20)
    traffic_action: Literal["add_gb", "reset_used", "set_base_gb"] | None = None
    gb: int | None = Field(default=None, ge=1, le=5000)
    financial_hold: bool | None = None
    topup_blocked: bool | None = None
    note: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def check_action_fields(self) -> "AdminUserAdjustPayload":
        if self.action == "balance":
            if self.amount_rub is None or self.amount_rub == 0:
                raise ValueError("amount_rub must be non-zero for balance adjustment")
        elif self.action == "grant_standard":
            if self.days is None:
                raise ValueError("days is required for grant_standard")
            if self.device_limit is not None and not (2 <= self.device_limit <= 10):
                raise ValueError("device_limit must be between 2 and 10 for standard")
        elif self.action == "grant_wi":
            if self.days is None:
                raise ValueError("days is required for grant_wi")
            if self.device_limit is not None and not (1 <= self.device_limit <= 3):
                raise ValueError("device_limit must be between 1 and 3 for wi")
        elif self.action == "restrictions":
            if self.financial_hold is None and self.topup_blocked is None:
                raise ValueError("financial_hold or topup_blocked is required for restrictions")
        return self


class AdminDirectMessagePayload(BaseModel):
    text: str = Field(..., min_length=1, max_length=4096)


class BroadcastPreviewPayload(BaseModel):
    target_audience: str = Field(..., min_length=1, max_length=64)

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
        if clean.lower().startswith("http://"):
            raise ValueError("URL изображения должен использовать HTTPS")
        return clean


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

