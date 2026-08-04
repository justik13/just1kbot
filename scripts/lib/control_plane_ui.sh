#!/bin/bash
# Russian state-aware terminal UI loaded after the audited control plane.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly UI_PROJECT_DIR=${UI_PROJECT_DIR:-/opt/just1kbot}
readonly UI_ENV_FILE=${UI_ENV_FILE:-$UI_PROJECT_DIR/.env}
readonly UI_SERVICE=${UI_SERVICE:-just1kbot.service}
readonly UI_REDIS_SERVICE=${UI_REDIS_SERVICE:-just1kbot-redis.service}
readonly UI_BOT_LOG=${UI_BOT_LOG:-/var/log/just1kbot/bot.log}
readonly UI_INSTALL_LOG=${UI_INSTALL_LOG:-/var/log/just1kbot-deploy.log}
readonly UI_BACKUP_DIR=${UI_BACKUP_DIR:-/root/backups/just1kbot}
readonly UI_AGE_IDENTITY=${UI_AGE_IDENTITY:-/root/.config/just1kbot/backup.agekey}
readonly UI_RELEASE_METADATA=${UI_RELEASE_METADATA:-$UI_PROJECT_DIR/.release-version}

UI_STATE=unknown
UI_REASON='Не удалось определить состояние установки.'
UI_ACTION='Запустите диагностику состояния.'

ui_clear() {
    if [[ -t 1 && ${TERM:-dumb} != dumb ]] && command -v clear >/dev/null 2>&1; then
        clear
    fi
}

ui_box() {
    local title=${1:-JUST1KBOT MANAGER}
    shift || true
    {
        printf '%s\0' "$title"
        printf '%s\0' "$@"
    } | python3 -c '
import os
import shutil
import sys
import unicodedata

parts = sys.stdin.buffer.read().split(b"\0")
if parts and parts[-1] == b"":
    parts.pop()
values = [part.decode("utf-8", "replace") for part in parts]
title = values[0] if values else "JUST1KBOT MANAGER"
lines = values[1:]
terminal = shutil.get_terminal_size((72, 20)).columns
requested = int(os.environ.get("JUST1KBOT_UI_WIDTH", "68"))
width = max(56, min(requested, terminal - 2 if terminal > 58 else requested, 84))

def char_width(char):
    if unicodedata.combining(char) or char in {"\ufe0e", "\ufe0f", "\u200d"}:
        return 0
    return 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1

def text_width(text):
    return sum(char_width(char) for char in text)

def clip_word(word, limit):
    result = []
    used = 0
    for char in word:
        size = char_width(char)
        if used + size > limit:
            break
        result.append(char)
        used += size
    return "".join(result)

def wrap_line(text):
    if text == "__RULE__":
        return [text]
    if not text:
        return [""]
    indent = text[: len(text) - len(text.lstrip(" "))]
    continuation = indent
    stripped = text.lstrip(" ")
    if stripped.startswith(("• ", "- ", "⚠️ ", "✅ ", "❌ ")):
        continuation += "  "
    words = stripped.split(" ")
    output = []
    current = indent
    for word in words:
        if not word:
            continue
        candidate = word if not current.strip() else current + " " + word
        if text_width(candidate) <= width:
            current = candidate
            continue
        if current.strip():
            output.append(current)
            current = continuation
        while text_width(continuation + word) > width:
            room = max(1, width - text_width(continuation))
            piece = clip_word(word, room)
            output.append(continuation + piece)
            word = word[len(piece):]
        current = continuation + word
    if current or not output:
        output.append(current)
    return output

def pad(text):
    return text + " " * max(0, width - text_width(text))

def center(text):
    gap = max(0, width - text_width(text))
    left = gap // 2
    return " " * left + text + " " * (gap - left)

print("╔" + "═" * (width + 2) + "╗")
print("║ " + center(title) + " ║")
print("╠" + "═" * (width + 2) + "╣")
for raw in lines:
    for line in wrap_line(raw):
        if line == "__RULE__":
            print("╠" + "═" * (width + 2) + "╣")
        else:
            print("║ " + pad(line) + " ║")
print("╚" + "═" * (width + 2) + "╝")
'
}

ui_pause() {
    [[ -t 0 ]] || return 0
    printf '\n'
    read -r -p 'Нажми Enter, чтобы продолжить... ' _ || true
}

ui_choice() {
    local range=$1
    local answer
    read -r -p "Выбери действие [$range]: " answer || return 1
    printf '%s\n' "$answer"
}

ui_load_state() {
    local payload
    local -a fields=()
    set +e
    payload=$(bash "$SCRIPTS_DIR/inspect_install_state.sh" --json 2>/dev/null)
    set -e
    mapfile -t fields < <(
        STATE_JSON="$payload" python3 - <<'PY' 2>/dev/null || true
import json
import os
try:
    value = json.loads(os.environ.get("STATE_JSON", ""))
except Exception:
    print("unknown")
    print("Не удалось прочитать JSON состояния.")
    print("Запустите state и doctor.")
else:
    print(str(value.get("state") or "unknown").replace("\n", " "))
    print(str(value.get("reason") or "Причина не указана.").replace("\n", " "))
    print(str(value.get("action") or "Запустите doctor.").replace("\n", " "))
PY
    )
    UI_STATE=${fields[0]:-unknown}
    UI_REASON=${fields[1]:-Не удалось определить состояние установки.}
    UI_ACTION=${fields[2]:-Запустите диагностику состояния.}
}

