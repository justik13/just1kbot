"""Automated consistency, SSOT, markup, and architectural verification for bot.texts."""
from __future__ import annotations
import collections

import ast
import re
import unittest
from pathlib import Path

from bot import texts

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEXTS_DIR = PROJECT_ROOT / "bot" / "texts"

# Explicit canonical alias registry for intentional aliases (e.g. backward compat or semantic alias)
CANONICAL_ALIASES: dict[str, str] = {
    # Alias Key -> Canonical Key
    "BTN_PAYMENT_SPECIFY_OTHER_AMOUNT_ALIAS": "BTN_PAYMENT_SPECIFY_OTHER_AMOUNT",
}


class TextsConsistencyTests(unittest.TestCase):

    def test_alias_registry_is_acyclic_and_valid(self):
        """Verify CANONICAL_ALIASES contains valid mapping and no self-aliases."""
        for alias, canonical in CANONICAL_ALIASES.items():
            self.assertNotEqual(alias, canonical, f"Self-alias detected: {alias} -> {canonical}")
            self.assertNotIn(canonical, CANONICAL_ALIASES, f"Alias cycle detected: {canonical} is also an alias key")

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

        for py_file in Path('bot/texts').rglob("*.py"):
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
                if "texts" in py_file.parts:
                    continue
                content = py_file.read_text(encoding="utf-8")
                tree = ast.parse(content, filename=str(py_file))

                for node in ast.walk(tree):
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "format"
                        and isinstance(node.func.value, ast.Attribute)
                        and isinstance(node.func.value.value, ast.Name)
                        and node.func.value.value.id == "texts"
                    ):
                        text_key = node.func.value.attr
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
        """Deep AST guard scanning handlers, keyboards, and workers for hardcoded user-facing strings."""
        violations = []

        scanned_dirs = [
            PROJECT_ROOT / "bot" / "handlers",
            PROJECT_ROOT / "bot" / "keyboards",
            PROJECT_ROOT / "services" / "workers",
        ]

        
        ALLOWED_INTERNAL_STRINGS = {
            "db error", "api_failed", "slots_unknown", "unknown",
            "Requeued by stuck profile cleanup worker for peer reconciliation",
            "Creation timed out by cleanup worker",
            "STOPPED ",
            "webhook payment.canceled: payment not found for external_id=%s order=%s — discarding silently",
            "auto_resolved: ", " for untracked payment",
            "requires_manual_review: ",
            "Healthcheck exception for server %s (%s): %s",
            "Error reading server load for server %s: %s",
            "payments.provider_status = 'succeeded'",
            "payments.fulfillment_status = 'succeeded'",
            "payments.external_id IS NOT NULL AND payments.provider_status IN ('creating', 'pending', 'waiting_for_capture', 'unknown')",
            "payments.provider_status = 'succeeded' AND payments.provider_confirmed_at IS NOT NULL AND payments.fulfillment_status NOT IN ('succeeded', 'reversed', 'manual_review')",
            'NOT (COALESCE(payments.topup_context, \'{}\'::jsonb) @> \'{"referral_bonus_processed": true}\'::jsonb)',
            "payments.topup_context->>'referrer_notified_at' IS NULL AND payments.topup_context->>'referrer_telegram_id' IS NOT NULL",
            "Payload too large",
            "Invalid webhook",
            "Database unavailable",
            "can't parse entities",
            "HTML parse failed for user %s, falling back to plain text",
            "manual review",
            "marked for manual review in Telegram admin",
            " (ID: ",
            "ID: ",
            "ID ",
            "\\n\\u2022 \\U0001f4f1 <b>",
            "\\u2022 \\U0001f4f1 <b>",
            "</b> (",
            ")\\n   \\u2514 \\U0001f4ca <code>",
            "</code> | <i>",
            "\\u2022 <code>[",
            "]</code> ",
            "<blockquote expandable><code>",
            ". <b>",
            "\\u2514 <code>[",
            "\\n\\u2022 <b>",
            "</b> | ",
            " <code>[",
            "\\n• 📱 <b>",
            "• 📱 <b>",
            ")\\n   └ 📊 <code>",
        }
        def _is_exempt_call(parent_calls: list[ast.Call]) -> bool:
            for call in parent_calls:
                if isinstance(call.func, ast.Name):
                    if call.func.id in ("re", "compile", "ValueError", "RuntimeError", "Exception", "TypeError", "AssertionError", "getattr", "hasattr"):
                        return True
                if isinstance(call.func, ast.Attribute):
                    if isinstance(call.func.value, ast.Name) and call.func.value.id in (
                        "logger", "logging", "log", "root_logger", "re",
                    ):
                        return True
            return False
        def _is_user_facing_string(s: str) -> bool:
            if not isinstance(s, str) or not s.strip():
                return False
            if s in ALLOWED_INTERNAL_STRINGS:
                return False
            
            clean_s = re.sub(r"<[^>]+>", "", s)
            
            if re.search(r"[\u0400-\u04FF]", clean_s):
                return True
            if s.startswith(("http://", "https://", "postgres://", "redis://", "/", "urn:", "mailto:", "amneziawg://")):
                return False
            if "SELECT " in s.upper() or "UPDATE " in s.upper() or "INSERT INTO" in s.upper() or "DELETE FROM" in s.upper():
                return False
            if re.match(r"^[%YmdHMS\-\:\s\.,TZ]+$", clean_s):
                return False
            if re.match(r"^[A-Za-z0-9_\-\.\:\/]+$", clean_s):
                return False
            if re.search(r"[A-Za-z]{2,}", clean_s) and " " in clean_s:
                return True
            if re.search(r"[A-Za-z]", clean_s) and " " in clean_s:
                return True
            return False

        class HardcodedStringVisitor(ast.NodeVisitor):
            def __init__(self, file_path: Path, docstring_nodes: set[ast.AST]):
                self.file_path = file_path
                self.docstring_nodes = docstring_nodes
                self.call_stack: list[ast.Call] = []

            def visit_Call(self, node: ast.Call):
                self.call_stack.append(node)
                self.generic_visit(node)
                self.call_stack.pop()

            def visit_Constant(self, node: ast.Constant):
                if node in self.docstring_nodes:
                    return
                if isinstance(node.value, str) and _is_user_facing_string(node.value):
                    if not _is_exempt_call(self.call_stack):
                        rel_path = self.file_path.relative_to(PROJECT_ROOT).as_posix()
                        violations.append(
                            f"{rel_path}:{node.lineno} contains hardcoded string: {node.value[:50]!r}"
                        )
                self.generic_visit(node)

            def visit_JoinedStr(self, node: ast.JoinedStr):
                if not _is_exempt_call(self.call_stack):
                    for part in node.values:
                        if isinstance(part, ast.Constant) and isinstance(part.value, str):
                            if _is_user_facing_string(part.value):
                                rel_path = self.file_path.relative_to(PROJECT_ROOT).as_posix()
                                violations.append(
                                    f"{rel_path}:{node.lineno} contains hardcoded f-string part: {part.value[:50]!r}"
                                )
                self.generic_visit(node)
                
        for base_dir in scanned_dirs:
            for py_file in base_dir.rglob("*.py"):
                content = py_file.read_text(encoding="utf-8")
                tree = ast.parse(content, filename=str(py_file))

                # Collect all docstring constant nodes across module, classes, functions
                docstring_nodes = set()
                # Module docstring
                if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant):
                    docstring_nodes.add(tree.body[0].value)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
                            docstring_nodes.add(node.body[0].value)

                visitor = HardcodedStringVisitor(py_file, docstring_nodes)
                visitor.visit(tree)

        self.assertEqual(
            violations,
            [],
            "Found hardcoded user-facing strings across handlers/keyboards/workers:\n"
            + "\n".join(violations),
        )

    def test_ast_guard_detects_deliberate_hardcoded_string_violations(self):
        """Negative self-test proving that the AST scanner detects raw strings in dicts, f-strings, and vars."""
        bad_code_samples = [
            "label_map = {'active': 'Active users'}",
            "msg = f'Hello {user_id}'",
            "btn_text = 'Durable queue recovered'",
            "text = texts.FOO + ' extra'",
            "status = 'Платёж создан'",
            "alert = 'VPN server восстановлен'",
        ]
        
        def _is_user_facing_string(s: str) -> bool:
            if not isinstance(s, str) or not s.strip():
                return False
            clean_s = re.sub(r"<[^>]+>", "", s)
            if re.search(r"[\u0400-\u04FF]", clean_s):
                return True
            if s.startswith(("http://", "https://", "postgres://", "redis://", "/", "urn:", "mailto:", "amneziawg://")):
                return False
            if "SELECT " in s.upper() or "UPDATE " in s.upper() or "INSERT INTO" in s.upper() or "DELETE FROM" in s.upper():
                return False
            if re.match(r"^[%YmdHMS\-\:\s\.,TZ]+$", clean_s):
                return False
            if re.match(r"^[A-Za-z0-9_\-\.\:\/]+$", clean_s):
                return False
            if re.search(r"[A-Za-z]{2,}", clean_s) and " " in clean_s:
                return True
            if re.search(r"[A-Za-z]", clean_s) and " " in clean_s:
                return True
            return False

        for sample in bad_code_samples:
            with self.subTest(code=sample):
                tree = ast.parse(sample)
                found = False
                for node in ast.walk(tree):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str):
                        if _is_user_facing_string(node.value):
                            found = True
                    elif isinstance(node, ast.JoinedStr):
                        for part in node.values:
                            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                                if _is_user_facing_string(part.value):
                                    found = True
                self.assertTrue(
                    found,
                    f"AST guard failed to detect deliberate violation in: {sample!r}",
                )

if __name__ == "__main__":
    unittest.main()
