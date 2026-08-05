#!/bin/bash
# Production-safe PostgreSQL restore/cutover engine for Just1kBot.
# This file never handles obsolete rehearsal invocation.

set -Eeuo pipefail
IFS=$'\n\t'
umask 077
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

ENV_FILE=${ENV_FILE:-/opt/just1kbot/.env}
PROJECT_DIR=${PROJECT_DIR:-/opt/just1kbot}
VENV_DIR=${VENV_DIR:-$PROJECT_DIR/venv}
BOT_USER=${BOT_USER:-just1kbot}
SERVICE_NAME=${SERVICE_NAME:-just1kbot}
POSTGRES_LIBRARY=${POSTGRES_LIBRARY:-$PROJECT_DIR/scripts/lib/postgresql.sh}
VERIFY_BACKUP=${VERIFY_BACKUP:-/usr/local/bin/verify_backup.sh}
HEALTHCHECK_COMMAND=${HEALTHCHECK_COMMAND:-/usr/local/bin/just1kbot-healthcheck.sh}
BACKUP_SERVICE=${BACKUP_SERVICE:-just1kbot-backup.service}
BACKUP_DIR=${BACKUP_DIR:-/var/lib/just1kbot/backups}
BACKUP_IDENTITY_FILE=${BACKUP_IDENTITY_FILE:-/etc/just1kbot/backup.agekey}
BACKUP_TIMER=${BACKUP_TIMER:-just1kbot-backup.timer}
HEALTH_TIMER=${HEALTH_TIMER:-just1kbot-healthcheck.timer}
LOCK_FILE=${RESTORE_LOCK_FILE:-/run/lock/just1kbot-deploy.lock}
STATE_DIR=${RESTORE_STATE_DIR:-/var/lib/just1kbot/restore-transactions}
ACTIVE_STATE=${RESTORE_ACTIVE_STATE:-$STATE_DIR/active.env}
JOURNAL_STATE=${RESTORE_JOURNAL_STATE:-$STATE_DIR/cutover-journal.env}
RESTORE_TIMEOUT=${RESTORE_TIMEOUT:-600}
HEALTH_TIMEOUT=${RESTORE_HEALTH_TIMEOUT:-180}
MIN_FREE_MARGIN_BYTES=${RESTORE_FREE_MARGIN_BYTES:-1073741824}
CRITICAL_TABLES=${RESTORE_CRITICAL_TABLES:-users,vpn_profiles,payments,payment_provider_operations,payment_fulfillment_operations,webhook_inbox,payment_events}

ACTION=${1:-}
shift || true
ASSUME_YES=false
EXPECTED_SHA256=""
EXPECTED_TRANSACTION=""
ARTIFACT=""
WORK_DIR=""
POSTGRES_WORK_DIR=""
STAGING_DB=""
ROLLBACK_DB=""
FAILED_DB=""
TRANSACTION_ID=""
ARTIFACT_SHA256=""
BACKUP_CREATED_AT=""
BACKUP_REVISION=""
CODE_HEAD_REVISION=""
PRE_CUTOVER_BACKUP=""
CUTOVER_PHASE="none"
MUTATING_ACTION=false
RECOVERY_ACTION=false
RUNTIME_PAUSED=false
RUNTIME_RESTORED=false
SERVICE_WAS_ACTIVE=false
BACKUP_TIMER_WAS_ACTIVE=false
HEALTH_TIMER_WAS_ACTIVE=false
STATE_STATUS=""
STATE_TRANSACTION_ID=""
STATE_PRODUCTION_DB=""
STATE_ROLLBACK_DB=""
STATE_FAILED_DB=""
STATE_ARTIFACT_NAME=""
STATE_ARTIFACT_SHA256=""
STATE_BACKUP_CREATED_AT=""
STATE_CUTOVER_AT=""
STATE_PRE_CUTOVER_BACKUP=""
JOURNAL_OPERATION=""
JOURNAL_PHASE=""
JOURNAL_TRANSACTION_ID=""
JOURNAL_LIVE_DB=""
JOURNAL_STAGING_DB=""
JOURNAL_ROLLBACK_DB=""
JOURNAL_FAILED_DB=""
JOURNAL_ARTIFACT_NAME=""
JOURNAL_ARTIFACT_SHA256=""
JOURNAL_BACKUP_CREATED_AT=""
JOURNAL_PRE_CUTOVER_BACKUP=""

log() { printf '[restore] %s\n' "$*"; }
warn() { printf '[restore] WARNING: %s\n' "$*" >&2; }
fail() { printf '[restore] ERROR: %s\n' "$*" >&2; return 1; }

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
RESTORE_LIBRARY_DIR=${RESTORE_LIBRARY_DIR:-$(cd -- "$SCRIPT_DIR/../lib" && pwd -P)}
for library in \
    production_restore_core.sh \
    production_restore_runtime.sh \
    production_restore_actions.sh \
    production_restore_input.sh \
    production_restore_crash.sh \
    production_restore_recovery_cleanup.sh; do
    path="$RESTORE_LIBRARY_DIR/$library"
    [[ -f "$path" && ! -L "$path" ]] || {
        printf 'restore error: missing safe library: %s\n' "$path" >&2
        exit 1
    }
    owner=$(stat -c '%u' "$path")
    mode=$(stat -c '%a' "$path")
    (( (8#$mode & 8#022) == 0 )) || {
        printf 'restore error: unsafe library mode: %s\n' "$path" >&2
        exit 1
    }
    if (( EUID == 0 )) && [[ "$owner" != 0 ]]; then
        printf 'restore error: library is not root-owned: %s\n' "$path" >&2
        exit 1
    fi
    # shellcheck disable=SC1090
    source "$path"
done

if [[ "${RESTORE_FUNCTIONS_ONLY:-0}" != 1 ]]; then
    trap cleanup_on_exit EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    main
fi
