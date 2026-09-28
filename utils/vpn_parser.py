import base64
import json
import logging
import struct
import zlib
from typing import Any

logger = logging.getLogger(__name__)

class VPNConfigParseError(Exception):
    pass

def _decode_base64url(payload: str) -> bytes | None:
    try:
        b64 = payload.replace("-", "+").replace("_", "/")
        padding_needed = len(b64) % 4
        if padding_needed:
            b64 += "=" * (4 - padding_needed)
        return base64.b64decode(b64, validate=True)
    except Exception as e:
        logger.warning(f"_decode_base64url failed: {e}")
        raise VPNConfigParseError(f"Base64 decode failed: {e}") from e


try:
    from config.constants import DEFAULT_AWG_DNS1, DEFAULT_AWG_DNS2, DEFAULT_AWG_MTU
except ImportError:
    DEFAULT_AWG_DNS1 = "8.8.8.8"
    DEFAULT_AWG_DNS2 = "8.8.4.4"
    DEFAULT_AWG_MTU = "1280"

MAX_DECOMPRESSED_CONFIG_BYTES = 1024 * 1024  # 1 MiB

AWG3_1_EXCLUSIVE_KEYS = (
    "RandomTrailers",
    "DisableCookies",
)

AWG3_0_EXCLUSIVE_KEYS = (
    "HeaderProtectionKey",
    "ContentPaddingAddition",
    "RekeyAfterTime",
    "RekeyTimeout",
    "RejectAfterTime",
    "KeepaliveTimeout",
    "MaxHandshakeAttempts",
)

AWG3_EXCLUSIVE_KEYS = AWG3_1_EXCLUSIVE_KEYS + AWG3_0_EXCLUSIVE_KEYS

AWG_MANDATORY_BASE_KEYS = (
    "Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "H1", "H2", "H3", "H4",
)

ALL_AWG_KEYS = (
    "Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4",
    "H1", "H2", "H3", "H4", "I1", "I2", "I3", "I4", "I5",
    "HeaderProtectionKey", "ContentPaddingAddition",
    "RekeyAfterTime", "RekeyTimeout", "RejectAfterTime",
    "KeepaliveTimeout", "MaxHandshakeAttempts",
    "RandomTrailers", "DisableCookies",
)


def is_valid_awg_key(key: str) -> bool:
    """Validate 32-byte base64-encoded key (X25519 key, AmneziaWG HeaderProtectionKey, or PSK)."""
    if not key or not isinstance(key, str):
        return False
    key_str = key.strip()
    if len(key_str) != 44 or not key_str.endswith("="):
        return False
    try:
        raw = base64.b64decode(key_str, validate=True)
        return len(raw) == 32
    except Exception:
        return False


# Backward-compatible aliases
is_valid_wg_key = is_valid_awg_key
_is_valid_wg_key = is_valid_awg_key
_is_valid_awg_key = is_valid_awg_key


def detect_awg_version(params: dict[str, Any]) -> str:
    """Detect AWG protocol version ('3.1', '3.0', '2.0') adhering to Any-Tech-ARCHITECT specifications."""
    if not isinstance(params, dict):
        return "2.0"

    def has(k: str) -> bool:
        v = params.get(k)
        if v is None or v == "":
            v = params.get(k.upper())
        if v is None:
            return False
        # For toggle/integer keys (RandomTrailers, DisableCookies), "0", "false", "off", "no", "disabled" mean disabled
        if k in ("RandomTrailers", "DisableCookies") and str(v).strip().lower() in (
            "0",
            "false",
            "off",
            "no",
            "disabled",
            "",
        ):
            return False
        return str(v).strip() != ""

    pv = str(params.get("protocol_version", "")).strip()
    if any(has(k) for k in AWG3_1_EXCLUSIVE_KEYS) or pv == "3.1":
        return "3.1"
    if any(has(k) for k in AWG3_0_EXCLUSIVE_KEYS) or pv in ("3", "3.0"):
        return "3.0"

    return "2.0"


