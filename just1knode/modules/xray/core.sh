#!/usr/bin/env bash
# =============================================================================
# JUST1KNODE - Модуль Xray Core (modules/xray/core.sh)
# =============================================================================

XRAY_VERSION_PINNED="${XRAY_VERSION_PINNED:-26.7.28}"
XRAY_SHA256_64="8195d909f1109b8f3d99eefe401a3c451d7bf4af71f24d3815420f77e5dd2a40"
XRAY_SHA256_ARM64="f5698bb218ada3b4022db26fafc39601c5f53b46b19eb76c9616325985807501"

XRAY_BIN="${XRAY_BIN:-/usr/local/bin/xray}"
XRAY_CONFIG_DIR="${XRAY_CONFIG_DIR:-/usr/local/etc/xray}"
XRAY_CONFIG="${XRAY_CONFIG:-${XRAY_CONFIG_DIR}/config.json}"
XRAY_SHARE_DIR="${XRAY_SHARE_DIR:-/usr/local/share/xray}"
SYSTEMD_SYSTEM_DIR="${SYSTEMD_SYSTEM_DIR:-/etc/systemd/system}"

download_and_verify_xray() {
    local target_zip="$1"
    local arch
    arch="$(get_arch)"
    local url="https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VERSION_PINNED}/Xray-linux-${arch}.zip"
    local expected_hash="$XRAY_SHA256_64"
    if [[ "$arch" == "arm64-v8a" ]]; then
        expected_hash="$XRAY_SHA256_ARM64"
    fi

    log "Скачивание Xray-core v${XRAY_VERSION_PINNED} (${arch})..."
    if ! curl -sSL -f "$url" -o "$target_zip"; then
        error "Не удалось скачать Xray-core по адресу: $url"
    fi

    log "Проверка контрольной суммы SHA-256..."
    local actual_hash
    actual_hash="$(sha256sum "$target_zip" | awk '{print $1}')"
    if [[ "$actual_hash" != "$expected_hash" ]]; then
        rm -f "$target_zip"
        error "Контрольная сумма SHA-256 не совпадает! Ожидалось: $expected_hash, получено: $actual_hash"
    fi
    log "Верификация SHA-256 успешно пройдена."
}

install_xray_binaries() {
    local tmp_zip="/tmp/xray_install.zip"
    download_and_verify_xray "$tmp_zip"

    mkdir -p "$XRAY_CONFIG_DIR" "$XRAY_SHARE_DIR" "$(dirname "$XRAY_BIN")"
    unzip -q -o "$tmp_zip" xray -d "$(dirname "$XRAY_BIN")"
    unzip -q -o "$tmp_zip" geoip.dat geosite.dat -d "$XRAY_SHARE_DIR/" || true
    rm -f "$tmp_zip"
    chmod +x "$XRAY_BIN"
}

deploy_xray_systemd_service() {
    mkdir -p "${SYSTEMD_SYSTEM_DIR}"
    cat > "${SYSTEMD_SYSTEM_DIR}/xray.service" <<EOF
[Unit]
Description=Xray Service
Documentation=https://github.com/xtls
After=network.target nss-lookup.target

[Service]
User=root
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_BIND_SERVICE
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE
NoNewPrivileges=true
ExecStart=${XRAY_BIN} run -config ${XRAY_CONFIG}
Restart=on-failure
LimitNPROC=10000
LimitNOFILE=1000000

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable xray
}

update_xray_core() {
    title "ОБНОВЛЕНИЕ ЯДРА XRAY-CORE"
    check_root
    init_state_dir
    log "Текущая версия Xray: $($XRAY_BIN version 2>/dev/null | head -n 1 || echo 'не установлена')"

    local tmp_zip="/tmp/xray_update.zip"
    download_and_verify_xray "$tmp_zip"

    mkdir -p /tmp/xray_new
    unzip -q -o "$tmp_zip" xray -d /tmp/xray_new/
    chmod +x /tmp/xray_new/xray

    log "Проверка текущей конфигурации новым бинарником..."
    if /tmp/xray_new/xray run -test -config "$XRAY_CONFIG"; then
        log "Тест пройден успешно. Создание резервной копии старого бинарника..."
        mkdir -p "$BACKUP_DIR"
        local backup_bin="${BACKUP_DIR}/xray_$(date +%Y%m%d_%H%M%S).bak"
        if [[ -f "$XRAY_BIN" ]]; then
            cp "$XRAY_BIN" "$backup_bin"
        fi

        log "Применение обновления..."
        cp /tmp/xray_new/xray "$XRAY_BIN"
        set +e
        systemctl restart xray
        local restart_rc=$?
        set -e

        if [[ $restart_rc -eq 0 ]] && systemctl is-active --quiet xray; then
            log "Обновление завершено успешно! Версия: $($XRAY_BIN version | head -n 1)"
        else
            warn "Xray не запустился после обновления! Выполняем откат на предыдущую версию..."
            if [[ -f "$backup_bin" ]]; then
                cp "$backup_bin" "$XRAY_BIN"
                systemctl restart xray || true
            fi
            log "Откат на предыдущую версию успешно выполнен и подтвержден."
            error "Обновление прервано из-за сбоя запуска службы."
        fi
    else
        error "Тест новой версии провалился. Обновление отменено."
    fi
    rm -rf "$tmp_zip" /tmp/xray_new
}

update_xray() {
    update_xray_core "$@"
}
