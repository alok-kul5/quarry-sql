"""Run the eval set through the agent and score execution accuracy.

    py -3.11 evals/run_eval.py --models qwen2.5:7b qwen3:4b --modes retrieval none

Results are appended per question to evals/results/<model>__<mode>.jsonl, so an
interrupted run picks up where it stopped. evals/results/summary.md is rebuilt
at the end of every run.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quarry.agent import Agent  # noqa: E402
from quarry.db import Database  # noqa: E402
from quarry.llm import OllamaClient  # noqa: E402
from quarry.retrieval import SchemaRetriever  # noqa: E402
from quarry.scoring import result_matches  # noqa: E402

QUESTIONS = ROOT / "evals" / "questions.jsonl"
RESULTS = ROOT / "evals" / "results"


def load_questions() -> list[dict]:
    return [json.loads(line) for line in QUESTIONS.read_text(encoding="utf-8").splitlines() if line.strip()]


def infra_failure(res) -> bool:
    """Ollama down or the machine out of memory: not the model's fault, so never scored."""
    if res.error and "Ollama" in res.error:
        return True
    return any(e["type"] == "tool_result" and "Out of Memory" in (e.get("error") or "") for e in res.trace)


def run(models: list[str], modes: list[str], limit: int | None) -> None:
    db = Database(max_rows=1000)
    retriever = SchemaRetriever(db)
    questions = load_questions()[:limit]
    gold = {q["id"]: db.query(q["sql"], max_rows=1000).rows for q in questions}
    RESULTS.mkdir(parents=True, exist_ok=True)
    for model in models:
        llm = OllamaClient(model)
        for mode in modes:
            out = RESULTS / f"{model.replace(':', '-')}__{mode}.jsonl"
            done = set()
            if out.exists():
                done = {json.loads(line)["id"] for line in out.read_text(encoding="utf-8").splitlines() if line}
            agent = Agent(db, llm, retrieval=(mode == "retrieval"), retriever=retriever, log_dir=None)
            for q in questions:
                if q["id"] in done:
                    continue
                t = time.perf_counter()
                res = None
                for attempt in range(6):
                    res = agent.run(q["q"])
                    if not infra_failure(res):
                        break
                    print(f"  infra error on q{q['id']} (attempt {attempt + 1}), waiting", flush=True)
                    time.sleep(20)
                if infra_failure(res):
                    print(f"  skipping q{q['id']} for now; rerun to resume", flush=True)
                    continue
                ok = result_matches(gold[q["id"]], res.result["rows"] if res.result else None)
                row = {"id": q["id"], "level": q["level"], "correct": ok, "sql": res.sql,
                       "answer": res.answer[:400], "steps": res.steps, "repairs": res.repairs,
                       "tool_calls": sum(1 for e in res.trace if e["type"] == "tool_call"),
                       "failed_sql": sum(1 for e in res.trace if e["type"] == "tool_result"
                                         and e.get("tool") == "run_sql" and not e["ok"]),
                       "error": res.error, "seconds": round(time.perf_counter() - t, 1)}
                with open(out, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row) + "\n")
                print(f"{model:12} {mode:9} q{q['id']:>2} {'PASS' if ok else 'fail'} "
                      f"{row['seconds']:>5}s steps={res.steps} repairs={res.repairs}", flush=True)
    summarize()


def summarize() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    lines = ["| Model | Schema retrieval | Execution accuracy | Easy | Medium | Hard | Self-repairs that worked | Avg tool calls | Avg seconds |",
             "|---|---|---|---|---|---|---|---|---|"]
    summary = []
    for f in sorted(RESULTS.glob("*__*.jsonl")):
        rows = [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line]
        if not rows:
            continue
        model, mode = f.stem.split("__")
        model = model.replace("-", ":", 1) if model.count("-") else model

        def acc(level=None):
            sel = [r for r in rows if level is None or r["level"] == level]
            return (sum(r["correct"] for r in sel), len(sel))

        c, n = acc()
        repaired = sum(1 for r in rows if r["repairs"] and r["correct"])
        s = {"model": model, "mode": mode, "correct": c, "n": n, "accuracy": round(100 * c / n, 1),
             "easy": acc("easy"), "medium": acc("medium"), "hard": acc("hard"),
             "repaired_and_correct": repaired,
             "avg_tool_calls": round(sum(r["tool_calls"] for r in rows) / n, 1),
             "avg_seconds": round(sum(r["seconds"] for r in rows) / n, 1)}
        summary.append(s)
        fmt = lambda t: f"{t[0]}/{t[1]}"  # noqa: E731
        lines.append(f"| {model} | {'on' if mode == 'retrieval' else 'off'} | **{s['accuracy']}%** ({c}/{n}) | "
                     f"{fmt(s['easy'])} | {fmt(s['medium'])} | {fmt(s['hard'])} | {repaired} | "
                     f"{s['avg_tool_calls']} | {s['avg_seconds']} |")
    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    (RESULTS / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["qwen2.5:7b", "qwen3:4b"])
    ap.add_argument("--modes", nargs="+", default=["retrieval", "none"], choices=["retrieval", "none"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--summary-only", action="store_true")
    a = ap.parse_args()
    summarize() if a.summary_only else run(a.models, a.modes, a.limit)
