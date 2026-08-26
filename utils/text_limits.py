

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
    in_blockquote = False

    for line in text.split("\n"):
        current_line = line
        if "<blockquote" in current_line:
            in_blockquote = True

        candidate = f"{current}\n{current_line}" if current else current_line

        if len(candidate) > limit:
            if current:
                if in_blockquote and "</blockquote>" not in current:
                    current += "</blockquote>"
                    parts.append(current)
                    current_line = "<blockquote>" + current_line
                else:
                    parts.append(current)

            if len(current_line) > limit:
                remaining_line = current_line
                while len(remaining_line) > limit:
                    if in_blockquote:
                        chunk = remaining_line[:limit-13] + "</blockquote>"
                        parts.append(chunk)
                        remaining_line = "<blockquote>" + remaining_line[limit-13:]
                    else:
                        parts.append(remaining_line[:limit])
                        remaining_line = remaining_line[limit:]

                current = remaining_line
            else:
                current = current_line
        else:
            current = candidate

        if "</blockquote>" in current_line:
            in_blockquote = False

    if current:
        if in_blockquote and "</blockquote>" not in current:
            current += "</blockquote>"
        parts.append(current)

    return parts


def truncate_details(
    value: str | None,
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
