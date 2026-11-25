"""Agent tools. Every tool returns (model_view, ui_payload).

The model only ever sees a sanitized, size-capped JSON copy of the data, wrapped
as untrusted. The UI gets the full payload and renders it as text, never HTML.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .db import Database, QueryError, Result
from .guard import GuardError

MODEL_ROWS = 20
MAX_CELL = 120
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩]")
_INSTRUCTION = re.compile(
    r"(ignore (all |any )?(previous|prior|above)|disregard (the |all )?(previous|prior|above)|"
    r"system prompt|you are now|new instructions|</?(system|assistant|tool)>|"
    r"\b(drop|delete|truncate|insert|update)\s+(table|from|into)\b|run_sql|call the tool)",
    re.I,
)
WITHHELD = "[withheld: value looks like an instruction]"

TOOL_SPECS: list[dict] = [
    {"type": "function", "function": {
        "name": "list_tables",
        "description": "List every table with its row count and a short description.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "describe_table",
        "description": "Show the columns, types and descriptions of one table.",
        "parameters": {"type": "object", "properties": {
            "table": {"type": "string", "description": "Table name"}}, "required": ["table"]},
    }},
    {"type": "function", "function": {
        "name": "sample_rows",
        "description": "Return a few example rows from a table to see what the values look like.",
        "parameters": {"type": "object", "properties": {
            "table": {"type": "string"},
            "n": {"type": "integer", "description": "Rows to return, at most 10"}}, "required": ["table"]},
    }},
    {"type": "function", "function": {
        "name": "run_sql",
        "description": "Run one read-only DuckDB SELECT query and get the result. "
                       "If it fails, read the error, fix the SQL and call run_sql again.",
        "parameters": {"type": "object", "properties": {
            "sql": {"type": "string", "description": "A single DuckDB SELECT statement"}}, "required": ["sql"]},
    }},
    {"type": "function", "function": {
        "name": "make_chart",
        "description": "Chart the result of the last successful run_sql call.",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["bar", "line", "pie", "scatter"]},
            "x": {"type": "string", "description": "Column for the x axis or labels"},
            "y": {"type": "string", "description": "Numeric column for the y axis or values"},
            "title": {"type": "string"}}, "required": ["kind", "x", "y"]},
    }},
]
TOOL_NAMES = {t["function"]["name"] for t in TOOL_SPECS}


def sanitize(value: Any) -> tuple[Any, bool]:
    """Make one cell safe to show a model. Returns (value, was_flagged)."""
    if not isinstance(value, str):
        return value, False
    text = _CTRL.sub("", value)
    if _INSTRUCTION.search(text):
        return WITHHELD, True
    if len(text) > MAX_CELL:
        text = text[:MAX_CELL] + "..."
    return text, False


def as_untrusted(payload: dict) -> str:
    return json.dumps({"untrusted_data": payload}, default=str, ensure_ascii=False)


@dataclass
class ToolState:
    last_result: Result | None = None
    chart: dict | None = None
    sql_attempts: list[dict] = field(default_factory=list)
    flagged_cells: int = 0


class Toolbox:
    def __init__(self, db: Database):
        self.db = db
        self.state = ToolState()

    def reset(self) -> None:
        self.state = ToolState()

    def call(self, name: str, args: dict | None) -> tuple[str, dict]:
        args = args if isinstance(args, dict) else {}
        if name not in TOOL_NAMES:
            return _err(f"unknown tool {name!r}; use one of {sorted(TOOL_NAMES)}")
        try:
            return getattr(self, f"_{name}")(**{k: v for k, v in args.items() if isinstance(k, str)})
        except TypeError as e:
            return _err(f"bad arguments for {name}: {e}")

    # -- tools -------------------------------------------------------------
    def _list_tables(self, **_) -> tuple[str, dict]:
        tables = [{"table": t.name, "rows": t.row_count, "description": t.comment} for t in self.db.schema()]
        return as_untrusted({"tables": tables}), {"tables": tables}

    def _describe_table(self, table: str = "", **_) -> tuple[str, dict]:
        t = self.db.table(str(table))
        if not t:
            return _err(f"no table {table!r}; tables are {[x.name for x in self.db.schema()]}")
        cols = [{"column": c.name, "type": c.type, "description": c.comment} for c in t.columns]
        payload = {"table": t.name, "rows": t.row_count, "description": t.comment, "columns": cols}
        return as_untrusted(payload), payload

    def _sample_rows(self, table: str = "", n: int = 5, **_) -> tuple[str, dict]:
        t = self.db.table(str(table))
        if not t:
            return _err(f"no table {table!r}; tables are {[x.name for x in self.db.schema()]}")
        try:
            n = max(1, min(int(n), 10))
        except (TypeError, ValueError):
            n = 5
        res = self.db.query(f'SELECT * FROM "{t.name}" LIMIT {n}')
        return self._view(res), {"result": res.to_dict()}

    def _run_sql(self, sql: str = "", **_) -> tuple[str, dict]:
        try:
            res = self.db.query(str(sql))
        except (GuardError, QueryError) as e:
            kind = "blocked" if isinstance(e, GuardError) else "error"
            self.state.sql_attempts.append({"sql": sql, "ok": False, "error": str(e)})
            view, ui = _err(f"{kind}: {e}. Fix the SQL and call run_sql again.")
            ui["sql"] = sql
            return view, ui
        self.state.last_result = res
        self.state.sql_attempts.append({"sql": res.sql, "ok": True, "rows": len(res.rows)})
        return self._view(res), {"result": res.to_dict()}

    def _make_chart(self, kind: str = "bar", x: str = "", y: str = "", title: str = "", **_):
        res = self.state.last_result
        if res is None:
            return _err("run a successful run_sql query before make_chart")
        cols = {c.lower(): c for c in res.columns}
        if str(x).lower() not in cols or str(y).lower() not in cols:
            return _err(f"x and y must be columns of the last result: {res.columns}")
        if kind not in ("bar", "line", "pie", "scatter"):
            kind = "bar"
        xi, yi = res.columns.index(cols[str(x).lower()]), res.columns.index(cols[str(y).lower()])
        if not all(isinstance(r[yi], (int, float)) or r[yi] is None for r in res.rows):
            return _err(f"column {y!r} is not numeric")
        chart = {"kind": kind, "x": res.columns[xi], "y": res.columns[yi],
                 "title": sanitize(str(title or ""))[0][:80],
                 "labels": [r[xi] for r in res.rows[:60]], "values": [r[yi] for r in res.rows[:60]]}
        self.state.chart = chart
        return as_untrusted({"chart": "ok", "points": len(chart["values"])}), {"chart": chart}

    # -- helpers -----------------------------------------------------------
    def _view(self, res: Result) -> str:
        rows = []
        for r in res.rows[:MODEL_ROWS]:
            clean = []
            for v in r:
                cv, flagged = sanitize(v)
                self.state.flagged_cells += flagged
                clean.append(cv)
            rows.append(clean)
        return as_untrusted({
            "columns": res.columns, "rows": rows, "row_count": len(res.rows),
            "rows_shown": len(rows), "truncated": res.truncated,
        })


def _err(msg: str) -> tuple[str, dict]:
    return json.dumps({"error": msg}), {"error": msg}