ui_state_label() {
    case "$UI_STATE" in
        clean) printf '🔴 Бот не установлен' ;;
        installed_managed) printf '🟢 Установка подтверждена' ;;
        partial_install) printf '🟠 Незавершённая операция' ;;
        legacy_managed) printf '🟡 Legacy-установка без manifest' ;;
        residual_managed) printf '⚠️  Найдены управляемые остатки' ;;
        foreign_collision) printf '⛔ Конфликт с чужими ресурсами' ;;
        corrupted_state) printf '❌ Повреждено состояние ownership' ;;
        *) printf '❓ Состояние неизвестно' ;;
    esac
}

ui_short_sha() {
    local value=${1:-unknown}
    if [[ $value =~ ^[0-9a-fA-F]{40}$ ]]; then
        printf '%s\n' "${value:0:12}"
    else
        printf '%s\n' "$value"
    fi
}

ui_release_field() {
    local key=$1
    local value=''
    if [[ -f "$UI_RELEASE_METADATA" && ! -L "$UI_RELEASE_METADATA" ]]; then
        value=$(sed -n "s/^${key}=//p" "$UI_RELEASE_METADATA" 2>/dev/null | head -n 1)
    fi
    printf '%s\n' "${value:-unknown}"
}

ui_current_version() {
    local value
    value=$(ui_release_field source_commit)
    if [[ "$value" == unknown && -d "$ROOT_DIR/.git" && ! -L "$ROOT_DIR/.git" ]] &&
       command -v git >/dev/null 2>&1; then
        value=$(git -c safe.directory="$ROOT_DIR" -C "$ROOT_DIR" rev-parse --verify HEAD 2>/dev/null || printf 'unknown')
    fi
    ui_short_sha "$value"
}

ui_env_value() {
    local key=$1
    [[ -f "$UI_ENV_FILE" && ! -L "$UI_ENV_FILE" ]] || return 0
    ENV_PATH="$UI_ENV_FILE" ENV_KEY="$key" python3 - <<'PY' 2>/dev/null || true
import os
from pathlib import Path
key = os.environ["ENV_KEY"]
for raw in Path(os.environ["ENV_PATH"]).read_text(encoding="utf-8", errors="replace").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    name, value = line.split("=", 1)
    if name.strip() == key:
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"\"", "\'"}:
            value = value[1:-1]
        print(value.replace("\n", " "))
        break
PY
}

ui_human_seconds() {
    local seconds=${1:-0}
    (( seconds < 0 )) && seconds=0
    local days=$((seconds / 86400))
    local hours=$(((seconds % 86400) / 3600))
    local minutes=$(((seconds % 3600) / 60))
    if (( days > 0 )); then
        printf '%sд %sч %sм\n' "$days" "$hours" "$minutes"
    elif (( hours > 0 )); then
        printf '%sч %sм\n' "$hours" "$minutes"
    else
        printf '%sм\n' "$minutes"
    fi
}

ui_service_uptime() {
    local started now seconds
    command -v systemctl >/dev/null 2>&1 || { printf 'н/д\n'; return; }
    started=$(systemctl show "$UI_SERVICE" -p ActiveEnterTimestampMonotonic --value 2>/dev/null || true)
    [[ $started =~ ^[0-9]+$ && $started -gt 0 && -r /proc/uptime ]] || { printf 'н/д\n'; return; }
    now=$(awk '{printf "%.0f", $1 * 1000000}' /proc/uptime)
    seconds=$(((now - started) / 1000000))
    ui_human_seconds "$seconds"
}

ui_service_state() {
    local state
    state=$(systemctl is-active "$UI_SERVICE" 2>/dev/null || true)
    case "$state" in
        active) printf '🟢 active (running)' ;;
        inactive) printf '⚪ inactive' ;;
        failed) printf '🔴 failed' ;;
        activating|deactivating) printf '🟡 %s' "$state" ;;
        *) printf '❓ %s' "${state:-unknown}" ;;
    esac
}

ui_latest_backup() {
    [[ -d "$UI_BACKUP_DIR" && ! -L "$UI_BACKUP_DIR" ]] || return 0
    find "$UI_BACKUP_DIR" -maxdepth 1 -type f -name 'just1kbot-pg-v1-*.tar.age' \
        -printf '%T@\t%p\n' 2>/dev/null | sort -nr | head -n 1 | cut -f2-
}

ui_backup_summary_lines() {
    local latest size modified age_seconds age_text
    latest=$(ui_latest_backup)
    if [[ -z "$latest" ]]; then
        printf '%s\n' 'Последний бэкап: не найден' "Место: $UI_BACKUP_DIR"
        return
    fi
    size=$(du -h -- "$latest" 2>/dev/null | awk '{print $1}')
    modified=$(stat -c %Y -- "$latest" 2>/dev/null || printf '0')
    age_seconds=$(($(date +%s) - modified))
    age_text=$(ui_human_seconds "$age_seconds")
    printf 'Последний бэкап: %s (%s назад)\n' "$(date -d "@$modified" '+%d.%m.%Y %H:%M:%S' 2>/dev/null || printf 'неизвестно')" "$age_text"
    printf 'Размер: %s (зашифрован age)\n' "${size:-н/д}"
    printf 'Место: %s\n' "$UI_BACKUP_DIR"
}

