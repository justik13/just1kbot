# E2E Test Infra: just1kbot White Internet Production Hardening

## Test Philosophy
- Requirement-driven, opaque-box and grey-box verification across all 24 findings.
- Zero semantic weakening (no `@pytest.mark.skip`, no `if TESTING:`, no swallowed exceptions).
- Strict verification in Ubuntu 24.04 Docker environment with PostgreSQL 16 and pinned Xray v26.7.28 binary.

---

## 24-Finding Regression Matrix (F01–F24)

| Finding | Component | Requirement / Invariant | Automated Test | Target File | Status |
|---|---|---|---|---|---|
| **F01** | `scripts/xray_api/app.py` | Double-checked epoch loop with backoff retry and 503 fail-closed | `test_traffic_snapshot_epoch_drift_retry` | `scripts/xray_api/tests/test_api.py` | PLANNED |
| **F02** | `scripts/just1knode.sh` | Manifest-based rollback on `add_relay_node` failure | `test_relay_add_rollback_on_nginx_failure` | `tests/test_just1knode_sh.py` | PLANNED |
| **F03** | `scripts/just1knode.sh` | Full file restoration & fail-closed in `remove_relay_node` | `test_relay_remove_rollback_on_xray_failure` | `tests/test_just1knode_sh.py` | PLANNED |
| **F04** | `scripts/just1knode.sh` | Preservation of custom `direct` and `block` outbounds | `test_preserve_custom_xray_outbounds` | `tests/test_just1knode_sh.py` | PLANNED |
| **F05** | `scripts/just1knode.sh` | Namespaced `just1k-wl-inbound-*` tags | `test_preserve_custom_inbound_tags` | `tests/test_just1knode_sh.py` | PLANNED |
| **F06** | `workers/traffic.py` | Poison client UUID / malformed stats isolation | `test_traffic_worker_poison_record_isolation` | `tests/test_white_internet_workers.py` | PLANNED |
| **F07** | `workers/traffic.py` | Snapshot collision idempotency (`ON CONFLICT DO NOTHING`) | `test_traffic_event_duplicate_idempotency` | `tests/test_white_internet_pipeline_postgres.py` | PLANNED |
| **F08** | `scripts/xray_api/app.py` | Ephemeral local cache; startup `unsynchronized` status | `test_startup_reconciliation_db_authority` | `scripts/xray_api/tests/test_api.py` | PLANNED |
| **F09** | `scripts/xray_api/app.py` | Versioned `DELETE` parameter with monotonic fencing | `test_client_delete_version_fencing` | `scripts/xray_api/tests/test_api.py` | PLANNED |
| **F10** | `scripts/xray_api/app.py` | Dynamic discovery of inbounds with `just1k-wl-*` prefix | `test_dynamic_inbound_discovery` | `scripts/xray_api/tests/test_api.py` | PLANNED |
| **F11** | `scripts/xray_api/app.py` | Secret base path matching strictly managed tags | `test_secret_base_path_managed_tag_only` | `scripts/xray_api/tests/test_api.py` | PLANNED |
| **F12** | `database/servers` | Pruning removed relays in `extra_data["just1k"]` | `test_extra_data_prunes_removed_relays` | `tests/test_white_internet_service.py` | PLANNED |
| **F13** | `database/servers` | Merge `extra_data` under `SELECT ... FOR UPDATE` lock | `test_extra_data_concurrent_merge_lock` | `tests/test_white_internet_concurrency_postgres.py` | PLANNED |
| **F14** | `database/servers` | Count all capacity statuses (`PENDING`, `ACTIVE`, `EXHAUSTED`, `PENDING_UPDATE`) | `test_capacity_includes_pending_update` | `tests/test_admin_server_peers_and_capacity.py` | PLANNED |
| **F15** | `workers/reconcile`| Bounded parallelism `Semaphore(10)` + per-sub lock | `test_reconciliation_bounded_parallelism` | `tests/test_white_internet_workers.py` | PLANNED |
| **F16** | `workers/traffic` | Consistent server health state handling (`ONLINE`/`WAITING`) | `test_traffic_worker_status_invariants` | `tests/test_white_internet_workers.py` | PLANNED |
| **F17** | `services/white_int`| Disabled sub traffic recorded as overage; grants intact | `test_disabled_subscription_does_not_consume_grants` | `tests/test_white_internet_pipeline_postgres.py` | PLANNED |
| **F18** | `alembic/0016` | Reversible downgrade cycle `upgrade -> downgrade base -> upgrade` | `test_alembic_downgrade_cycle` | `tests/test_alembic_downgrade_cycle.py` | PLANNED |
| **F19** | `just1knode.sh` | Fail-closed UFW rules; doctor validates port 8444 ACL | `test_doctor_ufw_acl_validation` | `tests/test_just1knode_sh.py` | PLANNED |
| **F20** | `just1knode.sh` | Verified rollback in `update_xray` (`xray -test` + health) | `test_update_xray_fail_closed_rollback` | `tests/test_just1knode_sh.py` | PLANNED |
| **F21** | `just1knode.sh` | Certificate expiration arithmetic & domain SAN check | `test_cert_expiration_and_san_check` | `tests/test_just1knode_sh.py` | PLANNED |
| **F22** | `just1knode.sh` | Immutable package versions & checksum verification | `test_installer_immutable_dependencies` | `tests/test_just1knode_sh.py` | PLANNED |
| **F23** | `handlers/web.py` | Proper fallback cascade from `extra_data` to env vars | `test_web_feed_env_fallback_when_empty` | `tests/test_white_internet_web.py` | PLANNED |
| **F24** | `tests/e2e` | Full multi-hop data-plane proxy test with live Xray | `test_full_xhttp_dataplane_e2e` | `tests/test_full_xhttp_dataplane_e2e.py` | PLANNED |

---

## Data-Plane E2E Architecture (F24)
- **Harness**: `tests/test_full_xhttp_dataplane_e2e.py`
- **Topology**:
  `Client (XHTTP over HTTP/2) -> Origin Xray (port 18003, packet-up) -> Relay Xray (port 18004, VLESS TCP) -> Echo Server (port 18088)`
- **Verifications**:
  1. OPTIONS preflight & POST stream handshake.
  2. `X-Cache` obfuscation padding headers.
  3. Live byte integrity across Relay multi-hop path.
  4. Live byte integrity across Direct freedom path.
  5. Epoch transition and stream fencing across Xray restarts.
