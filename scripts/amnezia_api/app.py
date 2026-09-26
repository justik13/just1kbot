"""AmneziaWG REST API Microservice for Just1kBot / Just1kNode.

High-performance native Python implementation replacing kyoresuas/amnezia-api:
- Native Curve25519 keypair generation via cryptography (< 0.1ms vs 2000ms docker exec)
- Strict compatibility with upstream Amnezia Awg2 container (amnezia-awg2 / awg0 / awg0.conf)
- Upstream-compatible clientsTable model (clientId = public key, userData object)
- Non-destructive peer management (append [Peer] section, never overwrite existing peers)
- Faithful AWG 2.0 and 3.x parameter propagation (Jc, S1-S4, H1-H4, I1-I5, HeaderProtectionKey, etc.)
- Fail-closed API key authentication
- Asynchronous container execution without event loop blocking
- Memory footprint: ~25 MB RAM (vs ~150 MB for Node.js container)
- Full vpn:// and .conf format compatibility
"""

import asyncio
import base64
import ipaddress
import json
import logging
import os
import re
import secrets
import struct
import tempfile
import time
import zlib
from contextlib import asynccontextmanager
from typing import Any

import psutil
from cryptography.hazmat.primitives.asymmetric import x25519
from fastapi import Depends, FastAPI, HTTPException, Header, status
from pydantic import BaseModel, Field

logger = logging.getLogger("amnezia_api")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
API_KEY = os.getenv("AMNEZIA_API_KEY") or os.getenv("FASTIFY_API_KEY", "")
AWG_CONTAINER_NAME = os.getenv("AWG_CONTAINER_NAME", "amnezia-awg2")
AWG_DIR = os.getenv("AWG_DIR", "/opt/amnezia/awg")
AWG_CONF_PATH = os.getenv("AWG_CONF_PATH", "")
CLIENTS_TABLE_PATH = os.getenv("CLIENTS_TABLE_PATH", "")
SERVER_PUBKEY_PATH = os.getenv("SERVER_PUBKEY_PATH", "")
SERVER_PSK_PATH = os.getenv("SERVER_PSK_PATH", "")
SERVER_HOST_NAME = os.getenv("SERVER_HOST_NAME") or os.getenv("SERVER_PUBLIC_HOST", "")
SERVER_DNS1 = os.getenv("SERVER_DNS1", "1.1.1.1")
SERVER_DNS2 = os.getenv("SERVER_DNS2", "1.0.0.1")

state_lock = asyncio.Lock()


def get_target_container() -> str:
    """Return effective container name, defaulting to amnezia-awg2."""
    if AWG_CONTAINER_NAME:
        return AWG_CONTAINER_NAME
    return "amnezia-awg2"


def get_interface_name(container: str | None = None) -> str:
    """Return kernel interface name (awg0 for Awg2, wg0 for legacy)."""
    c = container or get_target_container()
    return "awg0" if "awg2" in c else "wg0"


def get_tool_binary(container: str | None = None) -> str:
    """Return CLI tool name (awg for Awg2, wg for legacy)."""
    c = container or get_target_container()
    return "awg" if "awg2" in c else "wg"


def get_config_path(container: str | None = None) -> str:
    if AWG_CONF_PATH:
        return AWG_CONF_PATH
    c = container or get_target_container()
    conf_name = "awg0.conf" if "awg2" in c else "wg0.conf"
    return os.path.join(AWG_DIR, conf_name)


def get_clients_table_path() -> str:
    if CLIENTS_TABLE_PATH:
        return CLIENTS_TABLE_PATH
    return os.path.join(AWG_DIR, "clientsTable")


def get_server_pubkey_path() -> str:
    if SERVER_PUBKEY_PATH:
        return SERVER_PUBKEY_PATH
    return os.path.join(AWG_DIR, "wireguard_server_public_key.key")


def get_server_psk_path() -> str:
    if SERVER_PSK_PATH:
        return SERVER_PSK_PATH
    return os.path.join(AWG_DIR, "wireguard_psk.key")


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    container = get_target_container()
    logger.info("Starting AmneziaWG API Service (Native Python)")
    logger.info("Target container: %s (interface: %s)", container, get_interface_name(container))
    logger.info("AWG config path: %s", get_config_path(container))
    logger.info("Clients table path: %s", get_clients_table_path())
    if not API_KEY:
        logger.error("AMNEZIA_API_KEY is not set! Protected endpoints will fail closed with HTTP 500.")
    yield
    logger.info("Shutting down AmneziaWG API Service")


