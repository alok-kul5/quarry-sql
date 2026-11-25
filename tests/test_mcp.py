import asyncio
import json

import quarry.mcp_server as m
from quarry.tools import Toolbox


def test_mcp_exposes_guarded_tools(db):
    m._box = Toolbox(db)
    names = {t.name for t in asyncio.run(m.mcp.list_tools())}
    assert names >= {"list_tables", "describe_table", "sample_rows", "run_sql"}
    assert json.loads(m.run_sql("SELECT count(*) FROM trips"))["untrusted_data"]["rows"] == [[6]]
    assert "blocked" in json.loads(m.run_sql("DROP TABLE trips"))["error"]
    m._box = None
