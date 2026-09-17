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
        local backup_bin
        backup_bin="${BACKUP_DIR}/xray_$(date +%Y%m%d_%H%M%S).bak"
        if [[ -f "$XRAY_BIN" ]]; then
            cp "$XRAY_BIN" "$backup_bin"
        fi

        log "Применение обновления..."
        install -m 755 /tmp/xray_new/xray "$XRAY_BIN"
        set +e
        systemctl restart xray
        local restart_rc=$?
        set -e

        if [[ $restart_rc -eq 0 ]] && systemctl is-active --quiet xray; then
            if systemctl is-active --quiet xray-api 2>/dev/null; then
                systemctl restart xray-api 2>/dev/null || true
            fi
            log "Обновление завершено успешно! Версия: $($XRAY_BIN version | head -n 1)"
        else
            warn "Xray не запустился после обновления! Выполняем откат на предыдущую версию..."
            local xray_rb_ok=false
            if [[ -f "$backup_bin" ]]; then
                install -m 755 "$backup_bin" "$XRAY_BIN"
                if systemctl restart xray 2>/dev/null && systemctl is-active --quiet xray 2>/dev/null; then
                    xray_rb_ok=true
                    log "Откат на предыдущую версию успешно выполнен и подтвержден."
                fi
            fi
            if [[ "$xray_rb_ok" != "true" ]]; then
                warn "Служба Xray не смогла перезапуститься после отката на резервную копию!"
            fi
            rm -rf "$tmp_zip" /tmp/xray_new
            error "Обновление прервано из-за сбоя запуска службы."
        fi
    else
        rm -rf "$tmp_zip" /tmp/xray_new
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

            # Подготовка безопасного каталога для резервных копий
            local backup_root=""
            local node_backup=""
            local code_backup=""
            local venv_backup=""
            local node_dir="${INSTALL_DIR:-/opt/just1knode}"
            local api_dir="${XRAY_API_DIR:-/opt/xray-api}"
            local api_was_active=false

            mkdir -p "${BACKUP_DIR:-/var/backups/just1knode}"
            chmod 700 "${BACKUP_DIR:-/var/backups/just1knode}" 2>/dev/null || true
            backup_root="$(mktemp -d "${BACKUP_DIR:-/var/backups/just1knode}/update_bak.XXXXXXXXXX" 2>/dev/null || mktemp -d -t just1knode_update_bak.XXXXXXXXXX)"
            chmod 700 "$backup_root" 2>/dev/null || true

            # 1. Резервная копия just1knode
            if [[ -d "$node_dir" ]]; then
                node_backup="${backup_root}/just1knode"
                mkdir -p "$node_backup"
                if ! cp -a "${node_dir}/." "$node_backup/" 2>/dev/null; then
                    rm -rf "$backup_root" "$tmp_tar" "$tmp_dir"
                    error "Не удалось создать резервную копию ${node_dir}. Обновление отменено."
                fi
            fi

            # 2. Резервная копия xray-api и venv
            if [[ -d "${tmp_dir}/scripts/xray_api" && -d "$api_dir" ]]; then
                if systemctl is-active --quiet xray-api 2>/dev/null; then
                    api_was_active=true
                fi
                code_backup="${backup_root}/xray_api"
                mkdir -p "$code_backup"
                if ! cp -a "${api_dir}/." "$code_backup/" 2>/dev/null; then
                    rm -rf "$backup_root" "$tmp_tar" "$tmp_dir"
                    error "Не удалось создать резервную копию исходных файлов ${api_dir}. Обновление отменено."
                fi
                rm -rf "$code_backup"/venv*

                if [[ -d "${api_dir}/venv" ]]; then
                    venv_backup="${api_dir}/venv_bak_$$"
                    if ! cp -a "${api_dir}/venv" "$venv_backup" 2>/dev/null; then
                        rm -rf "$backup_root" "$venv_backup" "$tmp_tar" "$tmp_dir"
                        error "Не удалось создать резервную копию venv для ${api_dir}. Обновление отменено."
                    fi
                fi
            fi

            # Функция транзакционного отката компонентов узла
            rollback_node_components() {
                warn "Сбой обновления компонентов узла! Запуск транзакционного отката..."
                local rb_ok=true

                # Откат just1knode
                if [[ -n "$node_backup" && -d "$node_backup" ]]; then
                    find "$node_dir" -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null || true
                    if ! cp -a "$node_backup"/. "${node_dir}/" 2>/dev/null; then
                        warn "Критическая ошибка: не удалось восстановить файлы ${node_dir} из бэкапа!"
                        rb_ok=false
                    else
                        chmod +x "${node_dir}/just1knode.sh" 2>/dev/null || true
                        ln -sf "${node_dir}/just1knode.sh" /usr/local/bin/just1knode 2>/dev/null || true
                        log "Модули ${node_dir} успешно восстановлены из резервной копии."
                    fi
                fi

                # Откат xray-api
                if [[ -n "$code_backup" && -d "$code_backup" ]]; then
                    find "$api_dir" -mindepth 1 -maxdepth 1 ! -name 'venv*' -exec rm -rf {} + 2>/dev/null || true
                    if ! cp -a "$code_backup"/. "${api_dir}/" 2>/dev/null; then
                        warn "Критическая ошибка: не удалось восстановить файлы ${api_dir} из бэкапа!"
                        rb_ok=false
                    fi
                    if [[ -n "$venv_backup" && -d "$venv_backup" ]]; then
                        rm -rf "${api_dir}/venv"
                        if ! mv "$venv_backup" "${api_dir}/venv" 2>/dev/null; then
                            warn "Критическая ошибка: не удалось восстановить venv для ${api_dir}!"
                            rb_ok=false
                        fi
                        venv_backup=""
                    fi
                    ensure_xrayapi_user
                    chown -R root:xrayapi "$api_dir" 2>/dev/null || true
                    chmod -R 750 "$api_dir" 2>/dev/null || true
                    if [[ "$api_was_active" == "true" ]]; then
                        if ! systemctl restart xray-api 2>/dev/null && ! systemctl start xray-api 2>/dev/null; then
                            warn "Служба xray-api не смогла перезапуститься после отката."
                            rb_ok=false
                        elif ! systemctl is-active --quiet xray-api 2>/dev/null; then
                            warn "Служба xray-api не активна после отката."
                            rb_ok=false
                        else
                            log "Служба xray-api успешно восстановлена и перезапущена на исходной версии."
                        fi
                    fi
                fi

                if [[ "$rb_ok" == "true" ]]; then
                    log "Транзакционный откат компонентов узла успешно завершен и подтвержден."
                    rm -rf "$backup_root"
                    return 0
                else
                    warn "ВНИМАНИЕ: Откат завершился с ошибками! Резервная копия сохранена в: ${backup_root}"
                    warn "Используйте данную директорию для ручного восстановления узла."
                    return 1
                fi
            }

            # 3. Установка обновлений just1knode
            mkdir -p "$node_dir"
            if ! cp -a "${tmp_dir}/just1knode/." "${node_dir}/" 2>/dev/null; then
                rollback_node_components || true
                rm -rf "$tmp_tar" "$tmp_dir"
                error "Не удалось скопировать модули в ${node_dir}. Обновление прервано."
            fi
            chmod +x "${node_dir}/just1knode.sh"
            ln -sf "${node_dir}/just1knode.sh" /usr/local/bin/just1knode 2>/dev/null || true
            log "Модули ${node_dir} успешно обновлены и проверены."

            # 4. Установка обновлений xray-api
            if [[ -d "${tmp_dir}/scripts/xray_api" && -d "$api_dir" ]]; then
                if ! cp -a "${tmp_dir}/scripts/xray_api/." "${api_dir}/" 2>/dev/null; then
                    rollback_node_components || true
                    rm -rf "$tmp_tar" "$tmp_dir"
                    error "Не удалось скопировать исходные файлы ${api_dir}. Обновление прервано."
                fi
                if [[ -x "${api_dir}/venv/bin/pip" && -f "${api_dir}/requirements.txt" ]]; then
                    if ! "${api_dir}/venv/bin/pip" install -q -r "${api_dir}/requirements.txt" --no-cache-dir; then
                        rollback_node_components || true
                        rm -rf "$tmp_tar" "$tmp_dir"
                        error "Ошибка обновления зависимостей Python для xray-api. Обновление прервано."
                    fi
                fi
                ensure_xrayapi_user
                chown -R root:xrayapi "$api_dir" 2>/dev/null || true
                chmod -R 750 "$api_dir" 2>/dev/null || true
                if [[ "$api_was_active" == "true" ]]; then
                    if ! systemctl restart xray-api 2>/dev/null || ! systemctl is-active --quiet xray-api 2>/dev/null; then
                        rollback_node_components || true
                        rm -rf "$tmp_tar" "$tmp_dir"
                        error "Служба xray-api не смогла перезапуститься после обновления."
                    fi
                fi
                log "Компоненты ${api_dir} успешно обновлены с синхронизацией Python-зависимостей и перезапуском службы."
            fi

            # Полный успех обновления компонентов узла - очистка бэкапов
            rm -rf "$backup_root"
            [[ -n "$venv_backup" && -d "$venv_backup" ]] && rm -rf "$venv_backup"
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

