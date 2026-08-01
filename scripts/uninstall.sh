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
LOCK_FILE=/run/lock/just1kbot-deploy.lock
STATE_FILE=/etc/just1kbot-amnezia.conf
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PG_LIB="$SCRIPT_DIR/lib/postgresql.sh"
MODE=

fail(){ printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }
log(){ printf '[uninstall] %s\n' "$*"; }
usage(){ cat <<'TXT'
Использование:
  sudo bash scripts/uninstall.sh --keep-data
  sudo bash scripts/uninstall.sh --purge-data

--keep-data  удаляет приложение, но сохраняет PostgreSQL data и encrypted backups;
             перед удалением создаёт и проверяет новый backup.
--purge-data удаляет приложение, database/role, backups и age identity;
             требует точную фразу DELETE JUST1KBOT.
Глобальные пакеты PostgreSQL, Redis, Nginx, Certbot и Python не удаляются.
TXT
}

parse(){
  (( $# == 1 )) || { usage >&2; exit 2; }
  case "$1" in
    --keep-data) MODE=keep;;
    --purge-data) MODE=purge;;
    -h|--help) usage; exit 0;;
    *) usage >&2; exit 2;;
  esac
}

safe_path(){
  [[ "$PROJECT_DIR" == /opt/just1kbot && "$PROJECT_DIR" != *'..'* ]] || fail 'unsafe PROJECT_DIR'
  [[ ! -L "$PROJECT_DIR" ]] || fail 'PROJECT_DIR is a symlink'
  [[ "$(realpath -m -- "$PROJECT_DIR")" == "$(realpath -e -- /opt)/just1kbot" ]] || fail 'canonical PROJECT_DIR mismatch'
}

lock_all(){
  install -d -o root -g root -m 0755 "$(dirname "$LOCK_FILE")"
  exec 200>"$LOCK_FILE"
  flock -n 200 || fail 'deploy/backup/restore is already running'
  systemctl stop just1kbot-backup.timer 2>/dev/null || true
  local end=$(( $(date +%s)+180 ))
  while systemctl is-active --quiet just1kbot-backup.service 2>/dev/null; do
    (( $(date +%s) <= end )) || fail 'active backup timeout'
    sleep 2
  done
}

latest_backup(){
  find "$BACKUP_DIR" -maxdepth 1 -type f -name 'just1kbot-pg-v1-*.tar.age' -printf '%T@ %p\n' 2>/dev/null |
    sort -rn | head -1 | cut -d' ' -f2-
}

backup_before_keep(){
  local identity=${AGE_IDENTITY_FILE:-$AGE_IDENTITY_DEFAULT} start artifact
  [[ -x /usr/local/bin/just1kbot-backup.sh ]] || fail 'backup tool is missing'
  [[ -x /usr/local/bin/verify_backup.sh ]] || fail 'backup verifier is missing'
  [[ -f "$BACKUP_CONF" && ! -L "$BACKUP_CONF" ]] || fail 'unsafe backup config'
  [[ -f "$identity" && ! -L "$identity" ]] || fail 'set AGE_IDENTITY_FILE for --keep-data'
  start=$(date +%s)
  systemctl start --wait just1kbot-backup.service
  artifact=$(latest_backup)
  [[ -n "$artifact" && -s "$artifact" && -s "$artifact.sha256" ]] || fail 'new backup was not published'
  (( $(stat -c %Y "$artifact") >= start )) || fail 'backup is not from this operation'
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
  for unit in just1kbot-healthcheck.timer just1kbot-backup.timer \
    just1kbot-healthcheck.service just1kbot-backup.service just1kbot.service \
    just1kbot-traffic.service just1kbot-notifications.service just1kbot-cleanup.service \
    just1kbot-stale-payments.service just1kbot-heartbeat.service; do
    systemctl stop "$unit" 2>/dev/null || true
    systemctl disable "$unit" 2>/dev/null || true
  done
  id "$BOT_USER" >/dev/null 2>&1 && pkill -TERM -u "$BOT_USER" 2>/dev/null || true
}

