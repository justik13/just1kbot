import json
import os

from config.settings import get_settings


def is_admin(telegram_id: int) -> bool:
    try:
        return telegram_id in get_settings().ADMIN_IDS
    except Exception:
        raw = os.getenv("ADMIN_IDS", "").strip()
        parsed: set[int] = set()
        if raw.startswith("[") and raw.endswith("]"):
            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, int):
                            parsed.add(item)
                        elif isinstance(item, str) and item.strip().isdigit():
                            parsed.add(int(item.strip()))
                    return telegram_id in parsed
            except Exception:
                pass
        for raw_chunk in raw.strip("[]").split(","):
            item = raw_chunk.strip().strip("'\"")
            if item.isdigit():
                parsed.add(int(item))
        return telegram_id in parsed