#!/bin/bash
# Fail-closed ownership policy for shared-host Nginx and Certbot resources.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

foundation_port_listener_is_nginx() {
    local port=$1 line seen=0
    command -v ss >/dev/null 2>&1 || return 1
    while IFS= read -r line; do
        [[ -n "$line" ]] || continue
        seen=1
        [[ "$line" == *'"nginx"'* ]] || return 1
    done < <(ss -H -ltnp "( sport = :$port )" 2>/dev/null || true)
    (( seen == 1 ))
}

foundation_preflight_public_listeners() {
    local port
    for port in 80 443; do
        foundation_port_in_use "$port" || continue
        foundation_port_listener_is_nginx "$port" ||
            foundation_fail PUBLIC_PORT_COLLISION \
                "public port $port занят не Nginx" \
                'existing listener ownership не соответствует Nginx' \
                "Освободите port $port или используйте --external-proxy; installer не остановит foreign service."
    done
}

foundation_setup_nginx_tls() {
    local domain=$1 email=$2 app_port=$3
    local live="$LETSENCRYPT_LIVE_DIR/$domain"
    local renewal="$LETSENCRYPT_RENEWAL_DIR/$domain.conf"
    local cert_created=false acme_created=false

    foundation_set_step "Настройка manifest-owned Nginx/TLS для $domain"
    foundation_validate_domain "$domain" ||
        foundation_fail INVALID_DOMAIN 'DOMAIN имеет небезопасный формат' "$domain" \
            'Передайте нормализованное DNS-имя.'
    validate_email "$email" ||
        foundation_fail INVALID_EMAIL 'SSL_EMAIL имеет небезопасный формат' "$email" \
            'Передайте реальный email для Certbot.'
    [[ "$app_port" =~ ^[1-9][0-9]{0,4}$ ]] && (( app_port <= 65535 )) ||
        foundation_fail INVALID_PORT 'Webhook port имеет неверный формат' "$app_port" \
            'Используйте свободный localhost TCP port.'

    command -v nginx >/dev/null 2>&1 ||
        foundation_fail NGINX_MISSING 'nginx binary отсутствует' nginx \
            'Установите Nginx и настройте shared service до повторного deploy.'
    command -v certbot >/dev/null 2>&1 ||
        foundation_fail CERTBOT_MISSING 'certbot binary отсутствует' certbot \
            'Установите Certbot до повторного deploy.'
    nginx -t >/dev/null 2>&1 ||
        foundation_fail NGINX_INVALID 'существующая Nginx configuration невалидна' 'nginx -t failed' \
            'Исправьте существующую конфигурацию; installer не будет её переписывать.'
    systemctl is-active --quiet nginx ||
        foundation_fail NGINX_INACTIVE \
            'Nginx service не активен' \
            'installer не включает и не запускает глобальный shared service автоматически' \
            'Запустите существующий Nginx вручную или используйте --external-proxy.'
    foundation_preflight_public_listeners
    foundation_preflight_domain "$domain" "$app_port"

    if foundation_path_exists "$ACME_ROOT"; then
        [[ -d "$ACME_ROOT" && ! -L "$ACME_ROOT" ]] ||
            foundation_fail FOREIGN_RESOURCE 'ACME webroot имеет небезопасный тип' "$ACME_ROOT" \
                'Освободите path или восстановите manifest ownership.'
        foundation_manifest_has_resource "path:$ACME_ROOT" ||
            foundation_fail FOREIGN_RESOURCE 'ACME webroot не принадлежит installation' "$ACME_ROOT" \
                'Не усыновляйте каталог автоматически; восстановите manifest или освободите path.'
    else
        install -d -o root -g www-data -m 0755 "$ACME_ROOT"
        acme_created=true
    fi
    install -d -o root -g www-data -m 0755 "$ACME_ROOT/.well-known/acme-challenge"
    foundation_register_resource "path:$ACME_ROOT" "$acme_created"

    foundation_write_nginx_site "$domain" "$app_port" false
    nginx -t >/dev/null 2>&1 ||
        foundation_fail NGINX_INVALID 'HTTP Nginx site не прошёл nginx -t' "$domain" \
            'Предыдущий site восстановлен; изучите nginx -t.'
    systemctl reload nginx ||
        foundation_fail NGINX_RELOAD_FAILED 'Nginx reload failed' "$domain" \
            'Проверьте journalctl -u nginx и повторите deploy.'

    if [[ ! -f "$live/fullchain.pem" || ! -f "$live/privkey.pem" ]]; then
        if foundation_path_exists "$live" || foundation_path_exists "$renewal"; then
            foundation_fail CERTIFICATE_CONFLICT \
                'certificate state частично существует' "$domain" \
                'Исправьте Certbot state вручную; installer не удаляет его вслепую.'
        fi
        certbot certonly --non-interactive --agree-tos --no-eff-email \
            --email "$email" --webroot -w "$ACME_ROOT" -d "$domain" ||
            foundation_fail CERTIFICATE_FAILED 'Certbot certificate request failed' "$domain" \
                'Проверьте DNS, port 80 и Certbot logs.'
        cert_created=true
    else
        foundation_manifest_has_resource "certbot:$domain" ||
            foundation_fail CERTIFICATE_CONFLICT \
                'существующий certificate не принадлежит ownership manifest' "$domain" \
                'Новая installation не усыновляет чужой cert; legacy migration выполняется отдельной strict проверкой.'
        [[ -f "$renewal" && ! -L "$renewal" ]] ||
            foundation_fail CERTIFICATE_CONFLICT 'renewal config отсутствует или небезопасен' "$renewal" \
                'Восстановите Certbot state вручную.'
        grep -Fq "archive_dir = /etc/letsencrypt/archive/$domain" "$renewal" ||
            foundation_fail CERTIFICATE_CONFLICT 'renewal config указывает на другой archive' "$renewal" \
                'Не изменяйте certificate автоматически.'
    fi
    foundation_register_resource "certbot:$domain" "$cert_created"

    foundation_write_nginx_site "$domain" "$app_port" true
    nginx -t >/dev/null 2>&1 ||
        foundation_fail NGINX_INVALID 'TLS Nginx site не прошёл nginx -t' "$domain" \
            'Предыдущий site восстановлен; изучите nginx -t.'
    systemctl reload nginx ||
        foundation_fail NGINX_RELOAD_FAILED 'TLS Nginx reload failed' "$domain" \
            'Проверьте journalctl -u nginx.'
}

if [[ "${INSTALL_SAFE_TLS_POLICY_SOURCE_ONLY:-0}" != 1 &&
      "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'install_safe_tls_policy.sh is source-only\n' >&2
    exit 64
fi
