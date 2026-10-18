"""Unit tests for Telegram Braille spacer utilities and keyboard button stabilization."""

import unittest

from bot.keyboards.common import get_hub_keyboard
from utils.telegram import BRAILLE_BLANK, equalize_buttons_braille, pad_braille


class TestBrailleSpacer(unittest.TestCase):
    """Test suite for Braille blank character padding and keyboard equalization."""

    def test_braille_blank_constant(self):
        """Verify BRAILLE_BLANK is the Unicode U+2800 character."""
        self.assertEqual(BRAILLE_BLANK, "\u2800")
        self.assertEqual(ord(BRAILLE_BLANK), 0x2800)

    def test_pad_braille_basic(self):
        """Verify pad_braille adds correct number of Braille blank characters."""
        text = "Test"
        target_len = 10
        padded = pad_braille(text, target_len, align="left")
        self.assertEqual(len(padded), target_len)
        self.assertTrue(padded.startswith(text))
        self.assertTrue(padded.endswith(BRAILLE_BLANK * (target_len - len(text))))

    def test_pad_braille_alignment(self):
        """Verify right and center alignment with Braille blank characters."""
        text = "Hi"
        target_len = 6

        padded_right = pad_braille(text, target_len, align="right")
        self.assertEqual(len(padded_right), 6)
        self.assertTrue(padded_right.startswith(BRAILLE_BLANK * 4))
        self.assertTrue(padded_right.endswith(text))

        padded_center = pad_braille(text, target_len, align="center")
        self.assertEqual(len(padded_center), 6)
        self.assertEqual(padded_center, f"{BRAILLE_BLANK * 2}{text}{BRAILLE_BLANK * 2}")

    def test_pad_braille_no_op_when_text_longer_or_equal(self):
        """Verify pad_braille returns original text when length >= target."""
        text = "Hello World"
        self.assertEqual(pad_braille(text, 5), text)
        self.assertEqual(pad_braille(text, len(text)), text)

    def test_equalize_buttons_braille(self):
        """Verify equalize_buttons_braille equalizes string lengths with Braille characters."""
        b1 = "Short"
        b2 = "Much Longer Button Title"

        e1, e2 = equalize_buttons_braille(b1, b2)
        self.assertEqual(len(e1), len(e2))
        self.assertEqual(len(e2), len(b2))
        self.assertIn(BRAILLE_BLANK, e1)

    def test_get_hub_keyboard_uses_braille_equalization(self):
        """Verify get_hub_keyboard generates stabilized buttons using Braille characters."""
        kb = get_hub_keyboard(is_active=True)
        self.assertIsNotNone(kb)
        self.assertTrue(len(kb.inline_keyboard) > 0)

        # Check that inline keyboard contains buttons and equalized rows
        row1 = kb.inline_keyboard[1]
        self.assertEqual(len(row1), 2)
        btn_left, btn_right = row1[0], row1[1]
        self.assertEqual(len(btn_left.text), len(btn_right.text))
