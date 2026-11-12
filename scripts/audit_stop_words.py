"""Standalone legal compliance audit script for user-facing texts and templates.

Enforces project rules (GEMINI.md Rule 5 and Rule 10) to protect YooKassa merchant
account from suspension due to forbidden vocabulary (VPN, bypass, whitelists, etc.).
"""

from __future__ import annotations

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
    "amnezia",
    "amneziawg",
    "amneziavpn",
    "defaultvpn",
)


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
        low = text_val.lower()
        if re.search(r"\bvpn\b", text_val, re.IGNORECASE):
            violations.append(f"[{key}] Contains forbidden English word 'VPN'")
        if "впн" in low:
            violations.append(f"[{key}] Contains forbidden Russian word 'ВПН'")
        for phrase in FORBIDDEN_USER_WORDING:
            if phrase.lower() in low:
                violations.append(f"[{key}] Contains forbidden wording: {phrase!r}")

        # Check raw protocols
        clean_no_placeholders = re.sub(r"\{[a-zA-Z0-9_]+\}", "", text_val).lower()
        for proto in ("vless", "wireguard", "amnezia", "amneziawg"):
            if proto in clean_no_placeholders:
                violations.append(f"[{key}] Exposes forbidden protocol name: {proto!r}")
        if re.search(r"\bawg\b", clean_no_placeholders):
            violations.append(f"[{key}] Exposes forbidden protocol name: 'awg'")

    # 2. Audit checkout descriptions (YooKassa strict compliance)
    from bot import texts

    descriptions = [
        ("CHECKOUT_DESCRIPTION_DEFAULT", texts.CHECKOUT_DESCRIPTION_DEFAULT),
    ]
    for desc_name, desc_val in descriptions:
        if re.search(r"(?<![A-Za-zА-Яа-я])VPN(?![A-Za-zА-Яа-я])", desc_val, re.IGNORECASE):
            violations.append(f"[{desc_name}] Checkout description contains forbidden 'VPN'")
        if "ВПН" in desc_val.upper():
            violations.append(f"[{desc_name}] Checkout description contains forbidden 'ВПН'")
        if "прокси" in desc_val.lower():
            violations.append(f"[{desc_name}] Checkout description contains forbidden 'прокси'")
        if "обход" in desc_val.lower():
            violations.append(f"[{desc_name}] Checkout description contains forbidden 'обход'")

    # 3. Audit HTML templates & MiniApp
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
                # Clean allowed URL-like scheme strings if any
                clean_content = content.replace(".vpn", "").replace("vpn://", "")
                rel_path = fpath.relative_to(REPO_ROOT).as_posix()
                if re.search(r"(?<![A-Za-zА-Яа-я])VPN(?![A-Za-zА-Яа-я])", clean_content):
                    violations.append(f"[{rel_path}] Contains forbidden 'VPN'")
                if "ВПН" in clean_content.upper():
                    violations.append(f"[{rel_path}] Contains forbidden 'ВПН'")
                if re.search(r"(?<![A-Za-z0-9])LTE(?![A-Za-z0-9])", clean_content, re.IGNORECASE):
                    violations.append(f"[{rel_path}] Contains forbidden 'LTE'")
                for phrase in FORBIDDEN_USER_WORDING:
                    if phrase == "LTE":
                        continue
                    if phrase.lower() in clean_content.lower():
                        violations.append(f"[{rel_path}] Contains forbidden phrase: {phrase!r}")

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
