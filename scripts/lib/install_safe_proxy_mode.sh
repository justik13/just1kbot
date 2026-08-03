#!/bin/bash
# Managed-Nginx and external-proxy modes for shared hosts.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

EXTERNAL_PROXY_SNIPPET=${EXTERNAL_PROXY_SNIPPET:-/var/lib/just1kbot/install-state/external-proxy.nginx.conf}
PROXY_MODE=${PROXY_MODE:-}

resolve_proxy_mode() {
    local requested=${JUST1KBOT_PROXY_MODE:-} recorded=''
    if foundation_manifest_validate >/dev/null 2>&1; then
        recorded=$(foundation_manifest_metadata proxy_mode 2>/dev/null || true)
    fi
    [[ -z "$requested" || "$requested" == managed || "$requested" == external ]] ||
        foundation_fail INVALID_PROXY_MODE 'неизвестный proxy mode' "$requested" \
            'Используйте managed или external.'
    [[ -z "$recorded" || "$recorded" == managed || "$recorded" == external ]] ||
        foundation_fail INVALID_PROXY_MODE 'manifest содержит неизвестный proxy mode' "$recorded" \
            'Проверьте ownership manifest вручную.'
    if [[ -n "$requested" && -n "$recorded" && "$requested" != "$recorded" ]]; then
        foundation_fail PROXY_MODE_MISMATCH \
            'нельзя автоматически сменить proxy mode' \
            "requested=$requested manifest=$recorded" \
            'Сначала удалите manifest-owned Nginx resources или настройте внешний proxy вручную.'
    fi
    PROXY_MODE=${recorded:-${requested:-managed}}
}

choose_internal_webhook_port() {
    local start=${JUST1KBOT_PORT_START:-18080}
    local end=${JUST1KBOT_PORT_END:-18179}
    local port
    [[ "$start" =~ ^[1-9][0-9]{0,4}$ && "$end" =~ ^[1-9][0-9]{0,4}$ ]] ||
        foundation_fail INVALID_PORT_RANGE 'неверный диапазон внутренних портов' "$start-$end" \
            'Используйте значения 1..65535.'
    (( start <= end && end <= 65535 )) ||
        foundation_fail INVALID_PORT_RANGE 'неверный диапазон внутренних портов' "$start-$end" \
            'Начало должно быть не больше конца, конец — не больше 65535.'
    for (( port=start; port<=end; port++ )); do
        if ! foundation_port_in_use "$port"; then
            printf '%s\n' "$port"
            return 0
        fi
    done
    foundation_fail NO_FREE_INTERNAL_PORT \
        'не найден свободный loopback port' "$start-$end" \
        'Освободите порт или задайте JUST1KBOT_PORT_START/JUST1KBOT_PORT_END.'
}

