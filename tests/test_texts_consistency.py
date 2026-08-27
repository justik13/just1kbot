import ast
import re
import unittest
from pathlib import Path

from bot import texts
from bot.texts import _ALL_MODULES


class TextsConsistencyTests(unittest.TestCase):
    """Automated consistency, markup, and placeholder verification for all application texts."""

    def test_all_text_keys_are_valid_identifiers(self):
        keys = texts.get_all_text_keys()
        self.assertGreater(len(keys), 100, "Text catalogue must contain loaded keys.")
        for key in keys:
            self.assertTrue(key.isidentifier(), f"Key {key!r} is not a valid Python identifier.")

    def test_strict_no_duplicate_keys_across_domain_modules(self):
        """Verify absolute SSOT: each text key exists in exactly ONE domain file."""
        seen_keys = {}
        duplicates = []

        for mod_name, mod in _ALL_MODULES:
            mod_texts = dict(getattr(mod, "TEXTS", {}))
            for var_name in dir(mod):
                if var_name.isupper() and not var_name.startswith('_') and var_name != 'TEXTS':
                    mod_texts[var_name] = getattr(mod, var_name)

            for key in mod_texts.keys():
                if key in seen_keys:
                    duplicates.append((key, seen_keys[key], mod_name))
                else:
                    seen_keys[key] = mod_name

        self.assertEqual(
            duplicates,
            [],
            f"SSOT Violation: Duplicate text keys found across domain modules:\n{duplicates}",
        )

    def test_no_duplicate_variable_assignments_in_texts_files(self):
        """Verify statically via AST that no file in texts_data defines the same variable twice."""
        project_root = Path(__file__).resolve().parent.parent
        texts_dir = project_root / "bot" / "texts_data"
        dup_assignments = []

        for py_file in texts_dir.rglob("*.py"):
            if py_file.name.startswith("__"):
                continue
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            file_seen = set()
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id.isupper() and target.id != "TEXTS":
                            if target.id in file_seen:
                                dup_assignments.append((str(py_file.relative_to(project_root)), node.lineno, target.id))
                            else:
                                file_seen.add(target.id)

        self.assertEqual(
            dup_assignments,
            [],
            f"Found duplicate variable assignments within texts_data files:\n{dup_assignments}",
        )

    def test_reload_texts_rebuilds_registry(self):
        """Verify that reload_texts executes cleanly and preserves loaded keys."""
        initial_keys = set(texts.get_all_text_keys())
        texts.reload_texts()
        reloaded_keys = set(texts.get_all_text_keys())
        self.assertEqual(initial_keys, reloaded_keys)

    def test_all_text_placeholders_syntax_is_valid(self):
        """Verify that any text containing {placeholders} does not have broken braces or syntax errors."""
        placeholder_pattern = re.compile(r"\{([^{}]+)\}")
        
        for key in texts.get_all_text_keys():
            val = getattr(texts, key)
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
                # Placeholder can be identifier or identifier with format spec like value_0:02d
                var_name = placeholder.split(":")[0].split("!")[0].strip()
                self.assertTrue(
                    var_name.isidentifier(),
                    f"Invalid placeholder name {placeholder!r} in text key {key!r}:\n{val}",
                )

    def test_html_markup_nesting_and_validity(self):
        """Verify that HTML tags used in Telegram messages are balanced and properly nested."""
        allowed_tags = {
            "b", "strong", "i", "em", "code", "pre",
            "a", "u", "s", "tg-spoiler", "blockquote",
        }
        
        tag_pattern = re.compile(r"<(/?[a-zA-Z0-9_-]+)(?:\s+[^>]*)?>")
        
        for key in texts.get_all_text_keys():
            val = getattr(texts, key)
            if not isinstance(val, str):
                continue
            
            # Skip checking templates that use placeholders as whole tags if any
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

    def test_no_call_site_missing_placeholders(self):
        """Statically inspect all texts.<KEY>.format(...) calls across the codebase to ensure no missing kwargs."""
        project_root = Path(__file__).resolve().parent.parent
        placeholder_regex = re.compile(r"\{([a-zA-Z0-9_]+)(?:[^}]*)\}")
        missing_kwargs_failures = []
        
        for py_file in project_root.rglob("*.py"):
            if any(part in [".git", ".venv", "__pycache__", "tests", "texts_data"] for part in py_file.parts):
                continue
            try:
                content = py_file.read_text(encoding="utf-8")
                tree = ast.parse(content, filename=str(py_file))
            except Exception:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
                    val = node.func.value
                    key_name = None
                    if isinstance(val, ast.Attribute) and isinstance(val.value, ast.Name) and val.value.id == "texts":
                        key_name = val.attr
                    elif isinstance(val, ast.Name):
                        key_name = val.id

                    if key_name and hasattr(texts, key_name):
                        template = getattr(texts, key_name)
                        if isinstance(template, str):
                            clean_tmpl = template.replace("{{", "").replace("}}", "")
                            expected_placeholders = set(placeholder_regex.findall(clean_tmpl))
                            
                            kwarg_names = {kw.arg for kw in node.keywords if kw.arg is not None}
                            
                            # If called with only kwargs (no *args)
                            if not node.args:
                                missing = expected_placeholders - kwarg_names
                                if missing:
                                    missing_kwargs_failures.append(
                                        f"{py_file.relative_to(project_root)}:{node.lineno} calls texts.{key_name}.format(...) but misses required placeholders: {missing}"
                                    )

        self.assertEqual(
            missing_kwargs_failures,
            [],
            "Found call sites with missing required format arguments:\n" + "\n".join(missing_kwargs_failures),
        )


if __name__ == "__main__":
    unittest.main()
