import asyncio
import json
import logging
import os
import secrets
import time
import uuid as uuid_lib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel, Field, model_validator

from client_store import ClientStore
from epoch_manager import EpochManager
from xray_grpc import XrayGrpcClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("xray_api")

# Configuration from environment / config file
CONFIG_ENV_FILE = "/etc/xray-api/config.env"
if os.path.exists(CONFIG_ENV_FILE):
    try:
        with open(CONFIG_ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if line_str and not line_str.startswith("#") and "=" in line_str:
                    k, v = line_str.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k not in os.environ:
                        os.environ[k] = v
    except Exception as e:
        logger.warning("Failed to load %s: %s", CONFIG_ENV_FILE, e)

API_KEY = os.getenv("XRAY_API_KEY", "")
GRPC_HOST = os.getenv("XRAY_GRPC_HOST", "127.0.0.1")
GRPC_PORT = int(os.getenv("XRAY_GRPC_PORT", "10085"))
RELAYS_FILE_PATH = Path(os.getenv("RELAYS_FILE_PATH", "/etc/just1knode/relays.json"))
XRAY_CONFIG_PATH = Path(os.getenv("XRAY_CONFIG_PATH", "/usr/local/etc/xray/config.json"))
CLIENTS_FILE_PATH = Path(os.getenv("CLIENTS_FILE_PATH", "/etc/just1knode/clients.json"))
STATE_FILE_PATH = Path(os.getenv("STATE_FILE_PATH", "/etc/just1knode/state.json"))


def get_secret_base_path() -> str:
    """Discovers canonical secret XHTTP base path configured for this Origin node.

    Strictly matches managed tags ('just1k-wl-*' or legacy 'inbound-default').
    """
    if STATE_FILE_PATH.exists():
        try:
            with open(STATE_FILE_PATH, "r", encoding="utf-8") as f:
                st = json.load(f)
                if isinstance(st, dict) and st.get("secret_base_path"):
                    return st["secret_base_path"]
        except Exception as e:
            logger.warning("Could not read secret_base_path from %s: %s", STATE_FILE_PATH, e)

    # Fallback: check config.json inbounds path prioritizing managed namespaced tags
    if XRAY_CONFIG_PATH.exists():
        try:
            with open(XRAY_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                inbounds = cfg.get("inbounds", [])
                # Priority 1: tags matching just1k-wl-default or legacy inbound-default
                for ib in inbounds:
                    tag = ib.get("tag", "")
                    if tag in ("just1k-wl-default", "inbound-default"):
                        path = (
                            ib.get("streamSettings", {})
                            .get("xhttpSettings", {})
                            .get("path", "")
                        )
                        if not path:
                            path = (
                                ib.get("streamSettings", {})
                                .get("httpSettings", {})
                                .get("path", "")
                            )
                        if path and path.startswith("/"):
                            parts = [p for p in path.strip("/").split("/") if p]
                            if parts:
                                return f"/{parts[0]}"
                # Priority 2: tags starting strictly with just1k-wl- (managed namespace)
                for ib in inbounds:
                    tag = ib.get("tag", "")
                    if tag.startswith("just1k-wl-"):
                        path = (
                            ib.get("streamSettings", {})
                            .get("xhttpSettings", {})
                            .get("path", "")
                        )
                        if not path:
                            path = (
                                ib.get("streamSettings", {})
                                .get("httpSettings", {})
                                .get("path", "")
                            )
                        if path and path.startswith("/"):
                            parts = [p for p in path.strip("/").split("/") if p]
                            if parts:
                                return f"/{parts[0]}"
        except Exception:
            pass

    return os.getenv("WHITE_INTERNET_PATH", "/stream/v1")


def get_target_inbounds() -> List[str]:
    """Dynamically discover all configured Just1k VLESS inbounds strictly filtering by managed namespaces."""
    # 1. Environment override if explicitly set
    raw = os.getenv("XRAY_INBOUND_TAGS")
    if raw:
        tags = [t.strip() for t in raw.split(",") if t.strip()]
        if tags:
            return tags

    discovered_tags: List[str] = []

    # 2. Read from relays.json if available
    relay_tags: List[str] = []
    if RELAYS_FILE_PATH.exists():
        try:
            with open(RELAYS_FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    for r in data:
                        t = r.get("inbound_tag")
                        if t and t.startswith("just1k-wl-"):
                            relay_tags.append(t)
        except Exception as e:
            logger.warning("Could not load relays from %s: %s", RELAYS_FILE_PATH, e)

    # 3. Read from Xray config.json if available
    config_tags: List[str] = []
    if XRAY_CONFIG_PATH.exists():
        try:
            with open(XRAY_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                for ib in cfg.get("inbounds", []):
                    protocol = ib.get("protocol", "").lower()
                    tag = ib.get("tag", "")
                    # Match managed VLESS/VMESS inbounds strictly by just1k-wl- namespace
                    if protocol in ("vless", "vmess") and (
                        tag.startswith("just1k-wl-")
                        or tag in ("just1k-wl-default", "inbound-default")
                    ):
                        config_tags.append(tag)
        except Exception as e:
            logger.warning("Could not load inbounds from %s: %s", XRAY_CONFIG_PATH, e)

    # Prioritize default tag if present
    all_candidate_tags = config_tags + relay_tags
    for tag in all_candidate_tags:
        if tag in ("just1k-wl-default", "inbound-default") and tag not in discovered_tags:
            discovered_tags.insert(0, tag)
        elif tag not in discovered_tags:
            discovered_tags.append(tag)

    if not discovered_tags and relay_tags:
        discovered_tags = relay_tags

    return discovered_tags


def get_active_relays() -> List[Dict[str, Any]]:
    """Return active relay configurations from relays.json."""
    if RELAYS_FILE_PATH.exists():
        try:
            with open(RELAYS_FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as e:
            logger.warning("Failed to load relays from %s: %s", RELAYS_FILE_PATH, e)
    return []


grpc_client = XrayGrpcClient(host=GRPC_HOST, port=GRPC_PORT)
epoch_manager = EpochManager()
client_store = ClientStore(CLIENTS_FILE_PATH)

# Node synchronization state (Central DB is authoritative SSOT; local cache is ephemeral hint)
node_sync_state: Dict[str, Any] = {
    "status": "unsynchronized",
    "last_synced_at": None,
}


def restore_persisted_clients_to_xray() -> int:
    """Restores active persisted clients from disk into Xray RAM as temporary crash-recovery hint.

    The node remains in 'unsynchronized' state until Central DB reconciliation runs.
    """
    active_clients = client_store.load_clients()
    if not active_clients:
        logger.info("No active persisted clients to restore.")
        return 0

    target_inbounds = get_target_inbounds()
    restored = 0
    for client_uuid in active_clients:
        for tag in target_inbounds:
            try:
                grpc_client.add_user(tag, client_uuid)
                restored += 1
            except Exception as e:
                logger.warning("Failed to restore client %s on inbound %s: %s", client_uuid[:8], tag, e)
    logger.info("Restored %d active client registrations across inbounds %s as ephemeral hints.", restored, target_inbounds)
    return len(active_clients)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: restore clients to Xray strictly as ephemeral hints
    node_sync_state["status"] = "unsynchronized"
    node_sync_state["last_synced_at"] = None
    target_inbounds = get_target_inbounds()
    logger.info("Starting Just1kBot Xray API Agent on inbounds: %s", target_inbounds)
    try:
        if grpc_client.is_healthy():
            restore_persisted_clients_to_xray()
        else:
            logger.warning("Xray gRPC is not immediately available at startup. Clients will sync on demand.")
    except Exception as e:
        logger.error("Startup client restoration error: %s", e)
    yield
    # Shutdown
    grpc_client.close()


app = FastAPI(
    title="Just1kBot Xray API Agent",
    description="Autonomous server agent for Xray management and traffic stats",
    version="2.0.0",
    lifespan=lifespan,
)


# Security dependency
def verify_api_key(x_api_key: Optional[str] = Header(None, alias="X-API-Key")):
    expected_key = os.getenv("XRAY_API_KEY") or API_KEY
    if not expected_key:
        logger.error("XRAY_API_KEY is not configured on the node")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Node API key is not configured",
        )
    if not x_api_key or not secrets.compare_digest(x_api_key, expected_key):
        logger.warning("Unauthorized access attempt with X-API-Key: %s", "present" if x_api_key else "missing")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key",
        )
    return True


# Models
class ClientSyncRequest(BaseModel):
    client_id: Optional[str] = Field(None, description="Client UUID")
    uuid: Optional[str] = Field(None, description="Client UUID alias")
    desired_state: Optional[str] = Field(None, description="Target state: 'active' or 'disabled'")
    is_active: Optional[bool] = Field(None, description="Target state boolean alias")
    version: Optional[int] = Field(None, description="Monotonic desired version")
    email: Optional[str] = Field(None, description="Optional client email/identifier")

    @model_validator(mode="before")
    @classmethod
    def resolve_fields(cls, values: Any) -> Any:
        if isinstance(values, dict):
            cid = values.get("client_id") or values.get("uuid")
            if not cid:
                raise ValueError("client_id or uuid is required")
            try:
                parsed_uuid = uuid_lib.UUID(str(cid).strip())
                values["client_id"] = str(parsed_uuid).lower()
            except ValueError:
                raise ValueError(f"Invalid UUID format: {cid}") from None

            state = values.get("desired_state")
            is_act = values.get("is_active")
            if state is not None:
                s = str(state).strip().lower()
                if s not in ("active", "disabled"):
                    raise ValueError("desired_state must be 'active' or 'disabled'")
                values["desired_state"] = s
            elif is_act is not None:
                values["desired_state"] = "active" if bool(is_act) else "disabled"
            else:
                values["desired_state"] = "active"
        return values


def _mask_uuid(val: str) -> str:
    if not val or len(val) < 8:
        return "***"
    return f"{val[:8]}...masked"


# Endpoints
@app.get("/v1/health")
def get_health(response: Response, _: bool = Depends(verify_api_key)) -> Dict[str, Any]:
    """
    Checks service health, Xray core gRPC connectivity, active inbounds, relays, and synchronization status.
    Fail-closed: returns HTTP 503 if Xray is not running, gRPC is down, or /proc process inspection fails.
    """
    grpc_ok = grpc_client.is_healthy()
    active_clients = client_store.load_clients()
    target_inbounds = get_target_inbounds()
    relays = get_active_relays()
    secret_path = get_secret_base_path()

    pid, starttime, boot_id, running_epoch = (
        epoch_manager.get_process_and_epoch() if epoch_manager else (None, None, None, None)
    )

    is_running = bool(
        grpc_ok
        and running_epoch is not None
        and pid is not None
        and starttime is not None
        and boot_id is not None
    )

    if not is_running:
        running_epoch = None
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ok" if is_running else "error",
        "xray_running": is_running,
        "grpc_ok": bool(grpc_ok),
        "active_clients_count": len(active_clients),
        "inbounds": target_inbounds,
        "relays": relays,
        "secret_base_path": secret_path,
        "node_epoch": running_epoch if is_running else None,
        "boot_id": boot_id if is_running else None,
        "starttime": starttime if is_running else None,
        "sync_status": node_sync_state.get("status", "unsynchronized"),
        "synchronized": node_sync_state.get("status") == "synchronized",
        "last_synced_at": node_sync_state.get("last_synced_at"),
    }


@app.get("/v1/relays")
def list_relays(_: bool = Depends(verify_api_key)) -> Dict[str, Any]:
    """Returns list of active relays configured on the node."""
    relays = get_active_relays()
    return {
        "status": "ok",
        "count": len(relays),
        "relays": relays,
    }


@app.get("/v1/clients/list")
def list_clients(_: bool = Depends(verify_api_key)) -> Dict[str, Any]:
    """Returns list of currently active clients persisted on the node."""
    clients = list(client_store.load_clients())
    return {
        "status": "ok",
        "count": len(clients),
        "clients": clients,
    }


@app.get("/v1/traffic/snapshot")
async def get_traffic_snapshot(_: bool = Depends(verify_api_key)) -> Dict[str, Any]:
    """
    Returns traffic stats aggregated by UUID/email with double-checked epoch atomicity.
    Fail-closed: requires genuine running process, valid starttime, boot_id, and matching epoch.
    """
    if not grpc_client.is_healthy():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Xray gRPC is not available",
        )

    max_attempts = 3
    for attempt in range(max_attempts):
        pid1, starttime1, boot_id1, epoch1 = (
            epoch_manager.get_process_and_epoch() if epoch_manager else (None, None, None, None)
        )
        if (
            pid1 is None
            or starttime1 is None
            or boot_id1 is None
            or epoch1 is None
        ):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Xray process or epoch is unavailable",
            )

        try:
            users_stats = grpc_client.get_users_stats(reset=False)
        except Exception as e:
            logger.error("Failed to fetch traffic stats from gRPC: %s", e)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Xray gRPC stats failure: {str(e)}",
            ) from e

        pid2, starttime2, boot_id2, epoch2 = (
            epoch_manager.get_process_and_epoch() if epoch_manager else (None, None, None, None)
        )
        if (
            pid2 is None
            or starttime2 is None
            or boot_id2 is None
            or epoch2 is None
        ):
            logger.warning(
                "Xray stopped during traffic snapshot read (attempt %d/%d). Retrying...",
                attempt + 1,
                max_attempts,
            )
            await asyncio.sleep(0.05 * (2**attempt))
            continue

        if (
            epoch1 == epoch2
            and pid1 == pid2
            and starttime1 == starttime2
            and boot_id1 == boot_id2
        ):
            return {
                "node_epoch": epoch1,
                "boot_id": boot_id1,
                "starttime": starttime1,
                "timestamp": int(time.time()),
                "users": users_stats,
            }

        logger.warning(
            "Epoch drift detected during snapshot read (attempt %d/%d): %s -> %s. Retrying...",
            attempt + 1,
            max_attempts,
            epoch1,
            epoch2,
        )
        await asyncio.sleep(0.05 * (2**attempt))

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="EpochMismatchError: Xray instance changed during stats read",
    )


