#!/usr/bin/env python3
"""Temporary final codemod for PR #50; removed before review."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def add_explicit_subprocess_policy() -> None:
    targets = (
        ROOT / "tests/test_backup_operations.py",
        ROOT / "tests/test_deploy_rollback.py",
    )
    pending: list[tuple[Path, ast.Call]] = []

    for path in targets:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "run"
                and isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"
            ):
                continue
            if any(keyword.arg == "check" for keyword in node.keywords):
                continue
            pending.append((path, node))

    if not pending:
        return
    if len(pending) != 13:
        raise RuntimeError(
            f"expected 13 subprocess.run calls without check, found {len(pending)}"
        )

    by_path: dict[Path, list[ast.Call]] = {}
    for path, node in pending:
        by_path.setdefault(path, []).append(node)

    for path, nodes in by_path.items():
        data = path.read_bytes()
        lines = data.splitlines(keepends=True)
        starts: list[int] = []
        offset = 0
        for line in lines:
            starts.append(offset)
            offset += len(line)

        insertions: list[int] = []
        for node in nodes:
            if node.end_lineno is None or node.end_col_offset is None:
                raise RuntimeError(f"{path}: AST call has no end position")
            closing = starts[node.end_lineno - 1] + node.end_col_offset - 1
            if data[closing : closing + 1] != b")":
                raise RuntimeError(f"{path}: subprocess call does not end with ')' at {closing}")
            insertions.append(closing)

        for closing in sorted(insertions, reverse=True):
            data = data[:closing] + b", check=False" + data[closing:]
        path.write_bytes(data)


def replace_or_verify(path: Path, old: str, new: str) -> None:
    content = path.read_text(encoding="utf-8")
    old_count = content.count(old)
    new_count = content.count(new)
    if old_count == 1 and new_count == 0:
        path.write_text(content.replace(old, new), encoding="utf-8")
        return
    if old_count == 0 and new_count == 1:
        return
    raise RuntimeError(
        f"{path}: expected one old or one new exception form; "
        f"old={old_count} new={new_count}"
    )


def add_exception_chaining() -> None:
    path = ROOT / "utils/encryption.py"
    replace_or_verify(
        path,
        '            raise RuntimeError("Encryption failed")\n',
        '            raise RuntimeError("Encryption failed") from e\n',
    )
    replace_or_verify(
        path,
        "                raise RuntimeError(\n"
        "                    \"CRITICAL: Failed to decrypt critical field. \"\n"
        "                    \"DB_ENCRYPTION_KEY may have changed or data \"\n"
        "                    \"is corrupted. Server cannot operate safely.\"\n"
        "                )\n",
        "                raise RuntimeError(\n"
        "                    \"CRITICAL: Failed to decrypt critical field. \"\n"
        "                    \"DB_ENCRYPTION_KEY may have changed or data \"\n"
        "                    \"is corrupted. Server cannot operate safely.\"\n"
        "                ) from None\n",
    )
    replace_or_verify(
        path,
        "                raise RuntimeError(\n"
        "                    f\"CRITICAL: Decryption error: {type(e).__name__}\"\n"
        "                )\n",
        "                raise RuntimeError(\n"
        "                    f\"CRITICAL: Decryption error: {type(e).__name__}\"\n"
        "                ) from e\n",
    )


def main() -> None:
    add_explicit_subprocess_policy()
    add_exception_chaining()


if __name__ == "__main__":
    main()