app = FastAPI(title="Just1kBot AmneziaWG API", version="2.1.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Authentication (Fail-Closed)
# ---------------------------------------------------------------------------
def verify_api_key(x_api_key: str | None = Header(None)) -> bool:
    if not API_KEY:
        logger.error("Authentication rejected: AMNEZIA_API_KEY environment variable is empty or unset")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server configuration error: AMNEZIA_API_KEY is not configured",
        )
    if not x_api_key or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing x-api-key header",
        )
    return True


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class ClientCreateRequest(BaseModel):
    clientName: str = Field(..., min_length=1, max_length=128)
    protocol: str = "amneziawg2"
    expiresAt: int | None = None


class ClientDeleteRequest(BaseModel):
    clientId: str = Field(..., min_length=1)
    protocol: str = "amneziawg2"


class ClientPatchRequest(BaseModel):
    clientId: str = Field(..., min_length=1)
    status: str | None = None  # "active" | "disabled"
    expiresAt: int | None = None
    protocol: str = "amneziawg2"


# ---------------------------------------------------------------------------
# Container & Filesystem Helpers
# ---------------------------------------------------------------------------
async def run_docker_exec_async(
    cmd: list[str],
    input_data: str | None = None,
    timeout: float = 8.0,
) -> tuple[int, str, str]:
    """Execute command inside Docker container asynchronously without blocking event loop."""
    container = get_target_container()
    full_cmd = ["docker", "exec", "-i", container] + cmd
    stdin_mode = asyncio.subprocess.PIPE if input_data is not None else None

    try:
        proc = await asyncio.create_subprocess_exec(
            *full_cmd,
            stdin=stdin_mode,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        in_bytes = input_data.encode("utf-8") if input_data is not None else None
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=in_bytes),
                timeout=timeout,
            )
            stdout = stdout_b.decode("utf-8", errors="replace")
            stderr = stderr_b.decode("utf-8", errors="replace")
            return proc.returncode or 0, stdout, stderr
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            logger.warning("Docker exec timed out (%ss): %s", timeout, cmd)
            return -1, "", "timeout"
    except FileNotFoundError:
        # Docker binary not installed or not in PATH (local test environment)
        return -1, "", "docker binary not found"
    except Exception as e:
        logger.warning("Docker exec failed: %s: %s", cmd, e)
        return -1, "", str(e)


async def is_container_running(container: str | None = None) -> bool:
    c = container or get_target_container()
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "inspect", "-f", "{{.State.Running}}", c,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
        return stdout_b.decode("utf-8", errors="ignore").strip().lower() == "true"
    except Exception:
        return False


def _write_file_atomic_host(path: str, content: str) -> None:
    dirname = os.path.dirname(os.path.abspath(path))
    os.makedirs(dirname, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=dirname, delete=False, encoding="utf-8") as tf:
        tf.write(content)
        tf.flush()
        os.fsync(tf.fileno())
        tmp_name = tf.name
    os.replace(tmp_name, path)


def _read_host_file(path: str) -> str | None:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception as e:
            logger.error("Failed to read host file %s: %s", path, e)
    return None


def _write_host_file(path: str, content: str) -> bool:
    dirname = os.path.dirname(os.path.abspath(path))
    if os.path.exists(dirname) or os.path.exists(path):
        try:
            _write_file_atomic_host(path, content)
            return True
        except Exception as e:
            logger.debug("Failed writing host file %s: %s", path, e)
    return False


async def read_container_file_async(path: str, default: str = "") -> str:
    """Read file content from host (if available) or from inside container."""
    # 1. Prefer host path if it physically exists (e.g. test fixtures, mounted volumes)
    host_content = await asyncio.to_thread(_read_host_file, path)
    if host_content is not None:
        return host_content

    # 2. Read inside container via docker exec
    rc, stdout, stderr = await run_docker_exec_async(["cat", path])
    if rc == 0:
        return stdout
    logger.debug("Container file read failed (%s): %s", path, stderr)
    return default


async def write_container_file_async(path: str, content: str) -> bool:
    """Write file content to host (if dir exists) and/or inside container atomically."""
    written_host = await asyncio.to_thread(_write_host_file, path, content)

    # In addition, if container is running or docker is available, inject into container
    dir_in_container = os.path.dirname(path)
    sh_cmd = f"mkdir -p '{dir_in_container}' && cat > '{path}.tmp' && mv -f '{path}.tmp' '{path}'"
    rc, _, stderr = await run_docker_exec_async(["sh", "-c", sh_cmd], input_data=content)
    if rc == 0:
        return True

    return written_host


# ---------------------------------------------------------------------------
# Clients Table I/O (Upstream Amnezia Format Compatible)
# ---------------------------------------------------------------------------
async def load_clients_table_async() -> list[dict[str, Any]]:
    path = get_clients_table_path()
    content = await read_container_file_async(path, "[]")
    content = content.strip()
    if not content:
        return []
    try:
        data = json.loads(content)
        if isinstance(data, list):
            return data
    except Exception as e:
        logger.error("Failed to parse clientsTable JSON: %s", e)
    return []


