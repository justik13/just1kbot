import ast
import re
import unittest
from pathlib import Path

from bot import texts


class TextsConsistencyTests(unittest.TestCase):
    """Automated consistency, markup, and placeholder verification for all application texts."""

    def test_all_text_keys_are_valid_identifiers(self):
        keys = texts.get_all_text_keys()
        self.assertGreater(len(keys), 100, "Text catalogue must contain loaded keys.")
        for key in keys:
            self.assertTrue(key.isidentifier(), f"Key {key!r} is not a valid Python identifier.")

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

    def test_html_markup_validity(self):
        """Verify that basic HTML tags used in Telegram messages are balanced and valid."""
        allowed_tags = {
            "b", "/b", "strong", "/strong",
            "i", "/i", "em", "/em",
            "code", "/code", "pre", "/pre",
            "a", "/a", "u", "/u", "s", "/s",
            "tg-spoiler", "/tg-spoiler",
            "blockquote", "/blockquote",
        }
        
        tag_pattern = re.compile(r"<(/?[a-zA-Z0-9_-]+)(?:\s+[^>]*)?>")
        
        for key in texts.get_all_text_keys():
            val = getattr(texts, key)
            if not isinstance(val, str):
                continue
            
            tags = tag_pattern.findall(val)
            for t in tags:
                tag_name = t.strip()
                self.assertIn(
                    tag_name,
                    allowed_tags,
                    f"Unsupported HTML tag <{tag_name}> in text key {key!r}:\n{val}",
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
