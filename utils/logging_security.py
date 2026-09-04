"""Secret-safe formatting for application logs and operator alerts."""

from __future__ import annotations

import logging
import re
import traceback
from typing import Any
from urllib.parse import urlsplit

REDACTED = "[REDACTED]"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@]+@"),
        rf"\1{REDACTED}@",
    ),
    (
        re.compile(
            r"(?i)([?&](?:token|access_token|api_key|password|secret)=)[^&#\s]+"
        ),
        rf"\1{REDACTED}",
    ),
    (
        re.compile(
            r"(?i)(['\"](?:authorization|proxy-authorization|cookie|set-cookie)"
            r"['\"]\s*:\s*)(['\"])[\s\S]*?\2"
        ),
        rf"\1\2{REDACTED}\2",
    ),
    (
        re.compile(
            r"(?i)((?:authorization|proxy-authorization|cookie|set-cookie)\s*:\s*)"
            r"[^\r\n]*"
        ),
        rf"\1{REDACTED}",
    ),
    (
        re.compile(
            r"(?i)(['\"]?(?:x[_-]?api[_-]?key|api[_-]?key|access[_-]?token|bot[_-]?token|"
            r"subscription[_-]?token|sub[_-]?token|client[_-]?secret|"
            r"database[_-]?url|redis[_-]?url|redis[_-]?password|postgres[_-]?password|"
            r"db[_-]?password|db[_-]?encryption[_-]?keys?|yookassa[_-]?secret[_-]?key|"
            r"age[_-]?secret[_-]?key|secret[_-]?key|private[_-]?key|preshared[_-]?key|"
            r"password|passwd|secret)['\"]?\s*[:=]\s*)"
            r"(['\"]?)[^\s,;'\"}\]]+\2"
        ),
        rf"\1{REDACTED}",
    ),
    (re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b"), "[TELEGRAM_TOKEN_REDACTED]"),
    (
        re.compile(
            r"(?i)\b(?:vpn|amnezia|vless|vmess|trojan|ss|wg|wireguard|incy|clash|sing-box|shadowrocket)://[^\s<>'\"]+"
        ),
        "[VPN_URI_REDACTED]",
    ),
    (re.compile(r"\bAGE-SECRET-KEY-(?:PQ-)?1[A-Z0-9]{58,}\b"), "[AGE_KEY_REDACTED]"),
    (
        re.compile(r"\b(?:live|test)_[A-Za-z0-9_-]{25,}\b"),
        "[YOOKASSA_KEY_REDACTED]",
    ),
    (re.compile(r"(?i)Fernet\([^\)]*\)"), "Fernet([REDACTED])"),
    (
        re.compile(
            r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{43}=(?![A-Za-z0-9_-])"
        ),
        "[FERNET_KEY_REDACTED]",
    ),
    (
        re.compile(
            r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
        ),
        "[JWT_REDACTED]",
    ),
)


def sanitize_text(value: Any) -> str:
    """Return printable text with known credential forms removed."""
    if value is None:
        return ""
    sanitized = str(value)
    for pattern, replacement in _PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


def sanitize_short(value: Any, limit: int = 200) -> str:
    sanitized = sanitize_text(value)
    return sanitized if len(sanitized) <= limit else sanitized[:limit] + "..."


def safe_url_target(value: Any) -> str:
    """Return only a URL hostname and optional port for logs/alerts."""
    try:
        parsed = urlsplit(str(value))
        host = parsed.hostname or "<invalid-host>"
        return f"{host}:{parsed.port}" if parsed.port is not None else host
    except (TypeError, ValueError):
        return "<invalid-host>"


_STANDARD_LOG_RECORD_ATTRS = frozenset({
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
    "message",
})


def _sanitize_extra_value(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        return {k: _sanitize_extra_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return type(value)(_sanitize_extra_value(v) for v in value)
    return value


class SensitiveDataFilter(logging.Filter):
    """Sanitize the final log message, rendered exception traceback, and extra fields."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = sanitize_text(record.getMessage())
            record.args = ()
            if record.exc_info:
                rendered = "".join(traceback.format_exception(*record.exc_info))
                record.exc_text = sanitize_text(rendered)
                record.exc_info = None
            elif record.exc_text:
                record.exc_text = sanitize_text(record.exc_text)

            for key, val in list(record.__dict__.items()):
                if key not in _STANDARD_LOG_RECORD_ATTRS and not key.startswith("_"):
                    try:
                        record.__dict__[key] = _sanitize_extra_value(val)
                    except Exception:
                        pass
        except Exception:
            record.msg = "Log message redaction failed"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
        return True


def install_sensitive_data_filter(logger: logging.Logger | None = None) -> None:
    """Install one redaction filter on a logger and each of its current handlers."""
    target = logger or logging.getLogger()
    if not any(isinstance(item, SensitiveDataFilter) for item in target.filters):
        target.addFilter(SensitiveDataFilter())
    for handler in target.handlers:
        if not any(isinstance(item, SensitiveDataFilter) for item in handler.filters):
            handler.addFilter(SensitiveDataFilter())
