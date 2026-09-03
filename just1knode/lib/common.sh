#!/usr/bin/env bash
# =============================================================================
# JUST1KNODE - Библиотека базовых утилит и функций окружения (lib/common.sh)
# =============================================================================

# Цвета терминала
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log() {
    echo -e "${GREEN}[+]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[!] ВНИМАНИЕ:${NC} $1"
}

error() {
    echo -e "${RED}[✗] ОШИБКА:${NC} $1" >&2
    exit 1
}

info() {
    echo -e "${CYAN}[i]${NC} $1"
}

title() {
    echo -e "\n${BOLD}${BLUE}=== $1 ===${NC}\n"
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        error "Скрипт должен быть запущен с правами root (используйте: sudo just1knode)"
    fi
}

get_arch() {
    local arch
    arch="$(uname -m)"
    case "$arch" in
        x86_64|amd64) echo "64" ;;
        aarch64|arm64) echo "arm64-v8a" ;;
        *) error "Неподдерживаемая архитектура: $arch" ;;
    esac
}

install_base_deps() {
    log "Проверка и установка системных пакетов..."
    apt-get update -qq
    apt-get install -y -qq curl wget unzip jq python3 python3-pip python3-venv ufw openssl
}

# Безопасная настройка UFW с детекцией SSH
configure_safe_ufw() {
    local ports=("$@")
    if ! command -v ufw >/dev/null 2>&1; then
        apt-get install -y -qq ufw
    fi

    # Детектируем порт SSH (Zero-Lockout гарантия)
    local ssh_port=22
    if [[ -f /etc/ssh/sshd_config ]]; then
        local detected
        detected="$(grep -E "^Port " /etc/ssh/sshd_config | awk '{print $2}' | head -n 1 || true)"
        if [[ -n "$detected" ]]; then
            ssh_port="$detected"
        fi
    fi

    ufw allow "$ssh_port/tcp" >/dev/null 2>&1 || true

    for p in "${ports[@]}"; do
        ufw allow "$p" >/dev/null 2>&1 || true
    done

    # Включаем UFW, если он отключен
    if ! ufw status | grep -q "Status: active"; then
        echo "y" | ufw enable >/dev/null 2>&1 || true
        log "Фаервол UFW успешно активирован (SSH порт ${ssh_port} защищен от блокировки)."
    fi
}
