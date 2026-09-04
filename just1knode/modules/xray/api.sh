#!/usr/bin/env bash
# =============================================================================
# JUST1KNODE - Модуль Xray API Агента (modules/xray/api.sh)
# =============================================================================

XRAY_API_DIR="${XRAY_API_DIR:-/opt/xray-api}"
XRAY_API_ETC="${XRAY_API_ETC:-/etc/xray-api}"
XRAY_API_LIB="${XRAY_API_LIB:-/var/lib/xray-api}"
XRAY_API_CONFIG_ENV="${XRAY_API_CONFIG_ENV:-${XRAY_API_ETC}/config.env}"
SYSTEMD_SYSTEM_DIR="${SYSTEMD_SYSTEM_DIR:-/etc/systemd/system}"

JUST1KBOT_REPO_URL="${JUST1KBOT_REPO_URL:-https://github.com/justik13/just1kbot}"
JUST1KBOT_REF="${JUST1KBOT_REF:-${JUST1KBOT_BRANCH:-main}}"

ensure_xrayapi_user() {
    if ! getent group xrayapi >/dev/null 2>&1; then
        groupadd -r xrayapi 2>/dev/null || true
    fi
    if ! id -u xrayapi >/dev/null 2>&1; then
        useradd -r -g xrayapi -s /usr/sbin/nologin -d /opt/xray-api -M xrayapi 2>/dev/null || useradd -r -s /usr/sbin/nologin -d /opt/xray-api -M xrayapi
    fi
}

