#!/bin/bash
# Capability-oriented platform policy. Ubuntu 24.04 remains primary CI target.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

platform_identifier() {
    [[ -f /etc/os-release && ! -L /etc/os-release ]] || {
        printf 'unknown-unknown\n'
        return
    }
    # shellcheck disable=SC1091
    . /etc/os-release
    printf '%s-%s\n' "${ID:-unknown}" "${VERSION_ID:-unknown}"
}

validate_supported_os() {
    local id=${1:-} version=${2:-}
    case "$id" in
        ubuntu|debian) ;;
        *)
            error "Installer требует apt/systemd-совместимую Ubuntu или Debian; обнаружено ${id:-unknown} ${version:-unknown}"
            return 1
            ;;
    esac
    [[ -n "$version" ]] || {
        error 'VERSION_ID отсутствует; capability preflight не может доказать совместимость'
        return 1
    }
    if [[ "$id" == ubuntu && "$version" == 24.04 ]]; then
        return 0
    fi
    foundation_warn "${id} ${version}: совместимая, но не основная протестированная платформа; Ubuntu 24.04 является primary CI target."
}

foundation_exact_ubuntu_2404() {
    [[ -f /etc/os-release && ! -L /etc/os-release ]] || foundation_fail \
        UNSUPPORTED_OS 'не удалось определить ОС' '/etc/os-release отсутствует или небезопасен' \
        'Используйте apt/systemd-совместимую Ubuntu или Debian.'
    # shellcheck disable=SC1091
    . /etc/os-release
    validate_supported_os "${ID:-}" "${VERSION_ID:-}" || foundation_fail \
        UNSUPPORTED_OS \
        'ОС не прошла platform policy' \
        "ID=${ID:-unknown} VERSION_ID=${VERSION_ID:-unknown}" \
        'Используйте Ubuntu/Debian с systemd, apt и Python 3.12; Ubuntu 24.04 рекомендуется.'
}

platform_manifest_definition=$(declare -f foundation_manifest_create)
platform_manifest_definition=${platform_manifest_definition/#"foundation_manifest_create ()"/"platform_base_manifest_create ()"}
eval "$platform_manifest_definition"
unset platform_manifest_definition

foundation_manifest_create() {
    platform_base_manifest_create
    local platform support
    platform=$(platform_identifier)
    support=compatible
    [[ "$platform" == ubuntu-24.04 ]] && support=primary-tested
    foundation_manifest_set_metadata platform "$platform"
    foundation_manifest_set_metadata platform_support "$support"
}

if [[ "${INSTALL_SAFE_PLATFORM_SUPPORT_SOURCE_ONLY:-0}" != 1 &&
      "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'install_safe_platform_support.sh is source-only\n' >&2
    exit 64
fi
