import ast
import re
from pathlib import Path

PROJECT_ROOT = Path(".")

def _is_exempt_call(parent_calls) -> bool:
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
    if re.search(r"[\u0400-\u04FF]", s):
        return True
    if s.startswith(("http://", "https://", "postgres://", "redis://", "/", "urn:", "mailto:", "amneziawg://")):
        return False
    if "SELECT " in s.upper() or "UPDATE " in s.upper() or "INSERT INTO" in s.upper() or "DELETE FROM" in s.upper():
        return False
    if re.match(r"^[%YmdHMS\-\:\s\.,TZ]+$", s):
        return False
    if re.match(r"^[A-Za-z0-9_\-\.\:\/]+$", s):
        return False
    if re.search(r"[A-Za-z]{2,}", s) and " " in s:
        return True
    if re.search(r"[A-Za-z]", s) and " " in s:
        return True
    return False

violations = []
scanned_dirs = [
    PROJECT_ROOT / "bot" / "handlers",
    PROJECT_ROOT / "bot" / "keyboards",
    PROJECT_ROOT / "services" / "workers",
]

class HardcodedStringVisitor(ast.NodeVisitor):
    def __init__(self, file_path: Path, docstring_nodes: set):
        self.file_path = file_path
        self.docstring_nodes = docstring_nodes
        self.call_stack = []

    def visit_Call(self, node: ast.Call):
        self.call_stack.append(node)
        self.generic_visit(node)
        self.call_stack.pop()

    def visit_Constant(self, node: ast.Constant):
        if node in self.docstring_nodes:
            return
        if _is_user_facing_string(node.value):
            if not _is_exempt_call(self.call_stack):
                rel_path = self.file_path.relative_to(PROJECT_ROOT).as_posix()
                violations.append(f"{rel_path}:{node.lineno} -> {node.value!r}")
        self.generic_visit(node)

    def visit_JoinedStr(self, node: ast.JoinedStr):
        if not _is_exempt_call(self.call_stack):
            for part in node.values:
                if isinstance(part, ast.Constant) and _is_user_facing_string(part.value):
                    rel_path = self.file_path.relative_to(PROJECT_ROOT).as_posix()
                    violations.append(f"{rel_path}:{node.lineno} -> {part.value!r} (f-string)")
        self.generic_visit(node)

for base_dir in scanned_dirs:
    for py_file in base_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(py_file))
        docstring_nodes = set()
        if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant):
            docstring_nodes.add(tree.body[0].value)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
                    docstring_nodes.add(node.body[0].value)
        visitor = HardcodedStringVisitor(py_file, docstring_nodes)
        visitor.visit(tree)

with open("violations.txt", "w", encoding="utf-8") as f:
    for v in violations:
        f.write(v + "\n")