deploy_xray_api_sources() {
    mkdir -p "${XRAY_API_DIR}"
    local project_xray_api="${SCRIPT_DIR}/../scripts/xray_api"
    if [[ ! -d "$project_xray_api" ]]; then
        project_xray_api="${SCRIPT_DIR}/../../scripts/xray_api"
    fi

    if [[ -d "$project_xray_api" && -f "$project_xray_api/app.py" ]]; then
        log "Копирование локальных исходников агента xray-api из репозитория..."
        cp -r "$project_xray_api"/* "${XRAY_API_DIR}/"
    elif [[ -d "/app/scripts/xray_api" && -f "/app/scripts/xray_api/app.py" ]]; then
        log "Копирование исходников xray-api из /app/scripts/xray_api..."
        cp -r /app/scripts/xray_api/* "${XRAY_API_DIR}/"
    else
        log "Автономная загрузка модулей xray-api (ref: $JUST1KBOT_REF)..."
        local tmp_tar="/tmp/just1k_repo_$$.tar.gz"
        rm -rf "$tmp_tar" /tmp/just1k_extracted_$$
        local archive_url
        if [[ "$JUST1KBOT_REF" =~ ^[0-9a-fA-F]{40}$ ]]; then
            archive_url="${JUST1KBOT_REPO_URL}/archive/${JUST1KBOT_REF}.tar.gz"
        else
            archive_url="${JUST1KBOT_REPO_URL}/archive/refs/heads/${JUST1KBOT_REF}.tar.gz"
        fi
        curl -fsSL "$archive_url" -o "$tmp_tar" 2>/dev/null || true

        if [[ -f "$tmp_tar" ]]; then
            mkdir -p /tmp/just1k_extracted_$$
            tar -xzf "$tmp_tar" -C /tmp/just1k_extracted_$$ --strip-components=1 2>/dev/null || true
            if [[ -d "/tmp/just1k_extracted_$$/scripts/xray_api" ]]; then
                cp -r /tmp/just1k_extracted_$$/scripts/xray_api/* "${XRAY_API_DIR}/"
            fi
            rm -rf "$tmp_tar" /tmp/just1k_extracted_$$
        fi
    fi
    chown -R xrayapi:xrayapi "${XRAY_API_DIR}" 2>/dev/null || true
    chmod -R 750 "${XRAY_API_DIR}" 2>/dev/null || true
}

setup_xray_api_venv() {
    ensure_xrayapi_user
    mkdir -p "${XRAY_API_DIR}"
    if [[ ! -d "${XRAY_API_DIR}/venv" || ! -f "${XRAY_API_DIR}/venv/bin/uvicorn" ]]; then
        log "Создание venv в ${XRAY_API_DIR}/venv..."
        python3 -m venv "${XRAY_API_DIR}/venv"
    fi
    if [[ -f "${XRAY_API_DIR}/requirements.txt" ]]; then
        log "Установка зафиксированных зависимостей xray-api..."
        "${XRAY_API_DIR}/venv/bin/pip" install --no-cache-dir -r "${XRAY_API_DIR}/requirements.txt"
    else
        warn "Файл ${XRAY_API_DIR}/requirements.txt не найден, пропуск pip install."
    fi
}

deploy_xray_api_service() {
    local api_key="$1"
    local cdn_domain="${2:-}"

    ensure_xrayapi_user
    mkdir -p "${XRAY_API_ETC}" "${STATE_DIR}" "${XRAY_API_LIB}"
    deploy_xray_api_sources
    setup_xray_api_venv

    cat > "${XRAY_API_CONFIG_ENV}" <<EOF
XRAY_API_KEY=${api_key}
XRAY_GRPC_HOST=127.0.0.1
XRAY_GRPC_PORT=10085
CLIENTS_FILE_PATH=${STATE_DIR}/clients.json
RELAYS_FILE_PATH=${STATE_DIR}/relays.json
EPOCH_FILE_PATH=${XRAY_API_LIB}/epoch.json
STATE_FILE_PATH=${STATE_FILE}
CDN_DOMAIN=${cdn_domain}
EOF

    chmod 640 "${XRAY_API_CONFIG_ENV}"
    chown root:xrayapi "${XRAY_API_CONFIG_ENV}" 2>/dev/null || true
    chmod 750 "${XRAY_API_ETC}"
    chown -R xrayapi:xrayapi "${XRAY_API_LIB}" 2>/dev/null || true
    chmod 750 "${XRAY_API_LIB}"

    # Права на чтение конфигурации Xray для пользователя xrayapi
    chmod 755 /usr/local/etc/xray 2>/dev/null || true
    chown root:xrayapi /usr/local/etc/xray/config.json 2>/dev/null || true
    chmod 644 /usr/local/etc/xray/config.json 2>/dev/null || true

    # Права на каталог состояния для пользователя xrayapi (SGID 2775)
    chown -R root:xrayapi "${STATE_DIR}" 2>/dev/null || true
    chmod 2775 "${STATE_DIR}"
    [[ -f "${CLIENTS_FILE}" ]] && { chown root:xrayapi "${CLIENTS_FILE}" 2>/dev/null || true; chmod 664 "${CLIENTS_FILE}"; }
    [[ -f "${RELAYS_FILE}" ]] && { chown root:xrayapi "${RELAYS_FILE}" 2>/dev/null || true; chmod 664 "${RELAYS_FILE}"; }
    [[ -f "${STATE_FILE}" ]] && { chown root:xrayapi "${STATE_FILE}" 2>/dev/null || true; chmod 664 "${STATE_FILE}"; }
    find "${STATE_DIR}" -name "*.lock" -exec chown root:xrayapi {} + 2>/dev/null || true
    find "${STATE_DIR}" -name "*.lock" -exec chmod 664 {} + 2>/dev/null || true

    cat > "${SYSTEMD_SYSTEM_DIR}/xray-api.service" <<EOF
[Unit]
Description=Just1kBot Xray API Agent
After=network.target xray.service
Wants=xray.service

[Service]
Type=simple
User=xrayapi
Group=xrayapi
UMask=0002
WorkingDirectory=${XRAY_API_DIR}
EnvironmentFile=${XRAY_API_CONFIG_ENV}
ExecStart=${XRAY_API_DIR}/venv/bin/uvicorn app:app --host 127.0.0.1 --port 5001 --workers 1 --log-level info
Restart=always
RestartSec=3
LimitNOFILE=65535
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
NoNewPrivileges=true
ReadWritePaths=${XRAY_API_DIR} ${STATE_DIR} ${XRAY_API_LIB}

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable --now xray-api
}
