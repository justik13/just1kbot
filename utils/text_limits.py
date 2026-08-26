

import re


def split_text_by_lines(
    text: str,
    limit: int = 4096,
) -> list[str]:
    """
    Разбивает длинный текст на части по строкам с сохранением целостности <blockquote>.
    Математически гарантирует, что каждая часть строго <= limit символов.
    """
    if text is None:
        return []

    if len(text) <= limit:
        return [text]

    # If limit cannot fit <blockquote expandable>x</blockquote> (needs at least 37 chars),
    # strip blockquote tags to plain text so we never emit broken unclosed HTML tag fragments.
    if limit < 37 and "<blockquote" in text:
        text = re.sub(r"</?blockquote(?:\s+[^>]*)?>", "", text)
        if len(text) <= limit:
            return [text]

    parts: list[str] = []
    current = ""
    in_blockquote = False
    bq_open_tag = "<blockquote>"
    close_tag = "</blockquote>"

    for line in text.split("\n"):
        line_has_open = "<blockquote" in line
        line_has_close = "</blockquote>" in line

        open_tag_match = re.search(r"<blockquote(?:\s+[^>]*)?>", line)
        current_open_tag = open_tag_match.group(0) if open_tag_match else bq_open_tag

        will_be_in_bq = (in_blockquote or line_has_open) and not line_has_close
        min_wrapper = len(current_open_tag) + len(close_tag) if will_be_in_bq else 0
        tag_enabled = limit >= min_wrapper + 1

        needed_close = close_tag if (tag_enabled and will_be_in_bq and not line.endswith(close_tag)) else ""
        test_candidate = f"{current}\n{line}" if current else line

        if len(test_candidate) + len(needed_close) <= limit:
            current = test_candidate
            if line_has_open:
                in_blockquote = True
                bq_open_tag = current_open_tag
            if line_has_close:
                in_blockquote = False
        else:
            if current:
                if tag_enabled and in_blockquote and not current.endswith(close_tag):
                    if len(current) + len(close_tag) <= limit:
                        current += close_tag
                    else:
                        current = current[:limit - len(close_tag)] + close_tag
                parts.append(current)
                current = ""

            actual_line = line
            if tag_enabled and in_blockquote and not actual_line.startswith("<blockquote"):
                actual_line = f"{bq_open_tag}{actual_line}"

            if "<blockquote" in actual_line:
                in_blockquote = True
                m = re.search(r"<blockquote(?:\s+[^>]*)?>", actual_line)
                if m:
                    bq_open_tag = m.group(0)

            will_be_in_bq = in_blockquote and not actual_line.endswith(close_tag)
            min_wrapper = len(bq_open_tag) + len(close_tag) if will_be_in_bq else 0
            tag_enabled = limit >= min_wrapper + 1
            needed_close = close_tag if (tag_enabled and will_be_in_bq) else ""

            if len(actual_line) + len(needed_close) > limit:
                rem = actual_line
                while len(rem) + (len(close_tag) if (tag_enabled and in_blockquote and not rem.endswith(close_tag)) else 0) > limit:
                    prefix_len = len(bq_open_tag) if (tag_enabled and in_blockquote and rem.startswith("<blockquote")) else 0
                    max_content_len = limit - (len(close_tag) if (tag_enabled and in_blockquote) else 0)
                    if max_content_len <= prefix_len:
                        max_content_len = limit

                    chunk = rem[:max_content_len]
                    if tag_enabled and in_blockquote and not chunk.endswith(close_tag):
                        chunk += close_tag
                    parts.append(chunk)

                    rem = rem[max_content_len:]
                    if tag_enabled and in_blockquote and rem and not rem.startswith("<blockquote"):
                        rem = f"{bq_open_tag}{rem}"
                current = rem
            else:
                current = actual_line

            if "</blockquote>" in actual_line:
                in_blockquote = False

    if current:
        min_wrapper = len(bq_open_tag) + len(close_tag) if in_blockquote else 0
        tag_enabled = limit >= min_wrapper + 1
        if tag_enabled and in_blockquote and not current.endswith(close_tag):
            if len(current) + len(close_tag) <= limit:
                current += close_tag
            else:
                current = current[:limit - len(close_tag)] + close_tag
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
