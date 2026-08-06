#!/usr/bin/env bash
set -Eeuo pipefail
umask 027

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

REPO_OWNER="${REPO_OWNER:-justik13}"
REPO_NAME="${REPO_NAME:-just1kbot}"
REPO_BRANCH="${REPO_BRANCH:-bot}"
SELF_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$SELF_PATH")" && pwd)"
LOCAL_ENTRYPOINT="$SCRIPT_DIR/installer/entrypoint.sh"

if [[ -f "$LOCAL_ENTRYPOINT" ]]; then
    exec bash "$LOCAL_ENTRYPOINT" "$@"
fi

command -v curl >/dev/null 2>&1 || {
    printf '[ERROR] curl is required to bootstrap just1kbot.\n' >&2
    exit 1
}
command -v tar >/dev/null 2>&1 || {
    printf '[ERROR] tar is required to bootstrap just1kbot.\n' >&2
    exit 1
}

TMP_DIR="$(mktemp -d -t just1kbot-bootstrap.XXXXXX)"
cleanup() { rm -rf -- "$TMP_DIR"; }
trap cleanup EXIT INT TERM HUP

ARCHIVE="$TMP_DIR/source.tar.gz"
printf '[*] Downloading just1kbot installer (%s)...\n' "$REPO_BRANCH" >&2
curl --fail --location --silent --show-error \
    --proto '=https' --tlsv1.2 \
    --connect-timeout 15 --max-time 180 --retry 3 \
    "https://codeload.github.com/${REPO_OWNER}/${REPO_NAME}/tar.gz/${REPO_BRANCH}" \
    --output "$ARCHIVE"
tar -xzf "$ARCHIVE" -C "$TMP_DIR"
SOURCE_DIR="$(find "$TMP_DIR" -mindepth 1 -maxdepth 1 -type d -print -quit)"
ENTRYPOINT="$SOURCE_DIR/installer/entrypoint.sh"
[[ -f "$ENTRYPOINT" ]] || {
    printf '[ERROR] Downloaded archive does not contain installer/entrypoint.sh.\n' >&2
    exit 1
}

bash "$ENTRYPOINT" "$@"
