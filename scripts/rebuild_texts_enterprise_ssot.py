"""Rebuilds modular enterprise texts SSOT, domain __init__.py files, eliminates duplicate text values, and decouples workers."""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEXTS_DIR = ROOT / "bot" / "texts"

# Explicit canonical text constants to add to common/buttons.py
ADDITIONAL_BUTTONS = {
    "BTN_ENABLE_SERVER": "🔘 Включить сервер",
    "BTN_TO_SERVER": "⚙️ К серверу",
    "BTN_SERVERS_LIST": "📋 Список серверов",
    "BTN_DISMISS_ALERT": "🗑 Прочитано",
    "BTN_HIDE": "✖ Скрыть",
    "BTN_OPEN_USER_CARD": "👤 Открыть карточку",
    "BTN_RENEW_ACCESS": "💳 Продлить доступ",
    "BTN_DISMISS_NOTIFICATION": "✅ Прочитано (убрать)",
    "BTN_DISMISS": "✅ Прочитано",
    "BTN_RESUME_PURCHASE": "🛒 Продолжить оформление",
    "BTN_MY_BALANCE": "🎁 Мой баланс",
    "BTN_PAYMENT_PAY": "💳 Оплатить",
    "BTN_PAYMENT_CHECK": "🔄 Проверить оплату",
    "BTN_PAYMENT_CANCEL": "❌ Отмена",
    "BTN_MAIN_MENU": "🏠 Главное меню",
}

# Explicit runtime notifications and alerts to ensure are present in runtime/
ADDITIONAL_RUNTIME_NOTIFICATIONS = {
    "NOTIFY_3D": """⏳ <b>Напоминание о подписке</b>

До окончания вашей подписки осталось <b>3 дня</b>.

Продлите доступ заранее, чтобы не потерять подключение.""",
    "NOTIFY_1D": """⏳ <b>Напоминание о подписке</b>

До окончания вашей подписки остался <b>1 день</b>.

Продлите доступ, чтобы ваши устройства не отключились.""",
    "NOTIFY_2H": """⚠️ <b>Подписка скоро закончится!</b>

До окончания подписки осталось <b>2 часа</b>.

Продлите доступ прямо сейчас, чтобы связь не прервалась.""",
    "NOTIFY_EXPIRED": """🔴 <b>Подписка закончилась!</b>

Доступ к серверам приостановлен.

⏳ До полного удаления ваших настроек и ключей осталось: <b>{countdown}</b>.

Продлите подписку, чтобы сохранить все настройки.""",
    "NOTIFY_GRACE_12H": """🚨 <b>Внимание: Скоро удаление устройств!</b>

Осталось менее <b>12 часов</b> до безвозвратного удаления ваших конфигураций.

Продлите подписку прямо сейчас, чтобы не настраивать всё заново.""",
    "NOTIFY_DEVICES_DELETED": "⚠️ Ваши устройства были удалены из-за истечения подписки. Продлите доступ, чтобы создать новые.",
    "BALANCE_PURCHASE_SUCCESS_NOTIFICATION": "🎉 <b>{title}</b>\n\nДлительность: <b>{duration}</b>\nЛимит устройств: <b>{device_limit}</b>",
    "TITLE_TARIFF_CHANGED": "Тариф успешно изменён!",
    "TITLE_SUBSCRIPTION_EXTENDED": "Подписка успешно продлена!",
    "TOPUP_LINK_CARD": "💳 <b>Оплата создана</b>\n\nСумма: <b>{amount} ₽</b>\n\nНажмите кнопку ниже для перехода к оплате.",
    "REFERRAL_BONUS_ACCREDITED": "🎁 Вам начислен реферальный бонус: <b>+{bonus} ₽</b>!",
    "BALANCE_TOPUP_CREDITED": "✅ <b>Баланс пополнен на +{amount} ₽!</b>\n\n💰 Баланс: <b>{real_balance} ₽</b>\n🎁 Бонусный баланс: <b>{bonus_balance} ₽</b>{resume_hint}{welcome_bonus}",
    "BALANCE_TOPUP_RESUME_HINT": "\n\n💡 Нажмите кнопку ниже, чтобы завершить начатую операцию.",
    "BALANCE_TOPUP_WELCOME_BONUS": "\n\n🎁 <b>Вам начислен приветственный бонус +{bonus} ₽ за первое пополнение по приглашению!</b>",
    "TIME_DAYS_HOURS_FORMAT": "{days} дн. {hours} ч.",
    "TIME_DAYS_FORMAT": "{days} дн.",
    "TIME_HOURS_MINUTES_FORMAT": "{hours} ч. {minutes} мин.",
    "TIME_SOON_LABEL": "в ближайшее время",
}

