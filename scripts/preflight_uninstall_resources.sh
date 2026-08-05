#!/bin/bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

PROJECT_DIR=/opt/just1kbot
ENV_FILE=$PROJECT_DIR/.env

fail() {
    printf 'ОШИБКА uninstall resource preflight: %s\n' "$*" >&2
    exit 1
}

path_exists() {
    [[ -e "$1" || -L "$1" ]]
}

assert_managed_unit() {
    local path=$1
    path_exists "$path" || return 0
    [[ -f "$path" && ! -L "$path" ]] ||
        fail "systemd resource имеет небезопасный тип: $path"
    grep -Eq 'Just1kBot|/opt/just1kbot|/usr/local/bin/just1kbot' "$path" ||
        fail "systemd resource не содержит ownership markers Just1kBot: $path"
}

assert_root_tool() {
    local path=$1 owner group mode
    path_exists "$path" || return 0
    [[ -f "$path" && ! -L "$path" ]] ||
        fail "operational tool имеет небезопасный тип: $path"
    IFS=' ' read -r owner group mode < <(stat -c '%U %G %a' "$path")
    [[ "$owner" == root && "$group" == root ]] ||
        fail "operational tool имеет неожиданного владельца: $path owner=$owner:$group"
    (( (8#$mode & 8#022) == 0 )) ||
        fail "operational tool writable для group/other: $path mode=$mode"
    grep -Eiq 'Just1kBot|just1kbot' "$path" ||
        fail "operational tool не содержит ownership marker Just1kBot: $path"
}

read_webhook_config() {
    [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || return 0
    ENV_FILE_PATH=$ENV_FILE python3 - <<'PY'
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

if counts.get("DOMAIN", 0) > 1 or counts.get("YOOKASSA_WEBHOOK_PORT", 0) > 1:
    raise SystemExit("duplicate DOMAIN or YOOKASSA_WEBHOOK_PORT")

domain = values.get("DOMAIN", "").lower().rstrip(".")
port = values.get("YOOKASSA_WEBHOOK_PORT", "8080")
if domain:
    label = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
    if len(domain) > 253 or len(domain.split(".")) < 2 or any(
        not label.fullmatch(part) for part in domain.split(".")
    ):
        raise SystemExit("unsafe DOMAIN")
if not port.isdigit() or not 1 <= int(port) <= 65535:
    raise SystemExit("unsafe YOOKASSA_WEBHOOK_PORT")
print(domain)
print(int(port))
PY
}

assert_nginx_site() {
    local output domain port available enabled
    output=$(read_webhook_config) || fail "production .env не прошёл безопасную проверку: $ENV_FILE"
    local -a values=()
    mapfile -t values <<<"$output"
    domain=${values[0]:-}
    port=${values[1]:-8080}
    [[ -n "$domain" ]] || return 0

    available="/etc/nginx/sites-available/$domain"
    enabled="/etc/nginx/sites-enabled/$domain"

    if path_exists "$available"; then
        [[ -f "$available" && ! -L "$available" ]] ||
            fail "Nginx site имеет небезопасный тип: $available"
        grep -Fq "server_name ${domain};" "$available" ||
            fail "Nginx site не содержит ожидаемый server_name: $available"
        grep -Fq 'location = /webhook/yookassa' "$available" ||
            fail "Nginx site не содержит webhook marker Just1kBot: $available"
        grep -Fq "proxy_pass http://127.0.0.1:${port}/webhook/yookassa;" "$available" ||
            fail "Nginx site не содержит ожидаемый internal upstream: $available"
    fi

    if path_exists "$enabled"; then
        [[ -L "$enabled" ]] || fail "enabled Nginx site не является symlink: $enabled"
        [[ "$(readlink -f -- "$enabled")" == "$(realpath -m -- "$available")" ]] ||
            fail "enabled Nginx symlink ведёт не на ожидаемый site: $enabled"
    fi
}

main() {
    [[ ${EUID:-$(id -u)} -eq 0 ]] || fail 'запустите от root'

    local path
    for path in \
        /etc/systemd/system/just1kbot.service \
        /etc/systemd/system/just1kbot-backup.service \
        /etc/systemd/system/just1kbot-healthcheck.service \
        /etc/systemd/system/just1kbot-backup.timer \
        /etc/systemd/system/just1kbot-healthcheck.timer \
        /etc/systemd/system/just1kbot-traffic.service \
        /etc/systemd/system/just1kbot-notifications.service \
        /etc/systemd/system/just1kbot-cleanup.service \
        /etc/systemd/system/just1kbot-stale-payments.service \
        /etc/systemd/system/just1kbot-heartbeat.service; do
        assert_managed_unit "$path"
    done

    for path in \
        /usr/local/bin/just1kbot-backup.sh \
        /usr/local/bin/just1kbot-restore.sh \
        /usr/local/bin/just1kbot-healthcheck.sh \
        /usr/local/bin/verify_backup.sh \
        /usr/local/bin/restore_rehearsal.sh \
        /usr/local/bin/just1kbot; do
        assert_root_tool "$path"
    done

    assert_nginx_site
    printf 'Uninstall resource preflight пройден: удаляемые systemd/tools/Nginx ресурсы имеют ожидаемые markers.\n'
}

main "$@"
