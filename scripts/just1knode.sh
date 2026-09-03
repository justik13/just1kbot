#!/usr/bin/env bash
# =============================================================================
# JUST1KBOT - Обертка обратной совместимости для just1knode
# Делегирует вызовы в модульную реализацию just1knode/just1knode.sh
# =============================================================================
set -euo pipefail

SCRIPT_SOURCE="${BASH_SOURCE[0]:-}"
SCRIPT_DIR=""
if [[ -n "$SCRIPT_SOURCE" && "$SCRIPT_SOURCE" != "bash" && "$SCRIPT_SOURCE" != "-bash" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_SOURCE")" 2>/dev/null && pwd)"
fi

TARGET=""
current_real="$(readlink -f "${BASH_SOURCE[0]:-$0}" 2>/dev/null || echo "${BASH_SOURCE[0]:-$0}")"

if [[ -n "$SCRIPT_DIR" && -f "${SCRIPT_DIR}/../just1knode/just1knode.sh" && -f "${SCRIPT_DIR}/../just1knode/lib/common.sh" ]]; then
    cand="$(readlink -f "${SCRIPT_DIR}/../just1knode/just1knode.sh" 2>/dev/null || echo "${SCRIPT_DIR}/../just1knode/just1knode.sh")"
    if [[ "$cand" != "$current_real" ]]; then
        TARGET="$cand"
    fi
fi

if [[ -z "$TARGET" && -f "/opt/just1knode/just1knode.sh" && -f "/opt/just1knode/lib/common.sh" ]]; then
    cand="$(readlink -f "/opt/just1knode/just1knode.sh" 2>/dev/null || echo "/opt/just1knode/just1knode.sh")"
    if [[ "$cand" != "$current_real" ]]; then
        TARGET="$cand"
    fi
fi

if [[ -z "$TARGET" || ! -f "$TARGET" ]]; then
    INSTALL_DIR="/opt/just1knode"
    mkdir -p "$INSTALL_DIR"
    JUST1KBOT_REPO_URL="${JUST1KBOT_REPO_URL:-https://github.com/justik13/just1kbot}"
    JUST1KBOT_REF="${JUST1KBOT_REF:-main}"
    if [[ "$JUST1KBOT_REF" =~ ^[0-9a-fA-F]{40}$ ]]; then
        archive_url="${JUST1KBOT_REPO_URL}/archive/${JUST1KBOT_REF}.tar.gz"
    else
        archive_url="${JUST1KBOT_REPO_URL}/archive/refs/heads/${JUST1KBOT_REF}.tar.gz"
    fi
    tmp_tar="/tmp/just1knode_boot_$$.tar.gz"
    tmp_extract="/tmp/just1knode_extract_$$"
    rm -rf "$tmp_tar" "$tmp_extract"
    mkdir -p "$tmp_extract"
    echo -e "\033[0;36m[i]\033[0m Модули just1knode не обнаружены в /opt/just1knode."
    echo -e "\033[0;36m[i]\033[0m Загрузка и распаковка компонентов с GitHub (${JUST1KBOT_REF})..."
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$archive_url" -o "$tmp_tar"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$tmp_tar" "$archive_url"
    fi
    if [[ -f "$tmp_tar" ]]; then
        tar -xzf "$tmp_tar" -C "$tmp_extract" --strip-components=1
        cp -r "$tmp_extract/just1knode"/* "$INSTALL_DIR/"
        if [[ -d "$tmp_extract/scripts/xray_api" ]]; then
            mkdir -p /opt/xray-api
            cp -r "$tmp_extract/scripts/xray_api"/* /opt/xray-api/
        fi
        rm -rf "$tmp_tar" "$tmp_extract"
        chmod +x "$INSTALL_DIR/just1knode.sh"
        ln -sf "$INSTALL_DIR/just1knode.sh" /usr/local/bin/just1knode
        TARGET="$INSTALL_DIR/just1knode.sh"
    fi
fi

if [[ -z "$TARGET" || ! -f "$TARGET" ]]; then
    echo "Ошибка: Не найден модульный исполняемый файл just1knode" >&2
    exit 1
fi

chmod +x "$TARGET" 2>/dev/null || true

if [[ "${BASH_SOURCE[0]:-}" != "${0:-}" && -n "${BASH_SOURCE[0]:-}" ]]; then
    # Sourced mode
    # shellcheck source=../just1knode/just1knode.sh
    source "$TARGET"
    return 0 2>/dev/null || true
else
    # Executed mode
    if [[ -t 0 ]]; then
        exec "$TARGET" "$@"
    elif (exec </dev/tty) 2>/dev/null; then
        exec "$TARGET" "$@" </dev/tty
    else
        exec "$TARGET" "$@"
    fi
fi
