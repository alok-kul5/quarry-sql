import json

import pytest
from fastapi.testclient import TestClient

from conftest import ScriptedLLM, call, say
from quarry.scoring import result_matches


@pytest.mark.parametrize("gold, pred, ok", [
    ([[6]], [[6]], True),
    ([[4.17327]], [[4.17]], True),             # rounded to cents
    ([[77.97]], [[0.7797]], False),            # fraction is not a percentage
    ([["Queens"]], [["queens ", 69]], True),   # extra column, case and spaces
    ([["a", 1], ["b", 2]], [["b", 2], ["a", 1]], True),  # order ignored
    ([[1, "a"]], [["a", 1]], True),            # column order ignored
    ([["a"], ["b"]], [["a"]], False),          # missing row
    ([["a"]], [["a"], ["b"]], False),          # extra row
    ([[1, 2]], [[1]], False),                  # missing column
    ([[5]], None, False),
    ([], [], True),
    ([["2024-01-17"]], [["2024-01-17"]], True),
    ([[18]], [[18.0]], True),
])
def test_result_matches(gold, pred, ok):
    assert result_matches(gold, pred) is ok


@pytest.fixture()
def client(db_path, monkeypatch, tmp_path):
    import quarry.agent
    import quarry.server as server

    monkeypatch.setenv("QUARRY_DB", str(db_path))
    monkeypatch.setattr(quarry.agent, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(server, "RUNS_DIR", tmp_path)

    class FakeOllama(ScriptedLLM):
        def __init__(self, model="fake"):
            super().__init__([
                call("run_sql", sql="SELECT taxi_type, count(*) AS n FROM trips GROUP BY 1 ORDER BY 1"),
                call("make_chart", kind="bar", x="taxi_type", y="n"),
                say("Yellow cabs made 4 of the 6 trips."),
            ], model)

        def models(self):
            return ["fake"]

    monkeypatch.setattr(server, "OllamaClient", FakeOllama)
    with TestClient(server.app) as c:
        yield c


def test_index_and_health(client):
    assert "Quarry" in client.get("/").text
    assert client.get("/api/health").json()["models"] == ["fake"]
    assert {t["table"] for t in client.get("/api/schema").json()} >= {"trips", "zones"}
    assert len(client.get("/api/examples").json()) >= 4


def test_ask_streams_trace_and_logs_run(client):
    with client.stream("POST", "/api/ask", json={"question": "Trips by taxi type", "model": "fake"}) as r:
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start" and kinds[-1] == "final" and "done" in kinds
    final = events[-1]
    assert final["answer"].startswith("Yellow") and final["chart"]["values"] == [2, 4]
    runs = client.get("/api/runs").json()
    assert runs[0]["question"] == "Trips by taxi type" and runs[0]["ok"]
    full = client.get(f"/api/runs/{runs[0]['run_id']}").json()
    assert full["trace"][0]["type"] == "start"


def test_run_id_is_validated(client):
    assert client.get("/api/runs/..%2F..%2Fsecret").status_code in (400, 404)
    assert client.get("/api/runs/not-a-run").status_code == 400


def test_question_length_is_validated(client):
    assert client.post("/api/ask", json={"question": "x" * 600}).status_code == 422