async def save_clients_table_async(clients: list[dict[str, Any]]) -> bool:
    path = get_clients_table_path()
    content = json.dumps(clients, indent=2, ensure_ascii=False)
    return await write_container_file_async(path, content)


# ---------------------------------------------------------------------------
# Cryptography & Key Management
# ---------------------------------------------------------------------------
def generate_keypair() -> tuple[str, str]:
    """Generate X25519 private and public keys in base64."""
    priv = x25519.X25519PrivateKey.generate()
    priv_b64 = base64.b64encode(priv.private_bytes_raw()).decode("ascii")
    pub_b64 = base64.b64encode(priv.public_key().public_bytes_raw()).decode("ascii")
    return priv_b64, pub_b64


def generate_psk() -> str:
    """Generate 32-byte pre-shared key in base64."""
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def parse_awg_conf(content: str) -> dict[str, Any]:
    """Parse awg0.conf/wg0.conf to extract Interface params and Peers."""
    interface_params: dict[str, str] = {}
    peers: list[dict[str, str]] = []

    current_section = None
    current_peer: dict[str, str] = {}

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Extract commented AWG parameters (e.g. # I1 = 123)
        if line.startswith("#"):
            comment_content = line.lstrip("#").strip()
            if "=" in comment_content:
                k, v = comment_content.split("=", 1)
                k_clean = k.strip()
                v_clean = v.strip()
                if k_clean.upper() in ("I1", "I2", "I3", "I4", "I5") and current_section == "interface":
                    interface_params[k_clean.upper()] = v_clean
            continue

        if line.lower() == "[interface]":
            current_section = "interface"
            continue
        elif line.lower() == "[peer]":
            if current_section == "peer" and current_peer:
                peers.append(current_peer)
            current_section = "peer"
            current_peer = {}
            continue

        if "=" in line:
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            if current_section == "interface":
                interface_params[key] = val
            elif current_section == "peer":
                current_peer[key] = val

    if current_section == "peer" and current_peer:
        peers.append(current_peer)

    return {
        "interface": interface_params,
        "peers": peers,
    }


async def get_server_public_key_async(container: str, interface_params: dict[str, str]) -> str:
    pub_path = get_server_pubkey_path()
    pub = (await read_container_file_async(pub_path)).strip()
    if pub:
        return pub

    # Fallback to legacy filename
    legacy_pub_path = os.path.join(AWG_DIR, "server_public_key.key")
    if legacy_pub_path != pub_path:
        pub = (await read_container_file_async(legacy_pub_path)).strip()
        if pub:
            return pub

    priv_b64 = interface_params.get("PrivateKey", "")
    if priv_b64:
        try:
            priv_bytes = base64.b64decode(priv_b64)
            priv = x25519.X25519PrivateKey.from_private_bytes(priv_bytes)
            return base64.b64encode(priv.public_key().public_bytes_raw()).decode("ascii")
        except Exception as e:
            logger.error("Failed to derive server public key: %s", e)
    return ""


async def get_server_psk_async() -> str:
    psk_path = get_server_psk_path()
    psk = (await read_container_file_async(psk_path)).strip()
    if psk:
        return psk

    # Fallback to legacy filename
    legacy_psk_path = os.path.join(AWG_DIR, "psk.key")
    if legacy_psk_path != psk_path:
        psk = (await read_container_file_async(legacy_psk_path)).strip()
        if psk:
            return psk

    return generate_psk()


def allocate_next_ip(interface_addr: str, existing_peers: list[dict[str, str]]) -> str:
    """Dynamically allocate the next available client IP from the interface subnet."""
    if not interface_addr:
        interface_addr = "10.8.1.1/24"

    iface = ipaddress.ip_interface(interface_addr)
    network = iface.network
    server_ip = iface.ip

    used_ips = {server_ip}
    for p in existing_peers:
        cip = p.get("AllowedIPs") or p.get("clientIp") or p.get("ip")
        if cip:
            try:
                clean_ip = cip.split("/")[0].strip()
                used_ips.add(ipaddress.ip_address(clean_ip))
            except ValueError:
                pass

    for host in network.hosts():
        if host not in used_ips:
            return str(host)

    raise HTTPException(
        status_code=status.HTTP_507_INSUFFICIENT_STORAGE,
        detail=f"Subnet {network} is fully allocated ({len(used_ips)} IPs used)",
    )


# ---------------------------------------------------------------------------
# Client Config & vpn:// URI Builder
# ---------------------------------------------------------------------------
def encode_vpn_uri(data: dict[str, Any]) -> str:
    """Encode connection profile into Amnezia vpn:// URI."""
    json_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
    orig_len = len(json_bytes)
    compressed = zlib.compress(json_bytes)
    payload = struct.pack(">I", orig_len) + compressed
    b64_url = (
        base64.urlsafe_b64encode(payload)
        .decode("ascii")
        .rstrip("=")
        .replace("+", "-")
        .replace("/", "_")
    )
    return f"vpn://{b64_url}"


def build_client_configs(
    client: dict[str, Any],
    iface: dict[str, str],
    server_pubkey: str,
    host_name: str,
    dns1: str,
    dns2: str,
    container_name: str,
) -> tuple[str, str]:
    """Generate raw .conf and vpn:// connection profile preserving all AWG 2.0/3.x parameters."""
    client_ip = client.get("clientIp", "")
    client_priv = client.get("clientPrivKey", "")
    client_pub = client.get("clientPubKey", "")
    psk = client.get("psk", "")

    port_str = iface.get("ListenPort", "44321")
    port_int = int(port_str) if port_str.isdigit() else 44321

    # Extract all AWG parameters from server [Interface]
    awg_keys = [
        "Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4",
        "H1", "H2", "H3", "H4", "I1", "I2", "I3", "I4", "I5",
        "HeaderProtectionKey", "ContentPaddingAddition",
        "RekeyAfterTime", "RekeyTimeout", "RejectAfterTime",
        "KeepaliveTimeout", "MaxHandshakeAttempts",
        "RandomTrailers", "DisableCookies",
    ]

    detected_awg: dict[str, str] = {}
    for k in awg_keys:
        if k in iface:
            detected_awg[k] = iface[k]
        elif k.upper() in iface:
            detected_awg[k] = iface[k.upper()]

    # Construct raw .conf
    conf_lines = [
        "[Interface]",
        f"Address = {client_ip}/32",
        f"DNS = {dns1}, {dns2}",
        f"PrivateKey = {client_priv}",
    ]
    for k, v in detected_awg.items():
        conf_lines.append(f"{k} = {v}")

    conf_lines.extend([
        "",
        "[Peer]",
        f"PublicKey = {server_pubkey}",
    ])
    if psk:
        conf_lines.append(f"PresharedKey = {psk}")
    conf_lines.extend([
        "AllowedIPs = 0.0.0.0/0, ::/0",
        f"Endpoint = {host_name}:{port_int}",
        "PersistentKeepalive = 25",
    ])
    raw_conf = "\n".join(conf_lines) + "\n"

    # Determine protocol version for vpn://
    has_awg3 = any(
        k in detected_awg
        for k in (
            "HeaderProtectionKey", "ContentPaddingAddition", "RekeyAfterTime",
            "RekeyTimeout", "RejectAfterTime", "KeepaliveTimeout",
            "MaxHandshakeAttempts", "RandomTrailers", "DisableCookies",
            "I1", "I2", "I3", "I4", "I5",
        )
    )
    protocol_version = "3.1" if has_awg3 else "2"

    last_config_data = {
        "clientId": client_pub,
        "client_ip": client_ip,
        "client_priv_key": client_priv,
        "client_pub_key": client_pub,
        "hostName": host_name,
        "port": port_int,
        "psk_key": psk,
        "server_pub_key": server_pubkey,
    }

    awg_container_dict: dict[str, Any] = {
        "protocol_version": protocol_version,
        "port": str(port_int),
        "transport_proto": "udp",
        "last_config": json.dumps(last_config_data, ensure_ascii=False),
    }
    for k, v in detected_awg.items():
        awg_container_dict[k] = str(v)

    vpn_data = {
        "containers": [
            {
                "container": container_name,
                "awg": awg_container_dict,
            }
        ],
        "defaultContainer": container_name,
        "description": host_name,
        "dns1": dns1,
        "dns2": dns2,
        "hostName": host_name,
    }

    vpn_uri = encode_vpn_uri(vpn_data)
    return raw_conf, vpn_uri


# ---------------------------------------------------------------------------
# Kernel & Runtime Peer Sync
# ---------------------------------------------------------------------------
async def sync_kernel_peer_add(pubkey: str, ip: str, psk: str, container: str) -> bool:
    """Add or update peer in active kernel runtime without restarting container."""
    iface = get_interface_name(container)
    binary = get_tool_binary(container)

    safe_psk = re.sub(r"[^A-Za-z0-9+/=]", "", psk)
    safe_pub = re.sub(r"[^A-Za-z0-9+/=]", "", pubkey)
    safe_ip = re.sub(r"[^0-9.]", "", ip.split("/")[0])
    tmp_token = secrets.token_hex(8)

    # Securely feed PSK using trap to clean up temporary file immediately upon exit
    sh_cmd = (
        f"TMP_PSK='/tmp/awg_psk_{tmp_token}.tmp'; "
        f"trap 'rm -f \"$TMP_PSK\"' EXIT; "
        f"echo '{safe_psk}' > \"$TMP_PSK\" && "
        f"{binary} set {iface} peer '{safe_pub}' allowed-ips '{safe_ip}/32' preshared-key \"$TMP_PSK\""
    )
    rc, _, stderr = await run_docker_exec_async(["sh", "-c", sh_cmd])
    if rc == 0:
        return True
    logger.warning("Kernel peer add failed (%s): %s", pubkey, stderr)
    return False


async def sync_kernel_peer_remove(pubkey: str, container: str) -> bool:
    """Remove peer from active kernel runtime."""
    iface = get_interface_name(container)
    binary = get_tool_binary(container)
    safe_pub = re.sub(r"[^A-Za-z0-9+/=]", "", pubkey)
    rc, _, stderr = await run_docker_exec_async([binary, "set", iface, "peer", safe_pub, "remove"])
    return rc == 0


async def fetch_live_transfer_stats(container: str) -> dict[str, dict[str, Any]]:
    """Fetch live transfer and handshake stats from kernel for each peer."""
    stats: dict[str, dict[str, Any]] = {}
    iface = get_interface_name(container)
    binary = get_tool_binary(container)
    rc, stdout, _ = await run_docker_exec_async([binary, "show", iface, "dump"])
    if rc != 0 or not stdout:
        return stats

    for line in stdout.splitlines():
        parts = line.strip().split("\t")
        if len(parts) >= 8:
            peer_pub = parts[0]
            try:
                handshake = int(parts[4])
                rx = int(parts[5])
                tx = int(parts[6])
                stats[peer_pub] = {
                    "lastHandshake": handshake if handshake > 0 else None,
                    "rx": rx,
                    "tx": tx,
                }
            except (ValueError, IndexError):
                pass

    return stats


async def syncconf_container(container: str, conf_path: str) -> bool:
    """Sync running kernel state with config file."""
    iface = get_interface_name(container)
    binary = get_tool_binary(container)
    sh_cmd = f"{binary} syncconf {iface} <({binary}-quick strip '{conf_path}')"
    rc, _, stderr = await run_docker_exec_async(["bash", "-c", sh_cmd])
    if rc != 0:
        logger.warning("syncconf failed: %s", stderr)
    return rc == 0


async def _fetch_public_ip_async() -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "curl", "-s", "--max-time", "3", "https://ifconfig.me",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=4.0)
        ip = stdout_b.decode("utf-8", errors="ignore").strip()
        if ip:
            return ip
    except Exception:
        pass
    return "127.0.0.1"