ui_require_managed_install() {
    local state_value
    state_value=$(read_install_state)
    [[ "$state_value" == installed_managed ]] || {
        ui_box '⛔ ОПЕРАЦИЯ ЗАБЛОКИРОВАНА' \
            "Состояние: $state_value" \
            'Ownership действующей установки не подтверждён.' \
            'Разрешены только state, doctor и support bundle.'
        return 1
    }
}

ui_run_operation() {
    local title=$1
    shift
    local rc
    ui_clear
    ui_box "$title" 'Операция выполняется через безопасный control plane.' 'Следуйте подсказкам ниже.'
    printf '\n'
    set +e
    dispatch "$@"
    rc=$?
    set -e
    printf '\n'
    if (( rc == 0 )); then
        ui_box '✅ ОПЕРАЦИЯ ЗАВЕРШЕНА' "$title выполнена без ошибки." \
            "Проверено: $(date '+%d.%m.%Y %H:%M:%S')"
    else
        ui_box '❌ ОПЕРАЦИЯ НЕ ЗАВЕРШЕНА' "$title завершилась с кодом $rc." \
            'Первичная причина показана выше.' \
            'Запустите диагностику и не удаляйте transaction journal вручную.'
    fi
    ui_pause
    return "$rc"
}

ui_show_state() {
    ui_clear
    dispatch state || true
    ui_pause
}

ui_diagnostics() {
    local payload rc
    local -a lines=()
    ui_clear
    printf '[*] Выполняю полную read-only диагностику...\n'
    set +e
    payload=$(dispatch doctor --json 2>/dev/null)
    rc=$?
    set -e
    if [[ -n "$payload" ]]; then
        mapfile -t lines < <(
            DOCTOR_JSON="$payload" python3 - <<'PY' 2>/dev/null || true
import json
import os
try:
    value = json.loads(os.environ.get("DOCTOR_JSON", ""))
except Exception:
    raise SystemExit(0)
checks = value.get("checks", [])
ok = sum(item.get("status") == "ok" for item in checks)
warn = int(value.get("warnings", 0))
fail = int(value.get("failures", 0))
for item in checks:
    icon = {"ok": "✅", "warn": "⚠️ ", "fail": "❌"}.get(item.get("status"), "❓")
    print(f"{icon} {item.get('message', 'Проверка без описания')}")
print("__RULE__")
print(f"ИТОГО: {ok} ✅ OK, {warn} ⚠️ WARN, {fail} ❌ FAIL")
problems = [item.get("message", "") for item in checks if item.get("status") in {"warn", "fail"}]
if problems:
    print("")
    print("Требует внимания:")
    for problem in problems[:6]:
        print(f"• {problem}")
PY
        )
    fi
    if (( ${#lines[@]} > 0 )); then
        ui_box '🔬 ДИАГНОСТИКА' "${lines[@]}"
    else
        dispatch doctor || true
    fi
    ui_pause
    return "$rc"
}

ui_static_log() {
    local title=$1
    shift
    ui_clear
    ui_box "$title" 'Последние данные показаны ниже.'
    printf '\n'
    "$@" || true
    ui_pause
}

ui_bot_log_last() {
    if [[ -f "$UI_BOT_LOG" && ! -L "$UI_BOT_LOG" ]]; then
        ui_static_log '📄 ЛОГ БОТА — 50 СТРОК' tail -n 50 -- "$UI_BOT_LOG"
    else
        ui_static_log '📄 ЛОГ БОТА — JOURNAL' journalctl -u "$UI_SERVICE" -n 50 --no-pager
    fi
}

ui_bot_log_errors() {
    if [[ -f "$UI_BOT_LOG" && ! -L "$UI_BOT_LOG" ]]; then
        ui_static_log '⚠️  WARNING / ERROR' bash -c \
            'grep -Ei "(^|[[:space:]|])(WARNING|ERROR|CRITICAL)([[:space:]|]|$)" "$1" | tail -n 100' _ "$UI_BOT_LOG"
    else
        ui_static_log '⚠️  WARNING / ERROR' journalctl -u "$UI_SERVICE" -p warning -n 100 --no-pager
    fi
}

ui_live_stream() {
    local title=$1
    shift
    local pid key rc=0
    ui_clear
    ui_box "$title" 'Обновление в реальном времени.' 'Нажми q для возврата.'
    printf '\n'
    set +e
    "$@" &
    pid=$!
    trap 'kill "$pid" 2>/dev/null || true' INT TERM
    while kill -0 "$pid" 2>/dev/null; do
        if IFS= read -r -s -n 1 -t 1 key; then
            [[ "$key" == q || "$key" == Q ]] && break
        fi
    done
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null
    rc=$?
    trap - INT TERM
    set -e
    return "$rc"
}

ui_bot_log_live() {
    if [[ -f "$UI_BOT_LOG" && ! -L "$UI_BOT_LOG" ]]; then
        ui_live_stream '📡 ЛОГ БОТА — LIVE' tail -n 50 -F -- "$UI_BOT_LOG" || true
    else
        ui_live_stream '📡 ЛОГ БОТА — LIVE' journalctl -u "$UI_SERVICE" -n 50 -f || true
    fi
}

ui_service_log_live() {
    ui_live_stream '📡 ЛОГ СЕРВИСА — LIVE' journalctl -u "$UI_SERVICE" -n 50 -f || true
}

ui_logs_menu() {
    local choice
    while true; do
        ui_clear
        ui_box '📋 ЛОГИ' '' \
            '[1] ❓ Что не так? (диагностика)' \
            '[2] 📄 Лог бота (последние 50 строк)' \
            '[3] ⚠️  Лог бота (WARNING/ERROR)' \
            '[4] 📡 Лог бота (live, выход q)' \
            '[5] 📦 Journal сервиса (последние 50)' \
            '[6] 💥 Journal сервиса (только ошибки)' \
            '[7] 📡 Journal сервиса (live, выход q)' \
            '[8] 📋 Лог установки' \
            '[0] ↩️  Назад' ''
        choice=$(ui_choice '0-8') || return
        case "$choice" in
            1) ui_diagnostics || true ;;
            2) ui_bot_log_last ;;
            3) ui_bot_log_errors ;;
            4) ui_bot_log_live ;;
            5) ui_static_log '📦 JOURNAL СЕРВИСА' journalctl -u "$UI_SERVICE" -n 50 --no-pager ;;
            6) ui_static_log '💥 ОШИБКИ СЕРВИСА' journalctl -u "$UI_SERVICE" -p err -n 100 --no-pager ;;
            7) ui_service_log_live ;;
            8)
                if [[ -f "$UI_INSTALL_LOG" && ! -L "$UI_INSTALL_LOG" ]]; then
                    ui_static_log '📋 ЛОГ УСТАНОВКИ' tail -n 100 -- "$UI_INSTALL_LOG"
                else
                    ui_box '📋 ЛОГ УСТАНОВКИ' "Файл не найден: $UI_INSTALL_LOG"
                    ui_pause
                fi
                ;;
            0) return ;;
            *) ui_box '⚠️  НЕВЕРНЫЙ ВЫБОР' 'Выберите пункт из диапазона 0-8.'; ui_pause ;;
        esac
    done
}