collect_external_initial_input() {
    if [[ "$NON_INTERACTIVE" == true ]]; then
        BOT_TOKEN=${BOT_TOKEN:?BOT_TOKEN не задан}
        DB_PASSWORD=${DB_PASSWORD:?DB_PASSWORD не задан}
        REDIS_PASSWORD=${REDIS_PASSWORD:?REDIS_PASSWORD не задан}
        ADMIN_IDS=${ADMIN_IDS:?ADMIN_IDS не задан}
        SUPPORT_USERNAME=${SUPPORT_USERNAME:?SUPPORT_USERNAME не задан}
        YOOKASSA_SHOP_ID=${YOOKASSA_SHOP_ID:?YOOKASSA_SHOP_ID не задан}
        YOOKASSA_SECRET_KEY=${YOOKASSA_SECRET_KEY:?YOOKASSA_SECRET_KEY не задан}
        YOOKASSA_RETURN_URL=${YOOKASSA_RETURN_URL:-https://t.me/{bot_username}}
        DOMAIN=${DOMAIN:?DOMAIN не задан}
        SSL_EMAIL=${SSL_EMAIL:-external-proxy@invalid.invalid}
        DB_ENCRYPTION_KEY=${DB_ENCRYPTION_KEY:-}
        if [[ -z "${YOOKASSA_WEBHOOK_PORT:-}" || "${YOOKASSA_WEBHOOK_PORT:-}" == auto ]]; then
            YOOKASSA_WEBHOOK_PORT=$(choose_internal_webhook_port)
        fi
        return
    fi

    info '=== Первичная конфигурация: external proxy ==='
    read_bot_token
    read_db_password 'Пароль PostgreSQL: ' DB_PASSWORD
    read_db_password 'Пароль Redis: ' REDIS_PASSWORD
    read_admin_ids
    read_support_username
    while [[ -z "${YOOKASSA_SHOP_ID:-}" ]]; do
        read_optional 'YooKassa Shop ID' YOOKASSA_SHOP_ID ''
        [[ -n "$YOOKASSA_SHOP_ID" ]] || printf 'YooKassa Shop ID обязателен.\n'
    done
    while [[ -z "${YOOKASSA_SECRET_KEY:-}" ]]; do
        read_optional_secret 'YooKassa Secret Key' YOOKASSA_SECRET_KEY ''
        [[ -n "$YOOKASSA_SECRET_KEY" ]] || printf 'YooKassa Secret Key обязателен.\n'
    done
    YOOKASSA_RETURN_URL='https://t.me/{bot_username}'
    while true; do
        read_optional 'Публичный домен для YooKassa webhook/health' DOMAIN ''
        if normalized_domain=$(normalize_domain "$DOMAIN"); then
            DOMAIN=$normalized_domain
            break
        fi
        printf 'Укажите корректный публичный домен.\n'
    done
    SSL_EMAIL=external-proxy@invalid.invalid
    YOOKASSA_WEBHOOK_PORT=$(choose_internal_webhook_port)
}

clone_function collect_initial_input proxy_base_collect_initial_input
collect_initial_input() {
    local explicit_port=false
    [[ -n "${YOOKASSA_WEBHOOK_PORT+x}" ]] && explicit_port=true
    resolve_proxy_mode
    if [[ "$PROXY_MODE" == external ]]; then
        collect_external_initial_input
    else
        proxy_base_collect_initial_input
        if [[ "$explicit_port" == false ]]; then
            YOOKASSA_WEBHOOK_PORT=$(choose_internal_webhook_port)
        fi
    fi
}

clone_function validate_initial_input proxy_base_validate_initial_input
validate_initial_input() {
    resolve_proxy_mode
    if [[ "$PROXY_MODE" == external && -z "${SSL_EMAIL:-}" ]]; then
        SSL_EMAIL=external-proxy@invalid.invalid
    fi
    proxy_base_validate_initial_input
}

proxy_preflight_postgres() {
    if command -v pg_lsclusters >/dev/null 2>&1 &&
       [[ -n "$(pg_lsclusters --no-header 2>/dev/null || true)" ]]; then
        pg_select_cluster
        [[ "$PG_STATUS" == online ]] || {
            error 'Существующий PostgreSQL cluster должен быть online для ownership check.'
            return 1
        }
        preflight_postgres_names_absent
    fi
}

clone_function preflight_before_packages proxy_base_preflight_before_packages
preflight_before_packages() {
    resolve_proxy_mode
    if [[ "$PROXY_MODE" == managed ]]; then
        proxy_base_preflight_before_packages
        return
    fi
    foundation_preflight_static_resources
    foundation_validate_domain "$DOMAIN" || {
        error 'DOMAIN имеет небезопасный формат'
        return 1
    }
    foundation_preflight_port "$YOOKASSA_WEBHOOK_PORT" just1kbot.service
    foundation_preflight_path_absent_or_owned \
        "$EXTERNAL_PROXY_SNIPPET" "path:$EXTERNAL_PROXY_SNIPPET" 'External proxy snippet'
    proxy_preflight_postgres
}

clone_function run_existing_read_only_preflight proxy_base_existing_preflight
run_existing_read_only_preflight() {
    resolve_proxy_mode
    if [[ "$PROXY_MODE" == managed || ! -f "$INSTALL_MANIFEST" ]]; then
        proxy_base_existing_preflight
        return
    fi
    DOMAIN=$(read_env_value DOMAIN)
    SSL_EMAIL=$(read_env_value SSL_EMAIL)
    YOOKASSA_WEBHOOK_PORT=$(read_env_value YOOKASSA_WEBHOOK_PORT)
    REDIS_PASSWORD=$(read_env_value REDIS_PASSWORD)
    [[ -n "$DOMAIN" && -n "$YOOKASSA_WEBHOOK_PORT" && -n "$REDIS_PASSWORD" ]] || {
        error 'Production .env не содержит DOMAIN/YOOKASSA_WEBHOOK_PORT/REDIS_PASSWORD.'
        return 1
    }
    foundation_preflight_static_resources
    foundation_validate_domain "$DOMAIN" || {
        error 'DOMAIN имеет небезопасный формат'
        return 1
    }
    foundation_preflight_port "$YOOKASSA_WEBHOOK_PORT" just1kbot.service
    foundation_preflight_path_absent_or_owned \
        "$EXTERNAL_PROXY_SNIPPET" "path:$EXTERNAL_PROXY_SNIPPET" 'External proxy snippet'
}

write_external_proxy_contract() {
    local created=false
    foundation_path_exists "$EXTERNAL_PROXY_SNIPPET" || created=true
    foundation_atomic_write "$EXTERNAL_PROXY_SNIPPET" root root 0600 <<EOF_PROXY
# Just1kBot external proxy contract. This file is not loaded automatically.
# Public domain: ${DOMAIN}
# Application binds only to loopback: 127.0.0.1:${YOOKASSA_WEBHOOK_PORT}

location = /health {
    proxy_pass http://127.0.0.1:${YOOKASSA_WEBHOOK_PORT}/health;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
}

location = /webhook/yookassa {
    limit_except POST { deny all; }
    proxy_pass http://127.0.0.1:${YOOKASSA_WEBHOOK_PORT}/webhook/yookassa;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
}
EOF_PROXY
    foundation_register_resource "path:$EXTERNAL_PROXY_SNIPPET" "$created"
    foundation_manifest_set_metadata proxy_mode external
    foundation_manifest_set_metadata external_proxy_domain "$DOMAIN"
    foundation_manifest_set_metadata internal_webhook_port "$YOOKASSA_WEBHOOK_PORT"
    printf 'External proxy mode: глобальный Nginx/TLS не изменён.\n'
    printf 'Готовый upstream contract: %s\n' "$EXTERNAL_PROXY_SNIPPET"
}

clone_function setup_nginx_initial proxy_base_setup_nginx_initial
setup_nginx_initial() {
    resolve_proxy_mode
    if [[ "$PROXY_MODE" == external ]]; then
        write_external_proxy_contract
    else
        foundation_manifest_set_metadata proxy_mode managed
        foundation_manifest_set_metadata internal_webhook_port "$YOOKASSA_WEBHOOK_PORT"
        proxy_base_setup_nginx_initial
    fi
}

clone_function refresh_existing_nginx proxy_base_refresh_existing_nginx
refresh_existing_nginx() {
    resolve_proxy_mode
    if [[ "$PROXY_MODE" == external ]]; then
        DOMAIN=$(read_env_value DOMAIN)
        YOOKASSA_WEBHOOK_PORT=$(read_env_value YOOKASSA_WEBHOOK_PORT)
        write_external_proxy_contract
    else
        proxy_base_refresh_existing_nginx
    fi
}

clone_function configure_operational_transaction proxy_base_configure_operational_transaction
configure_operational_transaction() {
    resolve_proxy_mode
    proxy_base_configure_operational_transaction
    [[ "$PROXY_MODE" == external ]] || return 0

    local item
    local -a paths=() units=()
    for item in "${OPERATIONAL_PATHS[@]}"; do
        case "$item" in
            /etc/nginx/*|/etc/letsencrypt/*) ;;
            *) paths+=("$item") ;;
        esac
    done
    for item in "${OPERATIONAL_UNITS[@]}"; do
        case "$item" in
            nginx.service|certbot.timer) ;;
            *) units+=("$item") ;;
        esac
    done
    OPERATIONAL_PATHS=("${paths[@]}" "$EXTERNAL_PROXY_SNIPPET")
    OPERATIONAL_UNITS=("${units[@]}")
    OPERATIONAL_NGINX=false
}

clone_function show_status proxy_base_show_status
show_status() {
    proxy_base_show_status
    resolve_proxy_mode
    printf 'Proxy mode: %s\n' "$PROXY_MODE"
    if [[ "$PROXY_MODE" == external ]]; then
        printf 'External proxy contract: %s\n' "$EXTERNAL_PROXY_SNIPPET"
    fi
}

if [[ "${INSTALL_SAFE_PROXY_MODE_SOURCE_ONLY:-0}" != 1 &&
      "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'install_safe_proxy_mode.sh is source-only\n' >&2
    exit 64
fi
