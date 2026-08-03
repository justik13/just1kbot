#!/bin/bash
# Shared-host package policy: install only missing packages and restore global Redis service state.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

package_is_installed() {
    dpkg-query -W -f='${db:Status-Status}' "$1" 2>/dev/null | grep -qx installed
}

capture_unit_state() {
    local unit=$1 enabled active
    enabled=$(systemctl is-enabled "$unit" 2>/dev/null || true)
    active=$(systemctl is-active "$unit" 2>/dev/null || true)
    printf '%s\t%s\n' "${enabled:-not-found}" "${active:-inactive}"
}

restore_unit_state() {
    local unit=$1 expected_enabled=$2 expected_active=$3 current
    if [[ "$expected_active" != active ]] && systemctl is-active --quiet "$unit" 2>/dev/null; then
        systemctl stop "$unit" || foundation_warn "Не удалось остановить package-created $unit"
    fi
    current=$(systemctl is-enabled "$unit" 2>/dev/null || true)
    if [[ "$expected_enabled" == not-found || "$expected_enabled" == disabled ]]; then
        case "$current" in
            enabled|enabled-runtime)
                systemctl disable "$unit" >/dev/null 2>&1 || foundation_warn "Не удалось disable package-created $unit"
                ;;
        esac
    fi
}

install_dependencies() {
    resolve_proxy_mode
    installer_set_step 'Установка отсутствующих пакетов' 'Существующие package/service state не обновляются без необходимости.'
    command -v apt-get >/dev/null 2>&1 || {
        error 'apt-get отсутствует'
        return 1
    }
    command -v dpkg-query >/dev/null 2>&1 || {
        error 'dpkg-query отсутствует'
        return 1
    }

    local -a packages=(
        python3 python3-venv python3-pip python3-dev
        postgresql postgresql-contrib
        redis-server redis-tools
        age curl git rsync build-essential libpq-dev logrotate util-linux iproute2
    )
    if [[ "$PROXY_MODE" == managed ]]; then
        packages+=(nginx certbot)
    fi

    local -a missing=()
    local package
    for package in "${packages[@]}"; do
        package_is_installed "$package" || missing+=("$package")
    done
    if (( ${#missing[@]} == 0 )); then
        foundation_log 'Все обязательные packages уже установлены; apt package state не изменяется.'
        return 0
    fi

    local redis_was_installed=false redis_enabled redis_active
    package_is_installed redis-server && redis_was_installed=true
    IFS=$'\t' read -r redis_enabled redis_active < <(capture_unit_state redis-server.service)

    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
        "${missing[@]}" >/dev/null

    if [[ "$redis_was_installed" == false ]]; then
        # redis-server package may start its generic system instance. Just1kBot
        # uses only just1kbot-redis.service and restores the previous absent/
        # inactive state of the generic service immediately.
        restore_unit_state redis-server.service "$redis_enabled" "$redis_active"
        restore_unit_state redis.service "$redis_enabled" "$redis_active"
    fi
}

validate_runtime_commands() {
    resolve_proxy_mode
    local -a commands=(
        python3 rsync systemctl stat git flock runuser age age-keygen
        pg_dump pg_restore psql sha256sum timeout pg_lsclusters pg_ctlcluster
        pg_isready redis-server redis-cli ss apt-get dpkg-query
    )
    if [[ "$PROXY_MODE" == managed ]]; then
        commands+=(nginx certbot)
    fi
    local command
    for command in "${commands[@]}"; do
        command_required "$command"
    done
    python3 -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,12) else 1)' || {
        error 'Требуется Python 3.12 или новее'
        return 1
    }
    command -v systemd-run >/dev/null 2>&1 || {
        error 'systemd-run отсутствует: systemd capability не подтверждена'
        return 1
    }
}

if [[ "${INSTALL_SAFE_PACKAGE_POLICY_SOURCE_ONLY:-0}" != 1 &&
      "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'install_safe_package_policy.sh is source-only\n' >&2
    exit 64
fi