ADDITIONAL_RUNTIME_ALERTS = {
    "ALERT_BALANCE_LIMIT_EXCEEDED": "⚠️ <b>ВНИМАНИЕ: Превышен лимит баланса!</b>\n\nПлатёж #{payment_id}, пользователь {telegram_id}\nПозиция: {real_position} ₽",
    "ALERT_STALE_PAYMENTS_HEADER": "⚠️ <b>Обнаружены зависшие пополнения ({count} шт.)</b>\n\n{details}",
    "ALERT_STALE_PAYMENT_ROW": "• {icon} #{payment_id} (user {telegram_id}): {amount} {currency} via {method}\n",
    "ALERT_STALE_PAYMENTS_MORE": "• ...и ещё {more_count} платежей\n",
    "ALERT_SERVER_RESTORED": "✅ <b>VPN-сервер восстановлен</b>\n\n🌍 Сервер: <b>{server_name}</b> (ID: {server_id})\nAPI снова стабильно доступен.",
    "ALERT_SERVER_DISK_CRITICAL": "⚠️ <b>ВНИМАНИЕ: Диск VPN-ноды забит > 85%!</b>\n\nСервер: <b>{server_name}</b> (ID: {server_id})\nИспользование диска: <b>{disk_percent:.1f}%</b>\nРекомендуется очистить логи или расширить диск.",
    "ALERT_SERVER_AUTO_DISABLED_RECOVERED": "✅ <b>Сервер восстановлен</b>\n\n🌍 Сервер: <b>{server_name}</b> (ID: {server_id})\nAPI стабильно отвечает.\n\nСервер остаётся отключённым. При необходимости включите его вручную.",
    "ALERT_SERVER_PROBLEM": "⚠️ <b>Проблема с VPN-сервером</b>\n\n🌍 Сервер: <b>{server_name}</b> (ID: {server_id})\nAPI не отвечает после повторной проверки.\n\nВозможна недоступность или нестабильное соединение.\n\n🔍 <b>Проверьте сервер.</b>\nАвтоматический мониторинг продолжается.",
    "ALERT_SERVER_AUTO_DISABLED": "🔴 <b>Сервер автоматически отключён</b>\n\n🌍 Сервер: <b>{server_name}</b> (ID: {server_id})\nСервер не восстановил стабильное соединение в течение 15 минут.\n\nПричина: API недоступен / соединение нестабильно.\nСервер исключён из работы.\n\n🔕 Повторных уведомлений не будет.\nДоступность будет проверяться автоматически каждые 15 минут.",
    "ALERT_TRAFFIC_OVERUSAGE": "⚠️ <b>Fair Usage Policy: Превышение квоты трафика!</b>\n━━━━━━━━━━━━━━━━━━━━\n👤 <b>Пользователь:</b> <code>{telegram_id}</code>\n🖥 <b>Сервер:</b> {server_name}\n📊 <b>Трафик за сутки:</b> {tib:.2f} TiB\n🔑 <b>Профиль ID:</b> {profile_id}\n━━━━━━━━━━━━━━━━━━━━\n<i>Рекомендуется проверить активность пользователя.</i>",
    "ALERT_WORKER_CRASH": "🚨 <b>{title}</b>\n🧩 <b>Воркер:</b> <code>{worker}</code>\n🔁 <b>Падений:</b> {failure_count}\n⚠️ <b>Тип ошибки:</b> <code>{error_type}</code>",
    "ALERT_TITLE_CRITICAL_STOP": "Критическая остановка фоновых задач",
    "ALERT_TITLE_WORKER_FAILED": "Фоновый воркер упал",
}

