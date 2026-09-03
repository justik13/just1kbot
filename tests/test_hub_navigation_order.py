import unittest

from bot.keyboards.common import get_hub_keyboard


def callback_buttons(markup):
    return [
        button
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


class HubNavigationOrderTests(unittest.TestCase):
    def test_admin_action_is_last_and_white_internet_is_immediately_before_it(self):
        buttons = callback_buttons(
            get_hub_keyboard(is_admin=True, is_active=True)
        )
        callbacks = [button.callback_data for button in buttons]
        self.assertEqual(callbacks[-2:], ["white_internet", "menu_admin"])

    def test_white_internet_is_included_for_non_admin(self):
        buttons = callback_buttons(
            get_hub_keyboard(is_admin=False, is_active=True)
        )
        callbacks = [button.callback_data for button in buttons]
        self.assertIn("white_internet", callbacks)
        self.assertEqual(callbacks[-1], "white_internet")
