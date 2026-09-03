# Project: just1kbot Production Remediation & Full MVP Hardening

## Architecture
just1kbot is a high-performance Telegram VPN bot (aiogram 3.30+) managing censorship-resistant VPN nodes:
- **Protocol**: Exclusively AmneziaWG (`awg`/`amneziawg`) and Xray XHTTP (VLESS + REALITY + XHTTP). Pure WireGuard (`wg`) is strictly forbidden.
- **Node Agent (`just1knode.sh`)**: Automated installer for origin and relay nodes running on Ubuntu 24.04 / 22.04 LTS. Installs and configures Xray-core, Nginx (camouflage & reverse proxy), Certbot, and `xray-api` daemon.
- **Node REST API (`scripts/xray_api/app.py`)**: FastAPI daemon managing Xray client credentials, traffic snapshots, health checks, and lifecycle via CAS (Compare-And-Swap) loops over gRPC stats.
- **Web Layer & Ingress**: Nginx / Caddy reverse proxies handling XHTTP stream multiplexing, camouflage sites, and dynamic subscription feeds (`/sub/wl/...`).
- **Core Bot & Service Layer**: `services/white_internet_service.py`, `database/repositories/servers_repo.py`, `bot/handlers/white_internet_web.py`.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | R1.1 | Dedicated Python venv `/opt/xray-api/venv` and pip requirements installation in `just1knode.sh` | M1 | ORIGINAL_REQUEST §R1 |
| 2 | R1.2 | Pinned dependencies in `scripts/xray_api/requirements.txt` (`fastapi`, `uvicorn`, `psutil`, `pydantic`) | M1 | ORIGINAL_REQUEST §R1 |
| 3 | R1.3 | Eliminate hallucinated commit hashes (`JUST1KBOT_RELEASE_COMMIT`, `AMNEZIA_API_COMMIT`) | M1 | ORIGINAL_REQUEST §R1 |
| 4 | R1.4 | Purge broken third-party `amnezia-api` installer mode (AmneziaWG SSOT via INCY) | M1 | ORIGINAL_REQUEST §R1 |
| 5 | R2.1 | Align C1/Nginx 404 path mismatch (`/default` vs secret base path across web/nginx/bot) | M2 | ORIGINAL_REQUEST §R2 |
| 6 | R2.2 | Enforce C4 Relay Egress on Origin nodes (eliminate Russian direct exit) | M2 | ORIGINAL_REQUEST §R2 |
| 7 | R2.3 | Set H11 `xPaddingPlacement` to `queryInHeader` across server, client, and subscription configs | M2 | ORIGINAL_REQUEST §R2 |
| 8 | R2.4 | Support C3 dynamic subscription feeds in `Caddyfile` matching `{$WHITE_INTERNET_SUB_PATH_PREFIX:/sub/wl}/*` | M2 | ORIGINAL_REQUEST §R2 |
| 9 | R3.1 | Deploy H1 Nginx responsive camouflage static site at `/var/www/html/index.html` with root fallback | M2 | ORIGINAL_REQUEST §R3 |
| 10 | R3.2 | Deploy H2 Certbot post-renewal deploy hook (`/etc/letsencrypt/renewal-hooks/deploy/restart-xray-nginx.sh`) | M2 | ORIGINAL_REQUEST §R3 |
| 11 | R3.3 | Configure H12 buffering & limits (`client_max_body_size 0`, `large_client_header_buffers 8 64k`, `http2_max_field_size 64k`) | M2 | ORIGINAL_REQUEST §R3 |
| 12 | R3.4 | Configure H13/H14 Relay protocol REALITY default and fail-closed origin role guard | M2 | ORIGINAL_REQUEST §R3 |
| 13 | R4.1 | Provision C2 dedicated non-root `xrayapi` system user with strict directory permissions | M1 | ORIGINAL_REQUEST §R4 |
| 14 | R4.2 | Eliminate H6 synthetic CAS bypasses in `xray_api/app.py` and enforce fail-closed 503 on Xray down | M1 | ORIGINAL_REQUEST §R4 |
| 15 | R4.3 | Fix H5 `socat`/`ss` doctor tooling syntax error using portable Python socket check | M1 | ORIGINAL_REQUEST §R4 |
| 16 | R5.1 | Fix Python 3.10 PEP 701 f-string compatibility (purge inline quotes/backslashes) | M3 | ORIGINAL_REQUEST §R5 |
| 17 | R5.2 | Purge dead Amnezia key/button code (`generate_amnezia_vpn_key`, `BTN_WL_AMNEZIA_KEY`) | M3 | ORIGINAL_REQUEST §R5 |
| 18 | R5.3 | Fix `.gitignore` case sensitivity for `WL/` directory | M3 | ORIGINAL_REQUEST §R5 |
| 19 | R5.4 | Replace non-existent `geosite:ru` with `geosite:category-ru` and `geosite:tld-ru` | M3 | ORIGINAL_REQUEST §R5 |
| 20 | R5.5 | Implement dynamic `Profile-Title` base64 encoding and env-driven tariff pricing overrides | M3 | ORIGINAL_REQUEST §R5 |
| 21 | R5.6 | Align capacity accounting SSOT (`servers_repo.py` vs `white_internet_service.py`) | M3 | ORIGINAL_REQUEST §R5 |
| 22 | R5.7 | Clean up outdated `PROJECT.md` and `TEST_INFRA.md` documentation | M4 | ORIGINAL_REQUEST §R5 |
| 23 | R6.1 | Replace false-green mocks in `tests/test_just1knode_sh.py` with functional validation | M4 / E2E | ORIGINAL_REQUEST §R6 |
| 24 | R6.2 | Implement `tests/test_full_xhttp_dataplane_e2e.py` for end-to-end XHTTP dataplane verification | M4 / E2E | ORIGINAL_REQUEST §R6 |
| 25 | R6.3 | Validate full test suite (1,063+ tests) inside Ubuntu 24.04 Docker environment | M4 | ORIGINAL_REQUEST §R6 |
| 26 | R6.4 | Enforce strict zero-lint-error compliance with `ruff check`, `ruff format --check`, and `shellcheck` | M4 | ORIGINAL_REQUEST §R6 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Node Provisioning & Security Core | R1.1-R1.4, R4.1-R4.3 (venv, pinned deps, non-root user, real CAS, doctor fix) | none | DONE |
| M2 | Data-Plane & Web Server Ingress | R2.1-R2.4, R3.1-R3.4 (C1 path sync, C4 relay egress, H11 xPadding, C3 feeds, H1 camo, H2 certbot, H12 streaming, H13/H14 REALITY) | M1 | PLANNED |
| M3 | Codebase Hygiene & SSOT Alignment | R5.1-R5.6 (PEP 701 f-strings, dead Amnezia purge, .gitignore, geosite rules, Profile-Title base64, tariff overrides, capacity SSOT) | none | PLANNED |
| M4 | E2E Integration, Docker Verification & Hardening | R5.7, R6.1-R6.4 (100% E2E test suite pass in Ubuntu 24.04 Docker, ruff, shellcheck, Tier 5 adversarial hardening) | M1, M2, M3, E2E | PLANNED |
| E2E | E2E Testing Track | Requirement-driven opaque-box test suite (Tiers 1-4, `test_full_xhttp_dataplane_e2e.py`, `TEST_READY.md`) | none (parallel) | IN_PROGRESS |