def _decompress_amnezia_format(data: bytes) -> str | None:
    if len(data) < 4:
        raise VPNConfigParseError("Payload too short")
    expected_length = struct.unpack(">I", data[:4])[0]
    if expected_length > MAX_DECOMPRESSED_CONFIG_BYTES:
        raise VPNConfigParseError(f"Decompressed length exceeds limit: {expected_length}")
    compressed = data[4:]
    try:
        decompressor = zlib.decompressobj()
        decompressed = decompressor.decompress(compressed, MAX_DECOMPRESSED_CONFIG_BYTES)
        if decompressor.unconsumed_tail or not decompressor.eof:
            logger.error("_decompress_amnezia_format: payload exceeds maximum decompressed size")
            raise VPNConfigParseError("Decompressed payload exceeds maximum size limit")
        if len(decompressed) != expected_length:
            logger.error(
                "_decompress_amnezia_format length mismatch: expected %s, got %s",
                expected_length,
                len(decompressed),
            )
            raise VPNConfigParseError("Length mismatch")
        return decompressed.decode("utf-8")
    except VPNConfigParseError:
        raise
    except Exception as e:
        logger.warning(f"_decompress_amnezia_format zlib failed: {e}")
        raise VPNConfigParseError(f"Decompress failed: {e}") from e


def decode_vpn_uri_to_json(uri: str) -> dict | None:
    if not uri or not isinstance(uri, str):
        raise VPNConfigParseError("Invalid URI type")
    payload = uri[6:] if uri.startswith("vpn://") else None
    if not payload:
        raise VPNConfigParseError("Missing payload in URI")
    decoded = _decode_base64url(payload)
    if decoded is None:
        raise VPNConfigParseError("Failed to decode payload")
    json_str = _decompress_amnezia_format(decoded)
    if json_str is None:
        raise VPNConfigParseError("Failed to decompress payload")
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise VPNConfigParseError(f"JSON decode failed: {e}") from e
    if not isinstance(data, dict):
        raise VPNConfigParseError("Payload is not a JSON object")
    return data


def _looks_like_awg_conf(conf: str | None, last_config: dict | None = None) -> bool:
    """Validate that raw .conf has valid [Interface] and [Peer] sections and all mandatory AWG 2.0+ parameters."""
    if not conf or not isinstance(conf, str):
        return False
    if "[Interface]" not in conf or "[Peer]" not in conf:
        return False

    current_section = None
    interface_params: dict[str, str] = {}
    peer_params: dict[str, str] = {}

    for raw_line in conf.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1].strip()
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if current_section == "Interface":
                interface_params[k] = v
            elif current_section == "Peer":
                peer_params[k] = v

    if "PrivateKey" not in interface_params or "Address" not in interface_params:
        return False
    if "PublicKey" not in peer_params or "Endpoint" not in peer_params:
        return False

    # All AWG 2.0+ mandatory obfuscation parameters must be present in [Interface]
    for k in AWG_MANDATORY_BASE_KEYS:
        if not interface_params.get(k):
            return False

    # If last_config is provided, perform strict consistency check
    if last_config:
        priv = last_config.get("client_priv_key")
        if priv and interface_params.get("PrivateKey") != priv:
            return False
        pub = last_config.get("server_pub_key")
        if pub and peer_params.get("PublicKey") != pub:
            return False
        ip = str(last_config.get("client_ip", "")).split("/")[0].strip()
        if ip and ip not in interface_params.get("Address", ""):
            return False
        for k in AWG_MANDATORY_BASE_KEYS:
            val = str(last_config.get(k, "")).strip()
            if val and interface_params.get(k) != val:
                return False
        hpk = last_config.get("HeaderProtectionKey")
        if hpk and str(hpk).strip():
            if interface_params.get("HeaderProtectionKey") != str(hpk).strip():
                return False

    return True


def _get_first_awg_container(data: dict) -> dict | None:
    containers = data.get("containers", [])
    if not containers or not isinstance(containers, list):
        return None
    for container in containers:
        if not isinstance(container, dict):
            continue
        if container.get("container") == "amnezia-awg2":
            awg = container.get("awg")
            if awg and isinstance(awg, dict):
                return awg
    return None


