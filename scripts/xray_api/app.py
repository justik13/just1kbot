import logging
import os
import time
import uuid as uuid_lib
from typing import Optional, List, Dict, Any


from fastapi import FastAPI, Header, HTTPException, status, Depends, Response
from pydantic import BaseModel, Field, model_validator

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
INBOUND_TAGS_RAW = os.getenv("XRAY_INBOUND_TAGS", "inbound-de,inbound-nl")
TARGET_INBOUNDS = [tag.strip() for tag in INBOUND_TAGS_RAW.split(",") if tag.strip()]

epoch_manager = EpochManager()
grpc_client = XrayGrpcClient(host=GRPC_HOST, port=GRPC_PORT)

app = FastAPI(
    title="Just1kBot Xray API Agent",
    description="Autonomous server agent for Xray management and traffic stats",
    version="1.0.0",
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
    if not x_api_key or x_api_key != expected_key:
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
            values["client_id"] = str(cid).strip()
            # Validate UUID format
            try:
                uuid_lib.UUID(values["client_id"])
            except ValueError:
                raise ValueError(f"Invalid UUID format: {values['client_id']}") from None


            state = str(values.get("desired_state", "")).strip().lower()
            if state not in ("active", "disabled"):
                raise ValueError("desired_state must be 'active' or 'disabled'")
            values["desired_state"] = state
        return values


# Endpoints
@app.get("/v1/health")
def get_health(response: Response, _: bool = Depends(verify_api_key)) -> Dict[str, Any]:
    """
    Checks service health, Xray core process state, gRPC connectivity, and current epoch.
    Fail-closed: returns HTTP 503 if Xray is not running or gRPC is unhealthy.
    """
    pid, _starttime, current_epoch = epoch_manager.get_process_and_epoch()
    grpc_ok = grpc_client.is_healthy()
    is_running = pid is not None
    is_healthy = is_running and grpc_ok and (current_epoch is not None)

    data = {
        "status": "ok" if is_healthy else "error",
        "xray_running": is_running,
        "grpc_ok": grpc_ok,
        "node_epoch": current_epoch,
        "xray_pid": pid,
    }
    if not is_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return data


@app.get("/v1/traffic/snapshot")
def get_traffic_snapshot(_: bool = Depends(verify_api_key)) -> Dict[str, Any]:
    """
    Returns normalized traffic stats aggregated by UUID/email along with the node epoch.
    Format: { "node_epoch": str, "users": { uuid: { "uplink": int, "downlink": int } } }
    Guarantees generation atomicity: validates epoch_before == epoch_after around QueryStats.
    """
    max_attempts = 3
    for attempt in range(max_attempts):
        _pid1, _st1, epoch_before = epoch_manager.get_process_and_epoch()
        if not epoch_before:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Xray is not currently running",
            )
        try:
            users_stats = grpc_client.get_users_stats(reset=False)
        except Exception as e:
            logger.error("Failed to fetch traffic stats from gRPC: %s", e)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Xray gRPC stats failure: {str(e)}",
            ) from e

        _pid2, _st2, epoch_after = epoch_manager.get_process_and_epoch()
        if epoch_before == epoch_after and epoch_after is not None:
            return {
                "node_epoch": epoch_after,
                "users": users_stats,
            }

        logger.warning(
            "Epoch mismatch during traffic snapshot (attempt %d/%d): before=%s, after=%s",
            attempt + 1,
            max_attempts,
            epoch_before,
            epoch_after,
        )
        time.sleep(0.1)

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Concurrent Xray restart detected during traffic snapshot; generation mismatch",
    )



@app.post("/v1/clients/sync")
def sync_client(
    req: ClientSyncRequest, _: bool = Depends(verify_api_key)
) -> Dict[str, Any]:
    """
    Brings client's status across inbound-de and inbound-nl to the desired state idempotently.
    """
    client_uuid = req.client_id
    desired_state = req.desired_state

    failed_inbounds: List[str] = []
    for tag in TARGET_INBOUNDS:
        try:
            if desired_state == "active":
                grpc_client.add_user(tag, client_uuid)
            else:
                grpc_client.remove_user(tag, client_uuid)
        except Exception as e:
            logger.error("Failed to sync user %s on inbound %s: %s", client_uuid, tag, e)
            failed_inbounds.append(tag)

    if failed_inbounds:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to sync user on inbounds: {failed_inbounds}",
        )

    return {
        "status": "ok",
        "client_id": client_uuid,
        "state": desired_state,
        "inbounds": TARGET_INBOUNDS,
    }


@app.delete("/v1/clients/{uuid}")
def delete_client(uuid: str, _: bool = Depends(verify_api_key)) -> Dict[str, Any]:
    """
    Deletes client from both inbound-de and inbound-nl idempotently.
    """
    # Validate UUID
    try:
        clean_uuid = str(uuid_lib.UUID(uuid.strip()))
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid UUID: {uuid}",
        ) from None


    failed_inbounds: List[str] = []
    for tag in TARGET_INBOUNDS:
        try:
            grpc_client.remove_user(tag, clean_uuid)
        except Exception as e:
            logger.error("Failed to delete user %s from inbound %s: %s", clean_uuid, tag, e)
            failed_inbounds.append(tag)

    if failed_inbounds:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to remove user from inbounds: {failed_inbounds}",
        )

    return {
        "status": "ok",
        "client_id": clean_uuid,
        "action": "deleted",
        "inbounds": TARGET_INBOUNDS,
    }