## Interface Contracts
### xray-api ↔ just1knode.sh
- `xray-api.service`: Run as `User=xrayapi`, `Group=xrayapi`, `WorkingDirectory=/opt/xray-api`, `ExecStart=/opt/xray-api/venv/bin/uvicorn app:app --host 127.0.0.1 --port 8080`.
- Socket / Proc Health: `/v1/health` checks genuine Xray pid via `/proc` or `psutil`. If Xray process is not running or down, returns HTTP 503 (no synthetic CAS fallbacks).
- CAS Snapshots: `/v1/traffic/snapshot` requires genuine `boot_id` and `process_starttime`. Returns 503 if Xray is offline.

### Web Server (Nginx / Caddy) ↔ Xray XHTTP Ingress
- Ingress path: `/secret_path/default` mapped to Xray XHTTP inbound stream.
- Buffer / Stream: `client_max_body_size 0`, `proxy_request_buffering off`, `proxy_buffering off`.
- Camouflage: Root `/` serves `/var/www/html/index.html` responsive HTML (HTTP 200).
- Subscriptions: `@allowed_paths` in Caddy includes `{$WHITE_INTERNET_SUB_PATH_PREFIX:/sub/wl}/*`.

### Bot ↔ Web Subscription & Xray API
- VLESS Link: `vless://{uuid}@{host}:{port}?path=%2F{secret_path}%2Fdefault&type=xhttp&mode=multi&security=reality&pbk={pbk}&fp=chrome&sni={sni}&encryption=none&extra=%7B%22xPaddingPlacement%22%3A%22queryInHeader%22%7D#{encoded_tag}`
- Profile-Title: Header `Profile-Title: base64:{b64_title}` with URL-safe / standard base64 encoding.

## Code Layout & Write Ownership
- **M1 Owner**: `scripts/just1knode.sh` (provisioning, venv, service units, doctor, user setup), `scripts/xray_api/requirements.txt`, `scripts/xray_api/app.py`, `scripts/xray_api/tests/*`.
- **M2 Owner**: `scripts/just1knode.sh` (nginx template, certbot hook, relay menu, routing rules), `Caddyfile`, `Caddyfile.ci`, `services/white_internet_service.py` (VLESS link & xPadding), `bot/handlers/white_internet_web.py`.
- **M3 Owner**: `.gitignore`, `config/constants.py`, `database/repositories/servers_repo.py`, `bot/texts/user/white_internet.py`, `services/white_internet_service.py` (purge dead code, routing geosite), `bot/handlers/white_internet_web.py` (Profile-Title).
- **E2E / M4 Owner**: `tests/test_just1knode_sh.py`, `tests/test_full_xhttp_dataplane_e2e.py`, `TEST_INFRA.md`, `TEST_READY.md`.
