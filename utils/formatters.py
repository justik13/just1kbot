from datetime import datetime, timezone
from typing import Optional

from utils.datetime_helpers import format_datetime_msk, days_left_msk


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


def format_datetime(dt: Optional[datetime]) -> str:
    return format_datetime_msk(dt, "%d.%m.%Y %H:%M")


def format_days_left(dt: Optional[datetime]) -> str:
    return days_left_msk(dt)


def format_user_card_text(
    user,
    profiles: list,
    referrals: list,
    now: datetime,
    real_balance: int = 0,
    bonus_balance: int = 0,
    tariff_info: str = "—",
    referrer_info: str = "—",
) -> str:
    from bot import texts
    from utils.telegram import safe

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    has_access = user.subscription_end and user.subscription_end > now

    return texts.ADMIN_USER_CARD.format(
        telegram_id=user.telegram_id,
        username=safe(user.username),
        first_name=safe(user.first_name),
        status=("🟢 Активен" if has_access else "🔴 Неактивен"),
        ban=("🚫 ЗАБАНЕН" if user.is_banned else "✅ Не забанен"),
        tariff_info=safe(tariff_info),
        referrer_info=safe(referrer_info),
        real_balance=real_balance,
        bonus_balance=bonus_balance,
        valid_until=format_datetime(user.subscription_end),
        days_left=format_days_left(user.subscription_end),
        devices_count=len(profiles),
        device_limit=user.device_limit or 0,
        referrals_count=len(referrals),
        created_at=format_datetime(user.created_at),
    )



def get_country_display(country_flag: Optional[str], default_text: str = "🌐") -> str:
    """Return country flag string configured on the server, or default fallback."""
    if not country_flag:
        return default_text
    return country_flag.strip()


def format_audit_details(details: Optional[str]) -> str:
    """Format audit log raw JSON / key-value details into human-readable Russian text."""
    if not details:
        return ""
    
    import json
    
    # Try parsing JSON first
    parsed = None
    if details.strip().startswith("{") and details.strip().endswith("}"):
        try:
            parsed = json.loads(details)
        except Exception:
            parsed = None

    if isinstance(parsed, dict):
        kv_pairs = parsed
    else:
        # Key-value string like "debit=100, credit=0, conversion=True"
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
        "server_name": "Сервер",
        "server_id": "ID сервера",
        "device_name": "Устройство",
        "device_id": "ID устройства",
        "debit": "Списано",
        "credit": "Зачислено",
        "conversion": "Перерасчет",
        "force": "Принудительно",
        "audit_reason": "Причина",
        "success_count": "Успешно",
        "fail_count": "Ошибок",
        "target_audience": "Аудитория",
    }

    formatted_parts = []
    for k, v in kv_pairs.items():
        if k in ("debit", "credit") and str(v).isdigit():
            val = f"{v} ₽"
        elif k == "amount" and str(v).isdigit():
            val = f"{v} ₽"
        elif k == "days" and str(v).isdigit():
            val = f"{v} дн."
        elif k == "conversion":
            val = "Да" if str(v).lower() in ("true", "1") else "Нет"
        elif k == "force":
            val = "Да" if str(v).lower() in ("true", "1") else "Нет"
        else:
            val = str(v)

        label = labels.get(k, k)
        formatted_parts.append(f"{label}: {val}")

    return f" ({', '.join(formatted_parts)})"


def format_connection_device_card(
    profile,
    server_flag: str,
    server_name: str,
    last_connected_text: str,
) -> str:
    from bot import texts
    from utils.telegram import safe

    traffic_total = format_traffic(profile.traffic_down + profile.traffic_up)
    country_display = get_country_display(server_flag, default_text="🌐")

    return texts.DEVICE_CARD.format(
        device_name=safe(profile.device_name),
        flag=server_flag,
        country_display=country_display,
        server_name=safe(server_name),
        last_connected_text=last_connected_text,
        traffic_down=format_traffic(profile.traffic_down),
        traffic_up=format_traffic(profile.traffic_up),
        traffic_total=traffic_total,
    )


def format_admin_breadcrumbs(*crumbs: str) -> str:
    """
    Форматирует строку Хлебных крошек (Breadcrumbs) для административных меню.
    Пример: format_admin_breadcrumbs("🖥 Серверы", "Node #1")
    -> "📌 <b>🏠 Админка ➔ 🖥 Серверы ➔ Node #1</b>\n\n"
    """
    items = ["🏠 Админка"] + [c for c in crumbs if c]
    return f"📌 <b>{' ➔ '.join(items)}</b>\n\n"