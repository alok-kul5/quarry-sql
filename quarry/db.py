"""Read-only DuckDB access with a timeout and a row cap."""
from __future__ import annotations

import datetime as dt
import decimal
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from .guard import validate

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "quarry.duckdb"


class QueryError(RuntimeError):
    pass


@dataclass
class Result:
    columns: list[str]
    rows: list[list]
    truncated: bool
    elapsed_ms: int
    sql: str = ""

    def to_dict(self) -> dict:
        return {
            "columns": self.columns,
            "rows": self.rows,
            "row_count": len(self.rows),
            "truncated": self.truncated,
            "elapsed_ms": self.elapsed_ms,
            "sql": self.sql,
        }


@dataclass
class Column:
    name: str
    type: str
    comment: str = ""


@dataclass
class Table:
    name: str
    comment: str = ""
    row_count: int = 0
    columns: list[Column] = field(default_factory=list)


def _clean(v):
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat(sep=" ") if isinstance(v, dt.datetime) else v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, float):
        return round(v, 6)
    if isinstance(v, dt.timedelta):
        return v.total_seconds()
    return v


class Database:
    def __init__(self, path: str | Path = DEFAULT_DB, timeout_s: float = 10.0, max_rows: int = 500):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"{self.path} not found; run scripts/build_db.py first")
        self.timeout_s = timeout_s
        self.max_rows = max_rows
        self._con = duckdb.connect(
            str(self.path),
            read_only=True,
            config={"enable_external_access": False, "autoload_known_extensions": False,
                    "autoinstall_known_extensions": False},
        )
        self._con.execute("SET lock_configuration = true")
        self._schema: list[Table] | None = None

    def close(self) -> None:
        self._con.close()

    def query(self, sql: str, max_rows: int | None = None, timeout_s: float | None = None) -> Result:
        sql = validate(sql, {t.name for t in self.schema()})
        limit = min(max_rows or self.max_rows, self.max_rows)
        cur = self._con.cursor()
        timer = threading.Timer(timeout_s or self.timeout_s, cur.interrupt)
        start = time.perf_counter()
        timer.start()
        try:
            cur.execute(sql)
            columns = [d[0] for d in cur.description or []]
            raw = cur.fetchmany(limit + 1)
        except duckdb.InterruptException:
            raise QueryError(f"query timed out after {timeout_s or self.timeout_s:.0f}s") from None
        except duckdb.Error as e:
            raise QueryError(_short(e)) from None
        finally:
            timer.cancel()
            cur.close()
        rows = [[_clean(v) for v in r] for r in raw[:limit]]
        return Result(columns, rows, len(raw) > limit, int((time.perf_counter() - start) * 1000), sql)

    def schema(self) -> list[Table]:
        if self._schema is not None:
            return self._schema
        cur = self._con.cursor()
        tables = {
            name: Table(name, comment or "", int(est or 0))
            for name, comment, est in cur.execute(
                "SELECT table_name, comment, estimated_size FROM duckdb_tables() "
                "WHERE schema_name = 'main' ORDER BY table_name"
            ).fetchall()
        }
        for t, c, typ, comment in cur.execute(
            "SELECT table_name, column_name, data_type, comment FROM duckdb_columns() "
            "WHERE schema_name = 'main' ORDER BY table_name, column_index"
        ).fetchall():
            if t in tables:
                tables[t].columns.append(Column(c, typ, comment or ""))
        for t in tables.values():
            t.row_count = cur.execute(f'SELECT count(*) FROM "{t.name}"').fetchone()[0]
        cur.close()
        self._schema = list(tables.values())
        return self._schema

    def table(self, name: str) -> Table | None:
        return next((t for t in self.schema() if t.name.lower() == name.strip().lower()), None)

    def distinct_values(self, table: str, column: str, limit: int = 300) -> list[str]:
        cur = self._con.cursor()
        try:
            return [r[0] for r in cur.execute(
                f'SELECT DISTINCT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL LIMIT {limit}'
            ).fetchall()]
        finally:
            cur.close()


def _short(e: Exception) -> str:
    msg = str(e).strip()
    lines = [ln for ln in msg.splitlines() if ln.strip()]
    return " ".join(lines[:3])[:500]
