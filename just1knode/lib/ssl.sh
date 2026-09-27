#!/usr/bin/env bash
# =============================================================================
# JUST1KNODE - Библиотека управления SSL и маскировкой (lib/ssl.sh)
# =============================================================================

CERTBOT_DIR="${CERTBOT_DIR:-/var/www/certbot}"
WWW_HTML_DIR="${WWW_HTML_DIR:-/var/www/html}"
NGINX_CONF_DIR="${NGINX_CONF_DIR:-/etc/nginx}"

obtain_ssl_certificate() {
    local domain="$1"
    local email="$2"

    log "Получение SSL-сертификата Let's Encrypt для ${domain}..."
    mkdir -p "${CERTBOT_DIR}"

    if ! certbot certonly --webroot -w "${CERTBOT_DIR}" \
        -d "${domain}" \
        --email "${email}" \
        --agree-tos \
        --no-eff-email \
        --non-interactive \
        --keep-until-expiring; then
        error "Не удалось выпустить SSL-сертификат для ${domain}. Проверьте DNS A-запись и доступность порта 80."
    fi

    log "SSL-сертификат для ${domain} успешно получен!"
}

LETSENCRYPT_DIR="${LETSENCRYPT_DIR:-/etc/letsencrypt}"

deploy_camouflage_site() {
    # Zero-Signature Standard: удаление устаревших веб-заглушек для исключения детекции сканерами
    local www_index="${WWW_HTML_DIR:-/var/www/html}/index.html"
    if [[ -f "$www_index" ]] && (grep -q "Cloud Ingress Network Node" "$www_index" 2>/dev/null || grep -q "SimpleCalc" "$www_index" 2>/dev/null); then
        rm -f "$www_index" 2>/dev/null || true
    fi
}

deploy_certbot_renewal_hook() {
    mkdir -p "${LETSENCRYPT_DIR}/renewal-hooks/deploy"
    cat > "${LETSENCRYPT_DIR}/renewal-hooks/deploy/restart-xray-nginx.sh" <<'EOF'
#!/bin/bash
systemctl reload nginx 2>/dev/null || true
if [[ ! -f /etc/just1knode/traffic_cutoff.active ]]; then
    systemctl restart xray 2>/dev/null || true
    systemctl restart xray-api 2>/dev/null || true
fi
EOF
    chmod +x "${LETSENCRYPT_DIR}/renewal-hooks/deploy/restart-xray-nginx.sh"
}
