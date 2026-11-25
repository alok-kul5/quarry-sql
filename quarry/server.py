"""FastAPI app: streams the agent's trace over Server-Sent Events."""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import RUNS_DIR, Agent
from .db import Database
from .llm import DEFAULT_MODEL, OllamaClient
from .retrieval import SchemaRetriever

WEB = Path(__file__).resolve().parent / "web"
EXAMPLES = [
    "Which 5 pickup zones made the most revenue?",
    "How does the average tip change by hour of day?",
    "What share of trips were paid by credit card vs cash?",
    "Average fare from JFK Airport to Manhattan",
    "Which day of the week is busiest for green taxis?",
    "Top 10 dropoff zones in Brooklyn by trip count",
]
_RUN_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")

state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = Database(os.environ.get("QUARRY_DB") or Path(__file__).resolve().parent.parent / "data" / "quarry.duckdb")
    state["db"] = db
    state["retriever"] = SchemaRetriever(db)
    yield
    db.close()


app = FastAPI(title="Quarry", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=WEB), name="static")


class Ask(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    model: str = DEFAULT_MODEL
    retrieval: bool = True


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/health")
def health():
    models = OllamaClient().models()
    return {"ok": True, "models": models, "default_model": DEFAULT_MODEL, "ollama": bool(models)}


@app.get("/api/schema")
def schema():
    return [{"table": t.name, "rows": t.row_count, "description": t.comment,
             "columns": [{"name": c.name, "type": c.type, "description": c.comment} for c in t.columns]}
            for t in state["db"].schema()]


@app.get("/api/examples")
def examples():
    return EXAMPLES


@app.post("/api/ask")
async def ask(body: Ask):
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    agent = Agent(state["db"], OllamaClient(body.model), retrieval=body.retrieval,
                  retriever=state["retriever"])

    def work():
        try:
            res = agent.run(body.question, on_event=lambda ev: loop.call_soon_threadsafe(queue.put_nowait, ev))
            final = {"type": "final", **res.to_dict()}
            final.pop("trace", None)
            loop.call_soon_threadsafe(queue.put_nowait, final)
        except Exception as e:  # surface, never crash the stream
            loop.call_soon_threadsafe(queue.put_nowait, {"type": "error", "message": str(e)[:300]})
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=work, daemon=True).start()

    async def stream():
        while (ev := await queue.get()) is not None:
            yield f"data: {json.dumps(ev, default=str, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/runs")
def runs(limit: int = 20):
    out = []
    for p in sorted(RUNS_DIR.glob("*.json"), reverse=True)[: max(1, min(limit, 100))]:
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({k: r.get(k) for k in ("run_id", "question", "model", "elapsed_ms", "steps", "repairs", "error")}
                   | {"ok": bool(r.get("sql")) and not r.get("error")})
    return out


@app.get("/api/runs/{run_id}")
def run(run_id: str):
    if not _RUN_ID.match(run_id):
        raise HTTPException(400, "bad run id")
    p = RUNS_DIR / f"{run_id}.json"
    if not p.exists():
        raise HTTPException(404, "run not found")
    return json.loads(p.read_text(encoding="utf-8"))