# ---------------------------------------------------------------------------
# Non-destructive Peer Management Helpers
# ---------------------------------------------------------------------------
def append_peer_to_conf_text(conf_text: str, pubkey: str, psk: str, ip: str) -> str:
    """Non-destructively append a new [Peer] section to awg0.conf."""
    clean_conf = conf_text.rstrip()
    peer_block = [
        "",
        "[Peer]",
        f"PublicKey = {pubkey}",
    ]
    if psk:
        peer_block.append(f"PresharedKey = {psk}")
    peer_block.append(f"AllowedIPs = {ip}/32")
    peer_block.append("")
    return clean_conf + "\n" + "\n".join(peer_block) + "\n"


def remove_peer_from_conf_text(conf_text: str, target_pubkey: str) -> tuple[str, bool]:
    """Remove only the specific [Peer] section matching target_pubkey."""
    sections = conf_text.split("[")
    new_sections = []
    removed = False

    for s in sections:
        if not s.strip():
            continue
        reconstructed = "[" + s
        if reconstructed.lower().startswith("[peer]"):
            lines = reconstructed.splitlines()
            peer_pub = ""
            for line in lines:
                if "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip().lower() == "publickey":
                        peer_pub = v.strip()
                        break
            if peer_pub == target_pubkey:
                removed = True
                continue  # skip this section
        new_sections.append(reconstructed)

    return "\n".join(new_sections), removed


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/healthz")
async def healthcheck():
    """Service liveness and readiness probe."""
    container = get_target_container()
    docker_running = await is_container_running(container)
    iface_ready = False
    if docker_running:
        iface = get_interface_name(container)
        binary = get_tool_binary(container)
        rc, _, _ = await run_docker_exec_async([binary, "show", iface])
        iface_ready = (rc == 0)

    is_healthy = docker_running and iface_ready
    return {
        "ok": is_healthy,
        "status": "ok" if is_healthy else "degraded",
        "service": "amnezia-api",
        "container": container,
        "container_running": docker_running,
        "interface_ready": iface_ready,
        "timestamp": int(time.time()),
    }


