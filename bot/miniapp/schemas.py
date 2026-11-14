"""Pydantic schemas for Mini App Admin API requests, synced with just1knode v2 contract."""

from __future__ import annotations

import ipaddress
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from utils.security import normalize_public_domain


def _validate_fqdn(value: str) -> str:
    cleaned = normalize_public_domain(value, require_fqdn=True)
    if not cleaned:
        raise ValueError("SNI domain must be a valid public FQDN")
    return cleaned


def _validate_ipv4(value: str) -> str:
    raw = (value or "").strip()
    try:
        ip = ipaddress.IPv4Address(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid IPv4 address: {value!r}") from exc
    return str(ip)


def _validate_uuid(value: str) -> str:
    import uuid as uuid_mod

    raw = (value or "").strip()
    try:
        return str(uuid_mod.UUID(raw))
    except ValueError as exc:
        raise ValueError(f"Invalid UUID: {value!r}") from exc


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
    priority: int = Field(..., ge=0, le=100)


class AddNodePayload(BaseModel):
    api_url: str = Field(..., min_length=8, max_length=500)
    api_key: str = Field(..., min_length=8, max_length=512)
    name: str = Field(default="", max_length=255)
    country_flag: str | None = Field(default=None, max_length=10)
    allow_bootstrap_tls: bool = True


class UpdateNodePayload(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    country_flag: str | None = Field(default=None, max_length=10)
    allow_bootstrap_tls: bool | None = None


class ToggleNodePayload(BaseModel):
    is_active: bool | None = None


class DeployCertPayload(BaseModel):
    domain: str = Field(..., min_length=3, max_length=253)
    fullchain_pem: str = Field(..., min_length=32, max_length=32768)
    privkey_pem: str = Field(..., min_length=32, max_length=16384)

    @field_validator("domain")
    @classmethod
    def check_domain(cls, value: str) -> str:
        return _validate_fqdn(value)


class BridgePeerSpec(BaseModel):
    code: str = Field(..., min_length=1, max_length=32, pattern=r"^[a-zA-Z0-9_-]+$")
    ip: str
    port: int = Field(default=443, ge=1, le=65535)
    sni: str
    uuid: str
    is_default: bool = False

    @model_validator(mode="after")
    def validate_peer(self) -> "BridgePeerSpec":
        self.code = self.code.strip().lower()
        self.ip = _validate_ipv4(self.ip)
        self.sni = _validate_fqdn(self.sni)
        self.uuid = _validate_uuid(self.uuid)
        return self


class SniRouteSpec(BaseModel):
    sni: str
    bridge_code: str = Field(..., min_length=1, max_length=32, pattern=r"^[a-zA-Z0-9_-]+$")

    @model_validator(mode="after")
    def validate_route(self) -> "SniRouteSpec":
        self.sni = _validate_fqdn(self.sni)
        self.bridge_code = self.bridge_code.strip().lower()
        return self


class IncomingPeerSpec(BaseModel):
    code: str | None = Field(default=None, max_length=32)
    ip: str | None = None
    uuid: str | None = None
    route_user: str | None = None

    @model_validator(mode="after")
    def validate_incoming(self) -> "IncomingPeerSpec":
        if self.ip is not None and self.ip.strip():
            self.ip = _validate_ipv4(self.ip)
        if self.uuid is not None and self.uuid.strip():
            self.uuid = _validate_uuid(self.uuid)
        return self


class HubRouteSpec(BaseModel):
    code: str | None = Field(default=None, max_length=32)
    ingress_uuid: str | None = None
    route_user: str = Field(..., min_length=1, max_length=128)
    exit_id: str | None = Field(default=None, max_length=64)
    exit_code: str = Field(..., min_length=1, max_length=32, pattern=r"^[a-zA-Z0-9_-]+$")

    @model_validator(mode="after")
    def validate_hub_route(self) -> "HubRouteSpec":
        if self.ingress_uuid is not None and self.ingress_uuid.strip():
            self.ingress_uuid = _validate_uuid(self.ingress_uuid)
        self.exit_code = self.exit_code.strip().lower()
        return self


class NodeLinksSectionSpec(BaseModel):
    bridges: list[BridgePeerSpec] = Field(default_factory=list)
    sni_routes: list[SniRouteSpec] = Field(default_factory=list)
    locations: list[dict[str, Any]] = Field(default_factory=list)


class NodeLinksUpdatePayload(BaseModel):
    standard: NodeLinksSectionSpec | None = None
    bi: NodeLinksSectionSpec | None = None
    exit_nodes: list[BridgePeerSpec] | None = None
    allowed_ru_ips: list[str] | None = None
    allowed_hub_ips: list[str] | None = None
    incoming_ru_hubs: list[IncomingPeerSpec] | None = None
    incoming_hubs: list[IncomingPeerSpec] | None = None
    routes: list[HubRouteSpec] | None = None

    @model_validator(mode="after")
    def validate_ip_lists(self) -> "NodeLinksUpdatePayload":
        if self.allowed_ru_ips is not None:
            self.allowed_ru_ips = [_validate_ipv4(x) for x in self.allowed_ru_ips]
        if self.allowed_hub_ips is not None:
            self.allowed_hub_ips = [_validate_ipv4(x) for x in self.allowed_hub_ips]
        return self


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
    if clean in _ALLOWED_BROADCAST_AUDIENCES or re.fullmatch(r"server_\d+", clean):
        return clean
    raise ValueError("Недопустимая аудитория")


class AdminUserAdjustPayload(BaseModel):
    action: Literal["balance", "grant_standard", "grant_wi"]
    amount_rub: int | None = Field(default=None, ge=-100000, le=100000)
    days: int | None = Field(default=None, ge=1, le=3650)
    device_limit: int | None = Field(default=None, ge=1, le=20)
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
            if self.device_limit is not None and not (1 <= self.device_limit <= 5):
                raise ValueError("device_limit must be between 1 and 5 for wi")
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
