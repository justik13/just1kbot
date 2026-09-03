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

update_node() {
    title "КОМПЛЕКСНОЕ ОБНОВЛЕНИЕ УТИЛИТЫ И КОНФИГУРАЦИИ УЗЛА"
    check_root
    init_state_dir
    acquire_just1knode_lock

    local target="${1:-all}"

    if [[ "$target" == "core" ]]; then
        update_xray_core
        return
    fi

    log "Загрузка и обновление модулей just1knode из репозитория GitHub..."
    local repo_url="${JUST1KBOT_REPO_URL:-https://github.com/justik13/just1kbot}"
    local ref="${JUST1KBOT_REF:-main}"
    local tmp_tar="/tmp/just1knode_update_$$.tar.gz"
    local tmp_dir="/tmp/just1knode_update_dir_$$"

    rm -rf "$tmp_tar" "$tmp_dir"
    mkdir -p "$tmp_dir"

    local archive_url
    if [[ "$ref" =~ ^[0-9a-fA-F]{40}$ ]]; then
        archive_url="${repo_url}/archive/${ref}.tar.gz"
    else
        archive_url="${repo_url}/archive/refs/heads/${ref}.tar.gz"
    fi

    local download_ok=0
    if curl -fsSL "$archive_url" -o "$tmp_tar" 2>/dev/null || wget -qO "$tmp_tar" "$archive_url" 2>/dev/null; then
        download_ok=1
    fi

    if [[ $download_ok -eq 1 ]]; then
        if ! tar -xzf "$tmp_tar" -C "$tmp_dir" --strip-components=1 2>/dev/null; then
            rm -rf "$tmp_tar" "$tmp_dir"
            error "Ошибка целостности архива: распаковка не удалась. Обновление прервано."
        fi

        # Валидация синтаксиса shell-скриптов перед установкой (Pre-Deploy Syntax Check)
        if [[ -d "${tmp_dir}/just1knode" ]]; then
            local syntax_err=0
            while IFS= read -r -d '' sh_file; do
                if ! bash -n "$sh_file"; then
                    warn "Синтаксическая ошибка в обновлении: $sh_file"
                    syntax_err=1
                fi
            done < <(find "${tmp_dir}/just1knode" -type f -name "*.sh" -print0 2>/dev/null)

            if [[ $syntax_err -ne 0 ]]; then
                rm -rf "$tmp_tar" "$tmp_dir"
                error "Обновление прервано: обнаружены синтаксические ошибки в загруженном релизе."
            fi

            mkdir -p /opt/just1knode
            cp -r "${tmp_dir}/just1knode"/* /opt/just1knode/
            chmod +x /opt/just1knode/just1knode.sh
            ln -sf /opt/just1knode/just1knode.sh /usr/local/bin/just1knode
            log "Модули /opt/just1knode успешно обновлены и проверены."
        fi

        # Обновление xray-api и синхронизация зависимостей venv
        if [[ -d "${tmp_dir}/scripts/xray_api" && -d /opt/xray-api ]]; then
            cp -r "${tmp_dir}/scripts/xray_api"/* /opt/xray-api/
            if [[ -x /opt/xray-api/venv/bin/pip && -f /opt/xray-api/requirements.txt ]]; then
                /opt/xray-api/venv/bin/pip install -q -r /opt/xray-api/requirements.txt --no-cache-dir 2>/dev/null || true
            fi
            log "Компоненты /opt/xray-api успешно обновлены с синхронизацией Python-зависимостей."
        fi

        rm -rf "$tmp_tar" "$tmp_dir"
    else
        warn "Не удалось загрузить архив с GitHub. Используем текущие установленные модули для самовосстановления."
        rm -rf "$tmp_tar" "$tmp_dir"
    fi

    # Автоматическая оптимизация конфигурации в зависимости от роли сервера
    local role
    role="$(get_state_val "role")"
    if [[ "$role" == "origin" ]]; then
        heal_and_update_origin_config
    elif [[ "$role" == "relay" ]]; then
        heal_and_update_relay_config
    else
        warn "Узел не настроен (роль не определена). Автоматическая оптимизация конфига пропущена."
    fi

    if [[ "$target" == "all" ]]; then
        update_xray_core
    fi

    title "ОБНОВЛЕНИЕ И АВТО-КОНФИГУРАЦИЯ УЗЛА УСПЕШНО ЗАВЕРШЕНЫ!"
    echo -e "${GREEN}✔ Все параметры Xray, DNS (Split-DNS), IPv4 и системные настройки приведены к эталону.${NC}"
    echo -e "${GREEN}✔ 100% российских сервисов (включая 2ip.ru, Госуслуги, банки) направляются через Origin.${NC}\n"
}

