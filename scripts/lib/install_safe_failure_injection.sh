#!/bin/bash
# Deterministic failure injection used only by tests and controlled rehearsals.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

installer_failpoint() {
    local point=$1 configured=${JUST1KBOT_FAILPOINT:-}
    [[ -z "$configured" ]] && return 0
    [[ "$configured" == "$point" ]] || return 0
    foundation_fail INJECTED_FAILURE \
        'выполнена запрошенная failure injection' \
        "failpoint=$point" \
        'Это контролируемый тестовый сбой. Проверьте journal, recovery и rollback.'
}

if [[ "${INSTALL_SAFE_FAILURE_INJECTION_SOURCE_ONLY:-0}" != 1 &&
      "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'install_safe_failure_injection.sh is source-only\n' >&2
    exit 64
fi
