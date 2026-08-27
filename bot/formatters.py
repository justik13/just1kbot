"""Presentation formatting helpers for admin UI (breadcrumbs and audit details).

These helpers build user-facing strings and therefore live in the bot layer so they
can read canonical texts from ``bot.texts`` (the SSOT catalogue). The lower
``utils`` layer must stay free of user-facing text and bot imports.
"""

from bot import texts


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

    labels = texts.AUDIT_DETAIL_LABELS

    formatted_parts = []
    for k, v in kv_pairs.items():
        if k in ("debit", "credit", "amount") and str(v).isdigit():
            val = f"{v}{texts.AUDIT_DETAIL_CURRENCY_SUFFIX}"
        elif k == "days" and str(v).isdigit():
            val = f"{v}{texts.AUDIT_DETAIL_DAYS_SUFFIX}"
        elif k in ("conversion", "force", "devices_restored"):
            val = texts.AUDIT_DETAIL_YES if str(v).lower() in ("true", "1") else texts.AUDIT_DETAIL_NO
        elif k == "text":
            text_str = str(v)
            val = f'"{text_str[:40]}..."' if len(text_str) > 40 else f'"{text_str}"'
        elif k == "username":
            val = f"@{v}" if v and not str(v).startswith("@") else str(v or texts.PLACEHOLDER_DASH)
        else:
            val = str(v)

        label = labels.get(k, k)
        formatted_parts.append(f"{label}: {val}")

    return f" ({', '.join(formatted_parts)})"


def format_admin_breadcrumbs(*crumbs: str) -> str:
    """Format breadcrumbs string for admin menus."""
    items = [texts.ADMIN_BREADCRUMB_HOME] + [c for c in crumbs if c]
    path = texts.ADMIN_BREADCRUMB_SEPARATOR.join(items)
    return texts.ADMIN_BREADCRUMB_TEMPLATE.format(path=path)



def get_tariff_display_name(device_limit: int) -> str:
    if device_limit <= 2:
        return texts.TARIFF_DISPLAY_BASIC
    elif device_limit <= 5:
        return texts.TARIFF_DISPLAY_FAMILY
    return texts.TARIFF_DISPLAY_PRO


def get_tariff_group_name(device_limit: int) -> str:
    if device_limit <= 2:
        return texts.TARIFF_DISPLAY_BASIC_GROUP
    elif device_limit <= 5:
        return texts.TARIFF_DISPLAY_FAMILY_GROUP
    return texts.TARIFF_DISPLAY_PRO_GROUP.format(limit=device_limit)


def format_days_left(dt) -> str:
    from utils.datetime_helpers import now_msk, to_msk

    if dt is None:
        return texts.PLACEHOLDER_DASH
    msk_dt = to_msk(dt)
    now_msk_dt = now_msk()
    if msk_dt < now_msk_dt:
        return texts.PLACEHOLDER_DASH
    diff = msk_dt - now_msk_dt
    if diff.days >= 36500:
        return texts.TIME_FOREVER
    days = diff.days
    hours = diff.seconds // 3600
    if days > 0:
        return texts.TIME_DAYS_HOURS_FORMAT.format(days=days, hours=hours)
    return texts.TIME_HOURS_LEFT.format(hours=hours)
