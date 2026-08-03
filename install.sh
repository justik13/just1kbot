#!/usr/bin/env bash
set -Eeuo pipefail

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:${PATH:-}"

REPO_OWNER="justik13"
REPO_NAME="projectx" # replace this with actual later if needed
DEFAULT_REPO_BRANCH="main"

INSTALL_DIR="/opt/just1kbot"
STATE_DIR="${INSTALL_DIR}/.state"
VERSION_FILE="${STATE_DIR}/release_sha"
ENV_FILE="${INSTALL_DIR}/.env"
VENV_DIR="${INSTALL_DIR}/.venv"
SERVICE_NAME="just1kbot.service"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}"
PYTHON_BIN="$(command -v python3 || echo "/usr/bin/python3")"

REPO_BRANCH="${REPO_BRANCH:-$DEFAULT_REPO_BRANCH}"
REPO_URL="https://github.com/${REPO_OWNER}/${REPO_NAME}"
COMMIT_API_URL="https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/commits/${REPO_BRANCH}"

# Status variables
STATE_BOT_INSTALLED=0
STATE_SERVICE_ACTIVE=0
UPDATE_LOCAL_SHA=""
UPDATE_REMOTE_SHA=""
UPDATE_REMOTE_TITLE=""
UPDATE_STATUS="unknown"

# Colors
color_red() { printf '\033[0;31m%s\033[0m' "$1"; }
color_green() { printf '\033[0;32m%s\033[0m' "$1"; }
color_yellow() { printf '\033[0;33m%s\033[0m' "$1"; }

print_line() { printf '%s\n' "------------------------------------------------------------"; }
info() { printf '[*] %s\n' "$*" >&2; }
ok() { printf '[+] %s\n' "$*" >&2; }
warn() { printf '[!] %s\n' "$*" >&2; }
error() { printf '[ERROR] %s\n' "$*" >&2; }
die() { error "$*"; return 1; }

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    die "Этот скрипт нужно запускать от пользователя root (sudo)."
  fi
}

get_local_sha() {
  if [[ -f "$VERSION_FILE" ]]; then
    cat "$VERSION_FILE"
  fi
}

fetch_remote_commit_info() {
  local payload="" parsed=""
  payload="$(curl -fsSL --connect-timeout 10 --max-time 30 --retry 2 --retry-delay 5 "$COMMIT_API_URL" 2>/dev/null || true)"
  [[ -n "$payload" ]] || return 0
  parsed="$("$PYTHON_BIN" - "$payload" <<'PY' 2>/dev/null || true
import json
import sys

raw = sys.argv[1]
try:
    data = json.loads(raw)
except Exception:
    raise SystemExit(0)

sha = data.get("sha", "")
commit = data.get("commit", {})
message = ""
if isinstance(commit, dict):
    message = commit.get("message", "")

if isinstance(sha, str):
    sha = sha.strip()
else:
    sha = ""

if isinstance(message, str):
    title = message.splitlines()[0].strip()
else:
    title = ""

if sha:
    print(f"{sha}\t{title}")
PY
)"
  printf '%s' "$parsed"
}

detect_install_state() {
  if [[ -d "$INSTALL_DIR" && -d "$INSTALL_DIR/bot" && -f "$INSTALL_DIR/bot/main.py" ]]; then
    STATE_BOT_INSTALLED=1
  fi

  if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    STATE_SERVICE_ACTIVE=1
  fi

  UPDATE_LOCAL_SHA="$(get_local_sha)"

  info_line="$(fetch_remote_commit_info)"
  UPDATE_REMOTE_SHA="${info_line%%$'\t'*}"
  if [[ "$info_line" == *$'\t'* ]]; then
    UPDATE_REMOTE_TITLE="${info_line#*$'\t'}"
  fi

  if [[ -z "$UPDATE_REMOTE_SHA" ]]; then
    UPDATE_STATUS="unknown"
  elif [[ -n "$UPDATE_LOCAL_SHA" && "$UPDATE_REMOTE_SHA" == "$UPDATE_LOCAL_SHA" ]]; then
    UPDATE_STATUS="current"
  else
    UPDATE_STATUS="available"
  fi
}

