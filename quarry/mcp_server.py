"""The same guarded tools, exposed over MCP (stdio) for any MCP client.

    py -3.11 -m quarry.mcp_server
"""
from __future__ import annotations

import json

from mcp.server.mcpserver import MCPServer

from .db import Database
from .tools import Toolbox

mcp = MCPServer("quarry", instructions="Read-only, guarded SQL tools over NYC taxi trips (Jan 2024).")
_box: Toolbox | None = None


def box() -> Toolbox:
    global _box
    if _box is None:
        _box = Toolbox(Database())
    return _box


def _view(name: str, args: dict) -> str:
    view, _ = box().call(name, args)
    return view


@mcp.tool()
def list_tables() -> str:
    """List every table with its row count and description."""
    return _view("list_tables", {})


@mcp.tool()
def describe_table(table: str) -> str:
    """Columns, types and descriptions of one table."""
    return _view("describe_table", {"table": table})


@mcp.tool()
def sample_rows(table: str, n: int = 5) -> str:
    """A few example rows from a table (at most 10)."""
    return _view("sample_rows", {"table": table, "n": n})


@mcp.tool()
def run_sql(sql: str) -> str:
    """Run one read-only DuckDB SELECT. Writes, DDL and file access are blocked."""
    return _view("run_sql", {"sql": sql})


@mcp.tool()
def schema_json() -> str:
    """The full schema as JSON."""
    return json.dumps([{"table": t.name, "columns": [c.name for c in t.columns]} for t in box().db.schema()])


if __name__ == "__main__":
    mcp.run()
