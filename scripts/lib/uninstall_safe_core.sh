fail(){ foundation_fail UNINSTALL_ERROR "$1" "${2:-$1}" "${3:-Исправьте причину и повторите uninstall.}"; exit 1; }
log(){ printf '[uninstall] %s\n' "$*"; }
usage(){ cat <<'EOF'
Использование:
  sudo just1kbot uninstall --keep-data
  sudo just1kbot uninstall --purge-data

--keep-data  удаляет приложение, dedicated Redis и runtime, но сохраняет PostgreSQL и encrypted backups. Перед удалением создаёт и проверяет новый backup.
--purge-data удаляет только resources, перечисленные в ownership manifest: application, dedicated Redis, PostgreSQL role/database, backups и owned certificate.
Firewall, /etc/redis/redis.conf, чужие Nginx sites, Docker и VPN не изменяются.
EOF
}
parse(){
    while (( $# )); do case "$1" in --keep-data) [[ -z "$MODE" ]]||exit 2; MODE=keep;; --purge-data) [[ -z "$MODE" ]]||exit 2; MODE=purge;; --yes) ASSUME_YES=true;; --incomplete-install) INCOMPLETE_INSTALL=true;; -h|--help) usage; exit 0;; *) usage >&2; exit 2;; esac; shift; done
    [[ -n "$MODE" ]] || { usage >&2; exit 2; }
    [[ "$INCOMPLETE_INSTALL" == false || "$MODE" == purge ]] || exit 2
    [[ "$ASSUME_YES" == false || "$INCOMPLETE_INSTALL" == true ]] || fail '--yes разрешён только для installer rollback' 'обычный purge требует интерактивное подтверждение'
}
acquire_lock(){ install -d -o root -g root -m 0755 "$(dirname "$LOCK")"; exec 200>"$LOCK"; flock -n 200 || fail 'другая operation держит deploy lock'; }
read_env(){
    [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || return 0
    local output
    output=$(ENV="$ENV_FILE" python3 - <<'PY'
import os,re
from pathlib import Path
x={}
for raw in Path(os.environ['ENV']).read_text().splitlines():
 s=raw.strip()
 if s and not s.startswith('#') and '=' in s:
  k,v=s.split('=',1); v=v.strip()
  if len(v)>1 and v[0]==v[-1] and v[0] in "'\"": v=v[1:-1]
  if k.strip() in x: raise SystemExit(f'duplicate {k}')
  x[k.strip()]=v
d=x.get('DOMAIN','').lower().rstrip('.'); p=x.get('YOOKASSA_WEBHOOK_PORT','8080')
if d:
 q=re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$'); assert len(d)<=253 and len(d.split('.'))>=2 and all(q.fullmatch(i) for i in d.split('.'))
assert p.isdigit() and 1<=int(p)<=65535
print(d); print(p)
PY
) || fail 'production .env не прошёл безопасный parse'
    mapfile -t values <<<"$output"; DOMAIN=${values[0]:-}; WEBHOOK_PORT=${values[1]:-8080}
}
confirm(){
    [[ "$MODE" == purge ]] || return 0
    [[ "$INCOMPLETE_INSTALL" == true && "$ASSUME_YES" == true ]] && return 0
    [[ -t 0 ]] || fail 'purge требует TTY confirmation' 'запустите в интерактивном терминале'
    local answer; read -rp 'Введите точную фразу DELETE JUST1KBOT: ' answer; [[ "$answer" == 'DELETE JUST1KBOT' ]] || fail 'подтверждение не совпало'
}
manifest_preflight(){
    foundation_manifest_require
    [[ "$(foundation_manifest_metadata firewall_managed)" == false ]] || fail 'manifest неожиданно заявляет ownership firewall' 'автоматическое изменение firewall запрещено'
    local resource
    while IFS= read -r resource; do
        case "$resource" in
            path:/*|systemd:*|nginx-site:*|nginx-enabled:*|certbot:*|postgresql:*|service-user:*|tcp:*) :;;
            *) fail 'manifest содержит неизвестный resource type' "$resource";;
        esac
    done < <(foundation_manifest_resources)
}
backup_before_keep(){
    [[ -x /usr/local/bin/just1kbot-backup.sh && -x /usr/local/bin/verify_backup.sh ]] || fail 'backup tooling отсутствует'
    local identity=${AGE_IDENTITY_FILE:-/root/.config/just1kbot/backup.agekey}
    [[ -f "$identity" && ! -L "$identity" ]] || fail 'age identity отсутствует' 'Передайте AGE_IDENTITY_FILE, соответствующий backup recipient.'
    local started latest; started=$(date +%s); systemctl --wait start just1kbot-backup.service || fail 'backup service завершился ошибкой'
    latest=$(find /root/backups/just1kbot -maxdepth 1 -type f -name 'just1kbot-pg-v1-*.tar.age' -printf '%T@ %p\n'|sort -rn|head -1|cut -d' ' -f2-)
    [[ -n "$latest" && -s "$latest" && -s "$latest.sha256" && $(stat -c %Y "$latest") -ge $started ]] || fail 'новый backup не опубликован'
    AGE_IDENTITY_FILE="$identity" /usr/local/bin/verify_backup.sh "$latest" || fail 'новый backup не прошёл verification'
    log "verified backup: $latest"
}
stop_units(){
    local unit
    for unit in just1kbot-healthcheck.timer just1kbot-backup.timer just1kbot-healthcheck.service just1kbot-backup.service just1kbot.service "$REDIS_SERVICE"; do systemctl stop "$unit" 2>/dev/null||true; systemctl disable "$unit" 2>/dev/null||true; done
    if id "$BOT_USER" >/dev/null 2>&1; then pkill -TERM -u "$BOT_USER" 2>/dev/null||true; local end=$(( $(date +%s)+30 )); while pgrep -u "$BOT_USER" >/dev/null 2>&1 && (( $(date +%s)<=end )); do sleep 1; done; pgrep -u "$BOT_USER" >/dev/null 2>&1 && fail 'processes service user не остановлены'; fi
}
