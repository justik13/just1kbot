#!/usr/bin/env bash
# =============================================================================
# JUST1KBOT - Обертка обратной совместимости для just1knode
# Делегирует вызовы в модульную реализацию just1knode/just1knode.sh
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${SCRIPT_DIR}/../just1knode/just1knode.sh"

if [[ ! -f "$TARGET" ]]; then
    echo "Ошибка: Не найден модульный исполняемый файл just1knode по адресу: $TARGET" >&2
    exit 1
fi

chmod +x "$TARGET" 2>/dev/null || true

if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
    # Sourced mode
    # shellcheck source=../just1knode/just1knode.sh
    source "$TARGET"
    return 0 2>/dev/null || true
else
    # Executed mode
    exec "$TARGET" "$@"
fi
