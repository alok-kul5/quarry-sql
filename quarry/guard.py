"""SQL guard: only a single read-only SELECT may reach the database.

The database connection is already read-only with external access off, so this
is the second wall, and the one that gives the model a clear error to repair.
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

# Statements that change data, schema or session state.
_BLOCKED_NODES: tuple[type[exp.Expression], ...] = tuple(
    t
    for t in (
        getattr(exp, name, None)
        for name in (
            "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable",
            "TruncateTable", "Command", "Set", "Pragma", "Copy", "Attach", "Detach", "Use",
            "Transaction", "Commit", "Rollback", "Grant", "Revoke", "Install", "Load",
            "Export", "Into", "Kill", "Analyze", "Describe", "Show",
        )
    )
    if t is not None
)

# Table functions and scalar functions that touch files, the network or settings.
_BLOCKED_FUNCS = {
    "read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto", "read_text",
    "read_blob", "read_ndjson", "parquet_scan", "csv_scan", "glob", "sniff_csv",
    "parquet_metadata", "parquet_schema", "iceberg_scan", "delta_scan", "sqlite_scan",
    "postgres_scan", "mysql_scan", "httpfs", "current_setting", "duckdb_settings",
    "duckdb_secrets", "duckdb_extensions", "getenv", "load_extension", "query", "query_table",
}

_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)


class GuardError(ValueError):
    """The query is not allowed. The message is safe to show the model."""


def validate(sql: str, allowed_tables: set[str] | None = None) -> str:
    """Return the cleaned SQL if it is a single read-only query, else raise GuardError.

    With ``allowed_tables`` every table reference must be one of them or a CTE.
    """
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        raise GuardError("empty query")
    try:
        statements = [s for s in sqlglot.parse(text, read="duckdb") if s is not None]
    except ParseError as e:
        raise GuardError(f"could not parse SQL: {str(e).splitlines()[0]}") from None
    if len(statements) != 1:
        raise GuardError("only one statement is allowed per query")
    tree = statements[0]
    root = tree.this if isinstance(tree, exp.With) else tree
    if not isinstance(tree, _ROOTS) and not isinstance(root, _ROOTS):
        raise GuardError(f"only SELECT queries are allowed (got {tree.key.upper()})")
    for node in tree.walk():
        if isinstance(node, _BLOCKED_NODES):
            raise GuardError(f"{node.key.upper()} is not allowed; this connection is read-only")
        name = _func_name(node)
        if name and name in _BLOCKED_FUNCS:
            raise GuardError(f"function {name}() is not allowed")
        if isinstance(node, exp.Table) and isinstance(node.this, exp.Literal):
            raise GuardError("reading files by path is not allowed")
    if allowed_tables is not None:
        allowed = {t.lower() for t in allowed_tables}
        ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        for table in tree.find_all(exp.Table):
            name = table.name.lower()
            if not name:  # table functions such as range() or unnest()
                continue
            if table.db and table.db.lower() not in ("main", ""):
                raise GuardError(f"schema {table.db} is not available")
            if any(ch in name for ch in "/\\.:") or (name not in allowed and name not in ctes):
                raise GuardError(
                    f"unknown table {table.name!r}; available tables: {', '.join(sorted(allowed))}"
                )
    return text


def _func_name(node: exp.Expression) -> str | None:
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    if isinstance(node, exp.Func):
        return node.sql_name().lower()
    return None