ui_list_backups() {
    local -a files=()
    local file size modified
    [[ -d "$UI_BACKUP_DIR" && ! -L "$UI_BACKUP_DIR" ]] || {
        ui_box '💾 БЭКАПЫ' "Каталог не найден: $UI_BACKUP_DIR"
        ui_pause
        return
    }
    mapfile -t files < <(find "$UI_BACKUP_DIR" -maxdepth 1 -type f -name 'just1kbot-pg-v1-*.tar.age' -printf '%T@\t%p\n' | sort -nr | cut -f2-)
    ui_clear
    ui_box '📋 СПИСОК БЭКАПОВ' "Найдено: ${#files[@]}" "Каталог: $UI_BACKUP_DIR"
    printf '\n'
    if (( ${#files[@]} == 0 )); then
        printf 'Бэкапы не найдены.\n'
    else
        local index=1
        for file in "${files[@]}"; do
            size=$(du -h -- "$file" 2>/dev/null | awk '{print $1}')
            modified=$(stat -c %Y -- "$file" 2>/dev/null || printf '0')
            printf '%2d. %s | %s | %s\n' "$index" "$(basename -- "$file")" "${size:-н/д}" \
                "$(date -d "@$modified" '+%d.%m.%Y %H:%M' 2>/dev/null || printf 'н/д')"
            index=$((index + 1))
        done
    fi
    ui_pause
}

ui_choose_backup() {
    local -a files=()
    local index answer file real
    [[ -d "$UI_BACKUP_DIR" && ! -L "$UI_BACKUP_DIR" ]] || return 1
    mapfile -t files < <(find "$UI_BACKUP_DIR" -maxdepth 1 -type f -name 'just1kbot-pg-v1-*.tar.age' -printf '%T@\t%p\n' | sort -nr | cut -f2-)
    (( ${#files[@]} > 0 )) || return 1
    printf '\n'
    index=1
    for file in "${files[@]}"; do
        printf '[%d] %s\n' "$index" "$(basename -- "$file")"
        index=$((index + 1))
    done
    read -r -p "Выберите бэкап [1-${#files[@]}]: " answer || return 1
    [[ $answer =~ ^[0-9]+$ ]] || return 1
    (( answer >= 1 && answer <= ${#files[@]} )) || return 1
    file=${files[answer-1]}
    [[ -f "$file" && ! -L "$file" ]] || return 1
    real=$(realpath -e -- "$file") || return 1
    [[ "$real" == "$UI_BACKUP_DIR/"* ]] || return 1
    printf '%s\n' "$real"
}

ui_delete_old_backups() {
    local keep confirm file real
    local -a files=() remove=()
    ui_require_managed_install || { ui_pause; return; }
    read -r -p 'Сколько последних бэкапов оставить? [5]: ' keep || return
    keep=${keep:-5}
    [[ $keep =~ ^[1-9][0-9]?$ ]] || {
        ui_box '⚠️  ОШИБКА' 'Введите число от 1 до 99.'
        ui_pause
        return
    }
    mapfile -t files < <(find "$UI_BACKUP_DIR" -maxdepth 1 -type f -name 'just1kbot-pg-v1-*.tar.age' -printf '%T@\t%p\n' 2>/dev/null | sort -nr | cut -f2-)
    if (( ${#files[@]} <= keep )); then
        ui_box '💾 БЭКАПЫ' "Удалять нечего: найдено ${#files[@]}, сохраняется $keep."
        ui_pause
        return
    fi
    remove=("${files[@]:keep}")
    ui_box '⚠️  УДАЛЕНИЕ СТАРЫХ БЭКАПОВ' \
        "Будет удалено: ${#remove[@]}" \
        "Будет сохранено новых: $keep" \
        'Для подтверждения введите DELETE BACKUPS.'
    read -r -p 'Подтверждение: ' confirm || return
    [[ "$confirm" == 'DELETE BACKUPS' ]] || {
        ui_box '↩️  ОТМЕНЕНО' 'Бэкапы не изменены.'
        ui_pause
        return
    }
    for file in "${remove[@]}"; do
        [[ -f "$file" && ! -L "$file" ]] || continue
        real=$(realpath -e -- "$file") || continue
        [[ "$real" == "$UI_BACKUP_DIR/"* ]] || continue
        rm -f -- "$real"
    done
    ui_box '✅ ГОТОВО' 'Старые бэкапы удалены.' "Сохранено последних: $keep"
    ui_pause
}

ui_restore_backup() {
    local mode=$1 file identity
    file=$(ui_choose_backup) || {
        ui_box '💾 БЭКАПЫ' 'Не удалось выбрать безопасный backup.tar.age.'
        ui_pause
        return
    }
    if [[ "$mode" == rehearsal ]]; then
        ui_run_operation '🧪 ТЕСТОВОЕ ВОССТАНОВЛЕНИЕ' restore-test "$file" || true
        return
    fi
    identity=$UI_AGE_IDENTITY
    if [[ ! -f "$identity" || -L "$identity" ]]; then
        read -r -p 'Путь к age identity: ' identity || return
    fi
    [[ -f "$identity" && ! -L "$identity" ]] || {
        ui_box '❌ ВОССТАНОВЛЕНИЕ ЗАБЛОКИРОВАНО' 'Age identity отсутствует или небезопасен.'
        ui_pause
        return
    }
    AGE_IDENTITY_FILE=$identity ui_run_operation '📦 PRODUCTION RESTORE' restore-production "$file" || true
}

ui_backups_menu() {
    local choice file
    local -a summary=()
    while true; do
        mapfile -t summary < <(ui_backup_summary_lines)
        ui_clear
        ui_box '💾 БЭКАПЫ' "${summary[@]}" '' \
            '[1] 💾 Создать бэкап сейчас' \
            '[2] 📋 Список бэкапов' \
            '[3] ✅ Проверить выбранный бэкап' \
            '[4] 🧪 Тестовое восстановление' \
            '[5] 📦 Production-восстановление' \
            '[6] 🗑️  Удалить старые бэкапы' \
            '[0] ↩️  Назад' ''
        choice=$(ui_choice '0-6') || return
        case "$choice" in
            1) ui_run_operation '💾 СОЗДАНИЕ БЭКАПА' backup || true ;;
            2) ui_list_backups ;;
            3)
                file=$(ui_choose_backup) || { ui_box '💾 БЭКАПЫ' 'Бэкап не выбран.'; ui_pause; continue; }
                ui_run_operation '✅ ПРОВЕРКА БЭКАПА' verify-backup "$file" || true
                ;;
            4) ui_restore_backup rehearsal ;;
            5) ui_restore_backup production ;;
            6) ui_delete_old_backups ;;
            0) return ;;
            *) ui_box '⚠️  НЕВЕРНЫЙ ВЫБОР' 'Выберите пункт из диапазона 0-6.'; ui_pause ;;
        esac
    done
}

ui_update_menu() {
    local output rc current fetched subject author commit_date choice
    local temporary
    temporary=$(mktemp /tmp/just1kbot-update-check.XXXXXX)
    chmod 0600 "$temporary"
    ui_clear
    printf '[*] Проверяю обновления на GitHub...\n'
    set +e
    dispatch update --check >"$temporary" 2>&1
    rc=$?
    set -e
    output=$(cat "$temporary")
    rm -f -- "$temporary"
    if (( rc != 0 )); then
        printf '%s\n' "$output"
        ui_box '❌ ПРОВЕРКА ОБНОВЛЕНИЙ НЕ УДАЛАСЬ' \
            "Код ошибки: $rc" \
            'Основная причина показана выше.'
        ui_pause
        return
    fi
    current=$(sed -n 's/^Installed commit:[[:space:]]*//p' <<<"$output" | tail -n 1)
    fetched=$(sed -n 's/^Fetched main:[[:space:]]*//p' <<<"$output" | tail -n 1)
    subject=$(sed -n 's/^Fetched subject:[[:space:]]*//p' <<<"$output" | tail -n 1)
    author=$(sed -n 's/^Fetched author:[[:space:]]*//p' <<<"$output" | tail -n 1)
    commit_date=$(sed -n 's/^Fetched date:[[:space:]]*//p' <<<"$output" | tail -n 1)
    current=${current:-unknown}
    fetched=${fetched:-unknown}
    if [[ "$current" == "$fetched" && "$current" != unknown ]]; then
        ui_box '✅ УСТАНОВЛЕНА ПОСЛЕДНЯЯ ВЕРСИЯ' \
            "Версия: $(ui_short_sha "$current")" \
            "Проверено: $(date '+%d.%m.%Y %H:%M:%S')"
        ui_pause
        return
    fi
    ui_box '⬆️  ДОСТУПНО ОБНОВЛЕНИЕ' \
        "$(ui_short_sha "$current") → $(ui_short_sha "$fetched")" \
        "Коммит: ${subject:-описание отсутствует}" \
        "Автор: ${author:-неизвестно}" \
        "Дата: ${commit_date:-неизвестно}" \
        '__RULE__' \
        '[1] 🔄 Обновить эту проверенную версию' \
        '[0] ↩️  Назад' '' \
        'Перед установкой updater повторно сверит полный SHA и попросит ввести его целиком.'
    choice=$(ui_choice '0-1') || return
    case "$choice" in
        1)
            [[ $fetched =~ ^[0-9a-f]{40}$ ]] || {
                ui_box '❌ ОБНОВЛЕНИЕ ЗАБЛОКИРОВАНО' 'GitHub updater не вернул полный 40-hex SHA.'
                ui_pause
                return
            }
            ui_run_operation '🔄 ОБНОВЛЕНИЕ JUST1KBOT' update --sha "$fetched" || true
            ;;
        0) return ;;
        *) ui_box '⚠️  НЕВЕРНЫЙ ВЫБОР' 'Выберите 0 или 1.'; ui_pause ;;
    esac
}

ui_service_status_menu() {
    local choice status uptime pid memory cpu_nsec memory_text cpu_text
    while true; do
        status=$(ui_service_state)
        uptime=$(ui_service_uptime)
        pid=$(systemctl show "$UI_SERVICE" -p MainPID --value 2>/dev/null || printf '0')
        memory=$(systemctl show "$UI_SERVICE" -p MemoryCurrent --value 2>/dev/null || printf '0')
        cpu_nsec=$(systemctl show "$UI_SERVICE" -p CPUUsageNSec --value 2>/dev/null || printf '0')
        memory_text=$(MEMORY_BYTES="$memory" python3 - <<'PY' 2>/dev/null || printf 'н/д'
import os
try:
    value = int(os.environ["MEMORY_BYTES"])
except Exception:
    print("н/д")
else:
    units = ["B", "KB", "MB", "GB"]
    size = float(value)
    unit = units[0]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            break
        size /= 1024
    print(f"{size:.1f} {unit}")
PY
        )
        cpu_text=$(CPU_NSEC="$cpu_nsec" python3 - <<'PY' 2>/dev/null || printf 'н/д'
import os
try:
    value = int(os.environ["CPU_NSEC"])
except Exception:
    print("н/д")
else:
    print(f"{value / 1_000_000_000:.1f} сек")
PY
        )
        ui_clear
        ui_box '⚙️  СТАТУС СЕРВИСА' \
            "Сервис: $UI_SERVICE" \
            "Статус: $status" \
            "Uptime: $uptime" \
            "PID: ${pid:-0}" \
            "Память: $memory_text" \
            "CPU time: $cpu_text" \
            "Redis: $(systemctl is-active "$UI_REDIS_SERVICE" 2>/dev/null || printf 'unknown')" \
            '__RULE__' \
            '[1] ♻️  Перезапустить сервис' \
            '[2] ⏹  Остановить сервис' \
            '[3] ▶️  Запустить сервис' \
            '[0] ↩️  Назад' ''
        choice=$(ui_choice '0-3') || return
        case "$choice" in
            1) ui_run_operation '♻️  ПЕРЕЗАПУСК СЕРВИСА' restart || true ;;
            2)
                ui_require_managed_install || { ui_pause; continue; }
                ui_box '⚠️  ОСТАНОВКА СЕРВИСА' 'Бот перестанет обрабатывать сообщения.' 'Введите STOP для подтверждения.'
                read -r -p 'Подтверждение: ' choice || continue
                if [[ "$choice" == STOP ]]; then
                    systemctl stop "$UI_SERVICE"
                    ui_box '⏹  СЕРВИС ОСТАНОВЛЕН' "$UI_SERVICE остановлен."
                else
                    ui_box '↩️  ОТМЕНЕНО' 'Сервис не изменён.'
                fi
                ui_pause
                ;;
            3)
                ui_require_managed_install || { ui_pause; continue; }
                systemctl start "$UI_SERVICE"
                ui_box '▶️  СЕРВИС ЗАПУЩЕН' "$UI_SERVICE запущен."
                ui_pause
                ;;
            0) return ;;
            *) ui_box '⚠️  НЕВЕРНЫЙ ВЫБОР' 'Выберите пункт из диапазона 0-3.'; ui_pause ;;
        esac
    done
}