show_status() {
  clear
  print_line
  printf "                   %s\n" "$(color_green "УСТАНОВЩИК JUST1KBOT")"
  print_line
  info "Проверка состояния системы..."
  info "Получение информации с GitHub..."
  echo

  echo "Предварительная проверка:"
  if [[ "$STATE_BOT_INSTALLED" == 1 ]]; then
    echo "Бот установлен:      $(color_green "Да (${INSTALL_DIR})")"
  else
    echo "Бот установлен:      $(color_red "Нет")"
  fi

  if [[ "$STATE_BOT_INSTALLED" == 1 ]]; then
    if [[ "$STATE_SERVICE_ACTIVE" == 1 ]]; then
      echo "Сервис systemd:      $(color_green "Активен (running)")"
    else
      echo "Сервис systemd:      $(color_red "Неактивен (failed/stopped)")"
    fi
  fi

  echo
  if [[ "$STATE_BOT_INSTALLED" == 1 ]]; then
    echo "Локальная версия:    ${UPDATE_LOCAL_SHA:-неизвестно}"
    echo "Доступный commit:    ${UPDATE_REMOTE_SHA:-не удалось получить} (${UPDATE_REMOTE_TITLE})"

    if [[ "$UPDATE_STATUS" == "available" ]]; then
      echo "Обновление:          $(color_red "Доступна новая версия!")"
    elif [[ "$UPDATE_STATUS" == "current" ]]; then
      echo "Обновление:          $(color_green "Версия актуальна")"
    else
      echo "Обновление:          $(color_yellow "Неизвестно")"
    fi
  else
    echo "Доступный commit:    ${UPDATE_REMOTE_SHA:-не удалось получить} (${UPDATE_REMOTE_TITLE})"
  fi

  print_line
  if [[ "$STATE_BOT_INSTALLED" == 0 ]]; then
    echo "Состояние: Бот не установлен. Готов к чистой установке."
    echo "Что делать дальше:"
    echo "• Выберите \"Установить бота\", чтобы скачать и настроить последнюю версию."
  elif [[ "$UPDATE_STATUS" == "available" ]]; then
    echo "Состояние: Бот успешно работает, но требует обновления."
    printf '%s\n' "$(color_red '[!] ДОСТУПНО ОБНОВЛЕНИЕ')"
    echo "    Открой пункт меню: 1) Обновить бота"
  elif [[ "$UPDATE_STATUS" == "current" ]]; then
    echo "Состояние: Бот установлен и работает."
  else
    echo "Состояние: Бот установлен. Статус обновления неизвестен."
  fi
  print_line
}

main_menu() {
  while true; do
    detect_install_state
    show_status
    echo "Меню управления:"

    if [[ "$STATE_BOT_INSTALLED" == 0 ]]; then
      echo "  1) 🚀 Установить бота (последняя версия)"
      echo "  0) ❌ Выход"
      echo
      read -r -p "Введите номер действия: " choice
      case "$choice" in
        1) install_bot ;;
        0) echo "Выход."; break ;;
        *) warn "Неверный выбор" ; sleep 1 ;;
      esac
    elif [[ "$UPDATE_STATUS" == "available" ]]; then
      echo "  1) 🔄 Обновить бота"
      echo "  2) ⚙️  Управление сервисом (Перезапуск / Остановка)"
      echo "  3) 📋 Посмотреть логи (bot.log)"
      echo "  4) 🗑️  Удалить бота"
      echo "  0) ❌ Выход"
      echo
      read -r -p "Введите номер действия: " choice
      case "$choice" in
        1) install_bot ;; # Update uses the same flow but preserves data
        2) manage_service ;;
        3) view_logs ;;
        4) uninstall_bot ;;
        0) echo "Выход."; break ;;
        *) warn "Неверный выбор" ; sleep 1 ;;
      esac
    else
      echo "  1) 🔁 Переустановить бота (чистая переустановка текущей версии)"
      echo "  2) ⚙️  Управление сервисом (Перезапуск / Остановка)"
      echo "  3) 📋 Посмотреть логи (bot.log)"
      echo "  4) 🗑️  Удалить бота"
      echo "  0) ❌ Выход"
      echo
      read -r -p "Введите номер действия: " choice
      case "$choice" in
        1) install_bot ;;
        2) manage_service ;;
        3) view_logs ;;
        4) uninstall_bot ;;
        0) echo "Выход."; break ;;
        *) warn "Неверный выбор" ; sleep 1 ;;
      esac
    fi
  done
}

