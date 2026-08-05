clone_function install_backup_tooling base_install_backup_tooling
install_backup_tooling() {
    local saved_source=$SOURCE_DIR
    SOURCE_DIR="$SCRIPT_DIR"
    base_install_backup_tooling
    SOURCE_DIR=$saved_source

    sed -i -E "s/postgresql[.]service/${PG_UNIT}/g" \
        /etc/systemd/system/just1kbot-backup.service
    systemctl daemon-reload

    local path
    for path in \
        /usr/local/bin/just1kbot-backup.sh \
        /usr/local/bin/verify_backup.sh \
        /usr/local/bin/restore_rehearsal.sh \
        /usr/local/bin/just1kbot-restore.sh \
        /etc/systemd/system/just1kbot-backup.service \
        /etc/systemd/system/just1kbot-backup.timer \
        "$BACKUP_CONF"; do
        foundation_manifest_add "path:$path"
    done
    [[ ! -e "$BACKUP_IDENTITY" ]] ||
        foundation_manifest_add "path:$BACKUP_IDENTITY"
}

install_healthcheck() {
    # Healthcheck НЕ должен использовать flock на deploy.lock.
    # Причина: flock работает на уровне open file description, а не inode.
    # Deploy открывает fd 200, healthcheck — fd 8, это разные descriptions,
    # поэтому locks не взаимодействуют, создавая TOCTOU race condition.
    # Вместо этого healthcheck полагается на pause_operational_timers(),
    # которая останавливает healthcheck timer перед началом deploy.
    foundation_atomic_write "$HEALTHCHECK_COMMAND" root root 0750 <<'EOF_HEALTH'
#!/bin/bash
set -Eeuo pipefail

# No flock here - deploy pauses this timer before starting.
# Relying on flock with different file descriptors creates race conditions.

systemctl is-active --quiet just1kbot.service
systemctl is-active --quiet just1kbot-redis.service
[[ -f /run/just1kbot/heartbeat && ! -L /run/just1kbot/heartbeat ]]
age=$(( $(date +%s) - $(stat -c %Y /run/just1kbot/heartbeat) ))
(( age >= 0 && age <= 180 ))
cd /opt/just1kbot
exec timeout --signal=TERM --kill-after=5s 25s \
    runuser -u just1kbot -- env \
    HOME=/run/just1kbot \
    PYTHONPATH=/opt/just1kbot \
    /opt/just1kbot/venv/bin/python - <<'PY'
import asyncio
import redis.asyncio as redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from config.settings import get_settings

async def main():
    settings = get_settings()
    engine = create_async_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        pool_timeout=5,
        connect_args={"timeout": 5, "command_timeout": 5},
    )
    client = redis.from_url(
        settings.REDIS_URL,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        assert await client.ping()
    finally:
        await client.aclose()
        await engine.dispose()

asyncio.run(asyncio.wait_for(main(), 20))
PY
EOF_HEALTH

    foundation_atomic_write \
        /etc/systemd/system/just1kbot-healthcheck.service root root 0644 <<EOF_SERVICE
[Unit]
Description=Just1kBot application healthcheck
After=just1kbot.service just1kbot-redis.service
Requires=just1kbot-redis.service

[Service]
Type=oneshot
ExecStart=$HEALTHCHECK_COMMAND
TimeoutStartSec=35s
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
EOF_SERVICE

    foundation_atomic_write \
        /etc/systemd/system/just1kbot-healthcheck.timer root root 0644 <<'EOF_TIMER'
[Unit]
Description=Run Just1kBot healthcheck every two minutes

[Timer]
OnBootSec=3m
OnUnitActiveSec=2m
Persistent=true
Unit=just1kbot-healthcheck.service

[Install]
WantedBy=timers.target
EOF_TIMER

    systemctl daemon-reload
    foundation_manifest_add "path:$HEALTHCHECK_COMMAND"
    foundation_manifest_add path:/etc/systemd/system/just1kbot-healthcheck.service
    foundation_manifest_add path:/etc/systemd/system/just1kbot-healthcheck.timer
}

setup_systemd() {
    foundation_atomic_write "$UNIT_FILE" root root 0644 <<EOF_UNIT
[Unit]
Description=Just1kBot Telegram Bot
After=network-online.target $PG_UNIT $REDIS_SERVICE
Wants=network-online.target
Requires=$PG_UNIT $REDIS_SERVICE

[Service]
Type=simple
User=$BOT_USER
Group=$BOT_USER
WorkingDirectory=$PROJECT_DIR
Environment=HOME=$RUNTIME_DIR
Environment=PYTHONPATH=$PROJECT_DIR
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=JUST1KBOT_HEARTBEAT_FILE=$HEARTBEAT_FILE
ExecStart=$VENV_DIR/bin/python -m bot.main
Restart=always
RestartSec=5
TimeoutStopSec=45
RuntimeDirectory=just1kbot
RuntimeDirectoryMode=0750
UMask=0027
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
ReadOnlyPaths=$PROJECT_DIR
ReadWritePaths=$RUNTIME_DIR /var/log/just1kbot
MemoryMax=512M
CPUQuota=80%

[Install]
WantedBy=multi-user.target
EOF_UNIT

    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME" >/dev/null
    foundation_manifest_add systemd:just1kbot.service
}

