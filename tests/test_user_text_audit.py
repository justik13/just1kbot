import re
import unittest

from bot import texts
from bot.texts_data.user.referral import TEXTS as REFERRAL_TEXTS

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
    "глушил",
    "белый список",
    "белые списки",
    "белых списков",
)


def _effective_user_text_values() -> list[str]:
    values: list[str] = []
    for name in texts.get_all_text_keys():
        value = getattr(texts, name)
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
        clean_text = effective_text.replace(".vpn", "").replace("vpn://", "")
        assert re.search(r"(?<![A-Za-zА-Яа-я])VPN(?![A-Za-zА-Яа-я])", clean_text) is None
        assert "ВПН" not in effective_text.upper()

    def test_technical_connection_identifiers_are_preserved(self):
        faq = texts.FAQ_TEXT
        assert "AmneziaVPN" in faq
        assert "AmneziaWG" in faq
        assert "DefaultVPN" in faq
        assert "vpn://" in faq
        assert ".vpn" in faq
        assert ".conf" in faq

    def test_referral_texts_do_not_keep_removed_profile_screens(self):
        assert "PROFILE_TEXT_ACTIVE_REFERRAL_BALANCE" not in REFERRAL_TEXTS
        assert "PROFILE_TEXT_INACTIVE_REFERRAL_BALANCE" not in REFERRAL_TEXTS

    def test_faq_uses_dynamic_device_limit_wording(self):
        faq = texts.FAQ_TEXT
        assert "Количество доступных устройств зависит от выбранного тарифа." in faq
        assert "до 10 устройств" not in faq

    def test_active_user_facing_claims_are_neutralized(self):
        assert "в один клик" not in texts.WELCOME_TEXT.lower()
        assert "24 часа" not in texts.SUPPORT_TEXT
        assert "максимальной скорости" not in texts.PAYMENT_SHOWCASE_HEADER
        assert "Мы постараемся помочь как можно скорее." in texts.SUPPORT_TEXT

    def test_payment_and_receipt_descriptions_contain_no_vpn_wording(self):
        from services.account_topup import get_topup_description

        contexts = [
            {},
            {"auto_fulfill_action": "purchase", "operation": "renew"},
            {"auto_fulfill_action": "tariff_change"},
            {"auto_fulfill_action": "purchase", "operation": "new"},
        ]
        for ctx in contexts:
            desc = get_topup_description(ctx)
            assert re.search(r"(?<![A-Za-zА-Яа-я])VPN(?![A-Za-zА-Яа-я])", desc, re.IGNORECASE) is None
            assert "ВПН" not in desc.upper()
            assert "прокси" not in desc.lower()
            assert "обход" not in desc.lower()