# Explicit semantic consolidation map for duplicate keys / values
SEMANTIC_CONSOLIDATION_MAP = {
    "BTN_PAYMENT_CUSTOM_AMOUNT_OPTION": "BTN_PAYMENT_SPECIFY_OTHER_AMOUNT",
    "BTN_PAYMENT_TOPUP_AMOUNT_OPTION": "BTN_PAYMENT_TOPUP_PRESET_AMOUNT",
    "PAYMENT_SHOWCASE_SHORTAGE_WARN": "PAYMENT_SHORTAGE_WARNING",
    "PAYMENT_SHOWCASE_DEBT_BLOCKED": "PAYMENT_DEBT_BLOCKED_NOTICE",
    "PAYMENT_PURCHASE_DEBT_BLOCKED": "PAYMENT_DEBT_BLOCKED_NOTICE",
    "PAYMENT_CHANGE_TARIFF_DEBT_BLOCKED": "PAYMENT_DEBT_BLOCKED_NOTICE",
    "PAYMENT_SHOWCASE_DISPUTE_BLOCKED": "PAYMENT_DISPUTE_BLOCKED_NOTICE",
    "PAYMENT_PURCHASE_DISPUTE_BLOCKED": "PAYMENT_DISPUTE_BLOCKED_NOTICE",
    "PAYMENT_CHANGE_TARIFF_DISPUTE_BLOCKED": "PAYMENT_DISPUTE_BLOCKED_NOTICE",
    "PAYMENT_SHOWCASE_DEVICES_BLOCKED": "PAYMENT_DEVICES_BLOCKED_NOTICE",
    "PAYMENT_PURCHASE_DEVICES_BLOCKED": "PAYMENT_DEVICES_BLOCKED_NOTICE",
    "PAYMENT_CHANGE_TARIFF_DEVICES_BLOCKED": "PAYMENT_DEVICES_BLOCKED_NOTICE",
    "PAYMENT_SHOWCASE_OPEN_DISPUTE_BLOCKED": "PAYMENT_DISPUTE_BLOCKED_NOTICE",
    "PAYMENT_SHOWCASE_CHANGE_IN_PROGRESS": "PAYMENT_CHANGE_TARIFF_IN_PROGRESS_NOTICE",
    "PAYMENT_PURCHASE_INSUFFICIENT_FUNDS_ALERT": "PAYMENT_INSUFFICIENT_FUNDS_ALERT",
    "PAYMENT_CHANGE_TARIFF_INSUFFICIENT_FUNDS": "PAYMENT_INSUFFICIENT_FUNDS_ALERT",
    "PAYMENT_PURCHASE_CANCELLED_NO_DEBIT": "PAYMENT_CANCELLED_NO_DEBIT_NOTICE",
    "PAYMENT_CHANGE_TARIFF_CANCELLED_NO_DEBIT": "PAYMENT_CANCELLED_NO_DEBIT_NOTICE",
    "PAYMENT_PURCHASE_QUOTE_EXPIRED_ALERT": "PAYMENT_QUOTE_EXPIRED_RETRY_NOTICE",
    "PAYMENT_CHANGE_TARIFF_QUOTE_EXPIRED_ALERT": "PAYMENT_QUOTE_EXPIRED_RETRY_NOTICE",
    "PAYMENT_PURCHASE_QUOTE_EXPIRED_RETRY": "PAYMENT_QUOTE_EXPIRED_RETRY_NOTICE",
    "PAYMENT_CHANGE_TARIFF_QUOTE_EXPIRED_RETRY": "PAYMENT_QUOTE_EXPIRED_RETRY_NOTICE",
    "PAYMENT_PURCHASE_QUOTE_NOT_FOUND": "PAYMENT_QUOTE_NOT_FOUND_NOTICE",
    "PAYMENT_CHANGE_TARIFF_QUOTE_NOT_FOUND": "PAYMENT_QUOTE_NOT_FOUND_NOTICE",
    "PAYMENT_PURCHASE_NOT_ACTIVE": "PAYMENT_OPERATION_NOT_ACTIVE_NOTICE",
    "PAYMENT_CHANGE_TARIFF_NOT_ACTIVE": "PAYMENT_OPERATION_NOT_ACTIVE_NOTICE",
    "PAYMENT_PURCHASE_UNAVAILABLE": "PAYMENT_TARIFF_UNAVAILABLE_NOTICE",
    "PAYMENT_CHANGE_TARIFF_UNAVAILABLE": "PAYMENT_TARIFF_UNAVAILABLE_NOTICE",
    "PAYMENT_PURCHASE_CREATING_LINK": "PAYMENT_CREATING_LINK_NOTICE",
    "PAYMENT_CHANGE_TARIFF_CREATING_LINK": "PAYMENT_CREATING_LINK_NOTICE",
    "PAYMENT_PURCHASE_AMOUNT_PROMPT": "PAYMENT_CUSTOM_AMOUNT_PROMPT",
    "PAYMENT_CHANGE_TARIFF_AMOUNT_PROMPT": "PAYMENT_CUSTOM_AMOUNT_PROMPT",
    "PAYMENT_PURCHASE_SHORTAGE_LINE": "PAYMENT_SHORTAGE_LINE",
    "PAYMENT_CHANGE_TARIFF_SHORTAGE_LINE": "PAYMENT_SHORTAGE_LINE",
    "PAYMENT_SHOWCASE_DURATION_DAYS": "TIME_DAYS_FORMAT",
    "PAYMENT_CHANGE_TARIFF_DURATION_DAYS": "TIME_DAYS_FORMAT",
    "PAYMENT_SHOWCASE_TARIFF_CHANGE_CARD": "PAYMENT_TARIFF_CHANGE_HEADER_CARD",
    "PAYMENT_PURCHASE_CONFIRM_CARD": "PAYMENT_PURCHASE_CONFIRMATION_CARD",
    "PAYMENT_CHANGE_TARIFF_CONFIRM_CARD": "PAYMENT_TARIFF_CHANGE_CONFIRMATION_CARD",
    "PAYMENT_PURCHASE_SUCCESS_CARD": "PAYMENT_PURCHASE_SUCCESS_CARD",
    "PAYMENT_CHANGE_TARIFF_SUCCESS_CARD": "PAYMENT_TARIFF_CHANGE_SUCCESS_CARD",
    "DASHBOARD_MANAGE_POLZOVATELYAMI_I_RA": "ADMIN_DASHBOARD_SECTION_USERS_BROADCAST",
    "ADMIN_USERS_BALANCE_U_POLZOVATELYA_NEDOSTATOCHNO_B": "ADMIN_USER_BALANCE_INSUFFICIENT_FOR_DEBIT",
    "CONNECTION_CONFIG_DEVICE_VIEW_CHAST_SAYTOV_SERVISOV_MOZHET_B": "CONNECTION_THIRD_PARTY_SERVICE_NOTICE",
}

