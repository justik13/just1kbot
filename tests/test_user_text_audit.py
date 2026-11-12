import re
import unittest

from bot import texts

FORBIDDEN_USER_WORDING = (
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


def _effective_user_text_values() -> list[str]:
    from bot.texts import backend
    from bot.texts.runtime import notifications

    values: list[str] = []
    for mod in (backend, notifications):
        for name in dir(mod):
            if name.isupper() and not name.startswith("_") and not name.startswith("ADMIN_"):
                value = getattr(mod, name)
                if isinstance(value, str):
                    values.append(value)
    return values


class TestUserTextAudit(unittest.TestCase):
    def test_effective_user_texts_do_not_contain_known_legacy_or_restricted_wording(self):
        effective_text = "\n".join(_effective_user_text_values()).casefold()
        for forbidden in FORBIDDEN_USER_WORDING:
            assert forbidden.casefold() not in effective_text

    def test_generic_vpn_wording_is_not_used_in_user_facing_texts(self):
        effective_text = "\n".join(_effective_user_text_values())
        assert re.search(r"vpn", effective_text, re.IGNORECASE) is None
        assert "впн" not in effective_text.casefold()

    def test_protocol_terms_are_not_exposed_to_users(self):
        effective_text = "\n".join(_effective_user_text_values())
        clean_text = re.sub(r"\{[a-zA-Z0-9_]+\}", "", effective_text).lower()
        assert "vless" not in clean_text
        assert "wireguard" not in clean_text
        assert "amnezia" not in clean_text
        assert "amneziawg" not in clean_text
        assert re.search(r"\bawg\b", clean_text) is None

    def test_payment_and_receipt_descriptions_contain_no_vpn_wording(self):
        descriptions = [
            texts.CHECKOUT_DESCRIPTION_DEFAULT,
            getattr(texts, "CHECKOUT_DESCRIPTION_RENEW", texts.CHECKOUT_DESCRIPTION_DEFAULT),
            getattr(texts, "CHECKOUT_DESCRIPTION_TARIFF_CHANGE", texts.CHECKOUT_DESCRIPTION_DEFAULT),
        ]
        for desc in descriptions:
            assert re.search(r"(?<![A-Za-zА-Яа-я])VPN(?![A-Za-zА-Яа-я])", desc, re.IGNORECASE) is None
            assert "ВПН" not in desc.upper()
            assert "прокси" not in desc.lower()
            assert "обход" not in desc.lower()

    def test_html_templates_do_not_contain_forbidden_words(self):
        from pathlib import Path
        templates_dir = Path("bot/templates")
        if not templates_dir.exists():
            return
        for html_file in sorted(templates_dir.rglob("*.html")):
            content = html_file.read_text(encoding="utf-8")
            clean_text = content.replace(".vpn", "").replace("vpn://", "")
            assert re.search(r"(?<![A-Za-zА-Яа-я])VPN(?![A-Za-zА-Яа-я])", clean_text) is None, f"Forbidden 'VPN' found in {html_file}"
            assert "ВПН" not in content.upper(), f"Forbidden 'ВПН' found in {html_file}"
            assert re.search(r"(?<![A-Za-z0-9])LTE(?![A-Za-z0-9])", content, re.IGNORECASE) is None, f"Forbidden 'LTE' found in {html_file}"
            for forbidden in FORBIDDEN_USER_WORDING:
                if forbidden == "LTE":
                    continue
                assert forbidden.casefold() not in content.casefold(), f"Forbidden wording '{forbidden}' in {html_file}"
