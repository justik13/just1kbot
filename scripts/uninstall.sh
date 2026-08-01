#!/bin/bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

BOT_USER=just1kbot
PROJECT_DIR=/opt/just1kbot
BACKUP_DIR=/root/backups/just1kbot
BACKUP_CONF=/etc/just1kbot-backup.conf
AGE_IDENTITY_DEFAULT=/root/.config/just1kbot/backup.agekey
SNAPSHOT_DIR=/var/lib/just1kbot/rollback-releases
RESTORE_STATE_DIR=/var/lib/just1kbot/restore-transactions
RESTORE_ACTIVE_STATE=$RESTORE_STATE_DIR/active.env
RESTORE_JOURNAL_STATE=$RESTORE_STATE_DIR/cutover-journal.env
LOCK_FILE=/run/lock/just1kbot-deploy.lock
STATE_FILE=/etc/just1kbot-amnezia.conf
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PG_LIB="$SCRIPT_DIR/lib/postgresql.sh"
MODE=

fail(){ printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }
log(){ printf '[uninstall] %s\n' "$*"; }
usage(){ cat <<'TXT'
Использование:
  sudo bash scripts/uninstall.sh
  sudo bash scripts/uninstall.sh --keep-data
  sudo bash scripts/uninstall.sh --purge-data

Без аргументов открывается безопасное интерактивное меню.

--keep-data  удаляет приложение, но сохраняет PostgreSQL data и encrypted backups;
             перед удалением создаёт и проверяет новый backup.
--purge-data удаляет приложение, все Just1kBot production/restore databases,
             role, Redis keys, backups и age identity;
             требует точную фразу DELETE JUST1KBOT.

При незавершённой restore-транзакции uninstall запрещён: сначала выполните
restore recover, затем rollback или finalize.
Глобальные пакеты PostgreSQL, Redis, Nginx, Certbot и Python не удаляются.
TXT
}

choose_mode(){
  [[ -t 0 ]] || fail 'укажите --keep-data или --purge-data'
  printf '\nВыберите режим удаления:\n'
  printf '  1. Удалить приложение, сохранить данные\n'
  printf '  2. Полностью удалить данные Just1kBot\n'
  printf '  0. Отмена\n\n'
  local choice
  read -rp 'Выбор: ' choice
  case "$choice" in
    1) MODE=keep;;
    2) MODE=purge;;
    0) exit 0;;
    *) fail 'неизвестный пункт меню';;
  esac
}

parse(){
  case $# in
    0) choose_mode;;
    1)
      case "$1" in
        --keep-data) MODE=keep;;
        --purge-data) MODE=purge;;
        -h|--help) usage; exit 0;;
        *) usage >&2; exit 2;;
      esac
      ;;
    *) usage >&2; exit 2;;
  esac
}

safe_path(){
  [[ "$PROJECT_DIR" == /opt/just1kbot && "$PROJECT_DIR" != *'..'* ]] || fail 'unsafe PROJECT_DIR'
  [[ ! -L "$PROJECT_DIR" ]] || fail 'PROJECT_DIR is a symlink'
  [[ "$(realpath -m -- "$PROJECT_DIR")" == "$(realpath -e -- /opt)/just1kbot" ]] ||
    fail 'canonical PROJECT_DIR mismatch'
}

preflight_restore_state(){
  [[ ! -L "$RESTORE_STATE_DIR" ]] || fail 'restore state directory is a symlink'
  if [[ -e "$RESTORE_JOURNAL_STATE" || -L "$RESTORE_JOURNAL_STATE" ]]; then
    fail 'interrupted production restore exists; run restore recover before uninstall'
  fi
  if [[ -e "$RESTORE_ACTIVE_STATE" || -L "$RESTORE_ACTIVE_STATE" ]]; then
    fail 'pending production restore exists; run rollback or finalize before uninstall'
  fi
}

lock_all(){
  install -d -o root -g root -m 0755 "$(dirname "$LOCK_FILE")"
  exec 200>"$LOCK_FILE"
  flock -n 200 || fail 'deploy/backup/restore is already running'
  systemctl stop just1kbot-backup.timer 2>/dev/null || true
  systemctl stop just1kbot-healthcheck.timer 2>/dev/null || true
  ! systemctl is-active --quiet just1kbot-backup.timer 2>/dev/null || fail 'backup timer did not stop'
  ! systemctl is-active --quiet just1kbot-healthcheck.timer 2>/dev/null || fail 'health timer did not stop'
  local end=$(( $(date +%s)+180 ))
  while systemctl is-active --quiet just1kbot-backup.service 2>/dev/null; do
    (( $(date +%s) <= end )) || fail 'active backup timeout'
    sleep 2
  done
}

