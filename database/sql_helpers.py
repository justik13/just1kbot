"""Database SQL schema and constraint helper utilities."""
from __future__ import annotations

from enum import StrEnum


def sql_enum_in(column: str, enum_cls: type[StrEnum]) -> str:
    """Build a SQL CheckConstraint IN clause strictly derived from a canonical StrEnum."""
    if not isinstance(column, str) or not column.isidentifier():
        raise ValueError(f"Invalid SQL column identifier: {column!r}")
    # Keep quoting 3.11-compatible: no same-type nested quotes inside the
    # f-string expression (PEP 701 is 3.12+).
    escaped = ", ".join(
        "'{}'".format(str(s.value).replace("'", "''")) for s in enum_cls
    )
    return f"{column} IN ({escaped})"


__all__ = ["sql_enum_in"]
