"""Pydantic schemas for Mini App Admin API requests, synced with just1knode v2 contract."""

from __future__ import annotations

import ipaddress
from typing import Any

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


class Verify2FAStubPayload(BaseModel):
    code: str = Field(default="", max_length=32)


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