latest_backup(){
  find "$BACKUP_DIR" -maxdepth 1 -type f -name 'just1kbot-pg-v1-*.tar.age' \
    -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-
}

backup_before_keep(){
  local identity=${AGE_IDENTITY_FILE:-$AGE_IDENTITY_DEFAULT} start artifact
  [[ -x /usr/local/bin/just1kbot-backup.sh ]] || fail 'backup tool is missing'
  [[ -x /usr/local/bin/verify_backup.sh ]] || fail 'backup verifier is missing'
  [[ -f "$BACKUP_CONF" && ! -L "$BACKUP_CONF" ]] || fail 'unsafe backup config'
  [[ -f "$identity" && ! -L "$identity" ]] ||
    fail 'set AGE_IDENTITY_FILE for --keep-data'

  start=$(date +%s)
  systemctl --wait start just1kbot-backup.service
  artifact=$(latest_backup)
  [[ -n "$artifact" && -s "$artifact" && -s "$artifact.sha256" ]] ||
    fail 'new backup was not published'
  (( $(stat -c %Y "$artifact") >= start )) ||
    fail 'backup is not from this operation'

  AGE_IDENTITY_FILE="$identity" /usr/local/bin/verify_backup.sh "$artifact"
  log "verified backup: $artifact"
}

confirm_purge(){
  local answer
  read -rp 'Введите точную фразу DELETE JUST1KBOT: ' answer
  [[ "$answer" == 'DELETE JUST1KBOT' ]] || fail 'confirmation mismatch'
}

stop_units(){
  local unit
  for unit in \
    just1kbot-healthcheck.timer \
    just1kbot-backup.timer \
    just1kbot-healthcheck.service \
    just1kbot-backup.service \
    just1kbot.service \
    just1kbot-traffic.service \
    just1kbot-notifications.service \
    just1kbot-cleanup.service \
    just1kbot-stale-payments.service \
    just1kbot-heartbeat.service; do
    systemctl stop "$unit" 2>/dev/null || true
    systemctl disable "$unit" 2>/dev/null || true
  done
  id "$BOT_USER" >/dev/null 2>&1 &&
    pkill -TERM -u "$BOT_USER" 2>/dev/null || true
}

read_safe_domain(){
  [[ -f "$PROJECT_DIR/.env" && ! -L "$PROJECT_DIR/.env" ]] || return 0
  ENV_FILE_PATH="$PROJECT_DIR/.env" python3 - <<'PY'
import os
import re
from pathlib import Path

value = ""
for raw in Path(os.environ["ENV_FILE_PATH"]).read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, current = line.split("=", 1)
    if key.strip() != "DOMAIN":
        continue
    if value:
        raise SystemExit("duplicate DOMAIN")
    current = current.strip()
    if len(current) >= 2 and current[0] == current[-1] and current[0] in {"'", '"'}:
        current = current[1:-1]
    value = current.strip().lower().rstrip(".")

if not value:
    raise SystemExit(0)

label = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
if len(value) > 253 or len(value.split(".")) < 2 or any(
    not label.fullmatch(part) for part in value.split(".")
):
    raise SystemExit("unsafe DOMAIN")
print(value)
PY
}

redis_connection(){
  [[ -f "$PROJECT_DIR/.env" && ! -L "$PROJECT_DIR/.env" ]] ||
    fail 'cannot purge Redis keys without a safe production .env'

  ENV_FILE_PATH="$PROJECT_DIR/.env" python3 - <<'PY'
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

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
    values[key] = value

if counts.get('REDIS_URL') != 1:
    raise SystemExit("expected exactly one REDIS_URL")
parsed = urlsplit(values['REDIS_URL'])
if parsed.scheme != "redis" or parsed.hostname not in {"127.0.0.1", "localhost"}:
    raise SystemExit("REDIS_URL must target local redis://")
if parsed.username not in {None, ""}:
    raise SystemExit("unexpected Redis username")
try:
    port = parsed.port or 6379
except ValueError as exc:
    raise SystemExit("invalid Redis port") from exc

database_text = parsed.path.lstrip("/") or "0"
if not database_text.isdigit():
    raise SystemExit("invalid Redis database")
database = int(database_text)
if not (1 <= port <= 65535 and 0 <= database <= 15):
    raise SystemExit("Redis endpoint out of range")
prefix = values.get('REDIS_KEY_PREFIX', 'just1kbot_bot:')
if not re.fullmatch(r'[A-Za-z0-9:_-]{1,128}', prefix):
    raise SystemExit('invalid REDIS_KEY_PREFIX')

print(parsed.hostname)
print(port)
print(database)
print(unquote(parsed.password or ""))
print(prefix)
PY
}

