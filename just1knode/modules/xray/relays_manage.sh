#!/usr/bin/env bash
# =============================================================================
# JUST1KNODE - Управление Relay-нодами на Origin (modules/xray/relays_manage.sh)
# =============================================================================

NGINX_CONF_DIR="${NGINX_CONF_DIR:-/etc/nginx}"
NGINX_RELAYS_DIR="${NGINX_RELAYS_DIR:-${NGINX_CONF_DIR}/just1k_relays.d}"

auto_heal_relays_registry() {
    init_state_dir
    local healed=0
    local heal_out
    heal_out=$(python3 -c "
import glob, json, os, sys, tempfile

rf = sys.argv[1]
cfg_file = sys.argv[2]
backup_dir = sys.argv[3]

def is_valid_relays_file(path):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return isinstance(data, list) and len(data) > 0
    except Exception:
        return False

if is_valid_relays_file(rf):
    sys.exit(0)

relays = None

# 1. Попытка восстановить из свежей резервной копии
baks = sorted(glob.glob(os.path.join(backup_dir, 'relays_*.bak*')) + glob.glob(os.path.join(backup_dir, 'relays*.json*')), key=os.path.getmtime, reverse=True)
for b in baks:
    if is_valid_relays_file(b):
        try:
            with open(b, 'r', encoding='utf-8') as f:
                relays = json.load(f)
            print(f'[+] Реестр релеев автоматически восстановлен из резервной копии: {os.path.basename(b)}')
            break
        except Exception:
            continue

# 2. Реконструкция из живой конфигурации Xray (config.json)
if not relays and os.path.exists(cfg_file):
    try:
        with open(cfg_file, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        known_names = {'de': '🇩🇪 Германия', 'ee': '🇪🇪 Эстония', 'nl': '🇳🇱 Нидерланды', 'fi': '🇫🇮 Финляндия', 'se': '🇸🇪 Швеция', 'pl': '🇵🇱 Польша', 'fr': '🇫🇷 Франция', 'us': '🇺🇸 США', 'gb': '🇬🇧 Великобритания'}
        reconstructed = []
        for ob in cfg.get('outbounds', []):
            tag = ob.get('tag', '')
            if tag.startswith('just1k-wl-outbound-'):
                code = tag.replace('just1k-wl-outbound-', '')
                vnext = (ob.get('settings', {}).get('vnext') or [{}])[0]
                ip = vnext.get('address', '')
                port = vnext.get('port', 10443)
                uuid = ((vnext.get('users') or [{}])[0]).get('id', '')
                sec = ob.get('streamSettings', {}).get('security', 'tls')
                if sec == 'tls':
                    sni = ob.get('streamSettings', {}).get('tlsSettings', {}).get('serverName', '')
                    pk = ''
                    sid = ''
                else:
                    sni = ob.get('streamSettings', {}).get('realitySettings', {}).get('serverName', '')
                    pk = ob.get('streamSettings', {}).get('realitySettings', {}).get('publicKey', '')
                    sid = ob.get('streamSettings', {}).get('realitySettings', {}).get('shortId', '')
                in_tag = f'just1k-wl-inbound-{code}'
                in_ib = next((ib for ib in cfg.get('inbounds', []) if ib.get('tag') == in_tag), None)
                path = in_ib.get('streamSettings', {}).get('xhttpSettings', {}).get('path', f'/stream/{code}') if in_ib else f'/stream/{code}'
                in_port = in_ib.get('port') if in_ib else None
                name = known_names.get(code.lower(), f'Релей {code.upper()}')
                reconstructed.append({
                    'name': name,
                    'code': code,
                    'ip': ip,
                    'port': port,
                    'uuid': uuid,
                    'inbound_port': in_port,
                    'path': path,
                    'inbound_tag': in_tag,
                    'outbound_tag': tag,
                    'security': sec,
                    'public_key': pk,
                    'short_id': sid,
                    'sni': sni
                })
        if reconstructed:
            relays = reconstructed
            print(f'[+] Реестр релеев автоматически реконструирован из рабочего Xray config ({len(relays)} узлов)')
    except Exception as e:
        print(f'[!] Ошибка реконструкции: {e}')

if relays is not None:
    d = os.path.dirname(os.path.abspath(rf))
    os.makedirs(d, exist_ok=True)
    t_fd, t_path = tempfile.mkstemp(dir=d, suffix='.tmp')
    with os.fdopen(t_fd, 'w', encoding='utf-8') as fp:
        json.dump(relays, fp, ensure_ascii=False, indent=2)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(t_path, rf)
    try:
        import shutil
        shutil.chown(rf, user='root', group='xrayapi')
        os.chmod(rf, 0o660)
    except Exception:
        pass
    print('HEALED')
" "$RELAYS_FILE" "$XRAY_CONFIG" "${BACKUP_DIR:-/var/backups/just1knode}" 2>/dev/null || true)

    if echo "$heal_out" | grep -q "HEALED"; then
        echo -e "${GREEN}✔${NC} ${heal_out//HEALED/}"
        ensure_xray_api_healthy
    fi
}

list_relays() {
    title "СПИСОК АКТИВНЫХ RELAY-УЗЛОВ"
    init_state_dir
    auto_heal_relays_registry

    if [[ ! -s "$RELAYS_FILE" ]] || [[ "$(cat "$RELAYS_FILE")" == "[]" ]]; then
        info "На данном Origin-сервере нет подключенных Relay-узлов."
        return
    fi

    python3 -c "
import json, sys
rf = sys.argv[1]
try:
    with open(rf, 'r', encoding='utf-8') as f:
        relays = json.load(f)
    print(f'Всего релеев: {len(relays)}\n')
    print(f'{\"ИМЯ\":<18} {\"КОД\":<6} {\"IP\":<18} {\"ПОРТ\":<7} {\"РЕЖИМ\":<9} {\"SNI / ДОМЕН\":<26} {\"ПУТЬ\":<15}')
    print('-' * 102)
    for r in relays:
        name = r.get('name', '-')
        code = r.get('code', '-')
        ip = r.get('ip', '-')
        port = str(r.get('port', '-'))
        sec = r.get('security', '-')
        sni = r.get('sni', '-')
        path = r.get('path', '-')
        warning = ' ⚠️' if 'google.com' in sni else ''
        sni_display = f'{sni}{warning}'
        print(f'{name:<18} {code:<6} {ip:<18} {port:<7} {sec:<9} {sni_display:<26} {path:<15}')
except Exception as e:
    print(f'Ошибка чтения реестра релеев: {e}')
" "$RELAYS_FILE"
    echo ""
}

add_relay_node() {
    local name="${1:-}"
    local ip="${2:-}"
    local port="${3:-10443}"
    local uuid="${4:-}"
    local code="${5:-de}"
    code="$(echo "$code" | tr '[:upper:]' '[:lower:]')"
    local arg6="${6:-}"
    local arg7="${7:-}"
    local arg8="${8:-}"
    local arg9="${9:-}"
    local arg10="${10:-}"

    local security_type="tls"
    local pubkey=""
    local shortid=""
    local sni=""
    local badge=""

    if [[ "$arg6" == "tls" || "$arg6" == "reality" ]]; then
        security_type="$arg6"
        pubkey="$arg7"
        shortid="$arg8"
        sni="$arg9"
        badge="$arg10"
    elif [[ -n "$arg6" && "$arg6" != "-" ]]; then
        # Легаси-синтаксис (v2.1.2): 6-й аргумент являлся pubkey для REALITY
        security_type="reality"
        pubkey="$arg6"
        shortid="$arg7"
        sni="$arg8"
        badge="$arg9"
    else
        # arg6 пустой или "-"
        if [[ -n "$arg7" || -n "$arg8" ]]; then
            security_type="reality"
            pubkey="$arg7"
            shortid="$arg8"
            sni="$arg9"
            badge="$arg10"
        else
            security_type="tls"
            pubkey=""
            shortid=""
            sni="$arg9"
            badge="$arg10"
        fi
    fi

    [[ "$pubkey" == "-" ]] && pubkey=""
    [[ "$shortid" == "-" ]] && shortid=""
    [[ "$sni" == "-" ]] && sni=""

    local role
    role="$(get_state_val "role")"
    if [[ "$role" != "origin" ]]; then
        error "Управление Relay-узлами доступно ТОЛЬКО на Origin-сервере (текущая роль: ${role:-не установлена})."
    fi

    if [[ -z "$name" || -z "$ip" || -z "$uuid" ]]; then
        error "Имя, IP/Домен и UUID обязательны для добавления релея."
    fi

    # Валидация специфичных параметров безопасности
    if [[ "$security_type" == "tls" ]]; then
        if [[ -z "$sni" ]]; then
            if [[ -t 0 ]]; then
                read -rp "Введите домен / SNI для релея в режиме TLS: " sni_in || true
                sni="${sni_in:-}"
            fi
            if [[ -z "$sni" ]]; then
                error "Для режима TLS обязательно указание домена (SNI). Укажите домен релея."
                return 1
            fi
        fi
    elif [[ "$security_type" == "reality" ]]; then
        if [[ -z "$pubkey" ]]; then
            error "Для режима REALITY обязательно указание публичного ключа (PublicKey)."
            return 1
        fi
        if [[ -z "$sni" ]]; then
            if [[ -t 0 ]]; then
                read -rp "Введите домен / SNI для маскировки REALITY: " sni_in || true
                sni="${sni_in:-}"
            fi
            if [[ -z "$sni" ]]; then
                error "Для режима REALITY обязательно указание целевого SNI/домена."
                return 1
            fi
        fi
    fi

    # Санитизация кода страны во избежание path traversal
    if [[ ! "$code" =~ ^[a-z0-9_-]+$ ]]; then
        error "Недопустимый код страны: $code (разрешены только строчные буквы, цифры, дефис и подчеркивание)."
    fi

    local secret_path
    secret_path="$(get_state_val "secret_base_path" "/stream")"
    if [[ -z "$secret_path" ]]; then
        secret_path="/stream"
    fi

    init_state_dir
    local existing_relay_info
    existing_relay_info=$(python3 -c "
import json, os, sys
rf = sys.argv[1]
code = sys.argv[2].strip().lower()
if os.path.exists(rf):
    try:
        with open(rf, encoding='utf-8') as f:
            for r in json.load(f):
                if str(r.get('code', '')).strip().lower() == code:
                    print(f\"{r.get('name')}|{r.get('ip')}\")
                    sys.exit(0)
    except Exception:
        pass
" "$RELAYS_FILE" "$code" 2>/dev/null || true)

    if [[ -n "$existing_relay_info" ]]; then
        local old_name="${existing_relay_info%|*}"
        local old_ip="${existing_relay_info#*|}"
        if [[ "$old_ip" != "$ip" ]]; then
            warn "Внимание: релей с кодом '$code' уже существует в реестре ($old_name, IP: $old_ip). Запись и маршрут будут перезаписаны новыми параметрами ($name, IP: $ip)."
        fi
    fi

    acquire_just1knode_lock

    local relay_inbound_path="${secret_path}/${code}"
    local relay_inbound_tag="just1k-wl-inbound-${code}"
    local relay_outbound_tag="just1k-wl-outbound-${code}"

    manifest_begin "${NGINX_RELAYS_DIR}/${code}.conf"

    # Выделяем локальный порт (8004, 8005...)
    local next_port
    next_port=$(python3 -c "
import json, os
cfg = '$XRAY_CONFIG'
used_ports = set([8003])
if os.path.exists(cfg):
    try:
        with open(cfg) as f:
            c = json.load(f)
            for ib in c.get('inbounds', []):
                p = ib.get('port')
                if isinstance(p, int): used_ports.add(p)
    except: pass
p = 8004
while p in used_ports:
    p += 1
print(p)
")

    log "Обновление конфигурации Xray для моста '${name}' (Порт ${next_port}, путь: ${relay_inbound_path})..."
    if ! python3 -c "
import sys, json

cfg_file = sys.argv[1]
code = sys.argv[2]
in_tag = sys.argv[3]
out_tag = sys.argv[4]
in_path = sys.argv[5]
port = int(sys.argv[6])
r_ip = sys.argv[7]
r_port = int(sys.argv[8])
r_uuid = sys.argv[9]
r_sec = sys.argv[10]
r_pubkey = sys.argv[11]
r_shortid = sys.argv[12]
r_sni = sys.argv[13]

with open(cfg_file, 'r', encoding='utf-8') as f:
    cfg = json.load(f)

# Удаляем старые записи этого релея, если были (с защитой от любого регистра)
target_tags = {in_tag.lower(), f'just1k-wl-{code}'.lower(), f'inbound-{code}'.lower()}
target_out_tags = {out_tag.lower(), f'just1k-wl-out-{code}'.lower(), f'outbound-{code}'.lower()}
cfg['inbounds'] = [ib for ib in cfg.get('inbounds', []) if str(ib.get('tag', '')).strip().lower() not in target_tags]
cfg['outbounds'] = [ob for ob in cfg.get('outbounds', []) if str(ob.get('tag', '')).strip().lower() not in target_out_tags]

# 1. Добавляем локальный inbound для этого релея
new_ib = {
    'tag': in_tag,
    'listen': '127.0.0.1',
    'port': port,
    'protocol': 'vless',
    'settings': {'clients': [], 'decryption': 'none'},
    'streamSettings': {
        'network': 'xhttp',
        'xhttpSettings': {
            'mode': 'packet-up',
            'path': in_path,
            'uplinkHTTPMethod': 'GET',
            'uplinkDataPlacement': 'header',
            'uplinkDataKey': 'data',
            'scMaxEachPostBytes': 1000000,
            'scMaxConcurrentPosts': 1,
            'scMinPostsIntervalMs': 30,
            'serverMaxHeaderBytes': 65536,
            'xPaddingObfsMode': True,
            'xPaddingKey': 'dc',
            'xPaddingHeader': 'X-Cache',
            'xPaddingMethod': 'tokenish',
            'xPaddingPlacement': 'queryInHeader'
        }
    },
    'sniffing': {'enabled': True, 'destOverride': ['tls', 'http', 'quic'], 'routeOnly': False}
}
cfg['inbounds'].append(new_ib)

# 2. Добавляем outbound на зарубежный Relay
vnext = [{
    'address': r_ip,
    'port': r_port,
    'users': [{
        'id': r_uuid,
        'encryption': 'none',
        'flow': 'xtls-rprx-vision'
    }]
}]

stream_settings = {
    'network': 'tcp',
    'security': r_sec
}

if r_sec == 'reality':
    stream_settings['realitySettings'] = {
        'serverName': r_sni,
        'fingerprint': 'chrome',
        'show': False,
        'publicKey': r_pubkey,
        'shortId': r_shortid,
        'spiderX': ''
    }
else:
    stream_settings['tlsSettings'] = {
        'serverName': r_sni if r_sni else r_ip,
        'fingerprint': 'chrome',
        'alpn': ['h2', 'http/1.1']
    }

new_ob = {
    'tag': out_tag,
    'protocol': 'vless',
    'settings': {'vnext': vnext},
    'streamSettings': stream_settings
}
cfg['outbounds'].append(new_ob)

# 3. Добавляем inbound этого релея в правила прямого выхода в Рунет (just1k-wl-direct)
rules = cfg.setdefault('routing', {}).setdefault('rules', [])
rules = [r for r in rules if str(r.get('outboundTag', '')).strip().lower() not in target_out_tags]

for r in rules:
    if r.get('outboundTag') == 'just1k-wl-direct':
        # Relay inbounds MUST ONLY be in domain-based direct rules (ru_domains), NEVER in ip-based rules!
        if 'domain' in r:
            existing_ib = r.get('inboundTag', [])
            if isinstance(existing_ib, list):
                existing_ib = [t for t in existing_ib if str(t).strip().lower() not in target_tags]
                r['inboundTag'] = existing_ib + [in_tag]
            if 'domain:2ip.ru' not in r['domain']:
                r['domain'].append('domain:2ip.ru')
        elif 'ip' in r:
            # Exclude relay inbounds from geoip:ru to prevent Origin from resolving foreign domains
            existing_ib = r.get('inboundTag', [])
            if isinstance(existing_ib, list):
                r['inboundTag'] = [t for t in existing_ib if str(t).strip().lower() not in target_tags]

# Запрет BitTorrent (P2P трафик)
if not any(r.get('protocol') == ['bittorrent'] for r in rules):
    rules.insert(0, {
        'type': 'field',
        'protocol': ['bittorrent'],
        'outboundTag': 'just1k-wl-block'
    })

# Запрет SMTP (порт 25)
if not any((r.get('port') == '25' or r.get('port') == 25) for r in rules):
    rules.insert(0, {
        'type': 'field',
        'port': '25',
        'outboundTag': 'just1k-wl-block'
    })

# Вставляем правило выхода на Relay СТРОГО ПОСЛЕ правил прямого выхода для доменов РФ (dom_rule),
# но ДО любых IP-based правил, чтобы исключить DNS-резолвинг на Origin
dom_rule = next((r for r in rules if r.get('outboundTag') == 'just1k-wl-direct' and 'domain' in r), None)
if dom_rule:
    insert_idx = rules.index(dom_rule) + 1
else:
    direct_indices = [i for i, r in enumerate(rules) if r.get('outboundTag') == 'just1k-wl-direct']
    insert_idx = (min(direct_indices) + 1) if direct_indices else 0

rules.insert(insert_idx, {
    'type': 'field',
    'inboundTag': [in_tag],
    'outboundTag': out_tag
})

# Ensure default client traffic (Россия) always routes directly via Moscow IP
default_rule_found = False
for r in rules:
    if (r.get('inboundTag') == ['just1k-wl-default'] or 'just1k-wl-default' in r.get('inboundTag', [])) and 'domain' not in r and 'ip' not in r:
        r['inboundTag'] = ['just1k-wl-default']
        r['outboundTag'] = 'just1k-wl-direct'
        default_rule_found = True
        break
if not default_rule_found:
    rules.append({
        'type': 'field',
        'inboundTag': ['just1k-wl-default'],
        'outboundTag': 'just1k-wl-direct'
    })

cfg['routing']['rules'] = rules

for ob in cfg.get('outbounds', []):
    if ob.get('tag') == 'just1k-wl-direct' or ob.get('protocol') == 'freedom':
        ob.setdefault('settings', {})['domainStrategy'] = 'UseIPv4'

dns_conf = dict(cfg.get('dns', {}))
dns_conf['servers'] = [
    {
        'address': '77.88.8.8',
        'port': 53,
        'domains': [
            'geosite:category-ru',
            'geosite:tld-ru',
            'domain:ru',
            'domain:su',
            'domain:xn--p1ai',
            'domain:2ip.ru'
        ],
        'skipFallback': True
    },
    '195.208.4.1',
    '77.88.8.1'
]
dns_conf['queryStrategy'] = 'UseIPv4'
cfg['dns'] = dns_conf

with open(cfg_file, 'w', encoding='utf-8') as f:
    json.dump(cfg, f, indent=2)
try:
    import shutil
    shutil.chown(cfg_file, user='root', group='xrayapi')
    os.chmod(cfg_file, 0o640)
    os.chmod(os.path.dirname(os.path.abspath(cfg_file)), 0o755)
except Exception:
    pass
" "$XRAY_CONFIG" "$code" "$relay_inbound_tag" "$relay_outbound_tag" "$relay_inbound_path" "$next_port" "$ip" "$port" "$uuid" "$security_type" "$pubkey" "$shortid" "$sni"; then
        manifest_rollback
        error "Ошибка генерации конфигурации Xray для релея."
    fi
    ensure_xray_config_permissions "$XRAY_CONFIG"

    # Валидация конфигурации Xray
    if ! "$XRAY_BIN" run -test -config "$XRAY_CONFIG"; then
        manifest_rollback
        error "Ошибка тестирования Xray при добавлении релея $name ($code). Изменения полностью отменены."
    fi

    # Обновление relays.json (Durable-by-Default: атомарная запись через tempfile)
    python3 -c "
import json, os, sys, tempfile

def safe_arg(val):
    if not isinstance(val, str):
        return val
    try:
        return val.encode(sys.getfilesystemencoding(), 'surrogateescape').decode('utf-8', 'replace')
    except Exception:
        return val

rf = sys.argv[1]
code = safe_arg(sys.argv[2]).strip().lower()
name = safe_arg(sys.argv[3]).strip()
ip = sys.argv[4]
port = int(sys.argv[5])
in_port = int(sys.argv[6])
in_path = sys.argv[7]
in_tag = sys.argv[8]
out_tag = sys.argv[9]
sec = sys.argv[10]
sni = sys.argv[11]
badge = safe_arg(sys.argv[12]).strip() if len(sys.argv) > 12 else ''

relays = []
if os.path.exists(rf):
    try:
        with open(rf, 'r', encoding='utf-8', errors='replace') as f:
            data = json.load(f)
            if isinstance(data, list):
                relays = data
    except Exception:
        relays = []

relays = [r for r in relays if isinstance(r, dict) and str(r.get('code', '')).strip().lower() != code]
new_entry = {
    'name': name,
    'code': code,
    'ip': ip,
    'port': port,
    'inbound_port': in_port,
    'path': in_path,
    'inbound_tag': in_tag,
    'outbound_tag': out_tag,
    'security': sec,
    'sni': sni
}
if badge:
    new_entry['badge'] = badge[:30]

relays.append(new_entry)

d = os.path.dirname(os.path.abspath(rf))
os.makedirs(d, exist_ok=True)
t_fd, t_path = tempfile.mkstemp(dir=d, suffix='.tmp')
with os.fdopen(t_fd, 'w', encoding='utf-8', errors='replace') as fp:
    json.dump(relays, fp, ensure_ascii=False, indent=2)
    fp.flush()
    os.fsync(fp.fileno())
os.replace(t_path, rf)
try:
    import shutil
    shutil.chown(rf, user='root', group='xrayapi')
    os.chmod(rf, 0o660)
except Exception:
    pass
" "$RELAYS_FILE" "$code" "$name" "$ip" "$port" "$next_port" "$relay_inbound_path" "$relay_inbound_tag" "$relay_outbound_tag" "$security_type" "$sni" "$badge"

    # Синхронизация пулов Nginx upstreams (теперь relays.json содержит новый релей)
    if ! sync_xhttp_upstreams_conf; then
        manifest_rollback
        error "Ошибка генерации upstream-конфигурации Nginx при добавлении релея $name ($code). Изменения полностью отменены."
    fi

    # Генерация Nginx Location для этого релея
    mkdir -p "$NGINX_RELAYS_DIR"
    find "$NGINX_RELAYS_DIR" -maxdepth 1 -type f -iname "${code}.conf" ! -name "${code}.conf" -delete 2>/dev/null || true
    local nginx_relay_conf="${NGINX_RELAYS_DIR}/${code}.conf"
    local relay_base_path="${relay_inbound_path%/}"
    cat > "$nginx_relay_conf" <<EOF
# Relay location for ${name} (${code})
location = ${relay_base_path} {
    return 404;
}

location ^~ ${relay_inbound_path} {
    proxy_pass http://xray_xhttp_relay_${code};
    proxy_method \$xhttp_proxy_method;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_pass_request_headers on;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    client_max_body_size 0;
    proxy_buffering off;
    proxy_request_buffering off;
    proxy_max_temp_file_size 0;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    add_header Cache-Control "no-store, no-cache" always;
    add_header CDN-Cache-Control "no-store" always;
    add_header Pragma "no-cache" always;
    add_header Expires "0" always;
    add_header X-Accel-Buffering no always;
    add_header Accept-Ranges none always;
}
EOF

    # Валидация Nginx
    if ! nginx -t; then
        manifest_rollback
        error "Ошибка конфигурации Nginx при добавлении релея $name ($code). Изменения полностью отменены."
    fi

    if ! systemctl reload nginx; then
        manifest_rollback
        error "Не удалось перезагрузить Nginx после добавления релея $name ($code). Выполнен откат."
    fi

    set +e
    systemctl restart xray
    local xray_rc=$?
    set -e
    if [[ $xray_rc -ne 0 ]] || ! systemctl is-active --quiet xray; then
        manifest_rollback
        error "Xray не запустился после добавления релея $name ($code). Выполнен полный откат."
    fi
    ensure_xray_api_healthy || warn "Служба xray-api не ответила вовремя. Проверьте её статус вручную через 'systemctl status xray-api'."
    manifest_commit

    log "Relay '${name}' (код: ${code}) успешно добавлен и подключен к шлюзу Origin!"
}

remove_relay_node() {
    local target="$1"
    if [[ -z "$target" ]]; then
        error "Укажите код страны или имя релея для удаления."
    fi

    local role
    role="$(get_state_val "role")"
    if [[ "$role" != "origin" ]]; then
        error "Удаление Relay-узлов доступно ТОЛЬКО на Origin-сервере."
    fi

    init_state_dir
    local code
    code=$(python3 -c "
import json, os
rf = '$RELAYS_FILE'
target = '$target'.lower()
code = ''
if os.path.exists(rf):
    try:
        with open(rf, encoding='utf-8') as f:
            for r in json.load(f):
                if str(r.get('code', '')).lower() == target or str(r.get('name', '')).lower() == target:
                    code = str(r.get('code', '')).strip().lower()
                    break
    except Exception:
        pass
print(code)
")

    if [[ -z "$code" ]]; then
        warn "Relay '${target}' не найден в активном реестре."
        return
    fi

    acquire_just1knode_lock
    manifest_begin "${NGINX_RELAYS_DIR}/${code}.conf"

    # Удаление из Xray
    local in_tag="just1k-wl-inbound-${code}"
    local out_tag="just1k-wl-outbound-${code}"

    python3 -c "
import json, os
cfg_file = '$XRAY_CONFIG'
code = '$code'
in_tag = '$in_tag'
out_tag = '$out_tag'
rf = '$RELAYS_FILE'

with open(cfg_file) as f: cfg = json.load(f)
target_tags = {in_tag.lower(), f'just1k-wl-{code}'.lower(), f'inbound-{code}'.lower()}
target_out_tags = {out_tag.lower(), f'just1k-wl-out-{code}'.lower(), f'outbound-{code}'.lower()}

cfg['inbounds'] = [ib for ib in cfg.get('inbounds', []) if str(ib.get('tag', '')).strip().lower() not in target_tags]
cfg['outbounds'] = [ob for ob in cfg.get('outbounds', []) if str(ob.get('tag', '')).strip().lower() not in target_out_tags]

# Очищаем тег из правил маршрутизации
if 'routing' in cfg and 'rules' in cfg['routing']:
    cfg['routing']['rules'] = [r for r in cfg['routing']['rules'] if str(r.get('outboundTag', '')).strip().lower() not in target_out_tags]
    for r in cfg['routing']['rules']:
        if r.get('outboundTag') == 'just1k-wl-direct':
            existing_ib = r.get('inboundTag', [])
            if isinstance(existing_ib, list):
                r['inboundTag'] = [t for t in existing_ib if str(t).strip().lower() not in target_tags]

# Default inbound traffic for Russia always routes directly via Moscow IP
default_rule_found = False
for r in cfg.get('routing', {}).get('rules', []):
    if (r.get('inboundTag') == ['just1k-wl-default'] or 'just1k-wl-default' in r.get('inboundTag', [])) and 'domain' not in r and 'ip' not in r:
        r['inboundTag'] = ['just1k-wl-default']
        r['outboundTag'] = 'just1k-wl-direct'
        default_rule_found = True
        break
if not default_rule_found:
    cfg.setdefault('routing', {}).setdefault('rules', []).append({
        'type': 'field',
        'inboundTag': ['just1k-wl-default'],
        'outboundTag': 'just1k-wl-direct'
    })

with open(cfg_file, 'w', encoding='utf-8') as f:
    json.dump(cfg, f, indent=2)
"
    ensure_xray_config_permissions "$XRAY_CONFIG"

    # Удаление Nginx конфига (с поддержкой любого регистра: de.conf, DE.conf)
    rm -f "${NGINX_RELAYS_DIR}/${code}.conf"
    if [[ -d "$NGINX_RELAYS_DIR" ]]; then
        find "$NGINX_RELAYS_DIR" -maxdepth 1 -type f -iname "${code}.conf" -delete 2>/dev/null || true
    fi

    # Удаление из relays.json (Durable-by-Default: атомарная запись через tempfile)
    python3 -c "
import json, os, sys, tempfile

def safe_arg(val):
    if not isinstance(val, str):
        return val
    try:
        return val.encode(sys.getfilesystemencoding(), 'surrogateescape').decode('utf-8', 'replace')
    except Exception:
        return val

rf = sys.argv[1]
code = safe_arg(sys.argv[2]).strip().lower()
relays = []
if os.path.exists(rf):
    try:
        with open(rf, 'r', encoding='utf-8', errors='replace') as f:
            data = json.load(f)
            if isinstance(data, list):
                relays = data
    except Exception:
        relays = []
relays = [r for r in relays if isinstance(r, dict) and str(r.get('code', '')).strip().lower() != code]
d = os.path.dirname(os.path.abspath(rf))
os.makedirs(d, exist_ok=True)
t_fd, t_path = tempfile.mkstemp(dir=d, suffix='.tmp')
with os.fdopen(t_fd, 'w', encoding='utf-8', errors='replace') as fp:
    json.dump(relays, fp, ensure_ascii=False, indent=2)
    fp.flush()
    os.fsync(fp.fileno())
os.replace(t_path, rf)
try:
    import shutil
    shutil.chown(rf, user='root', group='xrayapi')
    os.chmod(rf, 0o660)
except Exception:
    pass
" "$RELAYS_FILE" "$code"

    if ! sync_xhttp_upstreams_conf; then
        manifest_rollback
        error "Ошибка генерации upstream-конфигурации Nginx при удалении релея $target. Изменения полностью отменены."
    fi

    if ! nginx -t; then
        manifest_rollback
        error "Ошибка валидации Nginx при удалении релея $target. Изменения полностью отменены."
    fi

    if ! "$XRAY_BIN" run -test -config "$XRAY_CONFIG"; then
        manifest_rollback
        error "Ошибка тестирования Xray при удалении релея $target. Изменения полностью отменены."
    fi

    if ! systemctl reload nginx; then
        manifest_rollback
        error "Не удалось перезагрузить Nginx при удалении релея $target. Выполнен откат."
    fi

    set +e
    systemctl restart xray
    local xray_rc=$?
    set -e
    if [[ $xray_rc -ne 0 ]] || ! systemctl is-active --quiet xray; then
        manifest_rollback
        error "Xray не запустился после удаления релея $target. Выполнен полный откат."
    fi
    ensure_xray_api_healthy || warn "Служба xray-api не ответила вовремя. Проверьте её статус вручную через 'systemctl status xray-api'."
    manifest_commit

    log "Relay '${target}' (код: ${code}) успешно удален."
}

rename_relay_node() {
    local target="$1"
    local new_name="$2"
    if [[ -z "$target" || -z "$new_name" ]]; then
        error "Использование: just1knode relay rename <код_или_текущее_имя> <новое_название>"
    fi

    local role
    role="$(get_state_val "role")"
    if [[ "$role" != "origin" ]]; then
        error "Переименование Relay-узлов доступно ТОЛЬКО на Origin-сервере."
    fi

    init_state_dir
    local updated
    updated=$(python3 -c "
import json, os, sys, tempfile

def safe_arg(val):
    if not isinstance(val, str):
        return val
    try:
        return val.encode(sys.getfilesystemencoding(), 'surrogateescape').decode('utf-8', 'replace')
    except Exception:
        return val

rf = sys.argv[1]
target = safe_arg(sys.argv[2]).strip().lower()
new_name = safe_arg(sys.argv[3]).strip()

if not os.path.exists(rf):
    print('no_file')
    sys.exit(0)

try:
    with open(rf, 'r', encoding='utf-8', errors='replace') as f:
        relays = json.load(f)
except Exception as e:
    print(f'read_error: {e}')
    sys.exit(0)

found = False
for r in relays:
    if not isinstance(r, dict):
        continue
    c = str(r.get('code') or '').strip().lower()
    n = str(r.get('name') or '').strip().lower()
    if c == target or n == target:
        r['name'] = new_name
        found = True
        break

if not found:
    print('not_found')
    sys.exit(0)

try:
    d = os.path.dirname(os.path.abspath(rf))
    os.makedirs(d, exist_ok=True)
    t_fd, t_path = tempfile.mkstemp(dir=d, suffix='.tmp')
    with os.fdopen(t_fd, 'w', encoding='utf-8', errors='replace') as fp:
        json.dump(relays, fp, ensure_ascii=False, indent=2)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(t_path, rf)
    try:
        import shutil
        shutil.chown(rf, user='root', group='xrayapi')
        os.chmod(rf, 0o660)
    except Exception:
        pass
    print('ok')
except Exception as e:
    print(f'write_error: {e}')
" "$RELAYS_FILE" "$target" "$new_name")

    if [[ "$updated" == "ok" ]]; then
        if systemctl is-active --quiet xray-api; then
            systemctl restart xray-api
        fi
        log "Релей '$target' успешно переименован в '$new_name'."
    elif [[ "$updated" == "not_found" ]]; then
        error "Relay с кодом или именем '$target' не найден в реестре."
    else
        error "Ошибка переименования: $updated (цель: '$target')."
    fi
}

update_relay_sni() {
    local relay_target="${1:-}"
    local new_sni="${2:-}"
    local new_sec="${3:-tls}"
    local force_flag="${4:-}"

    local role
    role="$(get_state_val "role")"
    if [[ "$role" != "origin" ]]; then
        error "Управление Relay-узлами доступно ТОЛЬКО на Origin-сервере (текущая роль: ${role:-не установлена})."
        return 1
    fi

    # Интерактивный выбор релея, если аргумент не передан
    if [[ -z "$relay_target" ]]; then
        local list_output
        list_output="$(get_relays_tsv)"
        if [[ -z "$list_output" ]]; then
            warn "Список Relay-узлов пуст."
            return 1
        fi
        echo -e "\n${BOLD}=== ВЫБЕРИТЕ RELAY ДЛЯ НАСТРОЙКИ ДОМЕНА (SNI / TLS) ===${NC}\n"
        local codes=()
        local names=()
        local ips=()
        local snis=()
        local secs=()
        local count=0
        while IFS=$'\t' read -r num code name ip sni sec; do
            count=$((count + 1))
            codes+=("$code")
            names+=("$name")
            ips+=("$ip")
            snis+=("$sni")
            secs+=("$sec")
            local warn_badge=""
            if [[ "$sni" == *"google.com"* || "$sec" == "reality" ]]; then
                warn_badge=" ${YELLOW}[${sec:-reality} ⚠️]${NC}"
            else
                warn_badge=" ${GREEN}[${sec:-tls} ✔]${NC}"
            fi
            echo -e "  ${BOLD}[$count]${NC} $name (код: ${CYAN}$code${NC}, IP: $ip, SNI: ${sni:-не задан})${warn_badge}"
        done <<< "$list_output"
        echo -e "  ${BOLD}[0]${NC} ⬅️  Отмена\n"
        read -rp "Выберите номер релея [0-$count]: " r_num
        if [[ "$r_num" =~ ^[1-9][0-9]*$ ]] && (( r_num >= 1 && r_num <= count )); then
            relay_target="${codes[$((r_num - 1))]}"
            local sel_name="${names[$((r_num - 1))]}"
            local sel_sni="${snis[$((r_num - 1))]}"
            echo -e "\nВыбран Relay: ${BOLD}$sel_name${NC} (код: $relay_target, текущий SNI: $sel_sni)"
        else
            log "Отмена."
            return 0
        fi
    fi

    if [[ -z "$new_sni" ]]; then
        read -rp "Введите домен / SNI для релея '$relay_target' (например: ${relay_target}.yourdomain.com): " new_sni_in
        new_sni="${new_sni_in:-}"
        if [[ -z "$new_sni" ]]; then
            error "Новый домен / SNI не может быть пустым."
            return 1
        fi
    fi

    # Проверка на кириллические символы
    local has_cyrillic
    has_cyrillic=$(python3 -c "import sys, re; print('YES' if re.search(r'[\u0400-\u04FF]', sys.argv[1]) else 'NO')" "$new_sni" 2>/dev/null || true)
    if [[ "$has_cyrillic" == "YES" ]]; then
        error "Домен '$new_sni' содержит русские (кириллические) буквы! Введите корректный латинский домен."
        return 1
    fi

    init_state_dir
    auto_heal_relays_registry

    acquire_just1knode_lock
    manifest_begin

    log "Обновление SNI и режима безопасности для релея '$relay_target' на '$new_sni' (${new_sec})..."

    local update_res
    update_res=$(python3 -c "
import json, os, sys, tempfile

cfg_file = sys.argv[1]
rf = sys.argv[2]
target = sys.argv[3].strip().lower()
new_sni = sys.argv[4].strip()
new_sec = sys.argv[5].strip().lower()
if new_sec not in ('tls', 'reality'):
    new_sec = 'tls'

if not os.path.exists(cfg_file):
    print('ERROR: config.json not found')
    sys.exit(1)

with open(cfg_file, 'r', encoding='utf-8') as f:
    cfg = json.load(f)

relays = []
if os.path.exists(rf):
    try:
        with open(rf, 'r', encoding='utf-8') as f:
            relays = json.load(f)
    except Exception:
        relays = []

# Поиск целевого релея по индексу, коду или имени
matched_code = None
target_ip = None
if target.isdigit():
    idx = int(target) - 1
    if 0 <= idx < len(relays):
        matched_code = relays[idx].get('code')
        target_ip = relays[idx].get('ip')
        relays[idx]['sni'] = new_sni
        relays[idx]['security'] = new_sec

if not matched_code:
    for r in relays:
        if str(r.get('code', '')).lower() == target or str(r.get('name', '')).lower() == target:
            matched_code = r.get('code')
            target_ip = r.get('ip')
            r['sni'] = new_sni
            r['security'] = new_sec
            break

if not matched_code:
    # Попробуем найти в outbounds Xray напрямую
    for ob in cfg.get('outbounds', []):
        tag = ob.get('tag', '')
        if tag.startswith('just1k-wl-outbound-'):
            c = tag.replace('just1k-wl-outbound-', '')
            if c.lower() == target:
                matched_code = c
                break

if not matched_code:
    print(f'NOT_FOUND: Relay {target} not found in relays registry or config')
    sys.exit(1)

out_tag = f'just1k-wl-outbound-{matched_code}'
target_ob = next((ob for ob in cfg.get('outbounds', []) if ob.get('tag') == out_tag), None)
if not target_ob:
    print(f'NOT_FOUND: Outbound {out_tag} not found in Xray config')
    sys.exit(1)

if not target_ip:
    vnext = target_ob.get('settings', {}).get('vnext', [{}])
    if vnext:
        target_ip = vnext[0].get('address')

if not any(r.get('code') == matched_code for r in relays):
    relays.append({
        'code': matched_code,
        'name': f'Релей {matched_code.upper()}',
        'ip': target_ip or '',
        'sni': new_sni,
        'security': new_sec
    })

# Валидация DNS для режима TLS во избежание NDPI_UNRESOLVED_HOSTNAME (сверка SNI ➔ DNS)
if new_sec == 'tls' and target_ip and os.environ.get('JUST1KNODE_SKIP_DNS_CHECK') != '1':
    import socket
    try:
        addr_info = socket.getaddrinfo(new_sni, None, socket.AF_INET)
        resolved_ips = {ai[4][0] for ai in addr_info if ai[4]}
        if target_ip not in resolved_ips:
            print(f'WARN_DNS_MISMATCH|{target_ip}|{\",\".join(resolved_ips)}')
    except Exception as e:
        print(f'WARN_DNS_ERROR|{target_ip}|{e}')

st = target_ob.setdefault('streamSettings', {})
st['security'] = new_sec

if new_sec == 'tls':
    st.pop('realitySettings', None)
    st['tlsSettings'] = {
        'serverName': new_sni,
        'fingerprint': 'chrome',
        'alpn': ['h2', 'http/1.1']
    }
else:
    st.pop('tlsSettings', None)
    rs = st.setdefault('realitySettings', {})
    rs['serverName'] = new_sni
    rs.setdefault('fingerprint', 'chrome')
    rs.setdefault('show', False)
    if not rs.get('publicKey'):
        # Check if stored in relays.json
        pub = next((r.get('public_key') or r.get('pubkey') for r in relays if r.get('code') == matched_code and (r.get('public_key') or r.get('pubkey'))), None)
        if pub and pub != '-':
            rs['publicKey'] = pub
        else:
            print(f'ERROR: Outbound {out_tag} lacks publicKey for REALITY mode')
            sys.exit(1)

# Сохраняем обновленный config.json
d = os.path.dirname(os.path.abspath(cfg_file))
t_fd, t_path = tempfile.mkstemp(dir=d, suffix='.tmp')
with os.fdopen(t_fd, 'w', encoding='utf-8') as fp:
    json.dump(cfg, fp, indent=2)
    fp.flush()
    os.fsync(fp.fileno())
os.replace(t_path, cfg_file)


# Сохраняем обновленный relays.json
if relays:
    rd = os.path.dirname(os.path.abspath(rf))
    rt_fd, rt_path = tempfile.mkstemp(dir=rd, suffix='.tmp')
    with os.fdopen(rt_fd, 'w', encoding='utf-8') as fp:
        json.dump(relays, fp, ensure_ascii=False, indent=2)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(rt_path, rf)
    try:
        import shutil
        shutil.chown(rf, user='root', group='xrayapi')
        os.chmod(rf, 0o660)
    except Exception:
        pass

print(f'OK:{matched_code}')
" "$XRAY_CONFIG" "$RELAYS_FILE" "$relay_target" "$new_sni" "$new_sec" 2>/dev/null || true)

    if ! echo "$update_res" | grep -q "^OK:"; then
        manifest_rollback
        error "Ошибка обновления релея: ${update_res:-Неизвестная ошибка}."
        return 1
    fi
    ensure_xray_config_permissions "$XRAY_CONFIG"

    if echo "$update_res" | grep -q "WARN_DNS_"; then
        local dns_warn
        dns_warn=$(echo "$update_res" | grep "WARN_DNS_" | head -n1)
        if echo "$dns_warn" | grep -q "^WARN_DNS_MISMATCH"; then
            local exp_ip
            exp_ip=$(echo "$dns_warn" | cut -d'|' -f2)
            local act_ips
            act_ips=$(echo "$dns_warn" | cut -d'|' -f3)
            warn "ВНИМАНИЕ: Домен '$new_sni' в DNS указывает на [$act_ips], а ожидаемый IP релея: $exp_ip!"
            warn "Несовпадение SNI и DNS на линке к зарубежному релею может вызвать блокировку ТСПУ (NDPI_UNRESOLVED_HOSTNAME)."
        else
            local err_detail
            err_detail=$(echo "$dns_warn" | cut -d'|' -f3)
            warn "ВНИМАНИЕ: Не удалось разрезолвить домен '$new_sni' в DNS ($err_detail)."
        fi

        if [[ -t 0 ]]; then
            echo -e "${YELLOW}Вы уверены, что хотите применить этот SNI? [y/N]: ${NC}"
            local ans=""
            read -rp "" ans || ans="n"
            if [[ "${ans,,}" != "y" && "${ans,,}" != "yes" ]]; then
                manifest_rollback
                error "Операция отменена пользователем."
                return 1
            fi
        else
            if [[ "$force_flag" != "--force" && "${JUST1KNODE_FORCE:-0}" != "1" ]]; then
                manifest_rollback
                error "DNS-валидация не пройдена в неинтерактивном режиме для '$new_sni'. Передайте --force или JUST1KNODE_FORCE=1 для принудительного применения."
                return 1
            fi
            warn "Неинтерактивный режим: принудительное применение SNI с несовпадающим DNS (--force)."
        fi
    fi

    local matched_code
    matched_code=$(echo "$update_res" | grep "^OK:" | head -n1 | cut -d: -f2)

    if ! "$XRAY_BIN" run -test -config "$XRAY_CONFIG"; then
        manifest_rollback
        error "Ошибка тестирования сгенерированной конфигурации Xray! Изменения отменены."
        return 1
    fi

    set +e
    systemctl restart xray
    local xray_rc=$?
    set -e
    if [[ $xray_rc -ne 0 ]] || ! systemctl is-active --quiet xray; then
        manifest_rollback
        error "Xray не смог запуститься после обновления релея '${matched_code:-$relay_target}'. Выполнен полный откат."
        return 1
    fi

    ensure_xray_api_healthy

    manifest_commit
    log "✔ Relay '$matched_code' успешно переключен на домен '$new_sni' (режим: $new_sec)!"
    return 0
}

get_relays_tsv() {
    python3 -c "
import json, os
rf = '$RELAYS_FILE'
if os.path.exists(rf):
    try:
        with open(rf, 'r', encoding='utf-8', errors='replace') as f:
            data = json.load(f)
        for i, r in enumerate(data, 1):
            print(f\"{i}\t{r.get('code','')}\t{r.get('name','')}\t{r.get('ip','')}\t{r.get('sni','')}\t{r.get('security','')}\")
    except:
        pass
"
}

manage_relays_menu() {
    title "УПРАВЛЕНИЕ RELAY-НОДАМИ НА ORIGIN"
    check_root
    init_state_dir

    local role
    role="$(get_state_val "role")"
    if [[ "$role" != "origin" ]]; then
        error "Управление Relay-узлами доступно ТОЛЬКО на Origin-сервере (текущая роль: ${role:-не установлена})."
    fi

    echo -e "  ${BOLD}[1]${NC} ➕ Добавить новый Relay-узел"
    echo -e "  ${BOLD}[2]${NC} ➖ Удалить Relay-узел"
    echo -e "  ${BOLD}[3]${NC} ✏️  Переименовать Relay-узел"
    echo -e "  ${BOLD}[4]${NC} 📋 Список активных Relay-узлов"
    echo -e "  ${BOLD}[5]${NC} 🔐 Изменить домен / SNI Relay-узла (VLESS TLS)"
    echo -e "  ${BOLD}[0]${NC} ⬅️  Назад"
    echo ""
    read -rp "Выберите действие [0-5]: " r_choice

    case "$r_choice" in
        1)
            echo -e "\n${BOLD}=== СПОСОБ ДОБАВЛЕНИЯ RELAY-УЗЛА ===${NC}\n"
            echo -e "  ${BOLD}[1]${NC} 📋 Вставить готовую строку 'just1knode relay add ...' (в 1 клик)"
            echo -e "  ${BOLD}[2]${NC} ✍️  Заполнить параметры вручную по шагам"
            echo -e "  ${BOLD}[0]${NC} ⬅️  Отмена\n"
            read -rp "Выберите способ [0-2]: " add_mode
            if [[ "$add_mode" == "1" ]]; then
                echo -e "\nВставьте команду, которую выдал Relay-сервер при установке:"
                read -rp "> " paste_cmd
                if [[ -n "$paste_cmd" ]]; then
                    local eval_args
                    eval_args=$(python3 -c "
import shlex, sys, json
cmd = sys.argv[1].strip()
try:
    tokens = shlex.split(cmd)
    while tokens and (tokens[0].endswith('just1knode') or tokens[0] in ('sudo', 'relay', 'add')):
        tokens = tokens[1:]
    if len(tokens) >= 5:
        name, ip, port, uuid, code = tokens[0], tokens[1], tokens[2], tokens[3], tokens[4]
        arg5 = tokens[5] if len(tokens) > 5 else ''
        if arg5 in ('tls', 'reality'):
            sec = arg5
            pk = tokens[6] if len(tokens) > 6 and tokens[6] != '-' else ''
            sid = tokens[7] if len(tokens) > 7 and tokens[7] != '-' else ''
            sni = tokens[8] if len(tokens) > 8 and tokens[8] != '-' else ''
            badge = tokens[9] if len(tokens) > 9 else ''
        elif arg5 and arg5 != '-':
            # Legacy command: 6th token was pubkey for reality
            sec = 'reality'
            pk = arg5
            sid = tokens[6] if len(tokens) > 6 and tokens[6] != '-' else ''
            sni = tokens[7] if len(tokens) > 7 and tokens[7] != '-' else ''
            badge = tokens[8] if len(tokens) > 8 else ''
        else:
            sec = 'tls'
            pk = ''
            sid = ''
            sni = tokens[8] if len(tokens) > 8 and tokens[8] != '-' else ''
            badge = tokens[9] if len(tokens) > 9 else ''
        print(' '.join(shlex.quote(x) for x in [name, ip, port, uuid, code, sec, pk, sid, sni, badge]))
    else:
        sys.exit(1)
except Exception:
    sys.exit(1)
" "$paste_cmd" 2>/dev/null || true)
                    if [[ -n "$eval_args" ]]; then
                        eval "add_relay_node $eval_args"
                    else
                        error "Не удалось распознать аргументы команды. Проверьте формат строки."
                    fi
                fi
            elif [[ "$add_mode" == "2" ]]; then
                read -rp "Название локации (например: Германия): " r_name
                read -rp "IP или Домен Relay сервера: " r_ip
                read -rp "Порт Relay сервера [по умолчанию: 10443]: " r_port
                r_port="${r_port:-10443}"
                read -rp "UUID туннеля Relay: " r_uuid
                read -rp "Код страны (например: de, nl, se) [по умолчанию: de]: " r_code
                r_code="${r_code:-de}"
                echo -e "Тип безопасности моста:"
                echo -e "  [1] TLS (Доменный сертификат Let's Encrypt на личном домене — Рекомендуется)"
                echo -e "  [2] REALITY (Бессертификатный x25519 по IP)"
                read -rp "Выберите тип [1/2, по умолчанию 1]: " t_choice
                t_choice="${t_choice:-1}"
                local r_sec="tls"
                local r_pubkey=""
                local r_shortid=""
                local r_sni=""
                if [[ "$t_choice" == "1" ]]; then
                    r_sec="tls"
                    read -rp "TLS Домен / SNI Relay-ноды (например: ${r_code}.yourdomain.com): " r_sni_in
                    r_sni="$r_sni_in"
                    if [[ -z "$r_sni" ]]; then error "Домен SNI обязателен для TLS."; return 1; fi
                else
                    r_sec="reality"
                    read -rp "REALITY Public Key: " r_pubkey
                    read -rp "REALITY Short ID: " r_shortid
                    read -rp "REALITY SNI: " r_sni_in
                    r_sni="$r_sni_in"
                    if [[ -z "$r_sni" ]]; then error "SNI обязателен для REALITY."; return 1; fi
                fi
                read -rp "Бейдж узла в INCY (например: ⚡ Зарубежный узел, Enter по умолчанию): " r_badge
                add_relay_node "$r_name" "$r_ip" "$r_port" "$r_uuid" "$r_code" "$r_sec" "$r_pubkey" "$r_shortid" "$r_sni" "$r_badge"
            fi
            ;;
        2)
            local list_output
            list_output="$(get_relays_tsv)"
            if [[ -z "$list_output" ]]; then
                warn "Список Relay-узлов пуст."
                return
            fi
            echo -e "\n${BOLD}=== ВЫБЕРИТЕ RELAY ДЛЯ УДАЛЕНИЯ ===${NC}\n"
            local codes=()
            local names=()
            local count=0
            while IFS=$'\t' read -r num code name ip sni sec; do
                count=$((count + 1))
                codes+=("$code")
                names+=("$name")
                echo -e "  ${BOLD}[$count]${NC} $name (код: ${CYAN}$code${NC}, IP: $ip)"
            done <<< "$list_output"
            echo -e "  ${BOLD}[0]${NC} ⬅️  Отмена\n"
            read -rp "Выберите номер релея для удаления [0-$count]: " r_num
            if [[ "$r_num" =~ ^[1-9][0-9]*$ ]] && (( r_num >= 1 && r_num <= count )); then
                local sel_code="${codes[$((r_num - 1))]}"
                local sel_name="${names[$((r_num - 1))]}"
                read -rp "Вы уверены, что хотите удалить Relay '$sel_name' ($sel_code)? [y/N]: " confirm_del
                if [[ "$confirm_del" =~ ^[YyДд]$ ]]; then
                    remove_relay_node "$sel_code"
                else
                    log "Удаление отменено."
                fi
            fi
            ;;
        3)
            local list_output
            list_output="$(get_relays_tsv)"
            if [[ -z "$list_output" ]]; then
                warn "Список Relay-узлов пуст."
                return
            fi
            echo -e "\n${BOLD}=== ВЫБЕРИТЕ RELAY ДЛЯ ПЕРЕИМЕНОВАНИЯ ===${NC}\n"
            local codes=()
            local names=()
            local count=0
            while IFS=$'\t' read -r num code name ip sni sec; do
                count=$((count + 1))
                codes+=("$code")
                names+=("$name")
                echo -e "  ${BOLD}[$count]${NC} $name (код: ${CYAN}$code${NC}, IP: $ip)"
            done <<< "$list_output"
            echo -e "  ${BOLD}[0]${NC} ⬅️  Отмена\n"
            read -rp "Выберите номер релея [0-$count]: " r_num
            if [[ "$r_num" =~ ^[1-9][0-9]*$ ]] && (( r_num >= 1 && r_num <= count )); then
                local sel_code="${codes[$((r_num - 1))]}"
                local sel_name="${names[$((r_num - 1))]}"
                echo -e "Выбран узел: ${BOLD}$sel_name${NC} (код: $sel_code)"
                read -rp "Введите новое название: " r_new_name
                if [[ -n "$r_new_name" ]]; then
                    rename_relay_node "$sel_code" "$r_new_name"
                else
                    warn "Название не может быть пустым."
                fi
            fi
            ;;
        4)
            list_relays
            ;;
        5)
            update_relay_sni ""
            ;;
        0)
            return
            ;;
        *)
            warn "Неверный выбор."
            ;;
    esac
}