purge_db(){
  [[ -f "$PG_LIB" && ! -L "$PG_LIB" ]] || fail 'PostgreSQL library is missing'
  # shellcheck source=lib/postgresql.sh
  source "$PG_LIB"
  pg_prepare update
  runuser -u postgres -- psql -X -v ON_ERROR_STOP=1 -h "$PG_SOCKET_DIR" -p "$PG_PORT" -d postgres \
    -v n="$PG_DATABASE" -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:'n' AND pid<>pg_backend_pid();" >/dev/null
  runuser -u postgres -- dropdb -h "$PG_SOCKET_DIR" -p "$PG_PORT" --maintenance-db=postgres "$PG_DATABASE"
  ! runuser -u postgres -- psql -XAtq -h "$PG_SOCKET_DIR" -p "$PG_PORT" -d postgres \
    -v n="$PG_DATABASE" -c "SELECT 1 FROM pg_database WHERE datname=:'n';" | grep -qx 1 || fail 'database still exists'
  runuser -u postgres -- psql -X -v ON_ERROR_STOP=1 -h "$PG_SOCKET_DIR" -p "$PG_PORT" -d postgres \
    -v n="$PG_ROLE" -c 'DROP ROLE IF EXISTS :"n";' >/dev/null
  ! runuser -u postgres -- psql -XAtq -h "$PG_SOCKET_DIR" -p "$PG_PORT" -d postgres \
    -v n="$PG_ROLE" -c "SELECT 1 FROM pg_roles WHERE rolname=:'n';" | grep -qx 1 || fail 'role still exists'
}

remove_installed(){
  local p amnezia_domain
  amnezia_domain=$([[ -f "$STATE_FILE" && ! -L "$STATE_FILE" ]] && awk -F= '$1=="DOMAIN"{v=$2}END{print v}' "$STATE_FILE" || true)
  for p in /etc/systemd/system/just1kbot{,-backup,-healthcheck}.service \
    /etc/systemd/system/just1kbot-{backup,healthcheck}.timer \
    /etc/systemd/system/just1kbot-{traffic,notifications,cleanup,stale-payments,heartbeat}.service \
    /usr/local/bin/just1kbot-{backup,restore,healthcheck}.sh \
    /usr/local/bin/{verify_backup,restore_rehearsal}.sh; do rm -f -- "$p"; done
  systemctl daemon-reload

  rm -f -- /etc/nginx/sites-{available,enabled}/just1kbot-webhook
  find /etc/nginx/sites-available -maxdepth 1 -type f -name 'just1kbot-amnezia-*' -print0 2>/dev/null |
    while IFS= read -r -d '' p; do rm -f -- "/etc/nginx/sites-enabled/$(basename "$p")" "$p"; done
  rm -f -- /etc/nginx/conf.d/just1kbot-amnezia-rate-limit.conf "$STATE_FILE"
  if [[ "$MODE" == purge && -n "$amnezia_domain" ]] && command -v certbot >/dev/null 2>&1; then
    certbot delete --cert-name "$amnezia_domain" --non-interactive >/dev/null 2>&1 || true
  fi
  command -v nginx >/dev/null 2>&1 && { nginx -t; systemctl reload nginx; }

  command -v ufw >/dev/null 2>&1 && {
    ufw delete deny 8080/tcp >/dev/null 2>&1 || true
    ufw delete deny 6379/tcp >/dev/null 2>&1 || true
    ufw delete deny 5432/tcp >/dev/null 2>&1 || true
    if [[ "${PG_PORT:-}" =~ ^[1-9][0-9]{0,4}$ ]] && (( PG_PORT<=65535 )) && [[ "$PG_PORT" != 5432 ]]; then
      ufw delete deny "$PG_PORT/tcp" >/dev/null 2>&1 || true
    fi
  }

  safe_path
  rm -rf -- "$PROJECT_DIR" /var/lib/just1kbot /var/log/just1kbot
  rm -f -- /etc/logrotate.d/just1kbot /var/log/just1kbot-deploy.log /var/log/just1kbot-rollback.log
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
  if [[ "$MODE" == keep ]]; then backup_before_keep; else confirm_purge; fi
  stop_units
  [[ "$MODE" == purge ]] && purge_db
  remove_installed
  if [[ "$MODE" == purge ]]; then
    purge_saved
    printf 'Just1kBot и его данные удалены; системные пакеты сохранены.\n'
  else
    printf 'Приложение удалено; PostgreSQL data и encrypted backups сохранены.\n'
  fi
}
main "$@"
