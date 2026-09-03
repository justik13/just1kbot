# White Internet 2026: Master Reference
**Date:** September 1, 2026
**Classification:** STRICTLY CONFIDENTIAL / ENGINEERING MASTER SPECIFICATION
**Author:** Senior Networking & Anti-Censorship Security Engineering Team

---

## 1. Threat Model & TSPU 2026 Reality

As of late 2026, the Russian federal censorship apparatus (TSPU / RKN) has transitioned to an aggressive "White List" (WL) mode on major mobile and terrestrial ISPs. 

### 1.1 White Lists Mode (L3 + L7)
The current filtering implementation combines Layer 3 IP blocking with deep Layer 7 SNI filtering:
- **Total UDP Block:** QUIC, WireGuard, and standard DNS over UDP are aggressively throttled or entirely dropped.
- **RST Injection:** Unrecognized or non-whitelisted TCP connections (unrecognized SNI, or traffic resembling shadowsocks/trojan without proper disguise) are aggressively terminated via TCP RST packets.

### 1.2 The Yandex Cloud CDN Bypass
To bypass this default-deny posture, we exploit the inherent operational requirements of the Russian internet. Yandex Cloud CDN IPs and SNIs are universally included in Russian ISP whitelists to ensure domestic infrastructure remains online. By hiding traffic behind Yandex Cloud CDN edge nodes, we inherit their pristine L3 IP reputation and L7 SNI whitelist status.

### 1.3 VLESS XHTTP & The OPTIONS Method
Standard CDN configurations typically cache or alter HTTP/GET/POST semantics, disrupting bidirectional proxy streams. To establish a duplex, bidirectional stream, we utilize the **Xray VLESS protocol over XHTTP (`packet-up`)** transport, specifically employing the HTTP `OPTIONS` method (`uplinkHTTPMethod="OPTIONS"`). Yandex CDN natively forwards `OPTIONS` requests directly to the origin without caching or altering the body, permitting a persistent, full-duplex tunnel.

### 1.4 Padding and Obfuscation Specifics
To thwart deep packet inspection (DPI) heuristics attempting to fingerprint Xray XHTTP patterns, strict padding rules are required:
- `xPaddingPlacement="queryInHeader"`
- `xPaddingKey="dc"`
- `xPaddingHeader="X-Cache"`
- `xPaddingMethod="tokenish"`

### 1.5 TLS Fingerprinting
TSPU active probes specifically target default Go and Chrome-based TLS ClientHellos. Using a `firefox` (or other non-chrome/non-default) TLS fingerprint (e.g., via uTLS) is critical. This masks the handshake, aligning it with standard mobile or alternative browser traffic, successfully bypassing TSPU active probing and protocol-specific filters.

---

## 2. Architecture

Our topology relies on decoupling the Domestic Gateway from the International Exit Relays to ensure stability and safety.

**Data Flow:**
`Client (INCY) -> Yandex CDN -> Origin Gateway (Russia) -> Exit Relays (Europe/Abroad) -> Internet`

### 2.1 Domestic Origin Isolation (`just1k-wl-direct`)
The Origin Gateway physically resides in a Russian datacenter (whitelisted by Yandex CDN). **CRITICAL SECURITY REQUIREMENT:** The Origin must *never* route traffic directly to the Russian internet. Doing so exposes the proxy to local loopback tracking. 
We enforce a **fail-closed** routing rule (tag: `just1k-wl-direct`). If the outbound connection to the Exit Relay drops, the proxy drops the traffic. It must not fallback to direct domestic routing.

### 2.2 Dynamic Multi-Relay Routing
A single Origin proxy serves multiple backend Exit Relays (e.g., DE, NL, SE, US). This is achieved via dynamic routing based on secret, high-entropy URI path suffixes.
- Route mapping: `/stream/{secret_base}/{relay_code}`
- The Origin inspects the `relay_code` in the XHTTP request path and transparently multiplexes the stream to the corresponding Exit Relay over a secured, internal inter-node link (e.g., encrypted gRPC or secondary VLESS).

