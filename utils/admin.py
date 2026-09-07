import os

from config.settings import get_settings


def is_admin(telegram_id: int) -> bool:
    try:
        return telegram_id in get_settings().ADMIN_IDS
    except Exception:
        raw = os.getenv("ADMIN_IDS", "")
        parsed: set[int] = set()
        for chunk in raw.split(","):
            chunk = chunk.strip()
            if chunk.isdigit():
                parsed.add(int(chunk))
        return telegram_id in parsed