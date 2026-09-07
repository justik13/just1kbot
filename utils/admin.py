import os

from config.settings import get_settings


def is_admin(telegram_id: int) -> bool:
    try:
        return telegram_id in get_settings().ADMIN_IDS
    except Exception:
        raw = os.getenv("ADMIN_IDS", "")
        parsed: set[int] = set()
        for raw_chunk in raw.split(","):
            item = raw_chunk.strip()
            if item.isdigit():
                parsed.add(int(item))
        return telegram_id in parsed