@app.post("/v1/clients/sync")
@app.post("/v1/clients")
async def sync_client(
    req: ClientSyncRequest, _: bool = Depends(verify_api_key)
) -> Dict[str, Any]:
    """
    Brings client's status across all inbounds to the desired state with monotonic version fencing.
    """
    client_uuid = req.client_id
    if not client_uuid:
        raise HTTPException(status_code=422, detail="client_id is required")

    desired_state = req.desired_state or "active"
    target_inbounds = get_target_inbounds()

    # Monotonic version fencing check (including tombstones)
    if req.version is not None:
        entries = client_store.load_client_entries()
        curr_entry = entries.get(client_uuid)
        if curr_entry:
            curr_ver = curr_entry.get("version")
            is_tombstone = curr_entry.get("tombstone", False)
            if curr_ver is not None and (req.version < curr_ver or (is_tombstone and req.version <= curr_ver)):
                logger.warning(
                    "Stale sync request for %s: incoming version %d <= stored version %d (tombstone=%s). Fencing.",
                    _mask_uuid(client_uuid),
                    req.version,
                    curr_ver,
                    is_tombstone,
                )
                return {
                    "status": "ok",
                    "client_id": client_uuid,
                    "result": "already_newer",
                    "state": "disabled" if is_tombstone else ("active" if curr_entry.get("is_active") else "disabled"),
                    "version": curr_ver,
                    "fenced": True,
                    "tombstone": is_tombstone,
                    "inbounds": target_inbounds,
                }

    succeeded_inbounds: List[str] = []
    failed_inbounds: List[str] = []
    for tag in target_inbounds:
        try:
            if desired_state == "active":
                grpc_client.add_user(tag, client_uuid)
            else:
                grpc_client.remove_user(tag, client_uuid)
            succeeded_inbounds.append(tag)
        except Exception as e:
            logger.error("Failed to sync user %s on inbound %s: %s", _mask_uuid(client_uuid), tag, e)
            failed_inbounds.append(tag)

    if failed_inbounds:
        # Atomic rollback across inbounds
        for rb_tag in succeeded_inbounds:
            try:
                if desired_state == "active":
                    grpc_client.remove_user(rb_tag, client_uuid)
                else:
                    grpc_client.add_user(rb_tag, client_uuid)
            except Exception as rb_exc:
                logger.error("Rollback failed for user %s on inbound %s: %s", _mask_uuid(client_uuid), rb_tag, rb_exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to sync user on inbounds: {failed_inbounds}",
        )

    # Update local persistent client store with monotonic version
    try:
        if desired_state == "active":
            client_store.add_client(client_uuid, version=req.version, email=req.email)
        else:
            client_store.remove_client(client_uuid, version=req.version)
    except Exception as e:
        logger.error("Failed to persist client state to disk: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to persist client state: {str(e)}",
        ) from e

    # Update synchronization state
    node_sync_state["status"] = "synchronized"
    node_sync_state["last_synced_at"] = time.time()

    return {
        "status": "ok",
        "client_id": client_uuid,
        "result": "applied",
        "state": desired_state,
        "version": req.version,
        "fenced": False,
        "inbounds": target_inbounds,
    }