ui_uninstall_menu() {
    local choice
    while true; do
        ui_clear
        ui_box '🗑️  УДАЛЕНИЕ' \
            '[1] 🧹 Удалить приложение, сохранить данные' \
            '    • Удалит manifest-owned сервисы, код, proxy/TLS и operational tools' \
            '    • Сохранит PostgreSQL и зашифрованные бэкапы' '' \
            '[2] 💥 Полное удаление' \
            '    • Удалит manifest-owned приложение и PostgreSQL data' \
            '    • Потребует точную фразу DELETE JUST1KBOT' \
            '    • Бэкапы сохраняются согласно безопасному uninstaller contract' '' \
            '[0] ↩️  Назад'
        choice=$(ui_choice '0-2') || return
        case "$choice" in
            1) ui_run_operation '🧹 УДАЛЕНИЕ С СОХРАНЕНИЕМ ДАННЫХ' uninstall --keep-data || true ;;
            2)
                ui_box '⚠️  ПОДТВЕРЖДЕНИЕ ПОЛНОГО УДАЛЕНИЯ' \
                    'Это действие необратимо для manifest-owned PostgreSQL data.' \
                    'Uninstaller сам повторно потребует DELETE JUST1KBOT.' \
                    'Не удаляйте manifest и ownership markers вручную.'
                read -r -p 'Продолжить? Введите YES: ' choice || continue
                [[ "$choice" == YES ]] && ui_run_operation '💥 ПОЛНОЕ УДАЛЕНИЕ' uninstall --purge-data || true
                ;;
            0) return ;;
            *) ui_box '⚠️  НЕВЕРНЫЙ ВЫБОР' 'Выберите пункт из диапазона 0-2.'; ui_pause ;;
        esac
    done
}