---

## 3. INCY Client Integration

The INCY client utilizes custom encryption and subscription mechanisms to securely provision and update routing profiles.

### 3.1 `crypt1` Encryption Specification
Provisioning links utilize the `incy://crypt1/...` format, processed via `@incy/link-encoder@1.0.0`.
- **Cipher:** AES-256-GCM.
- **Key Derivation:** SHA-256 HKDF (using the user's secret token and system salt).
- **Initialization Vector (IV):** 12-byte CSPRNG IV.
- **Authentication:** 16-byte GHASH auth tag ensures payload integrity against tampering.

### 3.2 HTTP Subscription Feed
Subscriptions are served over a high-entropy, randomized path (e.g., `/sub/wl/{token}`).
The HTTP response strictly enforces these headers:
- `Subscription-Userinfo: upload=<bytes>; download=<bytes>; total=<bytes>; expire=<epoch_time>`
- `Profile-Update-Interval: <hours>`
- `Profile-Title: base64(<Title>)`

### 3.3 Multi-Server Profiles
To enhance UX, a single INCY subscription feed dynamically returns a multi-server VLESS profile payload. The client parses this single feed to populate a list of available exit nodes (displaying multiple country flags: 🇩🇪, 🇳🇱, 🇸🇪, 🇺🇸), while all configurations route seamlessly through the unified Yandex CDN + Origin architecture.

---

## 4. Security & Anti-Scanning

Hardening the infrastructure against RKN active scanning and brute-force discovery.

### 4.1 Secret High-Entropy Paths
Predictable paths (`/api`, `/sub`, `/stream`) are strictly forbidden. All routing and API paths use dynamically generated, high-entropy hex strings (e.g., Python's `secrets.token_hex(8)`). This neutralizes automated vulnerability scanners and TSPU directory brute-forcing.

### 4.2 Xray API Local Isolation
The `xray-api` must be heavily isolated from the public interface:
- Bound exclusively to a Unix domain socket or local loopback (`127.0.0.1`).
- Accessed via an Nginx reverse proxy requiring strict Bearer API tokens.
- Protected by UFW (Uncomplicated Firewall) rules, locking the API port strictly to the `BOT_IP` (the Telegram bot server orchestrator).

### 4.3 Fail-Closed Mechanisms & Epoch Fencing
- **503 on Crash:** If the Xray process crashes or is rebooting, Nginx must return a hard `HTTP 503 Service Unavailable` to mask the failure reason.
- **403 on Quota Exhaustion:** If a user exceeds their bandwidth quota (communicated via the API), the gateway immediately returns `HTTP 403 Forbidden`.
- **Atomic Epoch Fencing:** Time-based access controls (epoch timestamps) are updated atomically to prevent race conditions during rapid multi-device connections.

---

## 5. Server Management CLI (`just1knode`)

To manage the distributed nodes, the `just1knode` command-line utility provides a robust, transactional interface.

### 5.1 Interactive Wizard Menu
Running `just1knode` invokes an interactive TUI (Text User Interface) with the following options:
1. **Amnezia API** [TODO: Integration pending]
2. **Install Origin Gateway:** Configures the domestic Yandex-facing node.
3. **Install Relay Node:** Configures an international exit node.
4. **Manage Relays on Origin:** Links exit nodes to the Origin via `{secret_base}` multiplexing.
5. **Status:** Real-time metrics (RAM, CPU, active Xray connections).
6. **Doctor Diagnostics:** Checks TLS certificates, UFW rules, and API socket health.
7. **Update Xray:** Fetches and deploys the latest Xray-core binary.

### 5.2 Manifest-Based Transactional Rollback
Any configuration change (Nginx or Xray JSON edits) is treated as a transaction.
- The CLI generates a deployment manifest.
- Changes are applied to a temporary staging environment.
- Configurations are tested (`nginx -t`, `xray -test -config`).
- If any syntax failure occurs, the transaction is immediately rolled back to the last known-good manifest, ensuring zero downtime.
