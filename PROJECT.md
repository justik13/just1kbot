# Project: just1kbot White Internet Remediation & Production Hardening

## Architecture
- **Control Plane**:
  - `bot/handlers/white_internet_web.py`: Web subscription feed generator for XHTTP / VLESS client configs.
  - `services/white_internet_service.py`: Business logic for subscriptions, tariff management, quota allocations, and origin node capacity reservations.
  - `database/repositories/`: Asynchronous database repositories (`servers_repo.py`, `white_internet_repo.py`) enforcing transactional integrity, row-level locks (`SELECT ... FOR UPDATE`), and `extra_data["just1k"]` namespace isolation.
  - `services/workers/`: Resilient background workers (`white_internet_traffic.py`, `white_internet_reconciliation.py`, `node_monitor.py`) with per-client isolation, bounded concurrency, and remote version fencing.
- **Data & Node Plane**:
  - `scripts/xray_api/`: Lightweight FastAPI control daemon running on each Origin node. Bridges bot central control to local Xray gRPC. Enforces double-checked epoch atomicity, versioned delete fencing, and isolated inbound discovery (`just1k-wl-*`).
  - `scripts/just1knode.sh`: Production installer and lifecycle orchestrator for Origin and Relay nodes. Enforces manifest-based transactional rollback, zero-collateral-damage namespacing (`just1k-wl-*`), fail-closed UFW firewalling, and immutable dependency pinning.
  - `Xray-core (v26.7.28)`: High-performance proxy core configured with XHTTP packet-up inbounds, XTLS Vision, and multi-hop VLESS relay outbounds.

---

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---|---|---|---|
| 1 | Double-Checked Epoch Atomicity (F01) | Double-checked epoch loop with exponential retry backoff and HTTP 503 EpochMismatchError in `/v1/traffic/snapshot`. | M1 | Survey / ORIGINAL_REQUEST |
| 2 | DB Authority & Ephemeral Cache (F08) | `clients.json` treated strictly as ephemeral cache hint; node starts in `unsynchronized` status until Central DB reconciliation. | M1 | Survey / ORIGINAL_REQUEST |
| 3 | Versioned DELETE & Lifecycle Contract (F09) | `DELETE /v1/clients/{uuid}` with version parameter, monotonic fencing, and explicit lifecycle responses (`applied`, `already_newer`, `fenced`). | M1 | Survey / ORIGINAL_REQUEST |
| 4 | Dynamic Namespaced Inbound Discovery (F10) | Dynamic discovery of VLESS inbounds strictly filtering by `just1k-wl-*` prefix from `relays.json` and config. | M1 | Survey / ORIGINAL_REQUEST |
| 5 | Managed Secret Base Path Discovery (F11) | Discovery of secret base path matching strictly `just1k-wl-*` (or legacy `inbound-default`) tags. | M1 | Survey / ORIGINAL_REQUEST |
| 6 | Manifest-Based Transactional Rollback (F02, F03) | Full state manifest capture and bit-for-bit restore (content, existence, permissions, ownership) on `add_relay_node` / `remove_relay_node` validation failure with verified health check. | M2 | Survey / ORIGINAL_REQUEST |
| 7 | Zero-Collateral Namespaced Tags (F04, F05) | Isolation of all Just1k inbounds, outbounds, and routing rules under `just1k-wl-*`, preserving third-party rules (`direct`, `block`, `api`). | M2 | Survey / ORIGINAL_REQUEST |
| 8 | Fail-Closed UFW Firewall & Doctor Check (F19, F21) | Require `BOT_IP` before exposing 8444, machine-check UFW ACLs, and openssl certificate expiration / SAN match in doctor. | M2 | Survey / ORIGINAL_REQUEST |
| 9 | Verified Update Rollback & Immutable Pins (F20, F22) | Verified service rollback in `update_xray` and immutable pinning of all package versions and binary checksums. | M2 | Survey / ORIGINAL_REQUEST |
| 10 | Poison Record Immunity (F06) | Data type guards and per-client isolation in traffic worker to prevent a malformed client UUID from aborting the server batch. | M3 | Survey / ORIGINAL_REQUEST |
| 11 | Traffic Snapshot Idempotency (F07) | Idempotent event insertion (`ON CONFLICT DO NOTHING`) on `uq_white_internet_traffic_event_snapshot` to eliminate retry loops. | M3 | Survey / ORIGINAL_REQUEST |
| 12 | Bounded Concurrency & Per-Sub Lock (F15) | Bounded concurrency (`asyncio.Semaphore(10)`) with per-subscription lock serialization and version fencing in reconciliation worker. | M3 | Survey / ORIGINAL_REQUEST |
| 13 | Consistent Server Health Invariants (F16) | Standardized `ServerHealthState` handling across background workers (`ONLINE` + `WAITING_CONFIRMATION`) and public feeds (`ONLINE` only). | M3 | Survey / ORIGINAL_REQUEST |
| 14 | Disabled Sub Quota Non-Consumption (F17) | Disabled and expired subscriptions record traffic strictly as overage without depleting active quota grants. | M3 | Survey / ORIGINAL_REQUEST |
| 15 | Pruning & Namespace Isolation in `extra_data` (F12, F13) | Isolate node snapshot in `extra_data["just1k"]` and merge authoritatively under `SELECT ... FOR UPDATE` row lock while preserving foreign keys. | M4 | Survey / ORIGINAL_REQUEST |
| 16 | Transitional Capacity Accounting (F14) | Count all capacity-consuming statuses (`PENDING`, `ACTIVE`, `EXHAUSTED`, `PENDING_UPDATE`) in peer count queries to prevent oversubscription. | M4 | Survey / ORIGINAL_REQUEST |
| 17 | Reversible Migration Downgrade (F18) | Complete reversible downgrade semantics and cycle verification `upgrade head -> downgrade base -> upgrade head` on PostgreSQL 16. | M4 | Survey / ORIGINAL_REQUEST |
| 18 | Web Feed Environment Fallback (F23) | Sequential fallback cascade in `white_internet_web.py` to correctly evaluate environment variables when `extra_data` is empty. | M4 | Survey / ORIGINAL_REQUEST |
| 19 | 24-Finding Automated Regression Matrix (R5) | Full suite of 24 automated regression tests covering F01 through F24. | M5 | Survey / ORIGINAL_REQUEST |
| 20 | Failure Injection Testing (R5) | Automated verification against injected syntax errors, mid-query epoch drift, and duplicate event collisions. | M5 | Survey / ORIGINAL_REQUEST |
| 21 | Full Data-Plane E2E Harness (F24) | Live multi-hop data-plane test harness (`test_full_xhttp_dataplane_e2e`) verifying Client → CDN → Origin → Relay → Target. | M5 | Survey / ORIGINAL_REQUEST |

