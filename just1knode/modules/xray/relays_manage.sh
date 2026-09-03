#!/usr/bin/env bash
# =============================================================================
# JUST1KNODE - Управление Relay-нодами на Origin (modules/xray/relays_manage.sh)
# =============================================================================

NGINX_CONF_DIR="${NGINX_CONF_DIR:-/etc/nginx}"
NGINX_RELAYS_DIR="${NGINX_RELAYS_DIR:-${NGINX_CONF_DIR}/just1k_relays.d}"

list_relays() {
    title "СПИСОК АКТИВНЫХ RELAY-УЗЛОВ"
    init_state_dir

    if [[ ! -s "$RELAYS_FILE" ]] || [[ "$(cat "$RELAYS_FILE")" == "[]" ]]; then
        info "На данном Origin-сервере нет подключенных Relay-узлов."
        return
    fi

    python3 -c "
import json, sys
try:
    with open('$RELAYS_FILE', 'r', encoding='utf-8') as f:
        relays = json.load(f)
    print(f'Всего релеев: {len(relays)}\n')
    print(f'{\"ИМЯ\":<16} {\"КОД\":<6} {\"IP/ДОМЕН\":<22} {\"ПОРТ\":<8} {\"ПРОТОКОЛ\":<10} {\"ПУТЬ\":<20}')
    print('-' * 84)
    for r in relays:
        name = r.get('name', '-')
        code = r.get('code', '-')
        ip = r.get('ip', '-')
        port = str(r.get('port', '-'))
        sec = r.get('security', '-')
        path = r.get('path', '-')
        print(f'{name:<16} {code:<6} {ip:<22} {port:<8} {sec:<10} {path:<20}')
except Exception as e:
    print(f'Ошибка чтения реестра релеев: {e}')
"
    echo ""
}

