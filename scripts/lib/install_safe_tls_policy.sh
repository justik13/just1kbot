#!/bin/bash
# Fail-closed ownership policy for shared-host Nginx and Certbot resources.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

foundation_port_listener_is_nginx() {
    local port=$1 line seen=0
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
                "existing listener ownership не соответствует Nginx" \
                "Освободите port $port или перенесите foreign service; installer не будет его останавливать."
    done
}

foundation_setup_nginx_tls() {
    local domain=$1 email=$2 app_port=$3
    local available renewal temporary cert_created=false acme_created=false

    foundation_validate_domain "$domain" ||
        foundation_fail INVALID_DOMAIN "DOMAIN имеет небезопасный формат" "$domain" \
            "Передайте нормализованное DNS-имя."
    foundation_validate_email "$email" ||
        foundation_fail INVALID_EMAIL "SSL_EMAIL имеет небезопасный формат" "$email" \
            "Передайте реальный email для Certbot."
    foundation_validate_port "$app_port" ||
        foundation_fail INVALID_PORT "Webhook port имеет неверный формат" "$app_port" \
            "Используйте свободный localhost TCP port."

    foundation_preflight_nginx_domain "$domain"
    foundation_preflight_port "$app_port" just1kbot.service
    foundation_preflight_public_listeners

    if foundation_path_exists "$ACME_ROOT"; then
        [[ -d "$ACME_ROOT" && ! -L "$ACME_ROOT" ]] ||
            foundation_fail FOREIGN_RESOURCE \
                "ACME webroot имеет небезопасный тип" "$ACME_ROOT" \
                "Освободите path или восстановите manifest ownership."
        foundation_manifest_has_resource "path:$ACME_ROOT" ||
            foundation_fail FOREIGN_RESOURCE \
                "существующий ACME webroot не принадлежит installation" "$ACME_ROOT" \
                "Не усыновляйте каталог автоматически; освободите path или восстановите manifest."
    else
        install -d -o www-data -g www-data -m 0755 "$ACME_ROOT"
        acme_created=true
    fi
    foundation_register_resource "path:$ACME_ROOT" "$acme_created"

    foundation_write_nginx_site "$domain" "$app_port" http
    nginx -t >/dev/null 2>&1 ||
        foundation_fail NGINX_INVALID "Nginx config validation failed" "$domain" \
            "Исправьте указанную nginx -t ошибку; предыдущий site уже восстановлен."

    systemctl is-active --quiet nginx ||
        foundation_fail NGINX_INACTIVE \
            "Nginx service не активен" \
            "installer не запускает и не включает глобальный shared service автоматически" \
            "Проверьте существующую Nginx installation и запустите её вручную, затем повторите deploy."
    systemctl reload nginx ||
        foundation_fail NGINX_RELOAD_FAILED "Nginx reload failed" "$domain" \
            "Проверьте journalctl -u nginx и повторите deploy."

    available="/etc/letsencrypt/live/$domain"
    renewal="/etc/letsencrypt/renewal/$domain.conf"
    if [[ ! -f "$available/fullchain.pem" || ! -f "$available/privkey.pem" ]]; then
        if foundation_path_exists "$available" || foundation_path_exists "$renewal"; then
            foundation_fail CERTIFICATE_CONFLICT \
                "certificate state частично существует" "$domain" \
                "Исправьте Certbot state вручную; installer не удаляет его вслепую."
        fi
        certbot certonly --non-interactive --agree-tos --no-eff-email \
            --email "$email" --webroot -w "$ACME_ROOT" -d "$domain" ||
            foundation_fail CERTIFICATE_FAILED \
                "Certbot certificate request failed" "$domain" \
                "Проверьте DNS, ports 80/443 и certbot logs."
        cert_created=true
    else
        foundation_manifest_has_resource "certbot:$domain" ||
            foundation_fail CERTIFICATE_CONFLICT \
                "существующий certificate не принадлежит ownership manifest" "$domain" \
                "Новая installation не усыновляет чужой cert; legacy migration выполняется отдельной strict проверкой."
        [[ -f "$renewal" && ! -L "$renewal" ]] ||
            foundation_fail CERTIFICATE_CONFLICT \
                "renewal config отсутствует или небезопасен" "$renewal" \
                "Восстановите Certbot state вручную."
        grep -Fq "archive_dir = /etc/letsencrypt/archive/$domain" "$renewal" ||
            foundation_fail CERTIFICATE_CONFLICT \
                "renewal config указывает на другой archive" "$renewal" \
                "Не изменяйте certificate автоматически."
    fi
    foundation_register_resource "certbot:$domain" "$cert_created"

    foundation_write_nginx_site "$domain" "$app_port" tls
    nginx -t >/dev/null 2>&1 ||
        foundation_fail NGINX_INVALID "TLS Nginx config validation failed" "$domain" \
            "Исправьте nginx config; предыдущий site уже восстановлен."
    systemctl reload nginx ||
        foundation_fail NGINX_RELOAD_FAILED "TLS Nginx reload failed" "$domain" \
            "Проверьте journalctl -u nginx."

    # Ensure temporary variables never preserve secret-bearing output.
    temporary=
    unset temporary
}

if [[ "${INSTALL_SAFE_TLS_POLICY_SOURCE_ONLY:-0}" != 1 &&
      "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'install_safe_tls_policy.sh is source-only\n' >&2
    exit 64
fi
