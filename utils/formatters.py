"""General formatting helpers for traffic, datetime, breadcrumbs, and audit logs."""
from datetime import datetime

from utils.datetime_helpers import days_left_msk, format_datetime_msk


def format_traffic(bytes_value: int) -> str:
    if bytes_value == 0:
        return "0 B"

    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    size = bytes_value
    unit_index = 0

    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1

    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"
    else:
        return f"{size:.1f} {units[unit_index]}"


def format_datetime(dt: datetime | None) -> str:
    return format_datetime_msk(dt, "%d.%m.%Y %H:%M")


def format_days_left(dt: datetime | None) -> str:
    return days_left_msk(dt)


def get_country_display(country_flag: str | None, default_text: str = "🌐") -> str:
    """Return country flag string configured on the server, or default fallback."""
    if not country_flag:
        return default_text
    return country_flag.strip()


def format_audit_details(details: str | None) -> str:
    """Format audit log raw JSON / key-value details into human-readable Russian text."""
    if not details:
        return ""

    import json

    parsed = None
    trimmed = details.strip()
    if trimmed.startswith("{") and trimmed.endswith("}"):
        try:
            parsed = json.loads(trimmed)
        except Exception:
            parsed = None

    if isinstance(parsed, dict):
        kv_pairs = parsed
    else:
        kv_pairs = {}
        for part in details.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                kv_pairs[k.strip()] = v.strip()

    if not kv_pairs:
        return f" ({details})"

    labels = {
        "amount": "Сумма",
        "days": "Срок",
        "reason": "Причина",
        "tariff_name": "Тариф",
        "tariff_id": "ID тарифа",
        "device_limit": "Лимит устройств",
        "server_name": "Сервер",
        "server_id": "ID сервера",
        "device_name": "Устройство",
        "device_id": "ID устройства",
        "profile_id": "ID устройства",
        "old_name": "Старое имя",
        "new_name": "Новое имя",
        "provider": "Провайдер",
        "payment_id": "ID платежа",
        "referrer_id": "ID пригласившего",
        "referrer_telegram_id": "Telegram ID пригласившего",
        "referred_by": "Пригласил",
        "from_user_id": "От пользователя",
        "telegram_id": "Telegram ID",
        "username": "Username",
        "debit": "Списано",
        "credit": "Зачислено",
        "conversion": "Перерасчет",
        "force": "Принудительно",
        "audit_reason": "Причина",
        "success_count": "Успешно",
        "fail_count": "Ошибок",
        "target_audience": "Аудитория",
        "batch_id": "Пакет",
        "text": "Текст",
        "target_telegram_id": "Telegram ID",
        "outcome": "Результат",
        "note": "Заметка",
        "case": "Кейс",
        "operation": "Операция",
        "profiles_deleted": "Удалено устройств",
        "payments_closed": "Закрыто платежей",
        "devices_restored": "Устройства восстановлены",
        "new_end": "Новый срок",
    }

    formatted_parts = []
    for k, v in kv_pairs.items():
        if k in ("debit", "credit", "amount") and str(v).isdigit():
            val = f"{v} ₽"
        elif k == "days" and str(v).isdigit():
            val = f"{v} дн."
        elif k in ("conversion", "force", "devices_restored"):
            val = "Да" if str(v).lower() in ("true", "1") else "Нет"
        elif k == "text":
            text_str = str(v)
            val = f'"{text_str[:40]}..."' if len(text_str) > 40 else f'"{text_str}"'
        elif k == "username":
            val = f"@{v}" if v and not str(v).startswith("@") else str(v or "—")
        else:
            val = str(v)

        label = labels.get(k, k)
        formatted_parts.append(f"{label}: {val}")

    return f" ({', '.join(formatted_parts)})"


def format_admin_breadcrumbs(*crumbs: str) -> str:
    """Format breadcrumbs string for admin menus."""
    items = ["🏠 Админка"] + [c for c in crumbs if c]
    return f"📌 <b>{' ➔ '.join(items)}</b>\n\n"