def _parse_last_config(awg: dict) -> dict | None:
    last_config_str = awg.get("last_config")
    if not last_config_str or not isinstance(last_config_str, str):
        return None
    try:
        last_config = json.loads(last_config_str)
    except json.JSONDecodeError:
        return None
    if not isinstance(last_config, dict):
        return None
    return last_config


def _build_conf_fallback(data: dict, last_config: dict, awg: dict | None = None) -> str | None:
    client_priv_key = last_config.get("client_priv_key")
    server_pub_key = last_config.get("server_pub_key")
    host_name = last_config.get("hostName") or data.get("hostName")
    port = last_config.get("port") or data.get("port")

    if not client_priv_key or not server_pub_key or not host_name or port is None:
        return None

    client_ip = last_config.get("client_ip")
    if not client_ip:
        return None
    if "/" not in str(client_ip):
        client_ip = f"{client_ip}/32"

    dns1 = data.get("dns1") or DEFAULT_AWG_DNS1
    dns2 = data.get("dns2") or DEFAULT_AWG_DNS2
    mtu = last_config.get("mtu") or DEFAULT_AWG_MTU
    persistent_keep_alive = last_config.get("persistent_keep_alive") or 25
    psk_key = last_config.get("psk_key")

    allowed_ips = last_config.get("allowed_ips")
    if isinstance(allowed_ips, list) and allowed_ips:
        allowed_ips_line = ", ".join(str(x) for x in allowed_ips)
    else:
        allowed_ips_line = "0.0.0.0/0, ::/0"

    def get_param(k: str) -> Any:
        v = last_config.get(k)
        if v is not None:
            return v
        if awg:
            return awg.get(k)
        return None

    # AWG 2.0+ mandatory obfuscation parameters
    for key in AWG_MANDATORY_BASE_KEYS:
        if get_param(key) is None or str(get_param(key)).strip() == "":
            return None

    lines = ["[Interface]", f"Address = {client_ip}", f"DNS = {dns1}, {dns2}"]
    if mtu:
        lines.append(f"MTU = {mtu}")
    lines.append(f"PrivateKey = {client_priv_key}")
    lines.extend([
        f"Jc = {get_param('Jc')}",
        f"Jmin = {get_param('Jmin')}",
        f"Jmax = {get_param('Jmax')}",
        f"S1 = {get_param('S1')}",
        f"S2 = {get_param('S2')}",
        f"S3 = {get_param('S3')}",
        f"S4 = {get_param('S4')}",
        f"H1 = {get_param('H1')}",
        f"H2 = {get_param('H2')}",
        f"H3 = {get_param('H3')}",
        f"H4 = {get_param('H4')}",
    ])

    for i in range(1, 6):
        val = get_param(f"I{i}")
        if val and str(val).strip():
            lines.append(f"I{i} = {val}")

    awg3_keys = [
        "HeaderProtectionKey", "ContentPaddingAddition", "RekeyAfterTime",
        "RekeyTimeout", "RejectAfterTime", "KeepaliveTimeout",
        "MaxHandshakeAttempts", "RandomTrailers", "DisableCookies",
    ]
    for key in awg3_keys:
        val = get_param(key)
        if val is not None and str(val).strip():
            lines.append(f"{key} = {val}")

    lines.extend([
        "",
        "[Peer]",
        f"PublicKey = {server_pub_key}",
    ])
    if psk_key:
        lines.append(f"PresharedKey = {psk_key}")
    lines.extend([
        f"AllowedIPs = {allowed_ips_line}",
        f"Endpoint = {host_name}:{port}",
        f"PersistentKeepalive = {persistent_keep_alive}",
    ])
    return "\n".join(lines) + "\n"