download_repo() {
  local tmp_dir src_dir download_url ref="${1:-$REPO_BRANCH}"
  tmp_dir="$(mktemp -d)"
  download_url="https://codeload.github.com/${REPO_OWNER}/${REPO_NAME}/tar.gz/${ref}"
  info "Скачиваю код из ${REPO_URL} (ref=${ref})..."
  if ! curl -fsSL --connect-timeout 20 --max-time 300 --retry 3 --retry-delay 5 "$download_url" -o "$tmp_dir/repo.tar.gz"; then
    warn "Не удалось скачать репозиторий. Проверяю сеть..."
    curl -v --connect-timeout 10 https://github.com 2>&1 | head -5 || true
    rm -rf "$tmp_dir"
    return 1
  fi
  tar -xzf "$tmp_dir/repo.tar.gz" -C "$tmp_dir"
  src_dir="$(find "$tmp_dir" -mindepth 1 -maxdepth 1 -type d | head -n1 || true)"
  if [[ -z "$src_dir" || ! -d "$src_dir/bot" ]]; then
    warn "Не удалось скачать корректную структуру репозитория."
    rm -rf "$tmp_dir"
    return 1
  fi
  printf '%s' "$tmp_dir"
}

deploy_repo() {
  local tmp_dir="$1" src_dir
  src_dir="$(find "$tmp_dir" -mindepth 1 -maxdepth 1 -type d | head -n1 || true)"
  if [[ -z "$src_dir" || ! -d "$src_dir/bot" ]]; then
    warn "Не найдены файлы репозитория для развёртывания."
    return 1
  fi

  mkdir -p "$INSTALL_DIR" "$STATE_DIR"

  # Remove old core files, except .env, DBs, and state
  rm -rf "$INSTALL_DIR/bot" "$INSTALL_DIR/scripts"
  rm -f "$INSTALL_DIR/deploy.sh" "$INSTALL_DIR/install.sh"

  cp -a "$src_dir/bot" "$INSTALL_DIR/"
  [[ -d "$src_dir/scripts" ]] && cp -a "$src_dir/scripts" "$INSTALL_DIR/"
  [[ -f "$src_dir/deploy.sh" ]] && cp "$src_dir/deploy.sh" "$INSTALL_DIR/"
  [[ -f "$src_dir/install.sh" ]] && cp "$src_dir/install.sh" "$INSTALL_DIR/"
  [[ -f "$src_dir/requirements.txt" ]] && cp "$src_dir/requirements.txt" "$INSTALL_DIR/"
  [[ -f "$src_dir/requirements.lock" ]] && cp "$src_dir/requirements.lock" "$INSTALL_DIR/"

  return 0
}

setup_venv() {
  info "Настройка виртуального окружения..."
  if ! command -v python3 >/dev/null 2>&1; then
    die "Python3 не установлен."
  fi

  if [[ ! -d "$VENV_DIR" ]]; then
    if ! "$PYTHON_BIN" -m venv "$VENV_DIR"; then
      info "Пытаюсь установить python3-venv..."
      export DEBIAN_FRONTEND=noninteractive
      apt-get update -qq || true
      apt-get install -yqq python3-venv || die "Не удалось установить venv"
      "$PYTHON_BIN" -m venv "$VENV_DIR" || die "Не удалось создать venv"
    fi
  fi

  "$VENV_DIR/bin/pip" install --upgrade pip wheel >/dev/null
  if [[ -f "$INSTALL_DIR/requirements.txt" ]]; then
    "$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt" >/dev/null || die "Не удалось установить зависимости."
  elif [[ -f "$INSTALL_DIR/bot/requirements.txt" ]]; then
    "$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/bot/requirements.txt" >/dev/null || die "Не удалось установить зависимости."
  fi
  ok "Зависимости установлены."
}

