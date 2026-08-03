clone_function(){ local source=$1 target=$2 definition; definition=$(declare -f "$source") || return 1; definition=${definition/#"$source ()"/"$target ()"}; eval "$definition"; }

validate_supported_os(){ [[ "${1:-}" == ubuntu && "${2:-}" == 24.04 ]] || { error "Поддерживается только Ubuntu 24.04 LTS; обнаружено ${1:-unknown} ${2:-unknown}"; return 1; }; }
install_dependencies(){
    installer_set_step 'Установка пакетов' 'Firewall state не изменяется.'
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        python3 python3-venv python3-pip python3-dev postgresql postgresql-contrib \
        redis-server redis-tools nginx certbot age curl git rsync build-essential \
        libpq-dev logrotate util-linux iproute2 >/dev/null
}
validate_runtime_commands(){
    local command
    for command in python3 rsync systemctl stat git flock runuser age age-keygen pg_dump pg_restore psql sha256sum timeout pg_lsclusters pg_ctlcluster pg_isready redis-server redis-cli nginx certbot ss; do command_required "$command"; done
    python3 -c 'import sys; raise SystemExit(0 if sys.version_info[:2]==(3,12) else 1)' || { error 'Требуется системный Python 3.12'; return 1; }
}
validate_lock(){
    [[ -f "$REQUIREMENTS_LOCK" && ! -L "$REQUIREMENTS_LOCK" ]] || { error 'requirements.lock отсутствует или небезопасен'; return 1; }
    LOCK="$REQUIREMENTS_LOCK" python3 - <<'PY' || { error 'requirements.lock должен содержать только exact versions и SHA-256 hashes'; return 1; }
from pathlib import Path
import os,re
logical=[]; current=''
for raw in Path(os.environ['LOCK']).read_text().splitlines():
 s=raw.strip()
 if not s or s.startswith('#'): continue
 current=(current+' '+s).strip()
 if current.endswith('\\'): current=current[:-1].rstrip(); continue
 logical.append(current); current=''
if current: logical.append(current)
for line in logical:
 if line.startswith('--'): continue
 left=line.split(' ; ',1)[0]
 if '==' not in left.split()[0] or not re.search(r'--hash=sha256:[0-9a-f]{64}(?:\s|$)',line): raise SystemExit(line)
PY
}
validate_source_tree(){
    local file
    for file in requirements.txt requirements.lock alembic.ini bot/main.py deploy.sh scripts/install_safe.sh scripts/uninstall_foundation.sh scripts/lib/installer_foundation.sh scripts/lib/postgresql.sh scripts/lib/operational_transaction.sh scripts/ops/deploy_application.sh scripts/ops/backup_postgres.sh scripts/ops/verify_backup.sh scripts/ops/restore_rehearsal.sh scripts/ops/just1kbot-restore.sh; do [[ -f "$ROOT_DIR/$file" && ! -L "$ROOT_DIR/$file" ]] || { error "Отсутствует безопасный source file: $file"; return 1; }; done
    validate_lock
    if [[ "$INITIAL_INSTALL" == false && "$(cd "$ROOT_DIR" && pwd -P)" == "$(cd "$PROJECT_DIR" && pwd -P)" ]]; then error 'Update нельзя запускать из live directory; используйте update/release checkout.'; return 1; fi
}
validate_env_file_safety(){
    [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || { error 'Production .env отсутствует или небезопасен'; return 1; }
    [[ "$(stat -c '%U:%G %a' "$ENV_FILE")" == "root:${BOT_USER} 640" ]] || { error "Production .env должен быть root:${BOT_USER} 0640"; return 1; }
}
ensure_env_permissions(){ chown root:"$BOT_USER" "$ENV_FILE"; chmod 0640 "$ENV_FILE"; validate_env_file; }
setup_user_and_dirs(){
    local shell=/usr/sbin/nologin home current_shell fresh=false
    [[ -x "$shell" ]] || shell=/sbin/nologin
    if ! id "$BOT_USER" >/dev/null 2>&1; then useradd -r -m -d "$BOT_HOME" -s "$shell" "$BOT_USER"; fresh=true; else home=$(getent passwd "$BOT_USER"|cut -d: -f6); current_shell=$(getent passwd "$BOT_USER"|cut -d: -f7); [[ "$home" == "$BOT_HOME" ]] || { error "Service user home mismatch: $home"; return 1; }; case "$current_shell" in /usr/sbin/nologin|/sbin/nologin) :;; /bin/bash) usermod -s "$shell" "$BOT_USER";; *) error "Service user shell mismatch: $current_shell"; return 1;; esac; fi
    [[ ! -L "$BOT_HOME" && ! -L "$PROJECT_DIR" && ! -L "$STATE_ROOT" ]] || { error 'Reserved directory является symlink'; return 1; }
    install -d -o root -g "$BOT_USER" -m 0750 "$PROJECT_DIR"
    install -d -o root -g root -m 0700 "$BACKUP_DIR" "$SNAPSHOT_DIR" "$STATE_ROOT" "$INSTALL_STATE_DIR"
    install -d -o "$BOT_USER" -g "$BOT_USER" -m 0750 /var/log/just1kbot "$RUNTIME_DIR"
    foundation_manifest_add "service-user:$BOT_USER"; foundation_manifest_add "path:$BOT_HOME"; foundation_manifest_add "path:$PROJECT_DIR"; foundation_manifest_add "path:$STATE_ROOT"; foundation_manifest_add "path:$BACKUP_DIR"; foundation_manifest_add "path:$SNAPSHOT_DIR"
    [[ "$fresh" == false ]] || foundation_journal_add_created "service-user:$BOT_USER"
}
legacy_install_valid(){
    [[ -d "$PROJECT_DIR" && ! -L "$PROJECT_DIR" && -f "$ENV_FILE" && ! -L "$ENV_FILE" && -f "$UNIT_FILE" && ! -L "$UNIT_FILE" ]] || return 1
    grep -Fq 'Description=Just1kBot' "$UNIT_FILE" && grep -Fq 'ExecStart=/opt/just1kbot/venv/bin/python -m bot.main' "$UNIT_FILE" && grep -Fq 'Just1kBot' "$PROJECT_DIR/deploy.sh"
}
ensure_manifest(){
    if foundation_manifest_validate; then return 0; fi
    if foundation_exists "$INSTALL_MANIFEST"; then foundation_fail MANIFEST_INVALID 'manifest повреждён' "$INSTALL_MANIFEST" 'Запустите state/doctor.'; return 1; fi
    if [[ "$INITIAL_INSTALL" == false ]]; then legacy_install_valid || { error 'Legacy installation ownership не доказан; manifest автоматически не создаётся.'; return 1; }; fi
    foundation_manifest_create
    if [[ "$INITIAL_INSTALL" == false ]]; then
        foundation_manifest_add "path:$PROJECT_DIR"; foundation_manifest_add "path:$BOT_HOME"; foundation_manifest_add systemd:just1kbot.service
        local item
        for item in /usr/local/bin/just1kbot-backup.sh /usr/local/bin/just1kbot-restore.sh /usr/local/bin/just1kbot-healthcheck.sh /usr/local/bin/verify_backup.sh /usr/local/bin/restore_rehearsal.sh /etc/systemd/system/just1kbot-backup.service /etc/systemd/system/just1kbot-backup.timer /etc/systemd/system/just1kbot-healthcheck.service /etc/systemd/system/just1kbot-healthcheck.timer /etc/logrotate.d/just1kbot; do [[ ! -e "$item" ]] || foundation_manifest_add "path:$item"; done
    fi
}
preflight_postgres_names_absent(){
    if pg_role_exists || pg_database_exists; then error "PostgreSQL role=$PG_ROLE или database=$PG_DATABASE уже существует без manifest ownership"; error 'Первичная установка не изменяет и не принимает существующие объекты.'; return 1; fi
}
setup_postgresql_initial(){ preflight_postgres_names_absent; pg_prepare_initial_database; foundation_manifest_add "postgresql:${PG_VERSION}/${PG_CLUSTER}:role:$PG_ROLE"; foundation_manifest_add "postgresql:${PG_VERSION}/${PG_CLUSTER}:database:$PG_DATABASE"; foundation_journal_add_created "postgresql:${PG_VERSION}/${PG_CLUSTER}:role:$PG_ROLE"; foundation_journal_add_created "postgresql:${PG_VERSION}/${PG_CLUSTER}:database:$PG_DATABASE"; }
record_existing_postgres(){ pg_assert_existing_database; foundation_manifest_add "postgresql:${PG_VERSION}/${PG_CLUSTER}:role:$PG_ROLE"; foundation_manifest_add "postgresql:${PG_VERSION}/${PG_CLUSTER}:database:$PG_DATABASE"; }