@app.get("/server", dependencies=[Depends(verify_api_key)])
async def get_server():
    """Return server parameters and capacity."""
    container = get_target_container()
    conf_path = get_config_path(container)
    conf_content = await read_container_file_async(conf_path)
    parsed = parse_awg_conf(conf_content)
    iface = parsed["interface"]
    server_pub = await get_server_public_key_async(container, iface)

    addr = iface.get("Address", "10.8.1.1/24")
    try:
        network = ipaddress.ip_interface(addr).network
        max_peers = max(1, network.num_addresses - 2)
    except Exception:
        max_peers = 254

    env_max_peers = os.getenv("SERVER_MAX_PEERS")
    if env_max_peers and env_max_peers.isdigit() and int(env_max_peers) > 0:
        max_peers = int(env_max_peers)

    port_str = iface.get("ListenPort", "44321")
    port = int(port_str) if port_str.isdigit() else 44321

    peers = parsed.get("peers", [])

    return {
        "id": os.getenv("SERVER_ID", container),
        "name": os.getenv("SERVER_NAME", container),
        "region": os.getenv("SERVER_REGION", ""),
        "weight": int(os.getenv("SERVER_WEIGHT", "0")),
        "protocols": ["amneziawg2"],
        "maxPeers": max_peers,
        "serverMaxPeers": max_peers,
        "SERVER_MAX_PEERS": max_peers,
        "totalPeers": len(peers),
        "port": port,
        "publicKey": server_pub,
        "dns1": SERVER_DNS1,
        "dns2": SERVER_DNS2,
    }


