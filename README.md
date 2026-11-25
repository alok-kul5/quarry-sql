<p align="center"><img src="docs/brand/mark.svg" width="84" alt="Quarry mark"></p>

<h1 align="center">Quarry</h1>

<p align="center"><b>A text-to-SQL analyst agent that runs on a laptop GPU.</b><br>
Ask a business question about 3 million real NYC taxi trips. A local model explores the schema, writes DuckDB SQL,
fixes its own errors, charts the result and shows every step it took.</p>

<p align="center">
<a href="https://github.com/alok-kul5/quarry-sql/actions/workflows/ci.yml"><img src="https://github.com/alok-kul5/quarry-sql/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
<img src="https://img.shields.io/badge/python-3.11-1d2429" alt="Python 3.11">
<img src="https://img.shields.io/badge/LLM-Ollama%20(local)-e8ae5c" alt="Ollama">
<img src="https://img.shields.io/badge/db-DuckDB%20read--only-86c2a1" alt="DuckDB">
</p>

![Quarry answering a question, with the generated SQL, a chart and the live trace](docs/screens/answer.png)

## What it does

- **Tool-using agent loop.** The model gets five tools: `list_tables`, `describe_table`, `sample_rows`,
  `run_sql` and `make_chart`. It decides which to call, through Ollama's native tool calling.
- **Self-repair.** When a query fails, the DuckDB error goes back to the model as a tool result, together with the
  binder's "candidate bindings" hint. The model fixes the SQL and tries again, up to an 8-step budget.
- **Schema retrieval.** BM25 over column names and descriptions, plus an index of real categorical values. A question
  that says "JFK" finds `zones.zone = 'JFK Airport'`, so the model never has to guess a string literal.
- **Guardrails** that do not trust the model or the data (see [Guardrails](#guardrails)).
- **Trace view and run logs.** Every step streams to the UI over Server-Sent Events and is saved as JSON in `runs/`.
  Old runs can be replayed from the Run log panel.
- **Eval harness.** 50 questions with gold SQL, scored by execution accuracy, for two local models with and without
  retrieval.
- **MCP server.** The same guarded tools are exposed over MCP (`python -m quarry.mcp_server`), so any MCP client can use them.

## Architecture

```mermaid
flowchart LR
    U([Browser UI]) -- "POST /api/ask" --> API[FastAPI]
    API -- "SSE trace events" --> U
    API --> A{{Agent loop<br/>max 8 steps}}
    R[Schema retriever<br/>BM25 + value index] -- "relevant columns,<br/>matching values" --> A
    A <-- "chat + tool specs" --> O[(Ollama<br/>qwen2.5:7b / qwen3:4b)]
    A -- "tool call" --> T[Toolbox]
    T -- "sanitized JSON<br/>wrapped as untrusted_data" --> A
    T --> G[sqlglot guard<br/>SELECT only, table allowlist,<br/>no file or settings functions]
    G --> D[(DuckDB<br/>read_only, no external access,<br/>timeout, row cap, 1 GB memory)]
    A --> L[[runs/*.json]]
    M[MCP server] --> T
    E[Eval harness<br/>50 gold questions] --> A
```

| Piece | File | Notes |
|---|---|---|
| Agent loop | [`quarry/agent.py`](quarry/agent.py) | Tool calls, repair, a nudge if the model answers without data, SQL pulled from prose as a fallback |
| Tools | [`quarry/tools.py`](quarry/tools.py) | Model sees at most 20 sanitized rows; the UI gets the full result |
| Guard | [`quarry/guard.py`](quarry/guard.py) | sqlglot AST walk, not regex |
| Database | [`quarry/db.py`](quarry/db.py) | Read-only connection, interrupt-based timeout, `fetchmany` row cap |
| Retrieval | [`quarry/retrieval.py`](quarry/retrieval.py) | BM25 over column docs, plus a value index of low-cardinality text columns |
| Server + UI | [`quarry/server.py`](quarry/server.py), [`quarry/web/`](quarry/web) | FastAPI, SSE, vanilla JS, Chart.js |
| Evals | [`evals/`](evals) | Questions, runner, scorer ([`quarry/scoring.py`](quarry/scoring.py)) and results |

## Results

<!-- results:start -->
<!-- results:end -->

## Guardrails

The agent writes SQL that runs against real data, so every layer assumes the layer above it can fail.

| Threat | Defence |
|---|---|
| Model writes `DELETE`, `DROP`, `COPY`, `ATTACH`, `PRAGMA`, `SET` | sqlglot parses the query. Anything but a single `SELECT`/`WITH`/set operation is rejected, and so is any write node inside a CTE |
| Model reads local files (`read_csv`, `'file.csv'`, `glob`) | File and settings functions are blocklisted, and every table must be a known table or CTE. DuckDB also runs with `enable_external_access = false` |
| Guard bypass | The connection is `read_only=True` with `lock_configuration`, so even raw SQL cannot write |
| Runaway query | A timer calls `interrupt()` after 10 s. Results are capped with `fetchmany(500)`, and DuckDB memory is capped at 1 GB |
| Prompt injection planted in the data (a zone named "Ignore previous instructions...") | Tool results reach the model as JSON under `untrusted_data`. Values that look like instructions are replaced with `[withheld]` before the model sees them. Control and bidi characters are stripped and long strings are cut. The UI still shows the real value, rendered with `textContent` |
| Injection through retrieval | Retrieved data values pass through the same filter before they enter the system prompt |
| Path traversal on run logs | Run ids are matched against a strict pattern |

All of these are covered in [`tests/`](tests) (71 tests). A planted injection row in the fixture database is checked to
never reach the model's context.

## Run it

Needs Python 3.11 and [Ollama](https://ollama.com). No Docker, no API keys, nothing paid.

```bash
ollama pull qwen2.5:7b          # 4.7 GB, fits an 8 GB GPU
ollama pull qwen3:4b            # optional second model
py -3.11 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python scripts\build_db.py     # downloads ~50 MB of TLC data, builds data/quarry.duckdb
.venv\Scripts\python -m uvicorn quarry.server:app --port 8765
```

Open http://localhost:8765. Pick a model and turn schema retrieval on or off from the controls under the question box.

```bash
.venv\Scripts\python -m pytest -q                        # no GPU needed: the LLM is scripted
.venv\Scripts\python evals\run_eval.py                   # full eval, resumable
.venv\Scripts\python -m quarry.mcp_server                # MCP over stdio
```

## Data

[NYC Taxi & Limousine Commission trip records](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page) for
January 2024: yellow and green taxis, 3,021,155 trips, joined to the 265 TLC taxi zones and to lookup tables for
vendors, payment types and rate codes. The build script adds column descriptions as DuckDB comments, which the agent
reads through `describe_table` and the retriever indexes.

| Table | Rows | What it is |
|---|---|---|
| `trips` | 3,021,155 | One row per trip: times, distance, zones, fare, tip, tolls, total |
| `zones` | 265 | Zone name, borough and service zone |
| `payment_types` | 7 | Credit card, cash, dispute, ... |
| `rate_codes` | 7 | Standard, JFK, Newark, negotiated, ... |
| `vendors` | 3 | Technology provider that logged the trip |

## Screens

| Live trace with a self-repair | Schema browser |
|---|---|
| ![Trace](docs/screens/repair.png) | ![Schema](docs/screens/schema.png) |

<p align="center"><sub>Built by <a href="https://alok.is-a.dev">Alok Kulkarni</a> · part of a portfolio of projects at <a href="https://github.com/alok-kul5">github.com/alok-kul5</a></sub></p>