def rebuild():
    print("Step 1: Updating domain modules and injecting additional templates...")
    
    # 1. Update common/buttons.py
    buttons_file = TEXTS_DIR / "common" / "buttons.py"
    b_vars = _load_vars(buttons_file)
    b_vars.update(ADDITIONAL_BUTTONS)
    _write_vars(buttons_file, b_vars)

    # 2. Update runtime/notifications.py
    notif_file = TEXTS_DIR / "runtime" / "notifications.py"
    n_vars = _load_vars(notif_file)
    n_vars.update(ADDITIONAL_RUNTIME_NOTIFICATIONS)
    _write_vars(notif_file, n_vars)

    # 3. Update runtime/alerts.py
    alert_file = TEXTS_DIR / "runtime" / "alerts.py"
    a_vars = _load_vars(alert_file)
    a_vars.update(ADDITIONAL_RUNTIME_ALERTS)
    _write_vars(alert_file, a_vars)

    print("Step 2: Consolidating duplicate keys and updating call sites...")
    # Apply consolidation across all domain files
    for py_file in TEXTS_DIR.rglob("*.py"):
        if py_file.name == "__init__.py":
            continue
        v = _load_vars(py_file)
        new_v = {}
        for k, val in v.items():
            canonical_k = SEMANTIC_CONSOLIDATION_MAP.get(k, k)
            new_v[canonical_k] = val
        _write_vars(py_file, new_v)

    # Deduplicate assignments across domain files (strict SSOT with priority)
    seen_keys: dict[str, Path] = {}
    ordered_domains = ["common", "runtime", "user", "connection", "payment", "admin"]
    for d in ordered_domains:
        d_dir = TEXTS_DIR / d
        for py_file in sorted(d_dir.glob("*.py")):
            if py_file.name == "__init__.py":
                continue
            v = _load_vars(py_file)
            cleaned_v = {}
            for k, val in v.items():
                if k in seen_keys:
                    print(f"Deduplicating cross-module key {k} in {py_file.name} (already in {seen_keys[k].name})")
                else:
                    seen_keys[k] = py_file
                    cleaned_v[k] = val
            _write_vars(py_file, cleaned_v)

    print("Step 3: Creating domain subpackage __init__.py files...")
    domains = ["common", "user", "connection", "payment", "admin", "runtime"]
    domain_exported_keys: dict[str, list[str]] = {}

    for d in domains:
        d_dir = TEXTS_DIR / d
        d_keys = []
        d_imports = []
        for py_file in sorted(d_dir.glob("*.py")):
            if py_file.name == "__init__.py":
                continue
            mod_name = py_file.stem
            v = _load_vars(py_file)
            sorted_k = sorted(v.keys())
            if sorted_k:
                d_imports.append(f"from bot.texts.{d}.{mod_name} import (")
                for k in sorted_k:
                    d_imports.append(f"    {k},")
                    d_keys.append(k)
                d_imports.append(")")
                d_imports.append("")

        init_lines = [
            f'"""Domain package bot.texts.{d}."""',
            "from __future__ import annotations",
            "",
        ]
        init_lines.extend(d_imports)
        init_lines.append("__all__ = [")
        for k in sorted(set(d_keys)):
            init_lines.append(f'    "{k}",')
        init_lines.append("]")
        init_lines.append("")

        (d_dir / "__init__.py").write_text("\n".join(init_lines), encoding="utf-8")
        domain_exported_keys[d] = sorted(set(d_keys))
        print(f"Generated bot/texts/{d}/__init__.py with {len(domain_exported_keys[d])} keys")

    print("Step 4: Rebuilding root facade bot/texts/__init__.py...")
    all_keys = []
    root_imports = []
    for d in domains:
        root_imports.append(f"# Domain: {d}")
        root_imports.append(f"from bot.texts.{d} import (")
        for k in domain_exported_keys[d]:
            root_imports.append(f"    {k},")
            all_keys.append(k)
        root_imports.append(")")
        root_imports.append("")

    all_keys = sorted(set(all_keys))

    root_lines = [
        '"""Centralized static facade for all bot texts.',
        "",
        "Maintains backward-compatibility for 'from bot import texts; texts.KEY'",
        'while keeping domain-based modularity under bot/texts/*.',
        '"""',
        "from __future__ import annotations",
        "",
    ]
    root_lines.extend(root_imports)
    root_lines.append("")
    root_lines.append("_TEXT_KEYS: frozenset[str] = frozenset([")
    for k in all_keys:
        root_lines.append(f'    "{k}",')
    root_lines.append("])")
    root_lines.append("")
    root_lines.append("def get_text(key: str, default: any = None) -> any:")
    root_lines.append("    return globals().get(key, default)")
    root_lines.append("")
    root_lines.append("def get_all_text_keys() -> list[str]:")
    root_lines.append("    return sorted(_TEXT_KEYS)")
    root_lines.append("")
    root_lines.append("def reload_texts() -> None:")
    root_lines.append('    """Static texts do not require dynamic reload."""')
    root_lines.append("    pass")
    root_lines.append("")
    root_lines.append("__all__ = [")
    root_lines.append('    "get_text",')
    root_lines.append('    "get_all_text_keys",')
    root_lines.append('    "reload_texts",')
    for k in all_keys:
        root_lines.append(f'    "{k}",')
    root_lines.append("]")
    root_lines.append("")

    (TEXTS_DIR / "__init__.py").write_text("\n".join(root_lines), encoding="utf-8")
    print(f"Generated root bot/texts/__init__.py with {len(all_keys)} text keys")

    print("Step 5: Updating call sites across codebase...")
    targets = list((ROOT / "bot").rglob("*.py")) + list((ROOT / "services").rglob("*.py")) + list((ROOT / "tests").rglob("*.py"))
    updated_files = 0
    for target in targets:
        if "texts" in target.parts:
            continue
        content = target.read_text(encoding="utf-8")
        orig = content
        for old_k, new_k in SEMANTIC_CONSOLIDATION_MAP.items():
            content = re.sub(rf"\btexts\.{old_k}\b", f"texts.{new_k}", content)
            content = re.sub(rf"\b{old_k}\b", new_k, content)
        if content != orig:
            target.write_text(content, encoding="utf-8")
            updated_files += 1

    print(f"Updated call sites in {updated_files} files.")


