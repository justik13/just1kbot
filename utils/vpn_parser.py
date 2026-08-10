import base64
import json
import zlib
import logging
import struct
from typing import Optional

logger = logging.getLogger(__name__)

class VPNConfigParseError(Exception):
    pass

def _decode_base64url(payload: str) -> Optional[bytes]:
    try:
        b64 = payload.replace("-", "+").replace("_", "/")
        padding_needed = len(b64) % 4
        if padding_needed:
            b64 += "=" * (4 - padding_needed)
        return base64.b64decode(b64, validate=True)
    except Exception as e:
        logger.warning(f"_decode_base64url failed: {e}")
        raise VPNConfigParseError(f"Base64 decode failed: {e}") from e


def _decompress_amnezia_format(data: bytes) -> Optional[str]:
    if len(data) < 4:
        raise VPNConfigParseError("Payload too short")
    expected_length = struct.unpack(">I", data[:4])[0]
    compressed = data[4:]
    try:
        decompressed = zlib.decompress(compressed)
        if len(decompressed) != expected_length:
            logger.error(
                "_decompress_amnezia_format length mismatch: expected %s, got %s",
                expected_length,
                len(decompressed),
            )
            raise VPNConfigParseError("Length mismatch")
        return decompressed.decode("utf-8")
    except Exception as e:
        logger.warning(f"_decompress_amnezia_format zlib failed: {e}")
        raise VPNConfigParseError(f"Decompress failed: {e}") from e


def decode_vpn_uri_to_json(uri: str) -> Optional[dict]:
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


def _looks_like_wireguard_conf(conf: Optional[str]) -> bool:
    if not conf or not isinstance(conf, str):
        return False
    return "[Interface]" in conf and "[Peer]" in conf


def _get_first_awg_container(data: dict) -> Optional[dict]:
    containers = data.get("containers", [])
    if not containers or not isinstance(containers, list):
        return None
    for container in containers:
        if not isinstance(container, dict):
            continue
        awg = container.get("awg")
        if awg and isinstance(awg, dict):
            return awg
    return None


def _parse_last_config(awg: dict) -> Optional[dict]:
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


def _build_conf_fallback(data: dict, last_config: dict) -> Optional[str]:
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

    dns1 = data.get("dns1") or "8.8.8.8"
    dns2 = data.get("dns2") or "8.8.4.4"
    mtu = last_config.get("mtu") or "1280"
    persistent_keep_alive = last_config.get("persistent_keep_alive") or 25
    psk_key = last_config.get("psk_key")

    allowed_ips = last_config.get("allowed_ips")
    if isinstance(allowed_ips, list) and allowed_ips:
        allowed_ips_line = ", ".join(str(x) for x in allowed_ips)
    else:
        allowed_ips_line = "0.0.0.0/0, ::/0"

    awg_required_keys = ["Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "H1", "H2", "H3", "H4"]
    for key in awg_required_keys:
        if last_config.get(key) is None:
            return None

    lines = ["[Interface]", f"Address = {client_ip}", f"DNS = {dns1}, {dns2}"]
    if mtu:
        lines.append(f"MTU = {mtu}")
    lines.append(f"PrivateKey = {client_priv_key}")
    lines.extend([
        f"Jc = {last_config.get('Jc')}",
        f"Jmin = {last_config.get('Jmin')}",
        f"Jmax = {last_config.get('Jmax')}",
        f"S1 = {last_config.get('S1')}",
        f"S2 = {last_config.get('S2')}",
        f"S3 = {last_config.get('S3')}",
        f"S4 = {last_config.get('S4')}",
        f"H1 = {last_config.get('H1')}",
        f"H2 = {last_config.get('H2')}",
        f"H3 = {last_config.get('H3')}",
        f"H4 = {last_config.get('H4')}",
        "",
        f"h1 = {last_config.get('I1', '') or ''}",
        f"h2 = {last_config.get('I2', '') or ''}",
        f"h3 = {last_config.get('I3', '') or ''}",
        f"h4 = {last_config.get('I4', '') or ''}",
        f"h5 = {last_config.get('I5', '') or ''}",
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


def build_conf_file_from_dict(data: dict) -> Optional[str]:
    try:
        awg = _get_first_awg_container(data)
        if not awg:
            raise VPNConfigParseError("No AWG container found")
        last_config = _parse_last_config(awg)
        if not last_config:
            raise VPNConfigParseError("No last_config found")
        config_str = last_config.get("config")
        if _looks_like_wireguard_conf(config_str):
            return config_str
        fallback_conf = _build_conf_fallback(data, last_config)
        if _looks_like_wireguard_conf(fallback_conf):
            return fallback_conf
        raise VPNConfigParseError("Failed to build wireguard conf")
    except VPNConfigParseError:
        raise
    except Exception as e:
        logger.error(f"build_conf_file_from_dict: unexpected error: {e}", exc_info=True)
        raise VPNConfigParseError(f"Unexpected error: {e}") from e


def build_vpn_file(uri: str) -> Optional[str]:
    if not uri or not isinstance(uri, str):
        return None
    try:
        data = decode_vpn_uri_to_json(uri)
        if data is None:
            return None
        return build_vpn_file_from_dict(data)
    except Exception:
        return None


def build_conf_file(uri: str) -> Optional[str]:
    if not uri or not isinstance(uri, str):
        return None
    try:
        data = decode_vpn_uri_to_json(uri)
        if data is not None:
            return build_conf_file_from_dict(data)
    except Exception:
        pass
    if _looks_like_wireguard_conf(uri):
        return uri
    return None


def is_valid_vpn_uri(uri: str) -> bool:
    try:
        data = decode_vpn_uri_to_json(uri)
        if not data or not isinstance(data, dict):
            return False
        awg = _get_first_awg_container(data)
        if not awg:
            return False
        protocol_version = awg.get("protocol_version")
        if str(protocol_version) == "2":
            return True
        last_config = _parse_last_config(awg)
        if not last_config:
            return False
        config_str = last_config.get("config")
        if _looks_like_wireguard_conf(config_str):
            return True
        fallback_conf = _build_conf_fallback(data, last_config)
        if _looks_like_wireguard_conf(fallback_conf):
            return True
        return False
    except Exception:
        return False


def customize_vpn_config_dict(
    data: dict,
    description: Optional[str] = None,
    dns1: str = "8.8.8.8",
    dns2: str = "8.8.4.4",
    mtu: str = "1280",
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
    description: Optional[str] = None,
    dns1: str = "8.8.8.8",
    dns2: str = "8.8.4.4",
    mtu: str = "1280",
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