add_relay_node() {
    local name="${1:-}"
    local ip="${2:-}"
    local port="${3:-10443}"
    local uuid="${4:-}"
    local code="${5:-de}"
    local security_type="${6:-reality}"
    local pubkey="${7:-}"
    local shortid="${8:-}"
    local sni="${9:-www.google.com}"

    local role
    role="$(get_state_val "role")"
    if [[ "$role" != "origin" ]]; then
        error "Управление Relay-узлами доступно ТОЛЬКО на Origin-сервере (текущая роль: ${role:-не установлена})."
    fi

    if [[ -z "$name" || -z "$ip" || -z "$uuid" ]]; then
        error "Имя, IP/Домен и UUID обязательны для добавления релея."
    fi

    # Санитизация кода страны во избежание path traversal
    if [[ ! "$code" =~ ^[a-zA-Z0-9_-]+$ ]]; then
        error "Недопустимый код страны: $code (разрешены только буквы, цифры, дефис и подчеркивание)."
    fi

    local secret_path
    secret_path="$(get_state_val "secret_base_path" "/stream")"
    if [[ -z "$secret_path" ]]; then
        secret_path="/stream"
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

# Удаляем старые записи этого релея, если были
cfg['inbounds'] = [ib for ib in cfg.get('inbounds', []) if ib.get('tag') != in_tag]
cfg['outbounds'] = [ob for ob in cfg.get('outbounds', []) if ob.get('tag') != out_tag]

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
        'serverName': r_sni,
        'fingerprint': 'chrome',
        'alpn': ['http/1.1']
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
rules = [r for r in rules if r.get('outboundTag') != out_tag]

for r in rules:
    if r.get('outboundTag') == 'just1k-wl-direct':
        existing_ib = r.get('inboundTag', [])
        if isinstance(existing_ib, list) and in_tag not in existing_ib:
            r['inboundTag'] = existing_ib + [in_tag]
        if 'domain' in r and 'domain:2ip.ru' not in r['domain']:
            r['domain'].append('domain:2ip.ru')

# Вставляем правило выхода на Relay СТРОГО ПОСЛЕ правил прямого выхода в Рунет
direct_indices = [i for i, r in enumerate(rules) if r.get('outboundTag') == 'just1k-wl-direct']
insert_idx = (max(direct_indices) + 1) if direct_indices else 0

rules.insert(insert_idx, {
    'type': 'field',
    'inboundTag': [in_tag],
    'outboundTag': out_tag
})

# Enforce relay egress for default client traffic (anti-Russian exit)
primary_relay_code = code
primary_relay_tag = f'just1k-wl-outbound-{primary_relay_code}'
default_rule_found = False
for r in rules:
    if (r.get('inboundTag') == ['just1k-wl-default'] or 'just1k-wl-default' in r.get('inboundTag', [])) and 'domain' not in r and 'ip' not in r:
        r['inboundTag'] = ['just1k-wl-default']
        r['outboundTag'] = primary_relay_tag
        default_rule_found = True
        break
if not default_rule_found:
    rules.append({
        'type': 'field',
        'inboundTag': ['just1k-wl-default'],
        'outboundTag': primary_relay_tag
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
    '1.1.1.1',
    'localhost'
]
dns_conf['queryStrategy'] = 'UseIPv4'
cfg['dns'] = dns_conf

with open(cfg_file, 'w', encoding='utf-8') as f:
    json.dump(cfg, f, indent=2)
" "$XRAY_CONFIG" "$code" "$relay_inbound_tag" "$relay_outbound_tag" "$relay_inbound_path" "$next_port" "$ip" "$port" "$uuid" "$security_type" "$pubkey" "$shortid" "$sni"; then
        manifest_rollback
        error "Ошибка генерации конфигурации Xray для релея."
    fi

    # Генерация Nginx Location для этого релея
    mkdir -p "$NGINX_RELAYS_DIR"
    local nginx_relay_conf="${NGINX_RELAYS_DIR}/${code}.conf"
    cat > "$nginx_relay_conf" <<EOF
# Relay location for ${name} (${code})
location ^~ ${relay_inbound_path} {
    proxy_pass http://127.0.0.1:${next_port};
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
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}
EOF

    # Валидация Nginx и Xray
    if ! nginx -t; then
        manifest_rollback
        error "Ошибка конфигурации Nginx при добавлении релея $name ($code). Изменения полностью отменены."
    fi

    if ! "$XRAY_BIN" run -test -config "$XRAY_CONFIG"; then
        manifest_rollback
        error "Ошибка тестирования Xray при добавлении релея $name ($code). Изменения полностью отменены."
    fi

    # Обновление relays.json
    python3 -c "
import json, os
rf = '$RELAYS_FILE'
relays = []
if os.path.exists(rf):
    try:
        with open(rf, 'r', encoding='utf-8') as f:
            relays = json.load(f)
    except: relays = []
relays = [r for r in relays if r.get('code') != '$code']
relays.append({
    'name': '$name',
    'code': '$code',
    'ip': '$ip',
    'port': int('$port'),
    'path': '$relay_inbound_path',
    'inbound_tag': '$relay_inbound_tag',
    'outbound_tag': '$relay_outbound_tag',
    'security': '$security_type',
    'sni': '$sni'
})
with open(rf, 'w', encoding='utf-8') as f:
    json.dump(relays, f, ensure_ascii=False, indent=2)
"

    nginx -t && systemctl reload nginx
    set +e
    systemctl restart xray
    local xray_rc=$?
    set -e
    if [[ $xray_rc -ne 0 ]] || ! systemctl is-active --quiet xray; then
        manifest_rollback
        error "Xray не запустился после добавления релея $name ($code). Выполнен полный откат."
    fi
    if systemctl is-active --quiet xray-api; then
        systemctl restart xray-api || true
    fi
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
        with open(rf) as f:
            for r in json.load(f):
                if r.get('code', '').lower() == target or r.get('name', '').lower() == target:
                    code = r.get('code')
                    break
    except: pass
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
cfg['inbounds'] = [ib for ib in cfg.get('inbounds', []) if ib.get('tag') not in (in_tag, f'just1k-wl-{code}', f'inbound-{code}')]
cfg['outbounds'] = [ob for ob in cfg.get('outbounds', []) if ob.get('tag') not in (out_tag, f'just1k-wl-out-{code}', f'outbound-{code}')]

# Очищаем тег из правил маршрутизации
if 'routing' in cfg and 'rules' in cfg['routing']:
    cfg['routing']['rules'] = [r for r in cfg['routing']['rules'] if r.get('outboundTag') not in (out_tag, f'just1k-wl-out-{code}', f'outbound-{code}')]
    for r in cfg['routing']['rules']:
        if r.get('outboundTag') == 'just1k-wl-direct':
            existing_ib = r.get('inboundTag', [])
            if isinstance(existing_ib, list) and in_tag in existing_ib:
                r['inboundTag'] = [t for t in existing_ib if t != in_tag]

# Если остались другие релеи, переключаем дефолтный маршрут на следующий релей, иначе на блок
remaining = []
if os.path.exists(rf):
    try:
        with open(rf) as f_r: remaining = [r for r in json.load(f_r) if r.get('code') != code]
    except: pass

new_def_out = f\"just1k-wl-outbound-{remaining[0]['code']}\" if remaining else 'just1k-wl-block'
for r in cfg.get('routing', {}).get('rules', []):
    if r.get('inboundTag') == ['just1k-wl-default'] and 'domain' not in r and 'ip' not in r:
        r['outboundTag'] = new_def_out

with open(cfg_file, 'w') as f: json.dump(cfg, f, indent=2)
"

    # Удаление Nginx конфига
    rm -f "${NGINX_RELAYS_DIR}/${code}.conf"

    # Удаление из relays.json
    python3 -c "
import json
rf = '$RELAYS_FILE'
with open(rf) as f: relays = json.load(f)
relays = [r for r in relays if r.get('code') != '$code']
with open(rf, 'w') as f: json.dump(relays, f, indent=2)
"

    if ! nginx -t; then
        manifest_rollback
        error "Ошибка валидации Nginx при удалении релея $target. Изменения полностью отменены."
    fi

    if ! "$XRAY_BIN" run -test -config "$XRAY_CONFIG"; then
        manifest_rollback
        error "Ошибка тестирования Xray при удалении релея $target. Изменения полностью отменены."
    fi

    nginx -t && systemctl reload nginx
    set +e
    systemctl restart xray
    local xray_rc=$?
    set -e
    if [[ $xray_rc -ne 0 ]] || ! systemctl is-active --quiet xray; then
        manifest_rollback
        error "Xray не запустился после удаления релея $target. Выполнен полный откат."
    fi
    if systemctl is-active --quiet xray-api; then
        systemctl restart xray-api || true
    fi
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
import json, os, sys
rf = '$RELAYS_FILE'
target = sys.argv[1].lower()
new_name = sys.argv[2]
found = False
if os.path.exists(rf):
    try:
        with open(rf, 'r', encoding='utf-8') as f:
            relays = json.load(f)
        for r in relays:
            if r.get('code', '').lower() == target or r.get('name', '').lower() == target:
                r['name'] = new_name
                found = True
                break
        if found:
            with open(rf, 'w', encoding='utf-8') as f:
                json.dump(relays, f, ensure_ascii=False, indent=2)
            print('ok')
    except:
        pass
if not found:
    print('not_found')
" "$target" "$new_name")

    if [[ "$updated" != "ok" ]]; then
        error "Relay с кодом или именем '$target' не найден."
    fi

    if systemctl is-active --quiet xray-api; then
        systemctl restart xray-api
    fi

    log "Релей '$target' успешно переименован в '$new_name'."
}

get_relays_tsv() {
    python3 -c "
import json, os
rf = '$RELAYS_FILE'
if os.path.exists(rf):
    try:
        with open(rf, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for i, r in enumerate(data, 1):
            print(f\"{i}\t{r.get('code','')}\t{r.get('name','')}\t{r.get('ip','')}\")
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
    echo -e "  ${BOLD}[0]${NC} ⬅️  Назад"
    echo ""
    read -rp "Выберите действие [0-4]: " r_choice

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
        sec = tokens[5] if len(tokens) > 5 else 'reality'
        pk = tokens[6] if len(tokens) > 6 else ''
        sid = tokens[7] if len(tokens) > 7 else ''
        sni = tokens[8] if len(tokens) > 8 else 'www.google.com'
        print(' '.join(shlex.quote(x) for x in [name, ip, port, uuid, code, sec, pk, sid, sni]))
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
                echo -e "  [1] REALITY (Бессертификатный x25519 по IP, рекомендуемый)"
                echo -e "  [2] TLS (Доменный сертификат Let's Encrypt)"
                read -rp "Выберите тип [1/2, по умолчанию 1]: " t_choice
                t_choice="${t_choice:-1}"
                local r_sec="reality"
                local r_pubkey=""
                local r_shortid=""
                local r_sni="www.google.com"
                if [[ "$t_choice" == "1" ]]; then
                    r_sec="reality"
                    read -rp "REALITY Public Key: " r_pubkey
                    read -rp "REALITY Short ID: " r_shortid
                    read -rp "REALITY SNI [по умолчанию: www.google.com]: " r_sni_in
                    r_sni="${r_sni_in:-www.google.com}"
                else
                    r_sec="tls"
                    read -rp "TLS Домен / SNI: " r_sni_in
                    if [[ -z "$r_sni_in" ]]; then error "Домен SNI обязателен для TLS."; fi
                    r_sni="$r_sni_in"
                fi
                add_relay_node "$r_name" "$r_ip" "$r_port" "$r_uuid" "$r_code" "$r_sec" "$r_pubkey" "$r_shortid" "$r_sni"
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
            while IFS=$'\t' read -r num code name ip; do
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
            while IFS=$'\t' read -r num code name ip; do
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
        0)
            return
            ;;
        *)
            warn "Неверный выбор."
            ;;
    esac
}