normalize_admin_ids_json(){ python3 - "$1" <<'PY'
import json,re,sys
x=sys.argv[1].strip(); assert re.fullmatch(r'[0-9]+(?:,[0-9]+)*',x); print(json.dumps([int(v) for v in x.split(',')],separators=(',',':')))
PY
}
create_env_if_missing(){
    if [[ -e "$ENV_FILE" || -L "$ENV_FILE" ]]; then validate_env_file; REDIS_PASSWORD=$(read_env_value REDIS_PASSWORD); foundation_update_redis_env "$REDIS_PASSWORD"; ensure_env_permissions; return; fi
    install -o root -g "$BOT_USER" -m 0640 /dev/null "$ENV_FILE"
    [[ -n "${DB_ENCRYPTION_KEY:-}" ]] || DB_ENCRYPTION_KEY=$(python3 - <<'PY'
import base64,secrets
print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
PY
)
    local db redis support=${SUPPORT_USERNAME#@}
    db=$(python3 -c 'import sys;from urllib.parse import quote;print(quote(sys.argv[1],safe=""))' "$DB_PASSWORD")
    redis=$(python3 -c 'import sys;from urllib.parse import quote;print(quote(sys.argv[1],safe=""))' "$REDIS_PASSWORD")
    write_env_var BOT_TOKEN "$BOT_TOKEN"; write_env_var ADMIN_IDS "$(normalize_admin_ids_json "$ADMIN_IDS")"; write_env_var SUPPORT_USERNAME "$support"
    write_env_var DATABASE_URL "postgresql+asyncpg://just1kbot:${db}@127.0.0.1:${PG_PORT}/just1kbot_bot"; write_env_var DB_ENCRYPTION_KEY "$DB_ENCRYPTION_KEY"
    write_env_var REDIS_URL "redis://:${redis}@127.0.0.1:${REDIS_PORT}/0"; write_env_var REDIS_PASSWORD "$REDIS_PASSWORD"
    write_env_var YOOKASSA_SHOP_ID "$YOOKASSA_SHOP_ID"; write_env_var YOOKASSA_SECRET_KEY "$YOOKASSA_SECRET_KEY"; write_env_var YOOKASSA_RETURN_URL 'https://t.me/{bot_username}'; write_env_var YOOKASSA_WEBHOOK_PORT "$YOOKASSA_WEBHOOK_PORT"; write_env_var DOMAIN "$DOMAIN"; write_env_var SSL_EMAIL "$SSL_EMAIL"
    ensure_env_permissions
}
setup_venv(){
    log 'Создание root-owned virtualenv из requirements.lock'
    local old="${VENV_DIR}.old.$$"; rm -rf "$old"; [[ ! -d "$VENV_DIR" ]] || mv "$VENV_DIR" "$old"
    if ! python3 -m venv "$VENV_DIR" || ! "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check --no-deps --require-hashes -r "$PROJECT_DIR/requirements.lock" --quiet; then rm -rf "$VENV_DIR"; [[ ! -d "$old" ]] || mv "$old" "$VENV_DIR"; error 'Не удалось создать locked virtualenv; предыдущий восстановлен.'; return 1; fi
    chown -R root:"$BOT_USER" "$VENV_DIR"; find "$VENV_DIR" -xdev -type d -exec chmod 0750 {} +; find "$VENV_DIR" -xdev -type f -perm /111 -exec chmod 0750 {} +; find "$VENV_DIR" -xdev -type f ! -perm /111 -exec chmod 0640 {} +; rm -rf "$old"
}
harden_live_tree(){ chown -R root:"$BOT_USER" "$PROJECT_DIR"; find "$PROJECT_DIR" -xdev -type d -exec chmod 0750 {} +; find "$PROJECT_DIR" -xdev -type f -perm /111 -exec chmod 0750 {} +; find "$PROJECT_DIR" -xdev -type f ! -perm /111 -exec chmod 0640 {} +; ensure_env_permissions; find "$PROJECT_DIR" -xdev -type l -not -path "$VENV_DIR/*" -print -quit | grep -q . && { error 'Symlink вне virtualenv запрещён'; return 1; } || true; }
prepare_release_runtime(){ setup_user_and_dirs; create_env_if_missing; setup_venv; harden_live_tree; }
init_database(){ runuser -u "$BOT_USER" -- env HOME="$RUNTIME_DIR" PYTHONPATH="$PROJECT_DIR" PYTHONDONTWRITEBYTECODE=1 bash -c "cd '$PROJECT_DIR' && '$VENV_DIR/bin/alembic' upgrade head" 2>>"$LOG_FILE"; }