setup_service() {
  info "Создание systemd сервиса..."
  cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Just1kBot Telegram Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=-${ENV_FILE}
ExecStart=${VENV_DIR}/bin/python bot/main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "$SERVICE_NAME"
  systemctl restart "$SERVICE_NAME"
  ok "Сервис настроен и запущен."
}

install_bot() {
  local deploy_sha="${UPDATE_REMOTE_SHA}"
  if [[ -z "$deploy_sha" ]]; then
    deploy_sha="$REPO_BRANCH"
  fi

  info "Начинаем установку..."

  local tmp_dir
  tmp_dir="$(download_repo "$deploy_sha")" || die "Не удалось скачать код проекта из GitHub."

  deploy_repo "$tmp_dir" || { rm -rf "$tmp_dir"; die "Не удалось развернуть файлы проекта."; }
  rm -rf "$tmp_dir"

  if [[ ! -f "$ENV_FILE" ]]; then
    info "Создаем начальный .env файл..."
    echo "# Just1kBot Environment Variables" > "$ENV_FILE"
    echo "BOT_TOKEN=" >> "$ENV_FILE"
    echo "ADMIN_ID=" >> "$ENV_FILE"
    warn "Пожалуйста, не забудьте заполнить $ENV_FILE вашим токеном бота!"
  fi

  setup_venv
  setup_service

  if [[ -n "$deploy_sha" && "$deploy_sha" != "$REPO_BRANCH" ]]; then
    echo "$deploy_sha" > "$VERSION_FILE"
  fi

  ok "Установка успешно завершена!"
  sleep 3
}
manage_service() {
  echo "Управление сервисом ${SERVICE_NAME}:"
  echo "  1) Перезапустить (restart)"
  echo "  2) Остановить (stop)"
  echo "  3) Запустить (start)"
  echo "  0) Назад"
  read -r -p "Выбор: " s_choice
  case "$s_choice" in
    1) systemctl restart "$SERVICE_NAME"; ok "Сервис перезапущен."; sleep 2 ;;
    2) systemctl stop "$SERVICE_NAME"; ok "Сервис остановлен."; sleep 2 ;;
    3) systemctl start "$SERVICE_NAME"; ok "Сервис запущен."; sleep 2 ;;
    0) return 0 ;;
    *) warn "Неверный выбор"; sleep 1 ;;
  esac
}

view_logs() {
  if systemctl list-unit-files "$SERVICE_NAME" >/dev/null 2>&1; then
    journalctl -u "$SERVICE_NAME" -n 50 -f
  else
    warn "Сервис не найден."
    sleep 2
  fi
}

uninstall_bot() {
  warn "ВНИМАНИЕ: Это удалит бота и сервис systemd."
  echo "  1) Удалить только бота (оставить .env и базу данных)"
  echo "  2) Полное удаление (удалить ВСЁ)"
  echo "  0) Отмена"
  read -r -p "Выбор: " d_choice
  case "$d_choice" in
    1)
      systemctl stop "$SERVICE_NAME" || true
      systemctl disable "$SERVICE_NAME" || true
      rm -f "$SERVICE_FILE"
      systemctl daemon-reload
      rm -rf "$INSTALL_DIR/bot" "$INSTALL_DIR/.venv" "$INSTALL_DIR/scripts"
      ok "Бот удален, данные сохранены в $INSTALL_DIR."
      sleep 3
      exit 0
      ;;
    2)
      systemctl stop "$SERVICE_NAME" || true
      systemctl disable "$SERVICE_NAME" || true
      rm -f "$SERVICE_FILE"
      systemctl daemon-reload
      rm -rf "$INSTALL_DIR"
      ok "Бот и все данные полностью удалены."
      sleep 3
      exit 0
      ;;
    0) return 0 ;;
    *) warn "Неверный выбор"; sleep 1 ;;
  esac
}

require_root
detect_install_state
main_menu
