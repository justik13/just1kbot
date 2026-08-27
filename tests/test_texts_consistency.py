"""Automated consistency, SSOT, markup, and architectural verification for bot.texts."""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from bot import texts

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEXTS_DIR = PROJECT_ROOT / "bot" / "texts"


class TextsConsistencyTests(unittest.TestCase):
    """Automated consistency, markup, and placeholder verification for all application texts."""

    def test_all_text_keys_are_valid_identifiers(self):
        """Verify that all keys in texts facade are valid uppercase/semantic identifiers."""
        keys = texts.get_all_text_keys()
        self.assertGreater(len(keys), 100, "Text catalogue must contain loaded keys.")
        for key in keys:
            self.assertTrue(key.isidentifier(), f"Key {key!r} is not a valid Python identifier.")
            # Disallow line-number based keys or unsemantic names
            self.assertIsNone(
                re.search(r"(_L\d+_\d+|_L\d+)$", key),
                f"Key {key!r} contains forbidden line-number suffix.",
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

            # Skip double braces
            clean_val = val.replace("{{", "").replace("}}", "")

            # Check for unclosed or broken braces
            open_count = clean_val.count("{")
            close_count = clean_val.count("}")
            self.assertEqual(
                open_count,
                close_count,
                f"Mismatched braces in text key {key!r}:\n{val}",
            )

            # Check placeholder names are valid
            for placeholder in placeholder_pattern.findall(clean_val):
                var_name = placeholder.split(":")[0].split("!")[0].strip()
                if var_name.isdigit():
                    continue
                self.assertTrue(
                    var_name.isidentifier(),
                    f"Invalid placeholder name {placeholder!r} in text key {key!r}:\n{val}",
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
        all_keys = set(texts.get_all_text_keys())
        for py_file in TEXTS_DIR.rglob("*.py"):
            if py_file.name == "__init__.py":
                continue
            content = py_file.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(py_file))
            for stmt in tree.body:
                if isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        if isinstance(target, ast.Name) and target.id.isupper():
                            self.assertIn(
                                target.id,
                                all_keys,
                                f"Key {target.id!r} in {py_file} is not exported in bot.texts facade.",
                            )
                            self.assertTrue(
                                hasattr(texts, target.id),
                                f"Key {target.id!r} not accessible via getattr(texts, ...).",
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

    def test_no_hardcoded_user_facing_strings_in_handlers_and_keyboards(self):
        """AST guard ensuring all user-facing text strings in handlers and keyboards come from canonical texts."""
        cyrillic_pattern = re.compile(r"[\u0400-\u04FF]")
        violations = []

        targets = list((PROJECT_ROOT / "bot" / "handlers").rglob("*.py")) + list(
            (PROJECT_ROOT / "bot" / "keyboards").rglob("*.py")
        )

        for py_file in targets:
            content = py_file.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(py_file))

            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func_name = ""
                    if isinstance(node.func, ast.Attribute):
                        func_name = node.func.attr
                    elif isinstance(node.func, ast.Name):
                        func_name = node.func.id

                    if func_name in ("answer", "edit_text", "button", "InlineKeyboardButton", "send_message"):
                        # Check positional arguments
                        for arg in node.args:
                            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                                if cyrillic_pattern.search(arg.value):
                                    violations.append(
                                        f"{py_file.relative_to(PROJECT_ROOT)}:{node.lineno} passes hardcoded Cyrillic string to {func_name}(): {arg.value!r}"
                                    )
                        # Check keyword arguments (text=..., caption=...)
                        for kw in node.keywords:
                            if kw.arg in ("text", "caption") and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                                if cyrillic_pattern.search(kw.value.value):
                                    violations.append(
                                        f"{py_file.relative_to(PROJECT_ROOT)}:{node.lineno} passes hardcoded Cyrillic string to {func_name}({kw.arg}=...): {kw.value.value!r}"
                                    )

        self.assertEqual(
            violations,
            [],
            "Found hardcoded user-facing strings in handlers/keyboards without canonical text keys:\n"
            + "\n".join(violations),
        )


if __name__ == "__main__":
    unittest.main()