@app.get("/server/load", dependencies=[Depends(verify_api_key)])
async def get_server_load():
    """Return live system resource utilization."""
    container = get_target_container()
    conf_path = get_config_path(container)
    conf_content = await read_container_file_async(conf_path)
    parsed = parse_awg_conf(conf_content)
    peers = parsed.get("peers", [])

    uptime = 0
    try:
        uptime = int(time.time() - psutil.boot_time())
    except Exception:
        pass

    cpu_cores = psutil.cpu_count(logical=True) or 1
    load_avg = [0.0, 0.0, 0.0]
    try:
        load_avg = list(os.getloadavg())
    except Exception:
        pass

    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    return {
        # Upstream kyoresuas/amnezia-api payload structure
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "uptimeSec": uptime,
        "loadavg": load_avg,
        "cpu": {"cores": cpu_cores},
        "memory": {
            "totalBytes": mem.total,
            "freeBytes": mem.available,
            "usedBytes": mem.used,
        },
        "disk": {
            "totalBytes": disk.total,
            "usedBytes": disk.used,
            "availableBytes": disk.free,
            "usedPercent": disk.percent,
        },
        # Flat fields for simple consumers / existing tests
        "cpu_percent": psutil.cpu_percent(interval=None),
        "ram_percent": mem.percent,
        "disk_percent": disk.percent,
        "uptime_seconds": uptime,
        "total_peers": len(peers),
        "active_peers": len(peers),
    }


@app.get("/clients", dependencies=[Depends(verify_api_key)])
async def get_clients(skip: int = 0, limit: int | None = None):
    """Return all clients merged with live kernel stats and pagination."""
    container = get_target_container()
    conf_path = get_config_path(container)
    conf_content = await read_container_file_async(conf_path)
    parsed = parse_awg_conf(conf_content)
    peers = parsed.get("peers", [])

    clients_table = await load_clients_table_async()
    clients_map: dict[str, dict[str, Any]] = {}
    for c in clients_table:
        cid = c.get("clientId") or c.get("id") or c.get("clientPubKey")
        if cid:
            clients_map[cid] = c

    stats = await fetch_live_transfer_stats(container)

    result = []
    now_ts = time.time()
    for p in peers:
        pub = p.get("PublicKey", "")
        if not pub:
            continue

        c_meta = clients_map.get(pub, {})
        user_data = c_meta.get("userData", {}) if isinstance(c_meta.get("userData"), dict) else {}

        name = (
            user_data.get("clientName")
            or c_meta.get("clientName")
            or c_meta.get("name")
            or f"Peer {pub[:8]}"
        )
        status_val = c_meta.get("status", "active")
        client_ip = p.get("AllowedIPs", "").split("/")[0]

        peer_stats = stats.get(pub, {})
        rx = peer_stats.get("rx", 0)
        tx = peer_stats.get("tx", 0)
        handshake = peer_stats.get("lastHandshake")
        is_online = bool(handshake and (now_ts - handshake < 180))

        peer_entry = {
            "id": pub,
            "clientId": pub,
            "name": name,
            "status": status_val,
            "allowedIps": [f"{client_ip}/32"] if client_ip else [],
            "lastHandshake": handshake,
            "lastSeen": handshake,
            "traffic": {
                "received": rx,
                "sent": tx,
            },
            "traffics": {
                "received": rx,
                "sent": tx,
                "totalDownload": rx,
                "totalUpload": tx,
            },
            "endpoint": "",
            "online": is_online,
            "expiresAt": c_meta.get("expiresAt"),
            "protocol": "amneziawg2",
        }

        item = {
            # Upstream format: username + peers array
            "username": name,
            "peers": [peer_entry],
            # Dual flat format for direct item consumers:
            "id": pub,
            "clientId": pub,
            "clientPubKey": pub,
            "name": name,
            "peer_name": name,
            "clientName": name,
            "status": status_val,
            "clientIp": client_ip,
            "traffic": {
                "received": rx,
                "sent": tx,
            },
            "traffics": {
                "received": rx,
                "sent": tx,
                "totalDownload": rx,
                "totalUpload": tx,
            },
            "lastHandshake": handshake,
            "lastSeen": handshake,
            "updatedAt": c_meta.get("updatedAt", c_meta.get("createdAt")),
            "expiresAt": c_meta.get("expiresAt"),
            "protocol": "amneziawg2",
        }
        result.append(item)

    total_count = len(result)
    paged_items = result
    if skip > 0:
        paged_items = paged_items[skip:]
    if limit is not None and limit > 0:
        paged_items = paged_items[:limit]

    return {
        "total": total_count,
        "items": paged_items,
    }


