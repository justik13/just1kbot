import re

from bot import texts
from bot.texts_data.referral_texts import REFERRAL_TEXTS


FORBIDDEN_USER_WORDING = (
    "Что делать, если VPN не подключается или медленно работает?",
    "Что делать, если ВПН не подключается или медленно работает?",
    "Мы отвечаем в течение 24 часов.",
    "Все серверы работают на максимальной скорости.",
    "👤 Профиль",
    "👥 Реферальная система",
    "не фиксируем и не храним истории подключений",
    "Pro:</n до 10 устройств",
)


def _effective_user_text_values() -> list[str]:
    values: list[str] = []
    for name in dir(texts):
        if name.startswith("_"):
            continue
        value = getattr(texts, name)
        if isinstance(value, str):
            values.append(value)
    return values


def test_effective_user_texts_do_not_contain_known_legacy_wording():
    effective_text = "\n".join(_effective_user_text_values())
    for legacy in FORBIDDEN_USER_WORDING:
        assert legacy not in effective_text


def test_generic_vpn_wording_is_not_used_in_faq():
    faq = texts.FAQ_TEXT
    assert re.search(r"(?<![A-Za-zА-Яа-я])VPN(?![A-Za-zА-Яа-я])", faq, re.IGNORECASE) is None
    assert "ВПН" not in faq.upper()


def test_technical_connection_identifiers_are_preserved():
    faq = texts.FAQ_TEXT
    assert "AmneziaVPN" in faq
    assert "AmneziaWG" in faq
    assert "DefaultVPN" in faq
    assert "vpn://" in faq
    assert ".vpn" in faq
    assert ".conf" in faq


def test_referral_texts_do_not_keep_removed_profile_screens():
    assert "PROFILE_TEXT_ACTIVE_REFERRAL_BALANCE" not in REFERRAL_TEXTS
    assert "PROFILE_TEXT_INACTIVE_REFERRAL_BALANCE" not in REFERRAL_TEXTS


def test_faq_uses_dynamic_device_limit_wording():
    faq = texts.FAQ_TEXT
    assert "Количество доступных устройств зависит от выбранного тарифа." in faq
    assert "до 10 устройств" not in faq
