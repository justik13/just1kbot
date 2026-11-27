"""Standalone legal compliance audit script for user-facing texts and templates.

Enforces project rules (GEMINI.md Rule 5 and Rule 10) to protect YooKassa merchant
account from suspension due to forbidden vocabulary (VPN, bypass, whitelists, etc.).
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import re
import sys

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Safe defaults if environment lacks .env in CI
os.environ.setdefault("BOT_TOKEN", "123456:TEST_AUDIT_TOKEN")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/db")
os.environ.setdefault("DB_ENCRYPTION_KEY", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("REDIS_PASSWORD", "secret")
os.environ.setdefault("YOOKASSA_SHOP_ID", "12345")
os.environ.setdefault("YOOKASSA_SECRET_KEY", "secret")
os.environ.setdefault("YOOKASSA_RETURN_URL", "https://t.me/{bot_username}")
os.environ.setdefault("YOOKASSA_WEBHOOK_PORT", "8080")
os.environ.setdefault("DOMAIN", "vpn.example.test")
os.environ.setdefault("SSL_EMAIL", "admin@example.test")
os.environ.setdefault("ADMIN_IDS", "[123456]")
os.environ.setdefault("SUPPORT_USERNAME", "support_test")

FORBIDDEN_USER_WORDING: tuple[str, ...] = (
    "Что делать, если VPN не подключается или медленно работает?",
    "Что делать, если ВПН не подключается или медленно работает?",
    "Мы отвечаем в течение 24 часов.",
    "Все серверы работают на максимальной скорости.",
    "👤 Профиль",
    "👥 Реферальная система",
    "не фиксируем и не храним истории подключений",
    "Pro: до 10 устройств",
    "в один клик",
    "в один тап",
    "LTE",
    "обход блокировок",
    "обход блокировки",
    "обход блокиров",
    "защиты от блокировок",
    "защита от блокировок",
    "глушил",
    "белый список",
    "белые списки",
    "белых списков",
    "белым спискам",
    "белых списках",
    "белыми списками",
    "amnezia",
    "amneziawg",
    "amneziavpn",
    "defaultvpn",
)

_CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁ]")
_WHITELIST_INFLECTION_RE = re.compile(r"бел\w*\s+спис\w*", re.IGNORECASE)
_PURE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9_./:-]+$")
_PROTOCOLS = ("vless", "xray", "wireguard", "amnezia", "amneziawg")

_USER_STRING_CALL_NAMES = frozenset({
    "ValueError",
    "safe_send_message",
    "_queue_user_notification",
    "answer",
    "edit_text",
    "reply",
    "json_response",
    "Response",
})


def _check_text_compliance(
    label: str,
    text_val: str,
    *,
    strict_checkout: bool = False,
    allow_identifier_only: bool = False,
) -> list[str]:
    violations: list[str] = []
    if not text_val:
        return violations
    clean = text_val
    low = clean.lower()
    if re.search(r"(?<![A-Za-zА-Яа-я])VPN(?![A-Za-zА-Яа-я])", clean, re.IGNORECASE):
        violations.append(f"[{label}] Contains forbidden 'VPN'")
    if "впн" in low:
        violations.append(f"[{label}] Contains forbidden 'ВПН'")
    if re.search(r"(?<![A-Za-z0-9])LTE(?![A-Za-z0-9])", clean, re.IGNORECASE):
        violations.append(f"[{label}] Contains forbidden 'LTE'")
    for phrase in FORBIDDEN_USER_WORDING:
        if phrase == "LTE":
            continue
        if phrase.lower() in low:
            violations.append(f"[{label}] Contains forbidden phrase: {phrase!r}")
    if _WHITELIST_INFLECTION_RE.search(clean):
        violations.append(f"[{label}] Contains forbidden whitelist inflection")

    clean_no_placeholders = re.sub(r"\{[a-zA-Z0-9_]*\}", "", clean).lower()
    is_pure_ident = allow_identifier_only and bool(_PURE_IDENTIFIER_RE.match(text_val.strip()))
    if not is_pure_ident:
        for proto in _PROTOCOLS:
            if proto in clean_no_placeholders:
                violations.append(f"[{label}] Exposes forbidden protocol name: {proto!r}")
        if re.search(r"\bawg\b", clean_no_placeholders):
            violations.append(f"[{label}] Exposes forbidden protocol name: 'awg'")

    if "прокси" in low:
        violations.append(f"[{label}] Contains forbidden 'прокси'")

    if strict_checkout:
        if "обход" in low:
            violations.append(f"[{label}] Checkout description contains forbidden 'обход'")

    return violations


def _collect_docstring_and_logger_ids(tree: ast.AST) -> tuple[set[int], set[int]]:
    skip_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                skip_ids.add(id(node.body[0].value))
        elif isinstance(node, ast.Call):
            fn = node.func
            is_logger = (
                isinstance(fn, ast.Attribute)
                and isinstance(fn.value, ast.Name)
                and fn.value.id in {"logger", "logging"}
            )
            if is_logger:
                for sub in ast.walk(node):
                    if isinstance(sub, (ast.Constant, ast.JoinedStr)):
                        skip_ids.add(id(sub))
    return skip_ids, set()


def _joined_str_text(node: ast.JoinedStr) -> str:
    parts: list[str] = []
    for val in node.values:
        if isinstance(val, ast.Constant) and isinstance(val.value, str):
            parts.append(val.value)
        else:
            parts.append("{}")
    return "".join(parts)


def _extract_python_user_strings(fpath: Path) -> list[tuple[int, str, bool]]:
    """Extract (lineno, text, is_checkout_desc) for user-facing strings in a Python file."""
    try:
        source = fpath.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(fpath))
    except Exception:
        return []

    skip_ids, _ = _collect_docstring_and_logger_ids(tree)
    desc_node_ids: set[int] = set()
    user_call_node_ids: set[int] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "description":
                    for sub in ast.walk(node.value):
                        if isinstance(sub, (ast.Constant, ast.JoinedStr)):
                            desc_node_ids.add(id(sub))
        elif isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "description":
                    for sub in ast.walk(kw.value):
                        if isinstance(sub, (ast.Constant, ast.JoinedStr)):
                            desc_node_ids.add(id(sub))
            fn_name = ""
            if isinstance(node.func, ast.Name):
                fn_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                fn_name = node.func.attr
            if fn_name in _USER_STRING_CALL_NAMES:
                for arg in node.args:
                    for sub in ast.walk(arg):
                        if isinstance(sub, (ast.Constant, ast.JoinedStr)):
                            user_call_node_ids.add(id(sub))
                for kw in node.keywords:
                    if kw.arg in {"text", "message", "msg", "description", "error"}:
                        for sub in ast.walk(kw.value):
                            if isinstance(sub, (ast.Constant, ast.JoinedStr)):
                                user_call_node_ids.add(id(sub))

    results: list[tuple[int, str, bool]] = []

    def _visit(node: ast.AST) -> None:
        if id(node) in skip_ids:
            return
        if isinstance(node, ast.JoinedStr):
            text_val = _joined_str_text(node)
            is_desc = id(node) in desc_node_ids
            if _CYRILLIC_RE.search(text_val) or is_desc or id(node) in user_call_node_ids:
                results.append((node.lineno, text_val, is_desc))
            return  # do not re-visit children of JoinedStr
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text_val = node.value
            is_desc = id(node) in desc_node_ids
            if _CYRILLIC_RE.search(text_val) or is_desc or id(node) in user_call_node_ids:
                results.append((node.lineno, text_val, is_desc))
            return
        for child in ast.iter_child_nodes(node):
            _visit(child)

    _visit(tree)
    return results


def _collect_user_texts() -> list[tuple[str, str]]:
    from bot.texts import backend
    from bot.texts.runtime import notifications

    results: list[tuple[str, str]] = []
    for mod, mod_name in ((backend, "bot.texts.backend"), (notifications, "bot.texts.runtime.notifications")):
        for name in dir(mod):
            if name.isupper() and not name.startswith("_") and not name.startswith("ADMIN_"):
                val = getattr(mod, name)
                if isinstance(val, str):
                    results.append((f"{mod_name}.{name}", val))
    return results


def run_audit() -> list[str]:
    violations: list[str] = []

    # 1. Audit user-facing text catalogue
    user_texts = _collect_user_texts()
    for key, text_val in user_texts:
        violations.extend(_check_text_compliance(key, text_val))

    # 2. Audit checkout descriptions (YooKassa strict compliance)
    from bot import texts

    descriptions = [
        ("CHECKOUT_DESCRIPTION_DEFAULT", texts.CHECKOUT_DESCRIPTION_DEFAULT),
    ]
    for desc_name, desc_val in descriptions:
        violations.extend(_check_text_compliance(desc_name, desc_val, strict_checkout=True))

    # 3. Audit HTML templates & MiniApp
    admin_allowlist_files = {"node_detail.js", "nodes.js", "user_detail.js", "security_2fa.js"}
    template_dirs = [REPO_ROOT / "bot" / "templates", REPO_ROOT / "frontend" / "miniapp"]
    for t_dir in template_dirs:
        if not t_dir.exists():
            continue
        for fpath in sorted(t_dir.rglob("*")):
            if fpath.suffix in (".html", ".js", ".css"):
                try:
                    content = fpath.read_text(encoding="utf-8")
                except Exception:
                    continue
                clean_content = content
                if fpath.suffix == ".js" and (
                    fpath.name.startswith("admin_") or fpath.name in admin_allowlist_files
                ):
                    clean_content = (
                        clean_content.replace("VLESS_HWID_RESET", "")
                        .replace("VLESS_TOKEN_RESET", "")
                        .replace("vless_synced_epoch", "")
                        .replace("xray_instance_epoch", "")
                        .replace("monthly_vless_bytes", "")
                        .replace("vless_name", "")
                        .replace('caps.includes("vless")', "")
                    )
                rel_path = fpath.relative_to(REPO_ROOT).as_posix()
                violations.extend(_check_text_compliance(rel_path, clean_content))

    # 3b. Audit Caddyfile user-facing error pages / responses
    caddyfile_path = REPO_ROOT / "Caddyfile"
    if caddyfile_path.exists():
        try:
            content = caddyfile_path.read_text(encoding="utf-8")
        except Exception:
            content = ""
        if content:
            rel_path = caddyfile_path.relative_to(REPO_ROOT).as_posix()
            violations.extend(_check_text_compliance(rel_path, content))

    # 4. Audit Telegram keyboards (bot/keyboards/)
    keyboards_dir = REPO_ROOT / "bot" / "keyboards"
    if keyboards_dir.exists():
        for fpath in sorted(keyboards_dir.rglob("*.py")):
            try:
                content = fpath.read_text(encoding="utf-8")
            except Exception:
                continue
            rel_path = fpath.relative_to(REPO_ROOT).as_posix()
            violations.extend(_check_text_compliance(rel_path, content))

    # 5. Audit hardcoded user-facing strings, DMs, errors, and YooKassa descriptions in handlers & services
    python_targets: list[Path] = []
    handlers_dir = REPO_ROOT / "bot" / "handlers"
    if handlers_dir.exists():
        python_targets.extend(sorted(handlers_dir.glob("*.py")))
    for rel in (
        "bot/miniapp/auth.py",
        "bot/miniapp/handlers_user.py",
        "bot/miniapp/handlers_admin.py",
        "bot/miniapp/admin_nodes_handlers.py",
        "bot/miniapp/admin_ops_handlers.py",
        "bot/miniapp/admin_user_adjust_handlers.py",
        "bot/miniapp/admin_users_handlers.py",
        "services/broadcast_service.py",
        "services/order_service.py",
        "services/admin_tools_service.py",
        "services/subscription.py",
        "services/white_internet_service.py",
        "services/fulfillment_service.py",
        "services/workers/traffic.py",
        "services/workers/notifications.py",
        "services/workers/credit_notifications.py",
    ):
        p = REPO_ROOT / rel
        if p.exists():
            python_targets.append(p)

    for fpath in python_targets:
        rel_path = fpath.relative_to(REPO_ROOT).as_posix()
        for lineno, text_val, is_desc in _extract_python_user_strings(fpath):
            violations.extend(
                _check_text_compliance(
                    f"{rel_path}:{lineno}",
                    text_val,
                    strict_checkout=is_desc and fpath.name == "order_service.py",
                    allow_identifier_only=True,
                )
            )

    return violations


def main() -> int:
    print("Running legal compliance stop-word audit (GEMINI.md Rule 5 & 10)...")
    violations = run_audit()
    if violations:
        print(f"\n[FAIL] Found {len(violations)} compliance violation(s):", file=sys.stderr)
        for v in violations:
            print(f"  • {v}", file=sys.stderr)
        return 1
    print("[OK] Legal compliance stop-word audit passed: 0 violations found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
