#!/bin/bash
# Strict migration of a verified legacy Just1kBot installation into ownership manifest.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

clone_function ensure_manifest foundation_base_ensure_manifest

legacy_env_webhook() {
    ENV_FILE_PATH="$ENV_FILE" python3 - <<'PY'
import os
import re
from pathlib import Path

values = {}
counts = {}
for raw in Path(os.environ["ENV_FILE_PATH"]).read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    key = key.strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    counts[key] = counts.get(key, 0) + 1
    values[key] = value.strip()

if counts.get("DOMAIN") != 1 or counts.get("YOOKASSA_WEBHOOK_PORT", 0) > 1:
    raise SystemExit("expected exactly one DOMAIN and at most one webhook port")
domain = values["DOMAIN"].lower().rstrip(".")
port = values.get("YOOKASSA_WEBHOOK_PORT", "8080")
label = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
if len(domain) > 253 or len(domain.split(".")) < 2 or any(
    label.fullmatch(part) is None for part in domain.split(".")
):
    raise SystemExit("unsafe DOMAIN")
if not port.isdigit() or not 1 <= int(port) <= 65535:
    raise SystemExit("unsafe webhook port")
print(domain)
print(port)
PY
}

legacy_adopt_nginx_and_certificate() {
    [[ "$INITIAL_INSTALL" == false ]] || return 0

    local output domain port available enabled renewal
    output=$(legacy_env_webhook) || {
        error 'Legacy .env не прошёл строгую проверку DOMAIN/webhook port'
        return 1
    }
    local -a values=()
    mapfile -t values <<<"$output"
    domain=${values[0]}
    port=${values[1]}
    available="/etc/nginx/sites-available/$domain"
    enabled="/etc/nginx/sites-enabled/$domain"

    if [[ -e "$available" || -L "$available" || -e "$enabled" || -L "$enabled" ]]; then
        [[ -f "$available" && ! -L "$available" ]] || {
            error "Legacy Nginx site имеет небезопасный тип: $available"
            return 1
        }
        grep -Fq "server_name ${domain};" "$available" || {
            error "Legacy Nginx site не содержит точный server_name: $domain"
            return 1
        }
        grep -Fq 'location = /webhook/yookassa' "$available" || {
            error 'Legacy Nginx site не содержит Just1kBot webhook marker'
            return 1
        }
        grep -Fq "proxy_pass http://127.0.0.1:${port}/webhook/yookassa;" "$available" || {
            error 'Legacy Nginx site указывает на неожиданный upstream'
            return 1
        }
        [[ -L "$enabled" ]] || {
            error "Legacy enabled Nginx site не является symlink: $enabled"
            return 1
        }
        [[ "$(readlink -f -- "$enabled")" == "$(realpath -m -- "$available")" ]] || {
            error 'Legacy enabled Nginx symlink ведёт не на ожидаемый site'
            return 1
        }
        foundation_manifest_add "nginx-site:$domain"
        foundation_manifest_add "nginx-enabled:$domain"
    fi

    renewal="/etc/letsencrypt/renewal/$domain.conf"
    if [[ -e "/etc/letsencrypt/live/$domain" || -L "/etc/letsencrypt/live/$domain" || -e "$renewal" || -L "$renewal" ]]; then
        [[ -f "$renewal" && ! -L "$renewal" ]] || {
            error "Legacy certificate renewal config отсутствует или небезопасен: $renewal"
            return 1
        }
        grep -Fq "archive_dir = /etc/letsencrypt/archive/$domain" "$renewal" || {
            error 'Legacy certificate renewal config указывает на неожиданный archive'
            return 1
        }
        [[ -f "/etc/letsencrypt/live/$domain/fullchain.pem" && -f "/etc/letsencrypt/live/$domain/privkey.pem" ]] || {
            error 'Legacy certificate не содержит fullchain.pem/privkey.pem'
            return 1
        }
        foundation_manifest_add "certbot:$domain"
    fi
}

legacy_register_preserved_state() {
    [[ "$INITIAL_INSTALL" == false ]] || return 0
    local path
    for path in \
        "$BACKUP_DIR" "$SNAPSHOT_DIR" "$BACKUP_CONF" "$BACKUP_IDENTITY" \
        /var/lib/just1kbot/restore-transactions \
        /var/lib/just1kbot/source-releases; do
        [[ ! -e "$path" && ! -L "$path" ]] || foundation_manifest_add "path:$path"
    done
}

ensure_manifest() {
    local had_manifest=false
    foundation_manifest_validate && had_manifest=true
    foundation_base_ensure_manifest || return 1
    if [[ "$had_manifest" == false && "$INITIAL_INSTALL" == false ]]; then
        legacy_adopt_nginx_and_certificate || return 1
        legacy_register_preserved_state
        foundation_manifest_set_metadata legacy_migrated true
    fi
}

if [[ "${INSTALL_SAFE_LEGACY_SOURCE_ONLY:-0}" != 1 && "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'install_safe_legacy.sh is source-only\n' >&2
    exit 64
fi
