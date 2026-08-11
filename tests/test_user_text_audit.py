import re
import unittest

from bot import texts


class TestUserTextAudit(unittest.TestCase):
    def test_effective_faq_has_no_vpn_term_in_user_facing_wording(self):
        self.assertNotIn(
            "Что делать, если VPN не подключается или медленно работает?",
            texts.FAQ_TEXT,
        )
        self.assertNotIn(
            "Что делать, если ВПН не подключается или медленно работает?",
            texts.FAQ_TEXT,
        )
        self.assertIn(
            "Что делать, если подключение не работает или работает медленно?",
            texts.FAQ_TEXT,
        )

    def test_technical_connection_identifiers_are_preserved(self):
        self.assertIn("AmneziaVPN", texts.FAQ_TEXT)
        self.assertIn("vpn://", texts.FAQ_TEXT)
        self.assertIn(".vpn", texts.FAQ_TEXT)
        self.assertIn(".conf", texts.FAQ_TEXT)

    def test_no_standalone_vpn_term_in_effective_text_catalog(self):
        standalone_vpn = re.compile(r"(?<![A-Za-zА-Яа-я])ВПН(?![A-Za-zА-Яа-я])|(?<![A-Za-zА-Яа-я])VPN(?![A-Za-zА-Яа-я])")
        technical_allowed = {"DEVICE_CONFIG_VPN_CAPTION", "DOWNLOAD_CONF_FALLBACK"}

        for key in texts.get_all_text_keys():
            value = texts.get_text(key)
            if not isinstance(value, str) or key in technical_allowed:
                continue
            self.assertIsNone(
                standalone_vpn.search(value),
                msg=f"Standalone VPN term remains in user-facing text {key!r}",
            )

    def test_text_loader_still_exposes_core_navigation_texts(self):
        self.assertIn("Главное меню", texts.HUB_HEADER)
        self.assertIn("Подключения", texts.FAQ_TEXT)
        self.assertIn("Пригласить друга", texts.FAQ_TEXT)
        self.assertNotIn("👤 Профиль", texts.FAQ_TEXT)


if __name__ == "__main__":
    unittest.main()