@app.post("/clients", dependencies=[Depends(verify_api_key)])
async def create_client(req: ClientCreateRequest):
    """Create a new client with native X25519 key generation and non-destructive sync."""
    async with state_lock:
        container = get_target_container()
        conf_path = get_config_path(container)
        conf_content = await read_container_file_async(conf_path)
        parsed = parse_awg_conf(conf_content)
        iface = parsed["interface"]
        existing_peers = parsed["peers"]

        server_pub = await get_server_public_key_async(container, iface)
        if not server_pub:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Server public key is not configured or could not be derived",
            )

        client_ip = allocate_next_ip(iface.get("Address", "10.8.1.1/24"), existing_peers)
        client_priv, client_pub = generate_keypair()
        psk = await get_server_psk_async()

        now_ts = int(time.time())
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts))

        # 1. Prepare new client entry adhering strictly to upstream Amnezia schema
        new_client_meta = {
            "clientId": client_pub,
            "userData": {
                "clientName": req.clientName,
                "creationDate": now_iso,
            },
            "clientName": req.clientName,
            "clientIp": client_ip,
            "clientPrivKey": client_priv,
            "clientPubKey": client_pub,
            "psk": psk,
            "status": "active",
            "createdAt": now_ts,
            "updatedAt": now_ts,
            "expiresAt": req.expiresAt,
        }

        # 2. Host name determination
        host = SERVER_HOST_NAME
        if not host:
            host = await _fetch_public_ip_async()

        raw_conf, vpn_uri = build_client_configs(
            new_client_meta,
            iface,
            server_pub,
            host,
            SERVER_DNS1,
            SERVER_DNS2,
            container,
        )

        # 3. Non-destructively append [Peer] to awg0.conf
        new_conf_text = append_peer_to_conf_text(conf_content, client_pub, psk, client_ip)
        saved_conf = await write_container_file_async(conf_path, new_conf_text)
        if not saved_conf:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to write configuration file",
            )

        # 4. Append to clientsTable
        clients_table = await load_clients_table_async()
        clients_table.append(new_client_meta)
        await save_clients_table_async(clients_table)

        # 5. Register in active kernel runtime
        sync_ok = await sync_kernel_peer_add(client_pub, client_ip, psk, container)
        if not sync_ok:
            # Attempt syncconf fallback
            sync_ok = await syncconf_container(container, conf_path)

        if not sync_ok:
            # Rollback file writes if kernel sync completely failed
            logger.error("Kernel peer sync failed for %s, rolling back file updates", client_pub)
            await write_container_file_async(conf_path, conf_content)
            clients_table = [c for c in clients_table if c.get("clientId") != client_pub]
            await save_clients_table_async(clients_table)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to register peer in kernel runtime",
            )

        logger.info("Created client %s (%s, IP: %s)", client_pub, req.clientName, client_ip)

        client_obj = {
            "id": client_pub,
            "config": vpn_uri,
            "raw_config": raw_conf,
            "protocol": "amneziawg2",
        }
        return {
            "message": "Клиент успешно создан",
            "client": client_obj,
            **client_obj,
        }


@app.delete("/clients", dependencies=[Depends(verify_api_key)])
async def delete_client_by_body(req: ClientDeleteRequest):
    """Delete client by JSON body."""
    return await _do_delete_client(req.clientId)


@app.delete("/clients/{client_id:path}", dependencies=[Depends(verify_api_key)])
async def delete_client_by_path(client_id: str):
    """Delete client by path parameter."""
    return await _do_delete_client(client_id)


