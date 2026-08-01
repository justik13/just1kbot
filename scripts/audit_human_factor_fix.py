#!/usr/bin/env python3
"""Temporary deterministic codemod for PR #50.

Every replacement is count-checked so an unexpected source change fails closed.
The file is removed before the pull request leaves draft state.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MARKER = ROOT / "tests/test_action_lock_validation.py"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def replace_exact(path: str, old: str, new: str, *, expected: int = 1) -> None:
    content = read(path)
    count = content.count(old)
    if count != expected:
        raise RuntimeError(
            f"{path}: expected {expected} occurrence(s), found {count}: {old!r}"
        )
    write(path, content.replace(old, new))


def replace_regex_in_trees(
    roots: tuple[str, ...], pattern: re.Pattern[str], replacement: str, expected: int
) -> None:
    changed = 0
    for root in roots:
        for path in sorted((ROOT / root).rglob("*.py")):
            content = path.read_text(encoding="utf-8")
            updated, count = pattern.subn(replacement, content)
            if count:
                path.write_text(updated, encoding="utf-8")
                changed += count
    if changed != expected:
        raise RuntimeError(
            f"pattern {pattern.pattern!r}: expected {expected} replacements, got {changed}"
        )


def fix_sqlalchemy_boolean_comparisons() -> None:
    trees = ("bot", "database", "services")
    replace_regex_in_trees(
        trees,
        re.compile(r"\b([A-Z][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*) == (True|False)"),
        r"\1.is_(\2)",
        35,
    )
    replace_regex_in_trees(
        trees,
        re.compile(r"\b([A-Z][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*) != None"),
        r"\1.is_not(None)",
        2,
    )


def fix_callback_regex_boundaries() -> None:
    path = "bot/middlewares/action_lock.py"
    content = read(path)
    count = content.count("\x08")
    if count != 8:
        raise RuntimeError(f"{path}: expected 8 backspace characters, found {count}")
    write(path, content.replace("\x08", r"\b"))


def fix_unused_results() -> None:
    replace_exact(
        "bot/handlers/connection/device_create_routes.py",
        "            profile = await DeviceService.create_device(\n",
        "            await DeviceService.create_device(\n",
    )
    replace_exact(
        "services/payment_service/service.py",
        "        operation = await enqueue_create(\n",
        "        await enqueue_create(\n",
    )
    replace_exact(
        "services/tariff_value_calculator.py",
        "            hourly_rate = target_price / Decimal(target_tariff.duration_hours)\n",
        "",
    )
    replace_exact(
        "tests/test_amnezia_typed_results.py",
        "                session = self.use_session(FakeResponse(status))\n",
        "                self.use_session(FakeResponse(status))\n",
    )
    replace_exact(
        "tests/test_amnezia_typed_results.py",
        "            session = self.use_session(FakeResponse(status))\n",
        "            self.use_session(FakeResponse(status))\n",
    )
    replace_exact(
        "tests/test_api_operations_queue.py",
        '        last = await self.enqueue("last-attempt", max_attempts=1)\n',
        '        await self.enqueue("last-attempt", max_attempts=1)\n',
    )
    replace_exact(
        "tests/test_payment_pipeline_boundaries.py",
        '        allowed = ROOT / "services/payment_fulfillment.py"\n',
        "",
    )
    replace_exact(
        "tests/test_tariff_change_quote_postgres.py",
        "            first = await create_tariff_change_quote(\n"
        "                session, user_id=user, target_tariff_id=target, as_of=as_of\n"
        "            )\n"
        "            different = (\n",
        "            first = await create_tariff_change_quote(\n"
        "                session, user_id=user, target_tariff_id=target, as_of=as_of\n"
        "            )\n"
        "            self.assertTrue(first.created)\n"
        "            different = (\n",
    )
    replace_exact(
        "tests/test_tariff_change_quote_postgres.py",
        "            change = await create_tariff_change_quote(\n"
        "                session, user_id=user, target_tariff_id=target, as_of=as_of\n"
        "            )\n"
        "            before_payments = await session.scalar(select(func.count(Payment.id)))\n",
        "            change = await create_tariff_change_quote(\n"
        "                session, user_id=user, target_tariff_id=target, as_of=as_of\n"
        "            )\n"
        "            self.assertTrue(change.created)\n"
        "            before_payments = await session.scalar(select(func.count(Payment.id)))\n",
    )


def fix_exception_assertions() -> None:
    replace_exact(
        "tests/test_payment_pipeline_postgres.py",
        "        with self.assertRaises(Exception):\n",
        "        with self.assertRaises(RuntimeError):\n",
    )
    replace_exact(
        "tests/test_subscription_balance_postgres.py",
        "from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine\n",
        "from sqlalchemy.exc import DBAPIError\n"
        "from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine\n",
    )
    replace_exact(
        "tests/test_subscription_balance_postgres.py",
        "        with self.assertRaises(Exception):\n",
        "        with self.assertRaises(DBAPIError):\n",
        expected=3,
    )


def fix_named_helpers_and_variables() -> None:
    replace_exact(
        "services/subscription_balance_projector.py",
        "    fail = lambda code, end=None: _failed(as_of, code, events, ledger, end)\n",
        "    def fail(code, end=None):\n"
        "        return _failed(as_of, code, events, ledger, end)\n",
    )
    replace_exact(
        "tests/test_payment_pipeline_postgres.py",
        "            for index, (status, next_at, locked_at) in enumerate(\n"
        "                (\n"
        "                    (\"pending\", now + timedelta(hours=1), None),\n"
        "                    (\"retry\", now - timedelta(minutes=2), None),\n"
        "                    (\"processing\", now, now - timedelta(minutes=3)),\n"
        "                    (\"dead\", now, None),\n"
        "                )\n"
        "            ):\n",
        "            for status, next_at, locked_at in (\n"
        "                (\"pending\", now + timedelta(hours=1), None),\n"
        "                (\"retry\", now - timedelta(minutes=2), None),\n"
        "                (\"processing\", now, now - timedelta(minutes=3)),\n"
        "                (\"dead\", now, None),\n"
        "            ):\n",
    )
    replace_exact(
        "tests/test_subscription_balance_projector.py",
        '        l = LedgerEntry(11, 1, "confirmed_payment", 24, Decimal(24), "RUB", 100, 10)\n',
        '        ledger_entry = LedgerEntry(\n'
        '            11, 1, "confirmed_payment", 24, Decimal(24), "RUB", 100, 10\n'
        '        )\n',
    )
    replace_exact(
        "tests/test_subscription_balance_projector.py",
        "            ledger_entries=(l,),\n",
        "            ledger_entries=(ledger_entry,),\n",
    )
    replace_exact(
        "utils/text_limits.py",
        "            if len(line) > limit:\n"
        "                while len(line) > limit:\n"
        "                    parts.append(line[:limit])\n"
        "                    line = line[limit:]\n\n"
        "                current = line\n",
        "            if len(line) > limit:\n"
        "                remaining_line = line\n"
        "                while len(remaining_line) > limit:\n"
        "                    parts.append(remaining_line[:limit])\n"
        "                    remaining_line = remaining_line[limit:]\n\n"
        "                current = remaining_line\n",
    )


def callback_regression_test() -> str:
    return '''import unittest\n\nfrom bot.middlewares.action_lock import _validate_callback_params\n\n\nclass CallbackValidationTests(unittest.TestCase):\n    def test_sql_word_boundaries_reject_injection_fragments(self):\n        for callback_data in (\n            "device:1 OR 1=1",\n            "device:1 AND 1=1",\n            "device:1 UNION SELECT",\n            "device:1 SELECT value",\n        ):\n            with self.subTest(callback_data=callback_data):\n                self.assertFalse(_validate_callback_params(callback_data))\n\n    def test_normal_callback_remains_valid(self):\n        self.assertTrue(_validate_callback_params("select_server:123"))\n\n\nif __name__ == "__main__":\n    unittest.main()\n'''


def ensure_callback_regression_test() -> None:
    expected = callback_regression_test()
    if MARKER.exists():
        current = MARKER.read_text(encoding="utf-8")
        if current != expected:
            raise RuntimeError(f"{MARKER}: unexpected existing content")
        return
    MARKER.write_text(expected, encoding="utf-8")


def verify_applied() -> None:
    if "\x08" in read("bot/middlewares/action_lock.py"):
        raise RuntimeError("callback regex still contains backspace characters")
    if MARKER.read_text(encoding="utf-8") != callback_regression_test():
        raise RuntimeError("callback regression marker differs from expected content")
    for root in ("bot", "database", "services"):
        for path in (ROOT / root).rglob("*.py"):
            content = path.read_text(encoding="utf-8")
            if re.search(
                r"\b[A-Z][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]* == (True|False)",
                content,
            ):
                raise RuntimeError(f"{path}: unfixed SQLAlchemy boolean comparison")


def main() -> None:
    if MARKER.exists():
        verify_applied()
        return
    fix_sqlalchemy_boolean_comparisons()
    fix_callback_regex_boundaries()
    fix_unused_results()
    fix_exception_assertions()
    fix_named_helpers_and_variables()
    ensure_callback_regression_test()


if __name__ == "__main__":
    main()
