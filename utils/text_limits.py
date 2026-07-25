from typing import Optional


def split_text_by_lines(
    text: str,
    limit: int = 4096,
) -> list[str]:
    """
    Разбивает длинный текст на части по строкам.
    """
    if text is None:
        return []

    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    current = ""

    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line

        if len(candidate) > limit:
            if current:
                parts.append(current)

            if len(line) > limit:
                while len(line) > limit:
                    parts.append(line[:limit])
                    line = line[limit:]

                current = line
            else:
                current = line
        else:
            current = candidate

    if current:
        parts.append(current)

    return parts


def truncate_details(
    value: Optional[str],
    limit: int = 300,
) -> str:
    """
    Обрезает длинные details для audit log и админских экранов.
    """
    if not value:
        return ""

    value = str(value)

    if len(value) <= limit:
        return value

    return value[:limit] + "…"


def truncate_button_text(
    text: str,
    max_bytes: int = 60,
) -> str:
    """
    Обрезает текст inline-кнопки с учётом лимита Telegram.

    Telegram ограничивает текст кнопки примерно 64 байтами.
    Используем 60 байт как безопасный лимит.
    """
    if text is None:
        return ""

    text = str(text)

    encoded = text.encode("utf-8")

    if len(encoded) <= max_bytes:
        return text

    ellipsis = "…"
    ellipsis_bytes = len(ellipsis.encode("utf-8"))

    target_bytes = max_bytes - ellipsis_bytes

    if target_bytes <= 0:
        return ellipsis

    truncated = encoded[:target_bytes].decode("utf-8", "ignore")

    return truncated.rstrip() + ellipsis