setup_firewall_initial() {
    foundation_noop_firewall
}

setup_nginx_initial() {
    foundation_setup_nginx_and_tls \
        "$DOMAIN" "$SSL_EMAIL" "$YOOKASSA_WEBHOOK_PORT"
}

refresh_existing_nginx() {
    DOMAIN=$(read_env_value DOMAIN)
    SSL_EMAIL=$(read_env_value SSL_EMAIL)
    YOOKASSA_WEBHOOK_PORT=$(read_env_value YOOKASSA_WEBHOOK_PORT)
    foundation_setup_nginx_and_tls \
        "$DOMAIN" "$SSL_EMAIL" "$YOOKASSA_WEBHOOK_PORT"
}

clone_function setup_logrotate base_setup_logrotate
setup_logrotate() {
    base_setup_logrotate 2>/dev/null || {
        foundation_atomic_write /etc/logrotate.d/just1kbot root root 0644 <<'EOF_LOGROTATE'
/var/log/just1kbot-deploy.log /var/log/just1kbot-rollback.log /var/log/just1kbot/*.log {
    weekly
    rotate 8
    compress
    delaycompress
    missingok
    notifempty
    create 0640 root root
}
EOF_LOGROTATE
    }
    foundation_manifest_add path:/etc/logrotate.d/just1kbot
}

activate_release_bundle() {
    install_backup_tooling
    install_healthcheck
    setup_logrotate
    if [[ "$INITIAL_INSTALL" == true ]]; then
        setup_nginx_initial
    else
        refresh_existing_nginx
    fi
    setup_systemd
    foundation_install_cli
}

pause_and_backup() {
    pause_operational_timers
    create_pre_migration_backup
}

clone_function show_status base_show_status
show_status() {
    base_show_status
    printf 'Dedicated Redis: %s, port=%s\n' \
        "$(systemctl is-active "$REDIS_SERVICE" 2>/dev/null || true)" \
        "$REDIS_PORT"
    printf 'Manifest: %s\n' "$INSTALL_MANIFEST"
}

# Safe installer overrides the generic operational set. In particular, the
# Nginx default site is never snapshotted, removed, or restored.
configure_operational_transaction() {
    local domain=${DOMAIN:-}
    local normalized=""

    if [[ "$INITIAL_INSTALL" == false ]]; then
        domain=$(read_env_value DOMAIN)
    fi
    if [[ -n "$domain" ]]; then
        normalized=$(normalize_domain "$domain") || {
            error 'DOMAIN из production .env имеет неверный формат'
            return 1
        }
        DOMAIN=$normalized
    else
        DOMAIN=""
    fi

    OPERATIONAL_PATHS=(
        /usr/local/bin/just1kbot-backup.sh
        /usr/local/bin/verify_backup.sh
        /usr/local/bin/restore_rehearsal.sh
        /usr/local/bin/just1kbot-restore.sh
        /usr/local/bin/just1kbot-healthcheck.sh
        /etc/systemd/system/just1kbot-backup.service
        /etc/systemd/system/just1kbot-backup.timer
        /etc/systemd/system/just1kbot-healthcheck.service
        /etc/systemd/system/just1kbot-healthcheck.timer
        /etc/logrotate.d/just1kbot
        "$BACKUP_CONF"
        "$BACKUP_IDENTITY"
        "$REDIS_CONFIG"
        "$REDIS_UNIT"
    )
    OPERATIONAL_UNITS=(
        just1kbot-backup.timer
        just1kbot-healthcheck.timer
        "$REDIS_SERVICE"
    )
    OPERATIONAL_NGINX=false

    if [[ -n "$DOMAIN" ]]; then
        OPERATIONAL_PATHS+=(
            "/etc/nginx/sites-available/$DOMAIN"
            "/etc/nginx/sites-enabled/$DOMAIN"
        )
        OPERATIONAL_UNITS+=(nginx.service certbot.timer)
        OPERATIONAL_NGINX=true
    fi
}

install_rollback_override() {
    rollback_application() {
        local code=$1
        local failed_pid state
        failed_pid=$(service_call mainpid | tail -1)
        failed_pid=${failed_pid:-0}
        service_call stop || true
        state=$(service_call state | tail -1)
        [[ "$state" == inactive || "$state" == failed ]] || return 2

        capture_diagnostics "$code"
        deploy_log 'database_downgrade=not_performed'
        restore_snapshot || return 2

        if [[ ! -f "$ROLLBACK_SNAPSHOT/systemd.service" ]]; then
            rm -f "$UNIT_FILE"
            service_call daemon-reload
            deploy_log \
                'deployment_result=rolled_back previous_service=absent start_not_attempted=true'
            return 1
        fi

        local started base_restarts previous_heartbeat
        started=$(date +%s)
        base_restarts=$(service_call nrestarts | tail -1)
        previous_heartbeat=$(heartbeat_mtime)
        service_call start || return 2
        readiness_gate previous \
            "$started" "$base_restarts" "$previous_heartbeat" "$failed_pid" &&
            return 1
        service_call stop || true
        return 2
    }
}