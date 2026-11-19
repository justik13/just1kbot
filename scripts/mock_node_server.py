#!/usr/bin/env python3
"""Standalone HTTP Mock Node Server implementing the just1knode REST API contract.

Used strictly in test / local development environments via `docker compose --profile mock up`.
Not used or loaded in production deployments.
"""

import argparse
import logging
import os
import time
from aiohttp import web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] mock_node: %(message)s",
)
logger = logging.getLogger("mock_node")

MANAGED_INBOUNDS = [
    "vless-direct",
    "vless-xhttp",
    "just1k-wl-default",
    "just1k-wl-inbound-ru-wi",
    "just1k-wl-inbound-de",
    "just1k-vless-direct",
    "just1k-vless-wi",
]

NODE_EPOCH = "epoch_mock_1"
BOOT_ID = "mock_boot"
START_TIME = 1700000000

# In-memory client tracking for realistic state
_client_states: dict[str, str] = {}


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({
        "status": "ok",
        "xray_running": True,
        "grpc_ok": True,
        "node_epoch": NODE_EPOCH,
        "version": "v3.0.0",
        "secret_base_path": "/assets_test",
        "boot_id": BOOT_ID,
        "starttime": START_TIME,
    })


async def handle_clients_sync(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        data = {}

    client_id = data.get("client_id", "unknown")
    desired_state = data.get("desired_state", "active")
    version = data.get("version")

    _client_states[client_id] = desired_state
    logger.info("Client %s synced -> %s (version=%s)", client_id, desired_state, version)

    return web.json_response({
        "status": "ok",
        "result": "applied",
        "client_id": client_id,
        "state": desired_state,
        "version": version,
        "verified_epoch": NODE_EPOCH,
        "verified_inbounds": MANAGED_INBOUNDS,
        "all_inbounds_verified": True,
        "inbounds": MANAGED_INBOUNDS,
    })


async def handle_clients_inventory(request: web.Request) -> web.Response:
    client_ids = []
    if request.method == "POST":
        try:
            data = await request.json()
            client_ids = data.get("client_ids") or []
        except Exception:
            pass

    inv = {}
    target_ids = client_ids if client_ids else list(_client_states.keys())
    for cid in target_ids:
        inv[cid] = {"observed_state": _client_states.get(cid, "active")}

    role = os.getenv("MOCK_NODE_ROLE", "ru_hub")
    return web.json_response({
        "inbounds": MANAGED_INBOUNDS,
        "inventory": inv,
        "version": "v3.0.0",
        "role": role,
    })


async def handle_traffic_snapshot(request: web.Request) -> web.Response:
    return web.json_response({
        "node_epoch": NODE_EPOCH,
        "boot_id": BOOT_ID,
        "starttime": START_TIME,
        "users": {},
        "host_tx_bytes": 0,
        "host_rx_bytes": 0,
    })


async def handle_node_status(request: web.Request) -> web.Response:
    role = os.getenv("MOCK_NODE_ROLE", "ru_hub")
    return web.json_response({
        "status": "ok",
        "xray_running": True,
        "role": role,
        "modules": {
            "vless_tls": {"enabled": True},
            "cdn_yandex": {"enabled": True},
        },
        "links": {},
        "xhttp_locations": [{"cdn": "cdn_yandex", "path": "/wi-test-xhttp", "location_code": "de"}],
    })


async def handle_node_links(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/v1/health", handle_health)
    app.router.add_post("/v1/clients/sync", handle_clients_sync)
    app.router.add_get("/v1/clients/inventory", handle_clients_inventory)
    app.router.add_post("/v1/clients/inventory", handle_clients_inventory)
    app.router.add_get("/v1/traffic/snapshot", handle_traffic_snapshot)
    app.router.add_get("/v1/node/status", handle_node_status)
    app.router.add_put("/v1/node/links", handle_node_links)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mock just1knode REST API server")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8444")))
    parser.add_argument("--host", type=str, default=os.getenv("HOST", "0.0.0.0"))
    args = parser.parse_args()

    logger.info("Starting mock just1knode HTTP daemon on %s:%d", args.host, args.port)
    web.run_app(create_app(), host=args.host, port=args.port)
