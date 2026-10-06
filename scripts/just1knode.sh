#!/usr/bin/env bash
# =============================================================================
# JUST1KBOT - Обертка обратной совместимости для just1knode
# Делегирует вызовы в /opt/just1knode/just1knode.sh или загружает из just1knode
# =============================================================================
set -euo pipefail

SCRIPT_SOURCE="${BASH_SOURCE[0]:-}"
SCRIPT_DIR=""
if [[ -n "$SCRIPT_SOURCE" && "$SCRIPT_SOURCE" != "bash" && "$SCRIPT_SOURCE" != "-bash" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_SOURCE")" 2>/dev/null && pwd)"
fi

TARGET=""
current_real="$(readlink -f "${BASH_SOURCE[0]:-$0}" 2>/dev/null || echo "${BASH_SOURCE[0]:-$0}")"

if [[ -f "/opt/just1knode/just1knode.sh" && -f "/opt/just1knode/lib/common.sh" ]]; then
    cand="$(readlink -f "/opt/just1knode/just1knode.sh" 2>/dev/null || echo "/opt/just1knode/just1knode.sh")"
    if [[ "$cand" != "$current_real" ]]; then
        TARGET="$cand"
    fi
fi

if [[ -z "$TARGET" || ! -f "$TARGET" ]]; then
    INSTALL_DIR="/opt/just1knode"
    mkdir -p "$INSTALL_DIR"
    JUST1KNODE_REPO_URL="${JUST1KNODE_REPO_URL:-https://github.com/justik13/just1knode}"
    JUST1KNODE_REF="${JUST1KNODE_REF:-main}"
    if [[ "$JUST1KNODE_REF" =~ ^[0-9a-fA-F]{40}$ ]]; then
        archive_url="${JUST1KNODE_REPO_URL}/archive/${JUST1KNODE_REF}.tar.gz"
    else
        archive_url="${JUST1KNODE_REPO_URL}/archive/refs/heads/${JUST1KNODE_REF}.tar.gz"
    fi
    tmp_tar="$(mktemp /tmp/just1knode_boot.XXXXXX.tar.gz 2>/dev/null || mktemp)"
    tmp_extract="$(mktemp -d /tmp/just1knode_extract.XXXXXX 2>/dev/null || mktemp -d)"
    trap 'rm -rf "$tmp_tar" "$tmp_extract"' EXIT INT TERM
    echo -e "\033[0;36m[i]\033[0m Модули just1knode не обнаружены в /opt/just1knode."
    echo -e "\033[0;36m[i]\033[0m Загрузка и распаковка компонентов с GitHub (${JUST1KNODE_REPO_URL} @ ${JUST1KNODE_REF})..."
    download_ok=0
    if command -v curl >/dev/null 2>&1; then
        if curl -fsSL --connect-timeout 15 --max-time 120 "$archive_url" -o "$tmp_tar"; then
            download_ok=1
        fi
    elif command -v wget >/dev/null 2>&1; then
        if wget -q --timeout=120 --tries=2 -O "$tmp_tar" "$archive_url"; then
            download_ok=1
        fi
    fi
    if [[ $download_ok -eq 1 && -f "$tmp_tar" ]]; then
        tar -xzf "$tmp_tar" -C "$tmp_extract" --strip-components=1 --no-same-owner
        if [[ -d "$tmp_extract/just1knode" ]]; then
            cp -r "$tmp_extract/just1knode"/* "$INSTALL_DIR/"
        else
            cp -a "$tmp_extract/just1knode.sh" "$INSTALL_DIR/" 2>/dev/null || true
            cp -a "$tmp_extract/VERSION" "$INSTALL_DIR/" 2>/dev/null || true
            cp -a "$tmp_extract/lib" "$INSTALL_DIR/" 2>/dev/null || true
            cp -a "$tmp_extract/modules" "$INSTALL_DIR/" 2>/dev/null || true
        fi
        if [[ -d "$tmp_extract/scripts/xray_api" ]]; then
            mkdir -p /opt/xray-api
            cp -r "$tmp_extract/scripts/xray_api"/* /opt/xray-api/
        fi
        if [[ -d "$tmp_extract/scripts/amnezia_api" ]]; then
            mkdir -p /opt/amnezia-api
            cp -r "$tmp_extract/scripts/amnezia_api"/* /opt/amnezia-api/
            mkdir -p "$INSTALL_DIR/scripts/amnezia_api"
            cp -r "$tmp_extract/scripts/amnezia_api"/* "$INSTALL_DIR/scripts/amnezia_api/"
        fi
        rm -rf "$tmp_tar" "$tmp_extract"
        trap - EXIT INT TERM
        chmod +x "$INSTALL_DIR/just1knode.sh"
        ln -sf "$INSTALL_DIR/just1knode.sh" /usr/local/bin/just1knode
        TARGET="$INSTALL_DIR/just1knode.sh"
    else
        rm -rf "$tmp_tar" "$tmp_extract"
        trap - EXIT INT TERM
        echo -e "\033[0;31m[✗]\033[0m Ошибка: не удалось скачать архив репозитория ($archive_url)." >&2
        exit 1
    fi
fi

if [[ -z "$TARGET" || ! -f "$TARGET" ]]; then
    echo "Ошибка: Не найден модульный исполняемый файл just1knode" >&2
    exit 1
fi

chmod +x "$TARGET" 2>/dev/null || true

if [[ "${BASH_SOURCE[0]:-}" != "${0:-}" && -n "${BASH_SOURCE[0]:-}" ]]; then
    # Sourced mode
    # shellcheck source=/dev/null
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
