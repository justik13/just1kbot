

def split_text_by_lines(
    text: str,
    limit: int = 4096,
) -> list[str]:
    """
    Разбивает длинный текст на части по строкам с сохранением целостности <blockquote>.
    Гарантирует, что каждая часть строго <= limit символов.
    """
    if text is None:
        return []

    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    current = ""
    in_blockquote = False

    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line

        # Check if candidate would leave an unclosed blockquote
        will_be_in_bq = (in_blockquote or "<blockquote" in line) and ("</blockquote>" not in line)
        reserve = 13 if (will_be_in_bq and limit > 25) else 0

        if len(candidate) + reserve > limit:
            if current:
                if in_blockquote and not current.endswith("</blockquote>"):
                    current += "</blockquote>"
                parts.append(current)

                if in_blockquote and not line.startswith("<blockquote"):
                    current_line = "<blockquote>" + line
                else:
                    current_line = line
            else:
                current_line = line

            if "<blockquote" in current_line:
                in_blockquote = True

            if len(current_line) > limit:
                remaining_line = current_line
                while len(remaining_line) > limit:
                    if in_blockquote and limit > 25:
                        chunk = remaining_line[:limit - 13] + "</blockquote>"
                        parts.append(chunk)
                        remaining_line = "<blockquote>" + remaining_line[limit - 13:]
                    else:
                        parts.append(remaining_line[:limit])
                        remaining_line = remaining_line[limit:]

                current = remaining_line
            else:
                current = current_line
        else:
            current = candidate
            if "<blockquote" in line:
                in_blockquote = True

        if "</blockquote>" in line:
            in_blockquote = False

    if current:
        if in_blockquote and not current.endswith("</blockquote>"):
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
