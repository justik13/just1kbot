#!/usr/bin/env bash

# Runtime compatibility guards loaded after the base installer modules.
# Keep temporary work under one parent so cleanup also works when helpers are
# invoked through command substitutions (which execute in a subshell).
INSTALLER_TMP_ROOT="$(mktemp -d -t just1kbot-session.XXXXXX)"
TMP_DIRS+=("$INSTALLER_TMP_ROOT")

make_temp_dir() {
    mktemp -d "${INSTALLER_TMP_ROOT}/work.XXXXXX"
}

normalize_admin_ids_value() {
    local raw="$1"
    JUST1KBOT_ADMIN_IDS="$raw" "$PYTHON_BIN" - <<'PY'
import json
import os
import re

raw = os.environ.pop("JUST1KBOT_ADMIN_IDS").strip()
if not raw:
    raise SystemExit(2)

try:
    value = json.loads(raw)
except json.JSONDecodeError:
    stripped = raw.strip().strip("[]")
    tokens = [item for item in re.split(r"[\s,;]+", stripped) if item]
    if not tokens or any(re.fullmatch(r"[0-9]+", item) is None for item in tokens):
        raise SystemExit(3)
    value = tokens

if isinstance(value, bool):
    raise SystemExit(4)
if isinstance(value, int):
    value = [value]
elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
    value = [value]
if not isinstance(value, list) or not value:
    raise SystemExit(5)

result = []
seen = set()
for item in value:
    if isinstance(item, bool):
        raise SystemExit(6)
    if isinstance(item, str) and re.fullmatch(r"[0-9]+", item):
        item = int(item)
    if not isinstance(item, int) or item <= 0:
        raise SystemExit(7)
    if item not in seen:
        seen.add(item)
        result.append(item)

print(json.dumps(result, separators=(",", ":")), end="")
PY
}

normalize_admin_ids() {
    local raw normalized
    raw="$(get_env_value ADMIN_IDS)"
    [[ -n "$raw" ]] || return 0
    normalized="$(normalize_admin_ids_value "$raw")" || return 1
    if [[ "$normalized" != "$raw" ]]; then
        set_env_value ADMIN_IDS "$normalized"
        info "ADMIN_IDS приведён к JSON-массиву: ${normalized}"
    fi
}

# Preserve the full base configuration flow and add migration of legacy scalar
# ADMIN_IDS values before application validation.
eval "$(declare -f configure_env | sed '1s/^configure_env /configure_env_base /')"
configure_env() {
    if ! normalize_admin_ids; then
        if is_interactive; then
            warn "Старое значение ADMIN_IDS имеет неверный формат и будет запрошено повторно."
            unset_env_value ADMIN_IDS
        else
            die "ADMIN_IDS должен быть положительным ID или JSON-массивом положительных ID."
        fi
    fi

    configure_env_base "$@"

    if ! normalize_admin_ids; then
        if is_interactive; then
            warn "ADMIN_IDS имеет неверный формат. Введите один ID или JSON-массив."
            unset_env_value ADMIN_IDS
            require_env_value ADMIN_IDS \
                "Telegram ID администраторов, например 872658825 или [872658825]" ""
            normalize_admin_ids \
                || die "Не удалось привести ADMIN_IDS к JSON-массиву."
        else
            die "ADMIN_IDS должен быть положительным ID или JSON-массивом положительных ID."
        fi
    fi
}

# Override the original function to avoid referencing `sha` in the same local
# declaration where it is assigned. With `set -u`, that pattern exits before
# the first log line because RHS expansions happen before local assignments.
prepare_release() {
    local sha release_dir marker
    sha="$1"
    [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die "Некорректный commit SHA релиза: $sha"
    release_dir="${RELEASES_DIR}/${sha}"
    marker="${release_dir}/.prepared-sha"

    if [[ -x "$release_dir/.venv/bin/python" && -f "$marker" ]] \
        && [[ "$(cat "$marker" 2>/dev/null || true)" == "$sha" ]]; then
        printf '%s' "$release_dir"
        return 0
    fi

    info "Подготавливаю релиз ${sha:0:12}..."
    rm -rf -- "$release_dir"
    download_source "$sha" "$release_dir" \
        || die "Не удалось скачать или распаковать релиз ${sha:0:12}."

    info "Создаю virtualenv релиза..."
    "$PYTHON_BIN" -m venv "$release_dir/.venv" \
        || die "Не удалось создать virtualenv в ${release_dir}."

    info "Обновляю pip/setuptools/wheel..."
    "$release_dir/.venv/bin/python" -m pip install --upgrade pip setuptools wheel >&2 \
        || die "Не удалось обновить инструменты Python в virtualenv."

    info "Устанавливаю Python-зависимости..."
    "$release_dir/.venv/bin/pip" install --requirement "$release_dir/requirements.txt" >&2 \
        || die "Не удалось установить зависимости из requirements.txt."

    info "Проверяю синтаксис Python-модулей..."
    "$release_dir/.venv/bin/python" -m compileall -q \
        "$release_dir/bot" "$release_dir/config" "$release_dir/database" \
        "$release_dir/services" "$release_dir/utils" "$release_dir/alembic" \
        || die "Python compileall завершился ошибкой."

    ln -sfn "$ENV_FILE" "$release_dir/.env"
    printf '%s\n' "$sha" > "$marker"
    chown -R root:"$BOT_GROUP" "$release_dir"
    chmod -R u=rwX,g=rX,o= "$release_dir"
    chmod +x "$release_dir/just1kbot.sh"
    printf '%s' "$release_dir"
}