ui_repair_menu() {
    local choice
    ui_clear
    ui_box '🔧 БЕЗОПАСНЫЙ REPAIR' \
        '[1] 🔎 Только проверить исправимые отклонения' \
        '[2] 🛠️  Применить manifest-bounded repair' \
        '[0] ↩️  Назад' '' \
        'Repair не усыновляет чужие ресурсы и не меняет firewall/PostgreSQL data.'
    choice=$(ui_choice '0-2') || return
    case "$choice" in
        1) ui_run_operation '🔎 ПРОВЕРКА REPAIR' repair --check || true ;;
        2) ui_run_operation '🛠️  ПРИМЕНЕНИЕ REPAIR' repair --apply || true ;;
        0) return ;;
        *) ui_box '⚠️  НЕВЕРНЫЙ ВЫБОР' 'Выберите 0, 1 или 2.'; ui_pause ;;
    esac
}

ui_clean_menu() {
    local choice version
    version=$(ui_current_version)
    ui_clear
    ui_box '🤖 JUST1KBOT MANAGER' '' \
        "Состояние:  $(ui_state_label)" \
        'Сервер:     свободен от зарезервированных ресурсов' \
        'Branch:     main' \
        "Код:        $version" \
        '__RULE__' '' \
        '[1] 🚀 Установить с managed Nginx/TLS' \
        '[2] 🌐 Установить за внешним reverse proxy' \
        '[3] 🔬 Предварительная диагностика (dry-run)' \
        '[4] 🔎 Подробности состояния' \
        '[0] ❌ Выход' ''
    choice=$(ui_choice '0-4') || return 10
    case "$choice" in
        1) ui_run_operation '🚀 УСТАНОВКА JUST1KBOT' deploy || true ;;
        2) ui_run_operation '🚀 УСТАНОВКА JUST1KBOT — EXTERNAL PROXY' deploy --external-proxy || true ;;
        3) ui_run_operation '🔬 READ-ONLY INSTALL DRY-RUN' deploy --dry-run || true ;;
        4) ui_show_state ;;
        0) return 10 ;;
        *) ui_box '⚠️  НЕВЕРНЫЙ ВЫБОР' 'Выберите пункт из диапазона 0-4.'; ui_pause ;;
    esac
}

