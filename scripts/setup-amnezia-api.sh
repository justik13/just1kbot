#!/bin/bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 027

LOCAL_URL=http://127.0.0.1:4001
STATE=/etc/just1kbot-amnezia.conf
RATE=/etc/nginx/conf.d/just1kbot-amnezia-rate-limit.conf
ACME=/var/www/just1kbot-certbot
LOCK=/run/lock/just1kbot-amnezia.lock
ACTION=${1:-check}
[[ $# -eq 0 ]] || shift
DOMAIN=
EMAIL=
PORT=8443
DELETE_CERT=false
TX=
COMMITTED=false
ADDED=false
REMOVED=false

fail(){ printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }
usage(){ cat <<'TXT'
sudo bash scripts/setup-amnezia-api.sh check|status
sudo bash scripts/setup-amnezia-api.sh publish --domain api.example.com --email admin@example.com [--port 8443]
sudo bash scripts/setup-amnezia-api.sh unpublish --domain api.example.com [--delete-certificate]
Публичный reverse proxy создаётся только явной командой publish.
TXT
}

while (( $# )); do
  case "$1" in
    --domain) [[ $# -ge 2 ]] || fail '--domain value missing'; DOMAIN=$2; shift 2;;
    --email) [[ $# -ge 2 ]] || fail '--email value missing'; EMAIL=$2; shift 2;;
    --port) [[ $# -ge 2 ]] || fail '--port value missing'; PORT=$2; shift 2;;
    --delete-certificate) DELETE_CERT=true; shift;;
    -h|--help) usage; exit 0;;
    *) fail "unknown argument: $1";;
  esac
done
case "$ACTION" in -h|--help|help) usage; exit 0;; esac
[[ "$ACTION" =~ ^(check|status|publish|unpublish)$ ]] || { usage >&2; exit 2; }
[[ ${EUID:-$(id -u)} -eq 0 ]] || fail 'run as root'
install -d -m 0755 "$(dirname "$LOCK")"
exec 200>"$LOCK"
flock -n 200 || fail 'operation already running'

norm(){
  DOMAIN_VALUE="$1" python3 - <<'PY'
import os,re
v=os.environ['DOMAIN_VALUE'].strip().lower().rstrip('.')
p=re.compile(r'^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$')
if len(v)>253 or len(v.split('.'))<2 or any(not p.fullmatch(x) for x in v.split('.')):
    raise SystemExit(1)
print(v)
PY
}
health(){ curl --fail --show-error --silent --max-time 5 "$LOCAL_URL/health" >/dev/null; }
paths(){ CONF="/etc/nginx/sites-available/just1kbot-amnezia-$DOMAIN"; ENABLED="/etc/nginx/sites-enabled/just1kbot-amnezia-$DOMAIN"; }
state(){ [[ -f "$STATE" && ! -L "$STATE" ]] && awk -F= -v k="$1" '$1==k{v=$2}END{print v}' "$STATE"; }

backup_one(){
  local p=$1 n
  n=$(printf %s "$p" | sha256sum | awk '{print $1}')
  if [[ -e "$p" || -L "$p" ]]; then cp -a "$p" "$TX/$n"; else n=-; fi
  printf '%s\t%s\n' "$n" "$p" >>"$TX/list"
}
begin(){
  TX=$(mktemp -d /run/just1kbot-amnezia.XXXX)
  chmod 700 "$TX"
  backup_one "$CONF"; backup_one "$ENABLED"; backup_one "$RATE"; backup_one "$STATE"
}
rollback(){
  local rc=$? n p
  [[ "$COMMITTED" == true || -z "$TX" || ! -f "$TX/list" ]] && return "$rc"
  while IFS=$'\t' read -r n p; do
    rm -rf "$p"
    [[ "$n" == - ]] || cp -a "$TX/$n" "$p"
  done <"$TX/list"
  [[ "$ADDED" == true ]] && ufw delete allow "$PORT/tcp" >/dev/null 2>&1 || true
  [[ "$REMOVED" == true ]] && ufw allow "$PORT/tcp" >/dev/null 2>&1 || true
  nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
  return "$rc"
}
trap rollback EXIT INT TERM

firewall_add(){
  command -v ufw >/dev/null 2>&1 || return 0
  ufw status | grep -q '^Status: active' || return 0
  ufw allow 80/tcp >/dev/null
  if ! ufw status | grep -Eq "(^| )$PORT/tcp( |$)"; then
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
  [[ "$EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] || fail 'invalid email'
  [[ "$PORT" =~ ^[1-9][0-9]{0,4}$ ]] && (( PORT<=65535 && PORT!=80 )) || fail 'invalid port'
  health
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nginx certbot curl >/dev/null
  systemctl enable --now nginx >/dev/null
  paths; begin
  install -d -m 0755 "$ACME"
  printf 'limit_req_zone $binary_remote_addr zone=just1kbot_amnezia_api:10m rate=30r/s;\n' >"$RATE"
  http_conf
  nginx -t
  systemctl reload nginx
  firewall_add
  certbot certonly --webroot --webroot-path "$ACME" -d "$DOMAIN" -m "$EMAIL" --agree-tos --non-interactive
  [[ -f /etc/letsencrypt/live/$DOMAIN/fullchain.pem && -f /etc/letsencrypt/live/$DOMAIN/privkey.pem ]] || fail 'certificate missing'
  https_conf
  nginx -t
  systemctl reload nginx
  local state_tmp="${STATE}.new.$$"
  {
    printf 'DOMAIN=%s\n' "$DOMAIN"
    printf 'PUBLIC_PORT=%s\n' "$PORT"
    printf 'UFW_PUBLIC_ADDED=%s\n' "$ADDED"
  } >"$state_tmp"
  install -o root -g root -m 600 "$state_tmp" "$STATE"
  rm -f "$state_tmp"
  COMMITTED=true
  rm -rf "$TX"
  printf 'Published: https://%s:%s\n' "$DOMAIN" "$PORT"
}

unpublish(){
  DOMAIN=$(norm "$DOMAIN") || fail 'invalid domain'
  paths; begin
  local sd sp su
  sd=$(state DOMAIN || true); sp=$(state PUBLIC_PORT || true); su=$(state UFW_PUBLIC_ADDED || true)
  [[ -z "$sd" || "$sd" == "$DOMAIN" ]] || fail 'state domain mismatch'
  rm -f "$ENABLED" "$CONF"
  find /etc/nginx/sites-available -maxdepth 1 -type f -name 'just1kbot-amnezia-*' -print -quit | grep -q . || rm -f "$RATE"
  nginx -t
  systemctl reload nginx
  if [[ "$su" == true && "$sp" =~ ^[1-9][0-9]{0,4}$ ]] && (( sp<=65535 )); then
    PORT=$sp
    ufw delete allow "$PORT/tcp" >/dev/null 2>&1 && REMOVED=true || true
  fi
  rm -f "$STATE"
  [[ "$DELETE_CERT" == true ]] && certbot delete --cert-name "$DOMAIN" --non-interactive
  COMMITTED=true
  rm -rf "$TX"
  printf 'Public proxy removed; local API remains at %s\n' "$LOCAL_URL"
}

case "$ACTION" in
  check) health;;
  status) health; printf 'domain=%s port=%s\n' "$(state DOMAIN || true)" "$(state PUBLIC_PORT || true)";;
  publish) publish;;
  unpublish) unpublish;;
esac
