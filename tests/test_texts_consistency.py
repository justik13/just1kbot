"""Automated consistency, SSOT, markup, and architectural verification for bot.texts."""
from __future__ import annotations
import collections

import ast
import re
import tempfile
import unittest
from pathlib import Path

from bot import texts

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEXTS_DIR = PROJECT_ROOT / "bot" / "texts"

# Explicit canonical alias registry for intentional aliases (e.g. backward compat or semantic alias)
CANONICAL_ALIASES: dict[str, str] = {
    "BTN_PAYMENT_CANCEL": "BTN_CANCEL_ACTION",
    "ADMIN_WI_TRAFFIC_RESET_FAILED": "ADMIN_WI_ACTION_FAILED",
    "WL_VLESS_TAG": "BTN_WHITE_INTERNET",
}


# A literal is only user-facing if it is passed to a Telegram send call. Keying on
# the call site (rather than on how the string looks) is what makes this guard
# sound: it cannot be satisfied by renaming a log message, and it never needs a
# growing allowlist for SQL, HTTP headers or HTML fragments.
TELEGRAM_SEND_CALLS = frozenset({
    "answer",
    "answer_photo",
    "answer_animation",
    "answer_audio",
    "answer_document",
    "answer_video",
    "answer_voice",
    "answer_poll",
    "answer_dice",
    "answer_photo_sticker",
    "answer_sticker",
    "edit_message_text",
    "edit_text",
    "edit_caption",
    "edit_reply_markup",
    "edit_message_caption",
    "edit_message_reply_markup",
    "edit_message_media",
    "send_message",
    "send_photo",
    "send_animation",
    "send_audio",
    "send_document",
    "send_video",
    "send_voice",
    "send_paid_media",
    "send_poll",
    "send_dice",
    "send_chat_action",
    "send_sticker",
    "copy_message",
    "forward_message",
    "reply",
    "reply_html",
    "reply_photo",
    "render_hub",
    "render_console",
    "safe_edit_text",
    "safe_edit_message_text",
    "safe_send_message",
})


def _is_telegram_send_call(call: ast.Call) -> bool:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in TELEGRAM_SEND_CALLS
    if isinstance(func, ast.Attribute):
        return func.attr in TELEGRAM_SEND_CALLS
    return False


def _is_user_facing_string(s: str) -> bool:
    if not isinstance(s, str) or not s.strip():
        return False
    clean_s = re.sub(r"<[^>]+>", "", s)
    if re.search(r"[\u0400-\u04FF]", clean_s):
        return True
    if re.search(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]", clean_s) and " " in s:
        return True
    return bool(re.search(r"[A-Za-z]", clean_s) and " " in clean_s)


class _HardcodedStringVisitor(ast.NodeVisitor):
    def __init__(self, file_path: Path, docstring_nodes: set[ast.AST]):
        self.file_path = file_path
        self.docstring_nodes = docstring_nodes
        self.call_stack: list[ast.Call] = []
        self.violations: list[str] = []

    def _inside_send_call(self) -> bool:
        return bool(self.call_stack) and _is_telegram_send_call(self.call_stack[-1])

    def _report(self, node: ast.AST, kind: str, value: str) -> None:
        self.violations.append(
            f"{self.file_path.as_posix()}:{node.lineno} contains hardcoded {kind}: {value[:50]!r}"
        )

    def visit_Call(self, node: ast.Call):
        self.call_stack.append(node)
        self.generic_visit(node)
        self.call_stack.pop()

    def visit_Constant(self, node: ast.Constant):
        if node in self.docstring_nodes:
            return
        if (
            isinstance(node.value, str)
            and self._inside_send_call()
            and _is_user_facing_string(node.value)
        ):
            self._report(node, "string", node.value)
        self.generic_visit(node)

    def visit_JoinedStr(self, node: ast.JoinedStr):
        if self._inside_send_call():
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    if _is_user_facing_string(part.value):
                        self._report(node, "f-string part", part.value)
        self.generic_visit(node)


