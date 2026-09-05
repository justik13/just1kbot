

MAX_INT64 = 9_223_372_036_854_775_807
MIN_INT64 = -9_223_372_036_854_775_808


def parse_callback_id(
    callback_data: str,
    index: int = 1,
) -> int | None:
    """
    Безопасно достаёт int из callback_data вида:
    admin_server_card:123
    """
    try:
        val = int(callback_data.split(":")[index])
        if not (MIN_INT64 <= val <= MAX_INT64):
            return None
        return val
    except (IndexError, ValueError, TypeError):
        return None


def parse_callback_parts(
    callback_data: str,
    min_parts: int,
) -> list[str] | None:
    """
    Безопасно разбивает callback_data.
    Возвращает None, если частей меньше min_parts.
    """
    if not callback_data:
        return None

    parts = callback_data.split(":")

    if len(parts) < min_parts:
        return None

    return parts


def parse_callback_int(
    parts: list[str],
    index: int,
) -> int | None:
    """
    Безопасно достаёт int из уже разбитых частей.
    """
    try:
        val = int(parts[index])
        if not (MIN_INT64 <= val <= MAX_INT64):
            return None
        return val
    except (IndexError, ValueError, TypeError):
        return None