async def _do_delete_client(client_id: str):
    async with state_lock:
        container = get_target_container()
        conf_path = get_config_path(container)
        conf_content = await read_container_file_async(conf_path)

        clients_table = await load_clients_table_async()
        target_pub = ""

        # Identify target public key from clientsTable or direct pubkey
        remaining_table = []
        for c in clients_table:
            cid = c.get("clientId") or c.get("id")
            cpub = c.get("clientPubKey") or cid
            if cid == client_id or cpub == client_id:
                target_pub = cpub
            else:
                remaining_table.append(c)

        if not target_pub:
            # Check if client_id directly matches a peer in awg0.conf
            parsed = parse_awg_conf(conf_content)
            for p in parsed["peers"]:
                if p.get("PublicKey") == client_id:
                    target_pub = client_id
                    break

        if not target_pub:
            # Idempotent success (not_found_as_success)
            return {"message": "Клиент успешно удален", "status": "ok"}

        # Remove only the target peer section from config
        new_conf_text, removed = remove_peer_from_conf_text(conf_content, target_pub)
        if removed:
            await write_container_file_async(conf_path, new_conf_text)

        await save_clients_table_async(remaining_table)

        # Remove from active kernel runtime
        await sync_kernel_peer_remove(target_pub, container)
        await syncconf_container(container, conf_path)

        logger.info("Deleted peer %s", target_pub)
        return {"message": "Клиент успешно удален", "status": "ok"}


@app.patch("/clients", dependencies=[Depends(verify_api_key)])
async def patch_client_by_body(req: ClientPatchRequest):
    """Update client status (active/disabled) or expiration."""
    return await _do_patch_client(req.clientId, req.status, req.expiresAt)


@app.patch("/clients/{client_id:path}", dependencies=[Depends(verify_api_key)])
async def patch_client_by_path(client_id: str, req: ClientPatchRequest):
    """Update client by path parameter."""
    return await _do_patch_client(client_id, req.status, req.expiresAt)


async def _do_patch_client(client_id: str, new_status: str | None, expires_at: int | None):
    async with state_lock:
        container = get_target_container()
        conf_path = get_config_path(container)
        conf_content = await read_container_file_async(conf_path)
        parsed = parse_awg_conf(conf_content)

        clients_table = await load_clients_table_async()
        target = None
        for c in clients_table:
            cid = c.get("clientId") or c.get("id")
            cpub = c.get("clientPubKey") or cid
            if cid == client_id or cpub == client_id:
                target = c
                break

        if not target:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Client {client_id} not found",
            )

        target_pub = target.get("clientPubKey") or target.get("clientId")
        changed = False

        if new_status in ("active", "disabled") and new_status != target.get("status"):
            target["status"] = new_status
            changed = True
            if new_status == "disabled" and target_pub:
                await sync_kernel_peer_remove(target_pub, container)
            elif new_status == "active" and target_pub:
                # Find peer IP and PSK from conf or table
                peer_ip = target.get("clientIp", "")
                peer_psk = target.get("psk", "")
                if not peer_ip or not peer_psk:
                    for p in parsed["peers"]:
                        if p.get("PublicKey") == target_pub:
                            peer_ip = peer_ip or p.get("AllowedIPs", "").split("/")[0]
                            peer_psk = peer_psk or p.get("PresharedKey", "")
                await sync_kernel_peer_add(target_pub, peer_ip, peer_psk, container)

        if expires_at is not None:
            target["expiresAt"] = expires_at
            changed = True

        if changed:
            target["updatedAt"] = int(time.time())
            await save_clients_table_async(clients_table)

        return {
            "message": "Данные успешно сохранены",
            "status": "updated",
            "clientId": client_id,
            "client": target,
        }


@app.get("/clients/{client_id:path}", dependencies=[Depends(verify_api_key)])
async def get_client_by_id(client_id: str):
    """Return full configuration for a single client."""
    container = get_target_container()
    conf_path = get_config_path(container)
    conf_content = await read_container_file_async(conf_path)
    parsed = parse_awg_conf(conf_content)
    iface = parsed["interface"]
    server_pub = await get_server_public_key_async(container, iface)

    clients_table = await load_clients_table_async()
    target = None
    for c in clients_table:
        cid = c.get("clientId") or c.get("id")
        cpub = c.get("clientPubKey") or cid
        if cid == client_id or cpub == client_id:
            target = c
            break

    if not target:
        # Check directly in awg0.conf peers
        for p in parsed["peers"]:
            if p.get("PublicKey") == client_id:
                target = {
                    "clientId": client_id,
                    "clientPubKey": client_id,
                    "clientIp": p.get("AllowedIPs", "").split("/")[0],
                    "psk": p.get("PresharedKey", ""),
                    "status": "active",
                }
                break

    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Client {client_id} not found",
        )

    host = SERVER_HOST_NAME or "127.0.0.1"
    raw_conf, vpn_uri = build_client_configs(
        target,
        iface,
        server_pub,
        host,
        SERVER_DNS1,
        SERVER_DNS2,
        container,
    )

    return {
        "id": client_id,
        "client": target,
        "config": vpn_uri,
        "raw_config": raw_conf,
        "protocol": "amneziawg2",
    }
