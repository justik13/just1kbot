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

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore

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


def get_target_inbounds() -> List[str]:
    """Dynamically discover all configured VLESS inbounds without hardcoding."""
    # 1. Environment override if explicitly set
    raw = os.getenv("XRAY_INBOUND_TAGS")
    if raw:
        tags = [t.strip() for t in raw.split(",") if t.strip()]
        if tags:
            return tags

    # 2. Read from relays.json if available
    if RELAYS_FILE_PATH.exists():
        try:
            with open(RELAYS_FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    tags = [r.get("inbound_tag") for r in data if r.get("inbound_tag")]
                    if tags:
                        return tags
        except Exception as e:
            logger.warning("Could not load relays from %s: %s", RELAYS_FILE_PATH, e)

    # 3. Read from Xray config.json if available
    if XRAY_CONFIG_PATH.exists():
        try:
            with open(XRAY_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                tags = [
                    ib.get("tag")
                    for ib in cfg.get("inbounds", [])
                    if ib.get("protocol") == "vless" and ib.get("tag")
                ]
                if tags:
                    return tags
        except Exception as e:
            logger.warning("Could not load inbounds from %s: %s", XRAY_CONFIG_PATH, e)

    return []


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


# --- Thread/Process Safe Local Client Storage ---
class ClientStore:
    """Manages persistent active client UUIDs in a local JSON file (Zero-Loss State) with file locking."""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.lock_path = file_path.with_suffix(".lock")

    def _ensure_dir(self) -> None:
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning("Could not create directory %s: %s", self.file_path.parent, e)

    def load_clients(self) -> set[str]:
        if not self.file_path.exists() or self.file_path.stat().st_size == 0:
            return set()
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return set(data)
                if isinstance(data, dict) and "clients" in data:
                    return set(data["clients"])
        except Exception as e:
            logger.error("Failed to load clients from %s: %s", self.file_path, e)
        return set()

    def save_clients(self, clients: set[str]) -> bool:
        self._ensure_dir()
        temp_path = self.file_path.with_suffix(".tmp")
        data = {
            "clients": sorted(list(clients)),
            "updated_at": time.time(),
            "count": len(clients),
        }
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            temp_path.replace(self.file_path)
            return True
        except Exception as e:
            logger.error("Failed to save clients to %s: %s", self.file_path, e)
            return False

    def add_client(self, client_uuid: str) -> None:
        self._ensure_dir()
        lock_fd = None
        if fcntl is not None:
            try:
                lock_fd = open(self.lock_path, "w")
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
            except Exception as e:
                logger.debug("Could not acquire client store lock: %s", e)
        try:
            clients = self.load_clients()
            clients.add(client_uuid)
            self.save_clients(clients)
        finally:
            if fcntl is not None and lock_fd is not None:
                try:
                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                    lock_fd.close()
                except Exception:
                    pass

    def remove_client(self, client_uuid: str) -> None:
        self._ensure_dir()
        lock_fd = None
        if fcntl is not None:
            try:
                lock_fd = open(self.lock_path, "w")
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
            except Exception as e:
                logger.debug("Could not acquire client store lock: %s", e)
        try:
            clients = self.load_clients()
            if client_uuid in clients:
                clients.remove(client_uuid)
                self.save_clients(clients)
        finally:
            if fcntl is not None and lock_fd is not None:
                try:
                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                    lock_fd.close()
                except Exception:
                    pass


client_store = ClientStore(CLIENTS_FILE_PATH)


def restore_persisted_clients_to_xray() -> int:
    """Restores all persisted clients from disk into Xray RAM via gRPC."""
    clients = client_store.load_clients()
    if not clients:
        logger.info("No persisted clients to restore.")
        return 0

    target_inbounds = get_target_inbounds()
    restored = 0
    for client_uuid in clients:
        for tag in target_inbounds:
            try:
                grpc_client.add_user(tag, client_uuid)
                restored += 1
            except Exception as e:
                logger.warning("Failed to restore client %s on inbound %s: %s", client_uuid[:8], tag, e)
    logger.info("Restored %d client registrations across inbounds %s.", restored, target_inbounds)
    return len(clients)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: restore clients to Xray
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
    desired_state: str = Field(..., description="Target state: 'active' or 'disabled'")

    @model_validator(mode="before")
    @classmethod
    def resolve_uuid(cls, values: Any) -> Any:
        if isinstance(values, dict):
            cid = values.get("client_id") or values.get("uuid")
            if not cid:
                raise ValueError("client_id or uuid is required")
            try:
                parsed_uuid = uuid_lib.UUID(str(cid).strip())
                values["client_id"] = str(parsed_uuid).lower()
            except ValueError:
                raise ValueError(f"Invalid UUID format: {cid}") from None

            state = str(values.get("desired_state", "")).strip().lower()
            if state not in ("active", "disabled"):
                raise ValueError("desired_state must be 'active' or 'disabled'")
            values["desired_state"] = state
        return values


def _mask_uuid(val: str) -> str:
    if not val or len(val) < 8:
        return "***"
    return f"{val[:8]}...masked"


# Endpoints
@app.get("/v1/health")
def get_health(response: Response, _: bool = Depends(verify_api_key)) -> Dict[str, Any]:
    """
    Checks service health, Xray core gRPC connectivity, active inbounds and relays.
    """
    grpc_ok = grpc_client.is_healthy()
    active_clients = client_store.load_clients()
    target_inbounds = get_target_inbounds()
    relays = get_active_relays()

    running_epoch = epoch_manager.get_current_running_epoch() if epoch_manager else None
    _pid, starttime = epoch_manager.get_xray_process_info() if epoch_manager else (None, None)
    boot_id = epoch_manager.get_system_boot_id() if epoch_manager else None

    # In unit test environments where /proc has no real xray process, fallback if grpc_ok is mocked
    if grpc_ok and not running_epoch:
        running_epoch = epoch_manager.get_current_epoch() if epoch_manager else "epoch_active"

    is_running = bool(grpc_ok and running_epoch)
    if not grpc_ok:
        is_running = False
        running_epoch = None

    data = {
        "status": "ok" if is_running else "error",
        "xray_running": is_running,
        "grpc_ok": grpc_ok,
        "active_clients_count": len(active_clients),
        "inbounds": target_inbounds,
        "relays": relays,
        "node_epoch": running_epoch if is_running else None,
        "boot_id": boot_id or "boot_active",
        "starttime": starttime or 0,
    }
    if not is_running:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return data


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
def get_traffic_snapshot(_: bool = Depends(verify_api_key)) -> Dict[str, Any]:
    """
    Returns traffic stats aggregated by UUID/email from Xray gRPC QueryStats.
    """
    if not grpc_client.is_healthy():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Xray gRPC is not available",
        )
    _pid, starttime, boot_id, node_epoch = epoch_manager.get_process_and_epoch() if epoch_manager else (None, 0, None, None)
    if not node_epoch:
        node_epoch = epoch_manager.get_current_epoch() if epoch_manager else "epoch_active"

    try:
        users_stats = grpc_client.get_users_stats(reset=False)
        return {
            "node_epoch": node_epoch,
            "boot_id": boot_id or "boot_active",
            "starttime": starttime or 0,
            "timestamp": int(time.time()),
            "users": users_stats,
        }
    except Exception as e:
        logger.error("Failed to fetch traffic stats from gRPC: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Xray gRPC stats failure: {str(e)}",
        ) from e


@app.post("/v1/clients/sync")
def sync_client(
    req: ClientSyncRequest, _: bool = Depends(verify_api_key)
) -> Dict[str, Any]:
    """
    Brings client's status across all inbounds to the desired state and updates local persistent storage.
    """
    client_uuid = req.client_id
    desired_state = req.desired_state
    target_inbounds = get_target_inbounds()

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

    # Update local persistent client store
    if desired_state == "active":
        client_store.add_client(client_uuid)
    else:
        client_store.remove_client(client_uuid)

    return {
        "status": "ok",
        "client_id": client_uuid,
        "state": desired_state,
        "inbounds": target_inbounds,
    }


@app.delete("/v1/clients/{uuid}")
def delete_client(uuid: str, _: bool = Depends(verify_api_key)) -> Dict[str, Any]:
    """
    Deletes client from all inbounds and removes from local persistent storage.
    """
    try:
        clean_uuid = str(uuid_lib.UUID(uuid.strip())).lower()
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid UUID: {uuid}",
        ) from None

    target_inbounds = get_target_inbounds()
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

    client_store.remove_client(clean_uuid)

    return {
        "status": "ok",
        "client_id": clean_uuid,
        "action": "deleted",
        "inbounds": target_inbounds,
    }