ui_installed_menu() {
    local choice version domain host status uptime
    version=$(ui_current_version)
    domain=$(ui_env_value DOMAIN)
    host=$(hostname -f 2>/dev/null || hostname)
    status=$(ui_service_state)
    uptime=$(ui_service_uptime)
    ui_clear
    ui_box '🤖 JUST1KBOT MANAGER' '' \
        "Состояние:  $status" \
        "Путь:       $UI_PROJECT_DIR" \
        "Сервис:     $UI_SERVICE ($uptime)" \
        "Версия:     $version" \
        "Домен:      ${domain:-не задан}" \
        "Сервер:     $host" \
        '__RULE__' '' \
        '[1] 📋 Логи' \
        '[2] 🔄 Проверить обновления' \
        '[3] 🔁 Повторно развернуть текущую версию' \
        '[4] 💾 Бэкапы и восстановление' \
        '[5] 🔬 Диагностика' \
        '[6] 🔧 Безопасный repair' \
        '[7] ⚙️  Статус сервиса' \
        '[8] 🗑️  Удаление' \
        '[9] 🔎 Подробности состояния' \
        '[0] ❌ Выход' ''
    choice=$(ui_choice '0-9') || return 10
    case "$choice" in
        1) ui_logs_menu ;;
        2) ui_update_menu ;;
        3) ui_run_operation '🔁 ПОВТОРНОЕ РАЗВЁРТЫВАНИЕ' deploy || true ;;
        4) ui_backups_menu ;;
        5) ui_diagnostics || true ;;
        6) ui_repair_menu ;;
        7) ui_service_status_menu ;;
        8) ui_uninstall_menu ;;
        9) ui_show_state ;;
        0) return 10 ;;
        *) ui_box '⚠️  НЕВЕРНЫЙ ВЫБОР' 'Выберите пункт из диапазона 0-9.'; ui_pause ;;
    esac
}

