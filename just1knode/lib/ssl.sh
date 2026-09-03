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
    mkdir -p "${WWW_HTML_DIR}"
    if [[ ! -f "${WWW_HTML_DIR}/index.html" ]]; then
        cat > "${WWW_HTML_DIR}/index.html" <<'EOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CDN Edge Gateway</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
        .card { background: #1e293b; padding: 2.5rem; border-radius: 1rem; box-shadow: 0 10px 25px rgba(0,0,0,0.5); text-align: center; max-width: 480px; }
        h1 { font-size: 1.5rem; margin-bottom: 0.5rem; color: #38bdf8; }
        p { color: #94a3b8; font-size: 0.95rem; line-height: 1.5; }
        .badge { display: inline-block; background: #0284c7; color: white; padding: 0.25rem 0.75rem; border-radius: 9999px; font-size: 0.75rem; font-weight: bold; margin-top: 1rem; }
    </style>
</head>
<body>
    <div class="card">
        <h1>Cloud Ingress Network Node</h1>
        <p>Enterprise high-performance edge acceleration and static caching proxy.</p>
        <span class="badge">Operational • 99.99% Uptime</span>
    </div>
</body>
</html>
EOF
        log "Развернут камуфляжный сайт в ${WWW_HTML_DIR}/index.html"
    fi
}

deploy_certbot_renewal_hook() {
    mkdir -p "${LETSENCRYPT_DIR}/renewal-hooks/deploy"
    cat > "${LETSENCRYPT_DIR}/renewal-hooks/deploy/restart-xray-nginx.sh" <<'EOF'
#!/bin/bash
systemctl reload nginx 2>/dev/null || true
systemctl restart xray 2>/dev/null || true
systemctl restart xray-api 2>/dev/null || true
EOF
    chmod +x "${LETSENCRYPT_DIR}/renewal-hooks/deploy/restart-xray-nginx.sh"
}