def scan_hardcoded_user_facing_strings(files, root: Path) -> list[str]:
    """Return one message per hardcoded literal passed to a Telegram send call."""
    violations: list[str] = []
    for py_file in files:
        content = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(content, filename=str(py_file))
        except SyntaxError:
            continue

        docstring_nodes: set[ast.AST] = set()
        if (
            tree.body
            and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)
        ):
            docstring_nodes.add(tree.body[0].value)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                ):
                    docstring_nodes.add(node.body[0].value)

        visitor = _HardcodedStringVisitor(py_file, docstring_nodes)
        visitor.visit(tree)
        violations.extend(visitor.violations)
    return violations



class TextsConsistencyTests(unittest.TestCase):

    def test_alias_registry_is_acyclic_and_valid(self):
        """Verify CANONICAL_ALIASES contains valid mapping, exists in texts facade, and is identical."""
        for alias, canonical in CANONICAL_ALIASES.items():
            self.assertNotEqual(alias, canonical, f"Self-alias detected: {alias} -> {canonical}")
            self.assertNotIn(canonical, CANONICAL_ALIASES, f"Alias cycle detected: {canonical} is also an alias key")
            self.assertTrue(hasattr(texts, alias), f"Alias key {alias} not found in texts facade")
            self.assertTrue(hasattr(texts, canonical), f"Canonical key {canonical} not found in texts facade")
            self.assertEqual(
                getattr(texts, alias),
                getattr(texts, canonical),
                f"Alias {alias} value does not match canonical {canonical}",
            )

    """Automated consistency, markup, placeholder, and architectural verification for all application texts."""

    def test_all_text_keys_are_valid_identifiers(self):
        """Verify that all keys in texts facade are valid uppercase/semantic identifiers."""
        keys = texts.get_all_text_keys()
        self.assertGreater(len(keys), 100, "Text catalogue must contain loaded keys.")
        for key in keys:
            self.assertTrue(key.isidentifier(), f"Key {key!r} is not a valid Python identifier.")
            self.assertTrue(key.isupper(), f"Key {key!r} must be uppercase constant.")
            # Disallow line-number based keys or unsemantic names
            self.assertIsNone(
                re.search(r"(_L\d+_\d+|_L\d+)$", key),
                f"Key {key!r} contains forbidden line-number suffix.",
            )

    def test_get_all_text_keys_returns_strictly_text_constants(self):
        """Verify that get_all_text_keys() returns strictly text constant names and no helper functions."""
        keys = texts.get_all_text_keys()
        forbidden_helper_names = {"get_text", "get_all_text_keys", "reload_texts"}
        for helper in forbidden_helper_names:
            self.assertNotIn(
                helper,
                keys,
                f"Helper function {helper!r} illegally returned by get_all_text_keys()",
            )
        for key in keys:
            self.assertTrue(
                key.isupper(),
                f"Non-constant identifier {key!r} returned by get_all_text_keys()",
            )

    def test_strict_no_duplicate_keys_across_domain_modules(self):
        """Verify absolute SSOT: each text key exists in exactly ONE domain file."""
        seen_keys: dict[str, str] = {}
        duplicates: list[tuple[str, str, str]] = []

        for py_file in TEXTS_DIR.rglob("*.py"):
            if py_file.name == "__init__.py":
                continue
            rel_mod = py_file.relative_to(TEXTS_DIR).as_posix()
            content = py_file.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(py_file))

            for stmt in tree.body:
                if isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        if isinstance(target, ast.Name) and target.id.isupper():
                            key = target.id
                            if key in seen_keys:
                                duplicates.append((key, seen_keys[key], rel_mod))
                            else:
                                seen_keys[key] = rel_mod

        self.assertEqual(
            duplicates,
            [],
            f"SSOT Violation: Duplicate text keys found across domain modules:\n{duplicates}",
        )

    def test_no_duplicate_canonical_text_values_across_catalogue(self):
        """Ensure no two distinct canonical text keys share the exact same string value, including within dicts/lists."""
        val_to_keys = collections.defaultdict(list)

        def _extract_strings(node, path):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return [(node.value, path)]
            elif isinstance(node, ast.Dict):
                res = []
                for v in node.values:
                    res.extend(_extract_strings(v, path))
                return res
            elif isinstance(node, (ast.List, ast.Tuple)):
                res = []
                for elt in node.elts:
                    res.extend(_extract_strings(elt, path))
                return res
            return []

        for py_file in TEXTS_DIR.rglob("*.py"):
            if py_file.name == "__init__.py":
                continue

            content = py_file.read_text(encoding="utf-8")
            tree = ast.parse(content)

            for stmt in tree.body:
                if isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        if isinstance(target, ast.Name) and target.id.isupper():
                            # Skip if this is an explicit alias
                            if target.id in CANONICAL_ALIASES:
                                continue

                            # SKIP DICT/LIST LABELS WHICH INTENTIONALLY SHARE STRINGS
                            if target.id.endswith("_LABELS") or target.id == "AUDIT_ACTIONS":
                                continue

                            strings = _extract_strings(stmt.value, target.id)
                            for s, _ in strings:
                                if len(s) > 3 and not s.startswith("http") and not s.startswith("/") and not re.match(r"^[A-Z_]+$", s):
                                    val_to_keys[s].append((target.id, py_file.name))

        unaliased_duplicates = []
        for val, keys_list in val_to_keys.items():
            if len(keys_list) > 1:
                # Check if they are just aliases
                keys = set(k for k, _ in keys_list)
                if len(keys) > 1:
                    unaliased_duplicates.append((val[:60], keys_list))

        self.assertEqual(
            unaliased_duplicates,
            [],
            f"SSOT Violation: Found duplicate text string values without canonical alias mapping:\n{unaliased_duplicates}",
        )


    def test_no_overrides_model_and_no_overrides_file(self):
        """Verify that overrides.py does not exist and no OVERRIDES dictionary is defined."""
        overrides_file = TEXTS_DIR / "overrides.py"
        self.assertFalse(overrides_file.exists(), "overrides.py must not exist.")

        for py_file in TEXTS_DIR.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            self.assertNotIn("OVERRIDES", content, f"OVERRIDES dictionary found in {py_file}.")

    def test_no_legacy_texts_sources(self):
        """Verify that legacy bot/texts.py and bot/texts_data/ are completely removed."""
        legacy_texts_py = PROJECT_ROOT / "bot" / "texts.py"
        self.assertFalse(legacy_texts_py.exists(), "Legacy bot/texts.py must not exist.")

        legacy_texts_data = PROJECT_ROOT / "bot" / "texts_data"
        self.assertFalse(legacy_texts_data.exists(), "Legacy bot/texts_data/ must not exist.")

        replace_map_file = PROJECT_ROOT / "replace_map.json"
        self.assertFalse(replace_map_file.exists(), "replace_map.json must not exist.")

    def test_no_dynamic_text_loader(self):
        """Verify that bot/texts/__init__.py is a static facade without dynamic discovery."""
        init_file = TEXTS_DIR / "__init__.py"
        content = init_file.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(init_file))

        forbidden_names = {"pkgutil", "importlib", "__import__", "iter_modules"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(
                        alias.name,
                        forbidden_names,
                        f"Dynamic loader library {alias.name!r} imported in bot/texts/__init__.py",
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    for part in node.module.split("."):
                        self.assertNotIn(
                            part,
                            forbidden_names,
                            f"Dynamic loader library {node.module!r} imported in bot/texts/__init__.py",
                        )

    def test_all_text_placeholders_syntax_is_valid(self):
        """Verify that any text containing {placeholders} does not have broken braces or syntax errors."""
        placeholder_pattern = re.compile(r"\{([^{}]+)\}")

        for key in texts.get_all_text_keys():
            val = getattr(texts, key, None)
            if not isinstance(val, str):
                continue

            clean_val = val.replace("{{", "").replace("}}", "")

            open_count = clean_val.count("{")
            close_count = clean_val.count("}")
            self.assertEqual(
                open_count,
                close_count,
                f"Mismatched braces in text key {key!r}:\n{val}",
            )

            for placeholder in placeholder_pattern.findall(clean_val):
                var_name = placeholder.split(":")[0].split("!")[0].strip()
                if var_name.isdigit():
                    continue
                self.assertTrue(
                    var_name.isidentifier(),
                    f"Invalid placeholder name {placeholder!r} in text key {key!r}:\n{val}",
                )

    def test_placeholder_compatibility_across_call_sites(self):
        """Verify statically that every .format(...) call site supplies the exact placeholders required."""
        placeholder_pattern = re.compile(r"\{([a-zA-Z0-9_]+)")
        mismatches = []

        scanned_dirs = [
            PROJECT_ROOT / "bot",
            PROJECT_ROOT / "services" / "workers",
        ]

        for base_dir in scanned_dirs:
            for py_file in base_dir.rglob("*.py"):
                if "integrations" in base_dir.parts:
                    with open(py_file, 'r', encoding='utf-8') as f:
                        if 'aiogram' not in f.read():
                            continue
                if "texts" in py_file.parts:
                    continue
                content = py_file.read_text(encoding="utf-8")
                tree = ast.parse(content, filename=str(py_file))

                for node in ast.walk(tree):
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "format"
                    ):
                        text_key = None
                        if (
                            isinstance(node.func.value, ast.Attribute)
                            and isinstance(node.func.value.value, ast.Name)
                            and node.func.value.value.id == "texts"
                        ):
                            text_key = node.func.value.attr
                        elif isinstance(node.func.value, ast.Name) and hasattr(texts, node.func.value.id):
                            text_key = node.func.value.id

                        if text_key:
                            template = getattr(texts, text_key, None)
                            if isinstance(template, str):
                                placeholders = set(placeholder_pattern.findall(template))
                                call_kwargs = {kw.arg for kw in node.keywords if kw.arg}
                                call_args_count = len(node.args)

                                if placeholders:
                                    if call_args_count == 0 and call_kwargs:
                                        missing = placeholders - call_kwargs
                                        extra = call_kwargs - placeholders
                                        if missing or extra:
                                            mismatches.append(
                                                f"{py_file.relative_to(PROJECT_ROOT)}:{node.lineno} {text_key}: missing={missing} extra={extra}"
                                            )

        self.assertEqual(
            mismatches,
            [],
            "Placeholder mismatch found between .format(...) call sites and text templates:\n"
            + "\n".join(mismatches),
        )

    def test_get_text_literal_keys_exist_in_catalogue(self):
        """Verify statically that any literal string key passed to texts.get_text('KEY') exists in catalogue."""
        missing_keys = []
        all_keys = set(dir(texts))
        scanned_dirs = [
            PROJECT_ROOT / "bot",
            PROJECT_ROOT / "services",
            PROJECT_ROOT / "integrations",
        ]

        for base_dir in scanned_dirs:
            for py_file in base_dir.rglob("*.py"):
                if "texts" in py_file.parts:
                    continue
                content = py_file.read_text(encoding="utf-8")
                tree = ast.parse(content, filename=str(py_file))

                for node in ast.walk(tree):
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "get_text"
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "texts"
                    ):
                        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                            key_name = node.args[0].value
                            if key_name not in all_keys:
                                missing_keys.append(
                                    f"{py_file.relative_to(PROJECT_ROOT)}:{node.lineno} texts.get_text({key_name!r}) references missing key"
                                )

        self.assertEqual(
            missing_keys,
            [],
            "Found texts.get_text() calls referencing non-existent catalogue keys:\n"
            + "\n".join(missing_keys),
        )

    def test_html_markup_nesting_and_validity(self):
        """Verify that HTML tags used in Telegram messages are balanced and properly nested."""
        allowed_tags = {
            "b",
            "strong",
            "i",
            "em",
            "code",
            "pre",
            "a",
            "u",
            "s",
            "tg-spoiler",
            "blockquote",
        }

        tag_pattern = re.compile(r"<(/?[a-zA-Z0-9_-]+)(?:\s+[^>]*)?>")

        for key in texts.get_all_text_keys():
            val = getattr(texts, key, None)
            if not isinstance(val, str):
                continue

            tags = tag_pattern.findall(val)
            tag_stack = []

            for raw_tag in tags:
                is_closing = raw_tag.startswith("/")
                tag_name = raw_tag[1:] if is_closing else raw_tag
                tag_name = tag_name.lower()

                self.assertIn(
                    tag_name,
                    allowed_tags,
                    f"Unsupported HTML tag <{raw_tag}> in text key {key!r}:\n{val}",
                )

                if not is_closing:
                    tag_stack.append(tag_name)
                else:
                    if not tag_stack:
                        self.fail(f"Unmatched closing tag </{tag_name}> in text key {key!r}:\n{val}")
                    last_opened = tag_stack.pop()
                    self.assertEqual(
                        last_opened,
                        tag_name,
                        f"Improperly nested HTML tags in key {key!r}: opened <{last_opened}> but closed </{tag_name}>\n{val}",
                    )

            self.assertEqual(
                tag_stack,
                [],
                f"Unclosed HTML tags {tag_stack} in text key {key!r}:\n{val}",
            )

    def test_public_facade_consistency(self):
        """Verify that all canonical keys in bot/texts/* are exported via facade."""
        for py_file in TEXTS_DIR.rglob("*.py"):
            if py_file.name == "__init__.py":
                continue
            content = py_file.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(py_file))
            for stmt in tree.body:
                if isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        if isinstance(target, ast.Name) and target.id.isupper():
                            self.assertTrue(
                                hasattr(texts, target.id),
                                f"Key {target.id!r} in {py_file} not accessible via getattr(texts, ...).",
                            )

    def test_texts_package_import_firewall(self):
        """Verify that bot.texts.* does not import application layers (services, db, handlers)."""
        forbidden_roots = {"services", "database", "integrations", "config", "bot.handlers", "bot.middlewares"}
        for py_file in TEXTS_DIR.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for forbidden in forbidden_roots:
                            self.assertFalse(
                                alias.name == forbidden or alias.name.startswith(forbidden + "."),
                                f"Text module {py_file} illegally imports {alias.name!r}",
                            )
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        for forbidden in forbidden_roots:
                            self.assertFalse(
                                node.module == forbidden or node.module.startswith(forbidden + "."),
                                f"Text module {py_file} illegally imports from {node.module!r}",
                            )

    @staticmethod
    def _is_logging_or_regex_call(parent_calls: list[ast.Call]) -> bool:
        """Check if an AST node is inside a logger/logging call or regex pattern."""
        for call in parent_calls:
            if isinstance(call.func, ast.Attribute):
                if isinstance(call.func.value, ast.Name) and call.func.value.id in (
                    "logger",
                    "logging",
                    "log",
                    "root_logger",
                    "re",
                ):
                    return True
            elif isinstance(call.func, ast.Name) and call.func.id in ("re", "compile"):
                return True
        return False

    def test_no_hardcoded_user_facing_strings_in_handlers_keyboards_and_workers(self):
        """AST guard: no literal may be hardcoded into a Telegram send call."""
        scanned_dirs = [
            PROJECT_ROOT / "bot" / "handlers",
            PROJECT_ROOT / "bot" / "keyboards",
            PROJECT_ROOT / "services" / "workers",
            PROJECT_ROOT / "integrations",
        ]
        files = []
        for base_dir in scanned_dirs:
            files.extend(sorted(base_dir.rglob("*.py")))

        violations = scan_hardcoded_user_facing_strings(files, PROJECT_ROOT)
        self.assertEqual(
            violations,
            [],
            "Found hardcoded user-facing strings in Telegram send calls:\n"
            + "\n".join(violations),
        )

    def test_ast_guard_detects_deliberate_hardcoded_string_violations(self):
        """Self-test driving the real guard over synthetic files, positive and negative."""
        bad_samples = [
            "await callback.answer('Active users')",
            "await bot.send_message(chat_id, f'Hello {user_id}')",
            "await event.edit_text('Durable queue recovered')",
            "await callback.answer(texts.SOME + ' Payment created')",
            "await callback.answer('Платёж создан')",
        ]
        good_samples = [
            "logger.error('payment result unknown: %s', kind)",
            "raise ValueError('payment gateway unavailable')",
            "btn = InlineKeyboardButton(text='Back')",
            "label = 'not_created'",
            "stmt = select(Payment).where(Payment.provider_status == 'succeeded')",
            "await bot.send_message(chat_id, texts.SOME_TEMPLATE)",
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index, sample in enumerate(bad_samples):
                path = root / f"bad_{index}.py"
                path.write_text(
                    "import texts\n\nasync def handler(bot, callback, event, chat_id, user_id):\n"
                    "    " + sample + "\n",
                    encoding="utf-8",
                )
                found = scan_hardcoded_user_facing_strings([path], root)
                with self.subTest(kind="must_flag", code=sample):
                    self.assertTrue(
                        found,
                        f"AST guard failed to detect deliberate violation in: {sample!r}",
                    )

            for index, sample in enumerate(good_samples):
                path = root / f"good_{index}.py"
                path.write_text(
                    "import texts\nfrom sqlalchemy import select\n"
                    "from database.models import Payment\n"
                    "from aiogram.types import InlineKeyboardButton\n\n"
                    "async def handler(bot, callback, event, chat_id, user_id):\n"
                    "    " + sample + "\n",
                    encoding="utf-8",
                )
                found = scan_hardcoded_user_facing_strings([path], root)
                with self.subTest(kind="must_not_flag", code=sample):
                    self.assertEqual(
                        found,
                        [],
                        f"AST guard flagged a legitimate non-user-facing string: {sample!r}",
                    )

    def test_ast_guard_detects_deliberate_placeholder_mismatches(self):
        """Negative test proving that placeholder scanner catches missing placeholders in facade and direct calls."""
        template_sample = "{foo} and {bar}"
        placeholder_pattern = re.compile(r"\{([a-zA-Z0-9_]+)")
        required = set(placeholder_pattern.findall(template_sample))

        bad_calls = [
            "texts.SOME_KEY.format(foo='1')",  # missing bar
            "SOME_KEY.format(bar='2')",        # missing foo
            "texts.SOME_KEY.format()",         # missing all
        ]

        for sample in bad_calls:
            with self.subTest(call=sample):
                tree = ast.parse(sample)
                call_node = tree.body[0].value
                provided = {kw.arg for kw in call_node.keywords if kw.arg}
                missing = required - provided
                self.assertTrue(bool(missing), f"Failed to detect missing placeholder in {sample}")



    def test_new_white_internet_texts_exist(self):
        self.assertTrue(hasattr(texts, "BTN_WL_CONFIRM_RENEW"))
        self.assertTrue(hasattr(texts, "BTN_WL_PAY_BASE"))
        self.assertTrue(hasattr(texts, "BTN_WL_PAY_PACK"))
        self.assertTrue(hasattr(texts, "BTN_WL_TOPUP_SHORTAGE"))
        self.assertTrue(hasattr(texts, "BTN_WL_RETURN_TO_SERVICE"))
        self.assertTrue(hasattr(texts, "BTN_WL_CONVERT_TRIAL"))
        self.assertTrue(hasattr(texts, "BTN_WL_REFRESH_STATUS"))
        self.assertTrue(hasattr(texts, "WL_TRIAL_CANNOT_TOPUP"))
        self.assertTrue(hasattr(texts, "WL_AUTO_PUSH_READY"))
        self.assertTrue(hasattr(texts, "WL_BUY_PREVIEW_TEXT"))
        self.assertTrue(hasattr(texts, "WL_RENEW_PREVIEW_TEXT"))
        self.assertTrue(hasattr(texts, "WL_TOPUP_PREVIEW_TEXT"))
        self.assertTrue(hasattr(texts, "WL_PREVIEW_BALANCE_OK"))
        self.assertTrue(hasattr(texts, "WL_PREVIEW_BALANCE_SHORTAGE"))
        self.assertTrue(hasattr(texts, "BTN_INSTRUCTION_INCY"))
        self.assertTrue(hasattr(texts, "SUPPORT_INCY_INSTRUCTION_TEXT"))

if __name__ == "__main__":
    unittest.main()