ui_partial_menu() {
    local choice
    ui_clear
    ui_box '⚠️  НЕЗАВЕРШЁННАЯ ОПЕРАЦИЯ' \
        "Состояние: $(ui_state_label)" \
        "Причина: $UI_REASON" \
        "Следующее действие: $UI_ACTION" \
        '__RULE__' \
        '[1] 🔎 Подробности состояния' \
        '[2] 🧭 Статус восстановления установки' \
        '[3] ↩️  Откатить незавершённую первичную установку' \
        '[4] 🔬 Диагностика' \
        '[5] 📦 Создать support bundle' \
        '[0] ❌ Выход'
    choice=$(ui_choice '0-5') || return 10
    case "$choice" in
        1) ui_show_state ;;
        2) ui_run_operation '🧭 INSTALL RECOVERY STATUS' install-recover || true ;;
        3) ui_run_operation '↩️  INSTALL ROLLBACK' install-rollback || true ;;
        4) ui_diagnostics || true ;;
        5) ui_run_operation '📦 SUPPORT BUNDLE' support-bundle || true ;;
        0) return 10 ;;
        *) ui_box '⚠️  НЕВЕРНЫЙ ВЫБОР' 'Выберите пункт из диапазона 0-5.'; ui_pause ;;
    esac
}

ui_residual_menu() {
    local choice
    ui_clear
    ui_box '⚠️  НАЙДЕНЫ ОСТАТКИ УСТАНОВКИ' \
        "Состояние: $(ui_state_label)" \
        "Причина: $UI_REASON" \
        "Следующее действие: $UI_ACTION" \
        '__RULE__' \
        '[1] 🔎 Подробности состояния' \
        '[2] 🚀 Strict migration / безопасное развёртывание' \
        '[3] 🔧 Проверить repair' \
        '[4] 🔬 Диагностика' \
        '[5] 📦 Создать support bundle' \
        '[6] 🗑️  Безопасное удаление' \
        '[0] ❌ Выход'
    choice=$(ui_choice '0-6') || return 10
    case "$choice" in
        1) ui_show_state ;;
        2) ui_run_operation '🚀 STRICT MIGRATION / DEPLOY' deploy || true ;;
        3) ui_run_operation '🔧 REPAIR CHECK' repair --check || true ;;
        4) ui_diagnostics || true ;;
        5) ui_run_operation '📦 SUPPORT BUNDLE' support-bundle || true ;;
        6) ui_uninstall_menu ;;
        0) return 10 ;;
        *) ui_box '⚠️  НЕВЕРНЫЙ ВЫБОР' 'Выберите пункт из диапазона 0-6.'; ui_pause ;;
    esac
}

ui_blocked_menu() {
    local choice
    ui_clear
    ui_box '⛔ ИЗМЕНЕНИЯ ЗАБЛОКИРОВАНЫ' \
        "Состояние: $(ui_state_label)" \
        "Причина: $UI_REASON" \
        "Следующее действие: $UI_ACTION" \
        '__RULE__' \
        '[1] 🔎 Подробности состояния' \
        '[2] 🔬 Диагностика' \
        '[3] 📄 Doctor JSON' \
        '[4] 📦 Создать support bundle' \
        '[0] ❌ Выход' '' \
        'Mutating actions скрыты: ownership не доказан.'
    choice=$(ui_choice '0-4') || return 10
    case "$choice" in
        1) ui_show_state ;;
        2) ui_diagnostics || true ;;
        3) ui_clear; dispatch doctor --json || true; ui_pause ;;
        4) ui_run_operation '📦 SUPPORT BUNDLE' support-bundle || true ;;
        0) return 10 ;;
        *) ui_box '⚠️  НЕВЕРНЫЙ ВЫБОР' 'Выберите пункт из диапазона 0-4.'; ui_pause ;;
    esac
}

menu() {
    local rc
    while true; do
        ui_load_state
        rc=0
        case "$UI_STATE" in
            clean) ui_clean_menu || rc=$? ;;
            installed_managed) ui_installed_menu || rc=$? ;;
            partial_install) ui_partial_menu || rc=$? ;;
            legacy_managed|residual_managed) ui_residual_menu || rc=$? ;;
            foreign_collision|corrupted_state|unknown) ui_blocked_menu || rc=$? ;;
            *) ui_blocked_menu || rc=$? ;;
        esac
        (( rc != 10 )) || return 0
    done
}

if [[ "${CONTROL_PLANE_UI_SOURCE_ONLY:-0}" != 1 &&
      "${BASH_SOURCE[0]}" == "$0" ]]; then
    printf 'control_plane_ui.sh is source-only\n' >&2
    exit 64
fi