@app.delete("/v1/clients/{uuid}")
async def delete_client(
    uuid: str,
    version: Optional[int] = None,
    _: bool = Depends(verify_api_key),
) -> Dict[str, Any]:
    """
    Deletes client from all inbounds and records a tombstone in local persistent storage with version fencing.
    """
    try:
        clean_uuid = str(uuid_lib.UUID(uuid.strip())).lower()
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid UUID: {uuid}",
        ) from None

    target_inbounds = get_target_inbounds()

    # Monotonic version check
    if version is not None:
        entries = client_store.load_client_entries()
        curr_entry = entries.get(clean_uuid)
        if curr_entry:
            curr_ver = curr_entry.get("version")
            if curr_ver is not None and version < curr_ver:
                logger.warning(
                    "Stale delete request for %s: version %d < stored %d. Fencing.",
                    _mask_uuid(clean_uuid),
                    version,
                    curr_ver,
                )
                return {
                    "status": "ok",
                    "client_id": clean_uuid,
                    "result": "already_newer",
                    "fenced": True,
                    "version": curr_ver,
                    "inbounds": target_inbounds,
                }

    failed_inbounds: List[str] = []
    for tag in target_inbounds:
        try:
            grpc_client.remove_user(tag, clean_uuid)
        except Exception as e:
            logger.error("Failed to delete user %s from inbound %s: %s", _mask_uuid(clean_uuid), tag, e)
            failed_inbounds.append(tag)

    if failed_inbounds:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to remove user from inbounds: {failed_inbounds}",
        )

    try:
        client_store.delete_client(clean_uuid, version=version)
    except Exception as e:
        logger.warning("Could not delete client from store: %s", e)

    return {
        "status": "ok",
        "client_id": clean_uuid,
        "result": "applied",
        "action": "deleted",
        "fenced": False,
        "version": version,
        "inbounds": target_inbounds,
    }