def build_vpn_file_from_dict(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def build_conf_file_from_dict(data: dict) -> str | None:
    try:
        awg = _get_first_awg_container(data)
        if not awg:
            raise VPNConfigParseError("No AWG container found")
        last_config = _parse_last_config(awg)
        if not last_config:
            raise VPNConfigParseError("No last_config found")
        config_str = last_config.get("config")
        if _looks_like_awg_conf(config_str, last_config):
            return config_str
        fallback_conf = _build_conf_fallback(data, last_config, awg)
        if _looks_like_awg_conf(fallback_conf, last_config):
            return fallback_conf
        raise VPNConfigParseError("Failed to build AWG conf")
    except VPNConfigParseError:
        raise
    except Exception as e:
        logger.error(f"build_conf_file_from_dict: unexpected error: {e}", exc_info=True)
        raise VPNConfigParseError(f"Unexpected error: {e}") from e


def build_vpn_file(uri: str) -> str | None:
    if not uri or not isinstance(uri, str):
        return None
    try:
        data = decode_vpn_uri_to_json(uri)
        if data is None:
            return None
        return build_vpn_file_from_dict(data)
    except Exception:
        return None


def build_conf_file(uri: str) -> str | None:
    if not uri or not isinstance(uri, str):
        return None
    try:
        data = decode_vpn_uri_to_json(uri)
        if data is not None:
            return build_conf_file_from_dict(data)
    except Exception:
        pass
    return None


def is_valid_vpn_uri(uri: str) -> bool:
    """Strictly validate an AmneziaWG 2.0 / 3.x vpn:// URI.

    Invariants enforced:
    1. Valid JSON payload with non-empty 'containers'.
    2. Container is strictly 'amnezia-awg2' (upstream canonical container for both AWG 2.0 and AWG 3.x).
    3. defaultContainer (if present) is strictly 'amnezia-awg2'.
    4. Valid protocol_version ('2', '2.0', '3', '3.0', '3.1').
    5. Valid last_config JSON with client_priv_key, server_pub_key, client_ip, hostName, port.
    6. All mandatory AWG 2.0+ obfuscation keys present (Jc, Jmin, Jmax, S1, S2, S3, S4, H1..H4).
    7. For AWG 3.x, HeaderProtectionKey is present.
    8. 3-way consistency between awg dict, last_config, and embedded config string (if present).
    """
    if not uri or not isinstance(uri, str) or not uri.startswith("vpn://"):
        return False
    try:
        data = decode_vpn_uri_to_json(uri)
        if not data or not isinstance(data, dict):
            return False

        def_container = data.get("defaultContainer")
        if def_container and def_container != "amnezia-awg2":
            return False

        containers = data.get("containers")
        if not containers or not isinstance(containers, list):
            return False

        awg_container = None
        for c in containers:
            if not isinstance(c, dict):
                continue
            if c.get("container") == "amnezia-awg2" and isinstance(c.get("awg"), dict):
                awg_container = c
                break
        if not awg_container:
            return False

        awg = awg_container.get("awg", {})
        proto_ver = str(awg.get("protocol_version", "")).strip()

        if proto_ver not in ("2", "2.0", "3", "3.0", "3.1"):
            return False

        last_config = _parse_last_config(awg)
        if not last_config:
            return False

        client_priv = last_config.get("client_priv_key")
        server_pub = last_config.get("server_pub_key")
        client_ip = last_config.get("client_ip")
        host_name = last_config.get("hostName") or data.get("hostName")
        port_val = last_config.get("port") or data.get("port")

        if not client_priv or not server_pub or not client_ip or not host_name or port_val is None:
            return False

        try:
            port_int = int(port_val)
            if not (1 <= port_int <= 65535):
                return False
        except (ValueError, TypeError):
            return False

        # Check all mandatory AWG 2.0+ parameters in last_config
        for k in AWG_MANDATORY_BASE_KEYS:
            v = last_config.get(k)
            if v is None or str(v).strip() == "":
                return False

        # Check HeaderProtectionKey consistency if present
        hpk_last = last_config.get("HeaderProtectionKey")
        hpk_awg = awg.get("HeaderProtectionKey")
        if hpk_last or hpk_awg:
            if not hpk_last or not hpk_awg:
                return False
            if str(hpk_last).strip() != str(hpk_awg).strip():
                return False

        # 3-way consistency check between awg dict and last_config:
        # All mandatory base keys and port must be present in awg dict and match last_config exactly
        for k in AWG_MANDATORY_BASE_KEYS:
            if k not in awg or str(awg[k]).strip() == "":
                return False
            if str(awg[k]).strip() != str(last_config[k]).strip():
                return False

        if "port" not in awg:
            return False
        try:
            if int(awg["port"]) != port_int:
                return False
        except (ValueError, TypeError):
            return False

        # Check config text or fallback
        config_str = last_config.get("config")
        if config_str:
            if not _looks_like_awg_conf(config_str, last_config):
                return False
        else:
            fallback = _build_conf_fallback(data, last_config, awg)
            if not fallback or not _looks_like_awg_conf(fallback, last_config):
                return False

        return True
    except Exception:
        return False


def customize_vpn_config_dict(
    data: dict,
    description: str | None = None,
    dns1: str = DEFAULT_AWG_DNS1,
    dns2: str = DEFAULT_AWG_DNS2,
    mtu: str = DEFAULT_AWG_MTU,
) -> dict:
    if not isinstance(data, dict):
        return data

    import copy
    import re
    customized = copy.deepcopy(data)

    if description:
        customized["description"] = description

    customized["dns1"] = dns1
    customized["dns2"] = dns2

    containers = customized.get("containers", [])
    if isinstance(containers, list):
        for container in containers:
            if not isinstance(container, dict):
                continue
            awg = container.get("awg")
            if not awg or not isinstance(awg, dict):
                continue

            last_config_str = awg.get("last_config")
            if last_config_str and isinstance(last_config_str, str):
                try:
                    last_config = json.loads(last_config_str)
                    if isinstance(last_config, dict):
                        last_config["mtu"] = mtu
                        config_str = last_config.get("config")
                        if config_str and isinstance(config_str, str):
                            match = re.search(r'(\[Interface\].*?)(?=\[Peer\]|$)', config_str, re.IGNORECASE | re.DOTALL)
                            if match:
                                interface_section = match.group(1)
                                interface_section = re.sub(r'^(DNS\s*=.*)$', '', interface_section, flags=re.MULTILINE | re.IGNORECASE)
                                interface_section = re.sub(r'^(MTU\s*=.*)$', '', interface_section, flags=re.MULTILINE | re.IGNORECASE)
                                interface_section = re.sub(r'\n{2,}', '\n', interface_section)
                                new_interface = re.sub(
                                    r'(\[Interface\])',
                                    f'\\1\nDNS = {dns1}, {dns2}\nMTU = {mtu}',
                                    interface_section,
                                    flags=re.IGNORECASE
                                )
                                last_config["config"] = config_str.replace(match.group(1), new_interface)

                        awg["last_config"] = json.dumps(last_config, ensure_ascii=False)
                except Exception as e:
                    logger.error(f"customize_vpn_config_dict patch failed: {e}", exc_info=True)

    return customized


def encode_json_to_vpn_uri(data: dict) -> str:
    json_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
    length_prefix = struct.pack(">I", len(json_bytes))
    compressed = zlib.compress(json_bytes)
    payload = length_prefix + compressed
    b64 = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"vpn://{b64}"


def customize_vpn_uri(
    uri: str,
    description: str | None = None,
    dns1: str = DEFAULT_AWG_DNS1,
    dns2: str = DEFAULT_AWG_DNS2,
    mtu: str = DEFAULT_AWG_MTU,
) -> str:
    if not uri or not isinstance(uri, str):
        return uri or ""
    try:
        data = decode_vpn_uri_to_json(uri)
        if not data:
            return uri
        customized = customize_vpn_config_dict(
            data,
            description=description,
            dns1=dns1,
            dns2=dns2,
            mtu=mtu,
        )
        return encode_json_to_vpn_uri(customized)
    except Exception as e:
        logger.debug("customize_vpn_uri skipped for non-vpn:// format: %s", e)
        return uri


