import json
import re

from config.constants import AMNEZIA_PROTOCOL, MAX_RAW_CONFIG_BYTES
from database.models import VPNProfile
from utils.vpn_parser import (
    customize_vpn_uri,
    decode_vpn_uri_to_json,
)


class InvalidAmneziaProfileError(ValueError):
    """Raised when profile or server state is invalid for Amnezia."""


class InvalidAmneziaConfigError(ValueError):
    """Raised when profile config is not a valid Amnezia AWG2 vpn:// URI."""


def _get_awg_block(data: dict) -> dict | None:
    """Safely extract the first awg dictionary from containers without private parser imports."""
    if not isinstance(data, dict):
        return None
    containers = data.get("containers", [])
    if isinstance(containers, list):
        for container in containers:
            if isinstance(container, dict):
                awg = container.get("awg")
                if isinstance(awg, dict):
                    return awg
    return None


def _get_effective_mtu(awg: dict) -> str | None:
    """Extract effective MTU from last_config."""
    if not isinstance(awg, dict):
        return None
    last_config_str = awg.get("last_config")
    if not last_config_str or not isinstance(last_config_str, str):
        return None
    try:
        last_config = json.loads(last_config_str)
        if isinstance(last_config, dict):
            mtu_val = last_config.get("mtu")
            if mtu_val is not None:
                return str(mtu_val)
            config_str = last_config.get("config", "")
            if isinstance(config_str, str):
                m = re.search(r'MTU\s*=\s*(\d+)', config_str, re.IGNORECASE)
                if m:
                    return m.group(1)
    except (json.JSONDecodeError, TypeError, KeyError):
        return None
    return None


def build_display_vpn_uri(profile: VPNProfile) -> str:
    """Build and customize a display vpn:// URI for an AmneziaWG 2.0 profile.

    Authoritative single-pass builder ensuring protocol consistency and
    post-customization integrity checks without altering legacy parser fallbacks.
    Preserves exact legacy naming semantics without stripping server names.
    """
    if not profile or not profile.server:
        raise InvalidAmneziaProfileError("Profile server must be eagerly loaded")
    if profile.server.protocol != AMNEZIA_PROTOCOL:
        raise InvalidAmneziaProfileError(
            f"Unsupported protocol for Amnezia display URI: {profile.server.protocol}"
        )

    raw_config = (profile.raw_config or "").strip()
    if not raw_config or not raw_config.startswith("vpn://"):
        raise InvalidAmneziaConfigError("raw_config is empty or does not start with vpn://")

    if len(raw_config.encode("utf-8")) > MAX_RAW_CONFIG_BYTES:
        raise InvalidAmneziaConfigError("raw_config exceeds maximum allowed size (64 KiB)")

    # Single-pass structural JSON and AWG2 decoding
    data = decode_vpn_uri_to_json(raw_config)
    if not data or not isinstance(data, dict):
        raise InvalidAmneziaConfigError("raw_config failed JSON decode")

    awg = _get_awg_block(data)
    if not awg or str(awg.get("protocol_version")) != "2":
        raise InvalidAmneziaConfigError("raw_config is not an AWG2 container (protocol_version != 2)")

    # Preserve exact legacy naming semantics (no .strip() on server.name)
    server_name = profile.server.name if profile.server.name else "server"
    m = re.search(r'#(\d+)$', profile.device_name or "")
    slot_suffix = f" #{m.group(1)}" if m else ""
    client_description = f"{server_name}{slot_suffix}"

    display_key = customize_vpn_uri(
        raw_config,
        description=client_description,
        dns1="8.8.8.8",
        dns2="8.8.4.4",
        mtu="1280",
    )

    if not display_key or not display_key.startswith("vpn://"):
        raise InvalidAmneziaConfigError("customize_vpn_uri did not produce a vpn:// URI")

    # Single-pass post-customization integrity validation
    customized_data = decode_vpn_uri_to_json(display_key)
    if not customized_data or not isinstance(customized_data, dict):
        raise InvalidAmneziaConfigError("customized display_key failed JSON decode")

    customized_awg = _get_awg_block(customized_data)
    if not customized_awg or str(customized_awg.get("protocol_version")) != "2":
        raise InvalidAmneziaConfigError("customized display_key is not valid AWG2")

    if customized_data.get("description") != client_description:
        raise InvalidAmneziaConfigError("customized display_key description mismatch")
    if customized_data.get("dns1") != "8.8.8.8" or customized_data.get("dns2") != "8.8.4.4":
        raise InvalidAmneziaConfigError("customized display_key DNS mismatch")
    if _get_effective_mtu(customized_awg) != "1280":
        raise InvalidAmneziaConfigError("customized display_key MTU mismatch")

    return display_key
