# E2E Test Infra: just1kbot Production Remediation & Full MVP Hardening

## Test Philosophy
- Requirement-driven, opaque-box and functional verification.
- Zero mock cheating: test scripts must exercise genuine file generation, shell parsing, JSON configuration schemas, XHTTP dataplane flows, and API behaviors.
- Execution Environment: Ubuntu 24.04 via Docker / Docker Desktop ONLY (per AGENTS.md / GEMINI.md).

## Feature Inventory & Test Coverage
| # | Feature | Requirement | Tier 1 (Feature) | Tier 2 (Boundary) | Tier 3 (Pairwise) | Tier 4 (Workload) |
|---|---------|-------------|:----------------:|:-----------------:|:-----------------:|:-----------------:|
| 1 | R1.1 Virtualenv & Dependencies | Node installer creates `/opt/xray-api/venv` and installs pinned deps | 5 | 5 | ✓ | ✓ |
| 2 | R1.3 Clean Commits & No Rogue Mode | Standalone node setup without hallucinated commit tags or amnezia-api | 5 | 5 | ✓ | ✓ |
| 3 | R2.1 C1 Path Synchronization | Secret path `/default` alignment across Nginx, Caddy, VLESS URLs | 5 | 5 | ✓ | ✓ |
| 4 | R2.2 C4 Relay Egress Enforcement | Origin node routing rule forces relay egress (no Russian exit) | 5 | 5 | ✓ | ✓ |
| 5 | R2.3 H11 xPaddingPlacement | `queryInHeader` parameter present in extra JSON | 5 | 5 | ✓ | ✓ |
| 6 | R2.4 C3 Dynamic Subscription Feeds | Caddy allowed paths match `/sub/wl/*` dynamic prefix | 5 | 5 | ✓ | ✓ |
| 7 | R3.1 H1 Nginx Camouflage Site | Port 443 root serves responsive HTML static site, not 404 | 5 | 5 | ✓ | ✓ |
| 8 | R3.2 H2 Certbot Renewal Hooks | Deploy hook scripts present and executable | 5 | 5 | ✓ | ✓ |
| 9 | R3.3 H12 Buffering & Max Body | `client_max_body_size 0`, large buffers configured | 5 | 5 | ✓ | ✓ |
| 10 | R3.4 H13/H14 REALITY Relay & Guard | Fail-closed role guard and REALITY default | 5 | 5 | ✓ | ✓ |
| 11 | R4.1 C2 Non-Root xrayapi User | Systemd unit runs as non-root with restricted permissions | 5 | 5 | ✓ | ✓ |
| 12 | R4.2 H6 Genuine CAS & Fail-Closed | Xray API returns 503 on Xray down, no synthetic CAS fallback | 5 | 5 | ✓ | ✓ |
| 13 | R4.3 H5 Doctor Tooling Portability | Python-based socket doctor passes gRPC checks | 5 | 5 | ✓ | ✓ |
| 14 | R5.1 PEP 701 f-string Portability | Python 3.10 AST parse and test execution with zero syntax errors | 5 | 5 | ✓ | ✓ |
| 15 | R5.2 Amnezia Key Dead Code Purge | Complete elimination of dead VPN key functions/buttons | 5 | 5 | ✓ | ✓ |
| 16 | R5.3 .gitignore Case Sensitivity | `WL/` directory untracked / tracked correctly | 5 | 5 | ✓ | ✓ |
| 17 | R5.4 Routing Geosite Rules | `category-ru` and `tld-ru` in white internet config | 5 | 5 | ✓ | ✓ |
| 18 | R5.5 Dynamic Profile-Title & Tariffs | Base64 encoded title and env overrides for pricing | 5 | 5 | ✓ | ✓ |
| 19 | R5.6 Capacity Accounting SSOT | Unified server capacity accounting across all server states | 5 | 5 | ✓ | ✓ |
| 20 | R6.1-R6.4 Full E2E & Linters | 1,063+ tests pass, test_full_xhttp_dataplane_e2e.py passes, ruff & shellcheck clean | 5 | 5 | ✓ | ✓ |

## Test Architecture
- **E2E Data-Plane Test (`tests/test_full_xhttp_dataplane_e2e.py`)**: Tests full end-to-end flow: mock Xray gRPC stats, FastAPI CAS snapshot lifecycle, XHTTP stream forwarding simulation, dynamic subscription rendering, and fail-closed security assertions.
- **Installer Test (`tests/test_just1knode_sh.py`)**: Tests node installation script bash syntax, templating outputs, systemd units, permissions, doctor diagnostics, and role guards.
- **Core Suite**: 1,063+ unit and integration tests across `tests/` and `scripts/xray_api/tests/`.
- **Static Analysis & Linters**: `ruff check`, `ruff format --check`, `shellcheck scripts/just1knode.sh`.
- **Runtime Execution**: Docker Ubuntu 24.04 container.

## Real-World Application Scenarios (Tier 4)
| # | Scenario | Features Exercised | Expected Outcome |
|---|----------|--------------------|------------------|
| 1 | Greenfield Node Provisioning | R1.1, R1.2, R1.3, R4.1, R3.1, R3.2 | Node config created with non-root service, venv, camo site, and certbot hook |
| 2 | High-Concurrency XHTTP Subscription & Stream | R2.1, R2.3, R2.4, R3.3, R5.5 | Subscription payload generated with base64 Profile-Title and xPaddingPlacement=queryInHeader; Caddy & Nginx routes succeed |
| 3 | Origin Node Relay Enforcement & Anti-Russian Exit | R2.2, R3.4, R5.4 | Origin node config routes all client traffic strictly via relay outbound; direct exit forbidden |
| 4 | Xray Process Crash & CAS Recovery | R4.2, R1.2 | Xray-api returns 503 during downtime; on recovery, CAS rejects stale boot_id and resynchronizes stats |
| 5 | Full Diagnostics & Capacity Under Load | R4.3, R5.6, R5.1 | Doctor tool checks pass cleanly without syntax errors; server capacity matches SSOT |

## Coverage Thresholds
- Tier 1: ≥5 test cases per feature
- Tier 2: ≥5 boundary / edge test cases per feature
- Tier 3: Pairwise cross-feature interactions
- Tier 4: ≥5 realistic end-to-end application scenarios
- Total Target: >1,063+ passing tests, 0 linter errors, 0 shellcheck warnings
