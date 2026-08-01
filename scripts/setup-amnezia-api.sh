#!/bin/bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 027

LOCAL_URL=http://127.0.0.1:4001
STATE=/etc/just1kbot-amnezia.conf
RATE=/etc/nginx/conf.d/just1kbot-amnezia-rate-limit.conf
ACME=/var/www/just1kbot-certbot
OPERATION_LOCK=/run/lock/just1kbot-deploy.lock
LOCAL_LOCK=/run/lock/just1kbot-amnezia.lock

if (( $# == 0 )); then
  ACTION=menu
else
  ACTION=$1
  shift
fi

DOMAIN=
EMAIL=
PORT=8443
DELETE_CERT=false
TX=
COMMITTED=false
ADDED=false
ADDED_HTTP=false
REMOVED=false
REMOVED_HTTP=false
CERT_CREATED=false

fail(){ printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }
usage(){ cat <<'TXT'
Использование:
  sudo bash scripts/setup-amnezia-api.sh
  sudo bash scripts/setup-amnezia-api.sh check|status
  sudo bash scripts/setup-amnezia-api.sh publish --domain api.example.com --email admin@example.com [--port 8443]
  sudo bash scripts/setup-amnezia-api.sh unpublish --domain api.example.com [--delete-certificate]

Без аргументов открывается интерактивное меню.
Публичный reverse proxy создаётся только явным действием publish.
Одновременно управляется только один публичный Amnezia API domain.
TXT
}

while (( $# )); do
  case "$1" in
    --domain)
      [[ $# -ge 2 ]] || fail '--domain value missing'
      DOMAIN=$2
      shift 2
      ;;
    --email)
      [[ $# -ge 2 ]] || fail '--email value missing'
      EMAIL=$2
      shift 2
      ;;
    --port)
      [[ $# -ge 2 ]] || fail '--port value missing'
      PORT=$2
      shift 2
      ;;
    --delete-certificate)
      DELETE_CERT=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

case "$ACTION" in
  -h|--help|help)
    usage
    exit 0
    ;;
esac

[[ "$ACTION" =~ ^(menu|check|status|publish|unpublish)$ ]] ||
  { usage >&2; exit 2; }
[[ ${EUID:-$(id -u)} -eq 0 ]] || fail 'run as root'

install -d -o root -g root -m 0755 "$(dirname "$OPERATION_LOCK")"
exec 199>"$OPERATION_LOCK"
flock -n 199 || fail 'deploy/backup/restore/uninstall operation already running'
exec 200>"$LOCAL_LOCK"
flock -n 200 || fail 'Amnezia operation already running'

norm(){
  DOMAIN_VALUE="$1" python3 - <<'PY'
import os
import re

value = os.environ["DOMAIN_VALUE"].strip().lower().rstrip(".")
label = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
if (
    not value
    or len(value) > 253
    or len(value.split(".")) < 2
    or any(not label.fullmatch(part) for part in value.split("."))
):
    raise SystemExit(1)
print(value)
PY
}

health(){
  curl --fail --show-error --silent --max-time 5 \
    "$LOCAL_URL/health" >/dev/null
}

paths(){
  CONF="/etc/nginx/sites-available/just1kbot-amnezia-$DOMAIN"
  ENABLED="/etc/nginx/sites-enabled/just1kbot-amnezia-$DOMAIN"
}

validate_state(){
  [[ ! -e "$STATE" && ! -L "$STATE" ]] && return 0
  [[ -f "$STATE" && ! -L "$STATE" ]] || fail 'Amnezia state is unsafe'
  [[ $(stat -c '%u' "$STATE") == 0 && $(stat -c '%a' "$STATE") == 600 ]] ||
    fail 'Amnezia state must be root-owned mode 0600'
}

state(){
  validate_state
  [[ -f "$STATE" ]] &&
    awk -F= -v key="$1" '$1==key{value=$2}END{print value}' "$STATE"
}

backup_one(){
  local path=$1 name
  name=$(printf %s "$path" | sha256sum | awk '{print $1}')
  if [[ -e "$path" || -L "$path" ]]; then
    cp -a -- "$path" "$TX/$name"
  else
    name=-
  fi
  printf '%s\t%s\n' "$name" "$path" >>"$TX/list"
}

begin(){
  TX=$(mktemp -d /run/just1kbot-amnezia.XXXXXX)
  chmod 700 "$TX"
  backup_one "$CONF"
  backup_one "$ENABLED"
  backup_one "$RATE"
  backup_one "$STATE"
}

rollback(){
  local rc=$? name path
  if [[ "$COMMITTED" != true && -n "$TX" && -f "$TX/list" ]]; then
    while IFS=$'\t' read -r name path; do
      rm -rf -- "$path"
      [[ "$name" == - ]] || cp -a -- "$TX/$name" "$path"
    done <"$TX/list"

    [[ "$ADDED" == true ]] &&
      ufw delete allow "$PORT/tcp" >/dev/null 2>&1 || true
    [[ "$ADDED_HTTP" == true ]] &&
      ufw delete allow 80/tcp >/dev/null 2>&1 || true
    [[ "$REMOVED" == true ]] &&
      ufw allow "$PORT/tcp" >/dev/null 2>&1 || true
    [[ "$REMOVED_HTTP" == true ]] &&
      ufw allow 80/tcp >/dev/null 2>&1 || true

    if [[ "$CERT_CREATED" == true ]] &&
      command -v certbot >/dev/null 2>&1; then
      certbot delete --cert-name "$DOMAIN" \
        --non-interactive >/dev/null 2>&1 || true
    fi

    if command -v nginx >/dev/null 2>&1 &&
      nginx -t >/dev/null 2>&1; then
      systemctl reload nginx || true
    fi
  fi
  [[ -z "$TX" ]] || rm -rf -- "$TX"
  return "$rc"
}
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

ufw_active(){
  command -v ufw >/dev/null 2>&1 || return 1
  ufw status | grep -q '^Status: active'
}

ufw_has_action(){
  local port=$1 action=$2
  ufw status | grep -Eq "^${port}/tcp([[:space:]]+[(]v6[)])?[[:space:]]+${action}([[:space:]]|$)"
}

firewall_add(){
  ufw_active || return 0

  if ufw_has_action 80 DENY; then
    fail 'UFW explicitly denies 80/tcp; remove the deny rule before publish'
  fi
  if ufw_has_action "$PORT" DENY; then
    fail "UFW explicitly denies $PORT/tcp; remove the deny rule before publish"
  fi

  if ! ufw_has_action 80 ALLOW; then
    ufw allow 80/tcp >/dev/null
    ADDED_HTTP=true
  fi
  if ! ufw_has_action "$PORT" ALLOW; then
    ufw allow "$PORT/tcp" >/dev/null
    ADDED=true
  fi
}

http_conf(){
  cat >"$CONF" <<EOF_HTTP
server {
  listen 80;
  listen [::]:80;
  server_name $DOMAIN;
  location /.well-known/acme-challenge/ { root $ACME; }
  location / { return 404; }
}
EOF_HTTP
  ln -sfn "$CONF" "$ENABLED"
}

https_conf(){
  cat >"$CONF" <<EOF_HTTPS
server {
  listen 80;
  listen [::]:80;
  server_name $DOMAIN;
  location /.well-known/acme-challenge/ { root $ACME; }
  location / { return 301 https://\$host:$PORT\$request_uri; }
}
server {
  listen $PORT ssl;
  listen [::]:$PORT ssl;
  server_name $DOMAIN;
  ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;
  include /etc/letsencrypt/options-ssl-nginx.conf;
  ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
  limit_req zone=just1kbot_amnezia_api burst=50 nodelay;
  limit_req_status 429;
  location ~ ^/(docs|redoc|openapi[.]json|metrics)(/|$) { return 404; }
  location / {
    proxy_pass $LOCAL_URL;
    proxy_http_version 1.1;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_connect_timeout 10s;
    proxy_read_timeout 60s;
  }
}
EOF_HTTPS
}

publish(){
  DOMAIN=$(norm "$DOMAIN") || fail 'invalid domain'
  [[ "$EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] ||
    fail 'invalid email'
  [[ "$PORT" =~ ^[1-9][0-9]{0,4}$ ]] &&
    (( PORT<=65535 && PORT!=80 )) || fail 'invalid port'

  validate_state
  local managed_domain
  managed_domain=$(state DOMAIN || true)
  [[ -z "$managed_domain" || "$managed_domain" == "$DOMAIN" ]] ||
    fail "another managed Amnezia domain is already published: $managed_domain"

  health

  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    nginx certbot curl >/dev/null
  systemctl enable --now nginx >/dev/null

  paths
  begin
  install -d -m 0755 "$ACME"

  printf 'limit_req_zone $binary_remote_addr zone=just1kbot_amnezia_api:10m rate=30r/s;\n' \
    >"$RATE"
  http_conf
  nginx -t
  systemctl reload nginx

  firewall_add

  local cert_preexisting=false
  [[ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" &&
    -f "/etc/letsencrypt/live/$DOMAIN/privkey.pem" ]] &&
    cert_preexisting=true

  certbot certonly --webroot \
    --webroot-path "$ACME" \
    -d "$DOMAIN" \
    -m "$EMAIL" \
    --agree-tos \
    --non-interactive

  [[ "$cert_preexisting" == true ]] || CERT_CREATED=true
  [[ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" &&
    -f "/etc/letsencrypt/live/$DOMAIN/privkey.pem" ]] ||
    fail 'certificate missing'

  https_conf
  nginx -t
  systemctl reload nginx

  local state_tmp="${STATE}.new.$$"
  {
    printf 'DOMAIN=%s\n' "$DOMAIN"
    printf 'PUBLIC_PORT=%s\n' "$PORT"
    printf 'UFW_PUBLIC_ADDED=%s\n' "$ADDED"
    printf 'UFW_HTTP_ADDED=%s\n' "$ADDED_HTTP"
  } >"$state_tmp"
  install -o root -g root -m 600 "$state_tmp" "$STATE"
  rm -f "$state_tmp"

  COMMITTED=true
  rm -rf "$TX"
  TX=
  printf 'Published: https://%s:%s\n' "$DOMAIN" "$PORT"
}

unpublish(){
  DOMAIN=$(norm "$DOMAIN") || fail 'invalid domain'
  paths
  begin

  local saved_domain saved_port saved_ufw saved_http
  saved_domain=$(state DOMAIN || true)
  saved_port=$(state PUBLIC_PORT || true)
  saved_ufw=$(state UFW_PUBLIC_ADDED || true)
  saved_http=$(state UFW_HTTP_ADDED || true)

  [[ -n "$saved_domain" ]] || fail 'no managed Amnezia publication state exists'
  [[ "$saved_domain" == "$DOMAIN" ]] || fail 'state domain mismatch'

  rm -f -- "$ENABLED" "$CONF"

  find /etc/nginx/sites-available -maxdepth 1 -type f \
    -name 'just1kbot-amnezia-*' -print -quit |
    grep -q . || rm -f -- "$RATE"

  nginx -t
  systemctl reload nginx

  if [[ "$saved_ufw" == true &&
    "$saved_port" =~ ^[1-9][0-9]{0,4}$ ]] &&
    (( saved_port<=65535 )); then
    PORT=$saved_port
    ufw delete allow "$PORT/tcp" >/dev/null 2>&1 &&
      REMOVED=true || true
  fi
  if [[ "$saved_http" == true ]]; then
    ufw delete allow 80/tcp >/dev/null 2>&1 &&
      REMOVED_HTTP=true || true
  fi

  rm -f -- "$STATE"

  if [[ "$DELETE_CERT" == true ]]; then
    command -v certbot >/dev/null 2>&1 ||
      fail 'certbot is required to delete certificate'
    certbot delete --cert-name "$DOMAIN" --non-interactive
  fi

  COMMITTED=true
  rm -rf "$TX"
  TX=
  printf 'Public proxy removed; local API remains at %s\n' "$LOCAL_URL"
}

interactive_menu(){
  [[ -t 0 ]] || fail 'укажите check, status, publish или unpublish'
  printf '\nAmnezia API\n'
  printf '  1. Проверить локальный API\n'
  printf '  2. Опубликовать HTTPS reverse proxy\n'
  printf '  3. Удалить публичный reverse proxy\n'
  printf '  0. Выход\n\n'

  local choice answer
  read -rp 'Выбор: ' choice
  case "$choice" in
    1)
      health
      printf 'Local Amnezia API is healthy: %s\n' "$LOCAL_URL"
      ;;
    2)
      read -rp 'Домен: ' DOMAIN
      read -rp "Email Let's Encrypt: " EMAIL
      read -rp 'HTTPS порт [8443]: ' answer
      PORT=${answer:-8443}
      printf 'Будет создан публичный HTTPS reverse proxy для Amnezia API.\n'
      read -rp 'Продолжить? Введите yes: ' answer
      [[ "$answer" == yes ]] || exit 0
      publish
      ;;
    3)
      read -rp 'Домен: ' DOMAIN
      read -rp 'Удалить также сертификат? [y/N]: ' answer
      [[ "$answer" =~ ^[Yy]$ ]] && DELETE_CERT=true
      unpublish
      ;;
    0)
      exit 0
      ;;
    *)
      fail 'неизвестный пункт меню'
      ;;
  esac
}

case "$ACTION" in
  menu)
    interactive_menu
    ;;
  check)
    health
    printf 'Local Amnezia API is healthy: %s\n' "$LOCAL_URL"
    ;;
  status)
    health
    printf 'local=%s domain=%s port=%s\n' \
      "$LOCAL_URL" \
      "$(state DOMAIN || true)" \
      "$(state PUBLIC_PORT || true)"
    ;;
  publish)
    publish
    ;;
  unpublish)
    unpublish
    ;;
esac