def _load_vars(path: Path) -> dict[str, any]:
    if not path.exists():
        return {}
    content = path.read_text(encoding="utf-8")
    tree = ast.parse(content, filename=str(path))
    res = {}
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    if isinstance(stmt.value, ast.Constant):
                        res[t.id] = stmt.value.value
                    elif isinstance(stmt.value, ast.Dict):
                        res[t.id] = ast.literal_eval(stmt.value)
                    elif isinstance(stmt.value, ast.List):
                        res[t.id] = ast.literal_eval(stmt.value)
                    elif isinstance(stmt.value, ast.Set):
                        res[t.id] = ast.literal_eval(stmt.value)
    return res


def _write_vars(path: Path, vars_dict: dict[str, any]) -> None:
    rel = path.relative_to(TEXTS_DIR).as_posix()
    lines = [
        f'"""Domain texts for {rel}."""',
        "from __future__ import annotations",
        "",
    ]
    for k in sorted(vars_dict.keys()):
        v = vars_dict[k]
        if isinstance(v, str):
            if "\n" in v:
                escaped = v.replace('"""', r'\"\"\"')
                lines.append(f'{k} = """{escaped}"""')
            else:
                escaped = v.replace('"', r'\"')
                lines.append(f'{k} = "{escaped}"')
        else:
            lines.append(f"{k} = {repr(v)}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    rebuild()