purge_redis(){
  command -v redis-cli >/dev/null 2>&1 || fail 'redis-cli is required to purge data'

  local -a connection
  mapfile -t connection < <(redis_connection)
  (( ${#connection[@]} == 5 )) || fail 'failed to parse REDIS_URL'

  local host=${connection[0]} port=${connection[1]} database=${connection[2]}
  local password=${connection[3]} prefix=${connection[4]} output key deleted=0

  if [[ -n "$password" ]]; then
    REDISCLI_AUTH="$password" redis-cli -h "$host" -p "$port" -n "$database" PING |
      grep -qx PONG || fail 'Redis PING failed'
    output=$(REDISCLI_AUTH="$password" redis-cli -h "$host" -p "$port" -n "$database" \
      --scan --pattern "${prefix}*") || fail 'Redis scan failed'
  else
    redis-cli -h "$host" -p "$port" -n "$database" PING |
      grep -qx PONG || fail 'Redis PING failed'
    output=$(redis-cli -h "$host" -p "$port" -n "$database" \
      --scan --pattern "${prefix}*") || fail 'Redis scan failed'
  fi

  while IFS= read -r key; do
    [[ -n "$key" ]] || continue
    if [[ -n "$password" ]]; then
      REDISCLI_AUTH="$password" redis-cli -h "$host" -p "$port" -n "$database" \
        DEL "$key" >/dev/null || fail 'Redis key deletion failed'
    else
      redis-cli -h "$host" -p "$port" -n "$database" \
        DEL "$key" >/dev/null || fail 'Redis key deletion failed'
    fi
    deleted=$((deleted+1))
  done <<<"$output"

  log "purged Redis keys: $deleted"
}

purge_db(){
  [[ -f "$PG_LIB" && ! -L "$PG_LIB" ]] || fail 'PostgreSQL library is missing'
  # shellcheck source=lib/postgresql.sh
  source "$PG_LIB"
  pg_select_cluster
  pg_start_cluster

  local -a databases
  local database
  mapfile -t databases < <(
    pg_admin_psql_on_port "$PG_PORT" -v main_database="$PG_DATABASE" <<'SQL'
SELECT datname
FROM pg_database
WHERE datname = :'main_database'
   OR datname ~ '^just1kbot_(stg|rb|fail)_[0-9]{14}_[0-9]+$'
ORDER BY datname;
SQL
  )

  for database in "${databases[@]}"; do
    [[ "$database" == "$PG_DATABASE" || "$database" =~ ^just1kbot_(stg|rb|fail)_[0-9]{14}_[0-9]+$ ]] ||
      fail "refusing to purge unexpected database: $database"
    pg_admin_psql_on_port "$PG_PORT" -v database_name="$database" >/dev/null <<'SQL'
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = :'database_name'
  AND pid <> pg_backend_pid();
SQL
    runuser -u postgres -- dropdb \
      -h "$PG_SOCKET_DIR" -p "$PG_PORT" \
      --if-exists --maintenance-db=postgres "$database"
  done

  local remaining
  remaining=$(pg_admin_psql_on_port "$PG_PORT" -v main_database="$PG_DATABASE" <<'SQL'
SELECT count(*)
FROM pg_database
WHERE datname = :'main_database'
   OR datname ~ '^just1kbot_(stg|rb|fail)_[0-9]{14}_[0-9]+$';
SQL
  )
  [[ "$remaining" == 0 ]] || fail 'one or more Just1kBot databases still exist'

  pg_admin_psql_on_port "$PG_PORT" -v role_name="$PG_ROLE" >/dev/null <<'SQL'
DROP ROLE IF EXISTS :"role_name";
SQL
  local role_remaining
  role_remaining=$(pg_admin_psql_on_port "$PG_PORT" -v role_name="$PG_ROLE" <<'SQL'
SELECT count(*)
FROM pg_roles
WHERE rolname = :'role_name';
SQL
  )
  [[ "$role_remaining" == 0 ]] || fail 'role still exists'
}

remove_installed(){
  local p amnezia_domain amnezia_port amnezia_ufw amnezia_http webhook_domain
  amnezia_domain=$(
    [[ -f "$STATE_FILE" && ! -L "$STATE_FILE" ]] &&
      awk -F= '$1=="DOMAIN"{v=$2}END{print v}' "$STATE_FILE" || true
  )
  amnezia_port=$(
    [[ -f "$STATE_FILE" && ! -L "$STATE_FILE" ]] &&
      awk -F= '$1=="PUBLIC_PORT"{v=$2}END{print v}' "$STATE_FILE" || true
  )
  amnezia_ufw=$(
    [[ -f "$STATE_FILE" && ! -L "$STATE_FILE" ]] &&
      awk -F= '$1=="UFW_PUBLIC_ADDED"{v=$2}END{print v}' "$STATE_FILE" || true
  )
  amnezia_http=$(
    [[ -f "$STATE_FILE" && ! -L "$STATE_FILE" ]] &&
      awk -F= '$1=="UFW_HTTP_ADDED"{v=$2}END{print v}' "$STATE_FILE" || true
  )
  webhook_domain=$(read_safe_domain) || fail 'unsafe DOMAIN in production .env'

  for p in \
    /etc/systemd/system/just1kbot{,-backup,-healthcheck}.service \
    /etc/systemd/system/just1kbot-{backup,healthcheck}.timer \
    /etc/systemd/system/just1kbot-{traffic,notifications,cleanup,stale-payments,heartbeat}.service \
    /usr/local/bin/just1kbot-{backup,restore,healthcheck}.sh \
    /usr/local/bin/{verify_backup,restore_rehearsal}.sh; do
    rm -f -- "$p"
  done
  systemctl daemon-reload

  rm -f -- /etc/nginx/sites-{available,enabled}/just1kbot-webhook
  if [[ -n "$webhook_domain" ]]; then
    rm -f -- \
      "/etc/nginx/sites-available/$webhook_domain" \
      "/etc/nginx/sites-enabled/$webhook_domain"
  fi

  find /etc/nginx/sites-available -maxdepth 1 -type f \
    -name 'just1kbot-amnezia-*' -print0 2>/dev/null |
    while IFS= read -r -d '' p; do
      rm -f -- "/etc/nginx/sites-enabled/$(basename "$p")" "$p"
    done

  rm -f -- \
    /etc/nginx/conf.d/just1kbot-amnezia-rate-limit.conf \
    "$STATE_FILE"

  if [[ "$MODE" == purge && -n "$amnezia_domain" ]] &&
    command -v certbot >/dev/null 2>&1; then
    certbot delete --cert-name "$amnezia_domain" \
      --non-interactive >/dev/null 2>&1 || true
  fi

  if command -v nginx >/dev/null 2>&1; then
    nginx -t
    systemctl reload nginx
  fi

  if command -v ufw >/dev/null 2>&1; then
    [[ "$amnezia_ufw" == true && "$amnezia_port" =~ ^[1-9][0-9]{0,4}$ ]] &&
      ufw delete allow "$amnezia_port/tcp" >/dev/null 2>&1 || true
    [[ "$amnezia_http" == true ]] &&
      ufw delete allow 80/tcp >/dev/null 2>&1 || true
    ufw delete deny 8080/tcp >/dev/null 2>&1 || true
    ufw delete deny 6379/tcp >/dev/null 2>&1 || true
    ufw delete deny 5432/tcp >/dev/null 2>&1 || true
    if [[ "${PG_PORT:-}" =~ ^[1-9][0-9]{0,4}$ ]] &&
      (( PG_PORT<=65535 )) && [[ "$PG_PORT" != 5432 ]]; then
      ufw delete deny "$PG_PORT/tcp" >/dev/null 2>&1 || true
    fi
  fi

  safe_path
  rm -rf -- "$PROJECT_DIR" /var/lib/just1kbot /var/log/just1kbot
  rm -f -- \
    /etc/logrotate.d/just1kbot \
    /var/log/just1kbot-deploy.log \
    /var/log/just1kbot-rollback.log
}

purge_saved(){
  rm -rf -- "$BACKUP_DIR" "$SNAPSHOT_DIR"
  rm -f -- "$BACKUP_CONF" "$AGE_IDENTITY_DEFAULT"
  id "$BOT_USER" >/dev/null 2>&1 && userdel "$BOT_USER"
}

main(){
  parse "$@"
  [[ ${EUID:-$(id -u)} -eq 0 ]] || fail 'run as root'
  safe_path
  lock_all
  preflight_restore_state

  if [[ "$MODE" == keep ]]; then
    backup_before_keep
  else
    confirm_purge
  fi

  stop_units

  if [[ "$MODE" == purge ]]; then
    purge_redis
    purge_db
  fi

  remove_installed

  if [[ "$MODE" == purge ]]; then
    purge_saved
    printf 'Just1kBot и его данные удалены; системные пакеты сохранены.\n'
  else
    printf 'Приложение удалено; PostgreSQL data и encrypted backups сохранены.\n'
  fi
}
main "$@"