---

## Milestones

| # | Name | Scope | Dependencies | Status | Assigned Worker / Sub-Orch |
|---|---|---|---|---|---|
| **M1** | Node Agent Invariants, Epoch Fencing & Versioning | `scripts/xray_api/` (`app.py`, `epoch_manager.py`, `client_store.py`), `services/xray_node_client.py` (F01, F08, F09, F10, F11) | none | PLANNED | `sub_orch_m1_node_agent` |
| **M2** | Node Provisioning, Namespacing & Transactional Rollback | `scripts/just1knode.sh`, `scripts/xray_api/requirements.txt` (F02, F03, F04, F05, F19, F20, F21, F22) | none | PLANNED | `sub_orch_m2_provisioning` |
| **M3** | Resilient Background Workers & Traffic Accounting | `services/workers/white_internet_traffic.py`, `services/workers/white_internet_reconciliation.py`, `services/white_internet_service.py` (F06, F07, F15, F16, F17) | none | PLANNED | `sub_orch_m3_workers` |
| **M4** | Database Consistency, Capacity Allocation & Web Handlers | `database/repositories/servers_repo.py`, `database/repositories/white_internet_repo.py`, `services/workers/node_monitor.py`, `bot/handlers/white_internet_web.py`, `alembic/versions/0016_white_internet_subscriptions.py` (F12, F13, F14, F18, F23) | M3 | PLANNED | `sub_orch_m4_database` |
| **M5** | Full 24-Matrix Regression Suite, Data-Plane E2E & Hardening | Full regression suite (F01-F24), `tests/test_full_xhttp_dataplane_e2e.py`, `tests/test_alembic_downgrade_cycle.py`, Failure Injection, `ruff`, `shellcheck`, Docker Ubuntu 24.04 runtime tests | M1, M2, M3, M4 | PLANNED | `sub_orch_m5_e2e_final` |

---

## Interface Contracts

### 1. Bot Control Plane ↔ Node Agent API (`/v1/clients`)
- **`POST /v1/clients`**:
  - Request: `{"client_id": "uuid", "is_active": bool, "version": int, "email": Optional[str]}`
  - Response: `{"status": "ok", "client_id": "uuid", "result": "applied" | "already_newer", "version": int, "inbounds": list[str]}`
- **`DELETE /v1/clients/{uuid}?version={version}`**:
  - Query Param: `version: Optional[int]`
  - Response: `{"status": "ok", "client_id": "uuid", "result": "applied" | "already_newer", "fenced": bool, "version": int, "inbounds": list[str]}`
- **`GET /v1/traffic/snapshot`**:
  - Response: `{"node_epoch": str, "boot_id": str, "starttime": int, "timestamp": int, "users": dict[str, dict[str, int]]}`
  - Error: HTTP 503 `{"detail": "EpochMismatchError: Xray instance changed during stats read"}` on unresolvable epoch drift.

### 2. Central DB ↔ Node Monitor (`extra_data["just1k"]`)
- `Server.extra_data`:
  ```json
  {
    "just1k": {
      "secret_base_path": "/secret_prefix",
      "relays": [
        {"code": "de", "name": "Frankfurt", "ip": "1.2.3.4", "port": 18001}
      ]
    },
    "custom_user_key": "preserved_value"
  }
  ```
- Merged under `SELECT ... FOR UPDATE` row lock in `update_server_health_snapshot`.

---

## Code Layout & Write Ownership
| Subagent / Milestone | Exclusive Writable Files |
|---|---|
| **M1: Node Agent** | `scripts/xray_api/app.py`, `scripts/xray_api/epoch_manager.py`, `scripts/xray_api/client_store.py`, `services/xray_node_client.py` |
| **M2: Node Provisioning** | `scripts/just1knode.sh`, `scripts/xray_api/requirements.txt` |
| **M3: Workers & Traffic** | `services/workers/white_internet_traffic.py`, `services/workers/white_internet_reconciliation.py`, `services/white_internet_service.py` |
| **M4: Database & Handlers** | `database/repositories/servers_repo.py`, `database/repositories/white_internet_repo.py`, `services/workers/node_monitor.py`, `bot/handlers/white_internet_web.py`, `alembic/versions/0016_white_internet_subscriptions.py` |
| **M5 & E2E Testing Track** | `tests/test_*.py`, `scripts/xray_api/tests/test_*.py`, `tests/test_full_xhttp_dataplane_e2e.py`, `tests/test_alembic_downgrade_cycle.py`, `tests/test_just1knode_sh.py` |
