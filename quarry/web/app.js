// Quarry UI. Every value from the database or the model is set with textContent, never innerHTML.
const $ = (s) => document.querySelector(s);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = String(text);
  return n;
};
const fmt = (v) => typeof v === "number"
  ? (Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 2 }))
  : v === null ? "null" : String(v);

let chart = null;
let busy = false;

// ---------- boot ----------
async function boot() {
  const [health, examples] = await Promise.all([
    fetch("/api/health").then((r) => r.json()).catch(() => ({ models: [] })),
    fetch("/api/examples").then((r) => r.json()).catch(() => []),
  ]);
  const status = $("#status");
  const sel = $("#model");
  if (health.models && health.models.length) {
    status.classList.add("ok");
    status.querySelector("b").textContent = `Ollama · ${health.models.length} models`;
    for (const m of health.models.filter((m) => !/embed/.test(m))) {
      const o = el("option", "", m); o.value = m;
      if (m === health.default_model) o.selected = true;
      sel.append(o);
    }
  } else {
    status.classList.add("bad");
    status.querySelector("b").textContent = "Ollama offline";
    const o = el("option", "", health.default_model || "qwen2.5:7b"); sel.append(o);
  }
  const box = $("#examples");
  for (const q of examples) {
    const c = el("button", "chip", q); c.type = "button";
    c.addEventListener("click", () => { $("#q").value = q; ask(); });
    box.append(c);
  }
}

// ---------- ask ----------
$("#askForm").addEventListener("submit", (e) => { e.preventDefault(); ask(); });

async function ask() {
  const question = $("#q").value.trim();
  if (!question || busy) return;
  busy = true;
  $("#goBtn").disabled = true;
  resetWork();
  const work = $("#work");
  work.hidden = false;
  work.scrollIntoView({ behavior: "smooth", block: "start" });

  try {
    const res = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, model: $("#model").value, retrieval: $("#retrieval").checked }),
    });
    if (!res.ok) throw new Error(`server said ${res.status}`);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
        if (chunk.startsWith("data: ")) onEvent(JSON.parse(chunk.slice(6)));
      }
    }
  } catch (err) {
    showError(`Could not reach the agent: ${err.message}`);
  } finally {
    busy = false;
    $("#goBtn").disabled = false;
    document.querySelectorAll(".step.live").forEach((s) => s.classList.remove("live"));
  }
}

function resetWork() {
  $("#trace").replaceChildren();
  $("#answer").className = "answer-text";
  $("#answer").replaceChildren(el("span", "shimmer", "Reading the schema…"));
  $("#kpis").replaceChildren();
  $("#sql").replaceChildren(el("code", "", "—"));
  $("#table").replaceChildren(el("p", "muted", "No rows yet."));
  $("#resultMeta").textContent = "";
  $("#traceMeta").textContent = "";
  $("#chartCard").hidden = true;
  if (chart) { chart.destroy(); chart = null; }
}

// ---------- events ----------
const TOOL_LABEL = {
  list_tables: "List tables", describe_table: "Describe table", sample_rows: "Sample rows",
  run_sql: "Run SQL", make_chart: "Make chart",
};

function onEvent(ev) {
  document.querySelectorAll(".step.live").forEach((s) => s.classList.remove("live"));
  const t = `${(ev.t_ms / 1000 || 0).toFixed(1)}s`;
  switch (ev.type) {
    case "start": {
      const body = ev.retrieval && ev.context ? pre(ev.context) : null;
      addStep("", `Question to ${ev.model}`, t, body, ev.retrieval ? "retrieval on" : "retrieval off");
      setShimmer(ev.retrieval ? "Matching your words to the schema…" : "Exploring the tables…");
      break;
    }
    case "thought":
      addStep("", "Model note", t, el("div", "", ev.text));
      break;
    case "tool_call": {
      const args = ev.tool === "run_sql" ? highlight(ev.args.sql || "") : pre(JSON.stringify(ev.args));
      addStep("call live", TOOL_LABEL[ev.tool] || ev.tool, t, args, ev.extracted ? "from text" : null);
      if (ev.tool === "run_sql") { setShimmer("Running the query…"); showSql(ev.args.sql || ""); }
      break;
    }
    case "tool_result": {
      if (!ev.ok) {
        addStep("err", `${TOOL_LABEL[ev.tool] || ev.tool} failed`, t, pre(ev.error), ev.tool === "run_sql" ? "repairing" : null);
        if (ev.tool === "run_sql") setShimmer("The query failed. Fixing it…");
      } else if (ev.result) {
        const r = ev.result;
        addStep("ok", `${r.row_count.toLocaleString()} row${r.row_count === 1 ? "" : "s"} · ${r.elapsed_ms} ms`, t);
        if (ev.tool === "run_sql") { showTable(r); showSql(r.sql); setShimmer("Writing the answer…"); }
      } else if (ev.chart) {
        addStep("ok", `Chart: ${ev.chart.kind} of ${ev.chart.y}`, t);
        showChart(ev.chart);
      } else if (ev.tables) {
        addStep("ok", `${ev.tables.length} tables`, t, el("div", "", ev.tables.map((x) => x.table).join(", ")));
      } else if (ev.columns) {
        addStep("ok", `${ev.table}: ${ev.columns.length} columns`, t,
          el("div", "", ev.columns.map((c) => c.column).join(", ")));
      }
      break;
    }
    case "answer":
      addStep("answer", ev.fallback ? "Answer (from the result)" : "Answer", t);
      $("#answer").replaceChildren(document.createTextNode(ev.text || "No answer."));
      $("#answer").classList.toggle("long", (ev.text || "").length > 180);
      break;
    case "error":
      addStep("err", "Stopped", t, pre(ev.message));
      if (!$("#answer").textContent || $("#answer").querySelector(".shimmer")) showError(ev.message);
      break;
    case "done":
      $("#traceMeta").textContent = `${(ev.elapsed_ms / 1000).toFixed(1)}s · ${ev.steps} steps`;
      kpis(ev);
      break;
    case "final":
      if (ev.result) showTable(ev.result);
      if (ev.chart) showChart(ev.chart);
      break;
  }
}

function addStep(cls, title, time, body, tag) {
  const li = el("li", `step ${cls}`);
  li.append(el("span", "dot"));
  const head = el("div", "step-title");
  const left = el("span", "", title);
  if (tag) left.append(el("span", "tag", tag));
  head.append(left, el("small", "", time));
  li.append(head);
  if (body) { const b = el("div", "step-body"); b.append(body); li.append(b); }
  const list = $("#trace");
  list.append(li);
  list.scrollTop = list.scrollHeight;
}

const pre = (text) => el("code", "", text);
function setShimmer(text) {
  const a = $("#answer");
  if (a.querySelector(".shimmer") || !a.textContent) a.replaceChildren(el("span", "shimmer", text));
}
function showError(msg) { const a = $("#answer"); a.className = "answer-text error"; a.textContent = msg; }

function kpis(ev) {
  const k = $("#kpis");
  const add = (label, value, warn) => {
    const s = el("span", `kpi${warn ? " warn" : ""}`); s.append(el("b", "", value), document.createTextNode(` ${label}`)); k.append(s);
  };
  add("s", (ev.elapsed_ms / 1000).toFixed(1));
  add(ev.steps === 1 ? "step" : "steps", ev.steps);
  if (ev.repairs) add(ev.repairs === 1 ? "repair" : "repairs", ev.repairs, true);
  if (ev.flagged_cells) add("values withheld", ev.flagged_cells, true);
}

// ---------- SQL ----------
const KW = new Set(("select from where group by order having limit join left right inner outer full cross on as and or not " +
  "in is null case when then else end with distinct count sum avg min max round union all desc asc between like ilike " +
  "extract date_trunc cast interval filter over partition coalesce nullif true false using lateral qualify").split(" "));
function highlight(sql) {
  const code = el("code");
  const re = /(--[^\n]*)|('(?:[^']|'')*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)(\s*\()?|(\s+|[^\sA-Za-z0-9_']+)/g;
  let m;
  while ((m = re.exec(sql))) {
    if (m[1]) code.append(el("span", "tk-c", m[1]));
    else if (m[2]) code.append(el("span", "tk-s", m[2]));
    else if (m[3]) code.append(el("span", "tk-n", m[3]));
    else if (m[4]) {
      const w = m[4];
      if (KW.has(w.toLowerCase())) code.append(el("span", "tk-k", w.toUpperCase()));
      else if (m[5]) code.append(el("span", "tk-f", w));
      else code.append(document.createTextNode(w));
      if (m[5]) code.append(document.createTextNode(m[5]));
    } else code.append(document.createTextNode(m[6] || m[0]));
  }
  return code;
}
function pretty(sql) {
  return sql.replace(/\s+/g, " ").trim()
    .replace(/\s+(FROM|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|LEFT JOIN|INNER JOIN|JOIN|UNION ALL|UNION)\b/gi, "\n$1")
    .replace(/\s+(AND|OR)\b/gi, "\n  $1");
}
function showSql(sql) { $("#sql").replaceChildren(highlight(pretty(sql))); $("#sql").dataset.raw = sql; }
$("#copySql").addEventListener("click", async () => {
  const raw = $("#sql").dataset.raw; if (!raw) return;
  try { await navigator.clipboard.writeText(raw); $("#copySql").textContent = "Copied"; } catch { $("#copySql").textContent = "Copy failed"; }
  setTimeout(() => ($("#copySql").textContent = "Copy"), 1400);
});

// ---------- table ----------
function showTable(r) {
  const wrap = $("#table");
  if (!r.rows.length) { wrap.replaceChildren(el("p", "muted", "The query returned no rows.")); return; }
  const table = el("table");
  const thead = el("thead"); const hr = el("tr");
  r.columns.forEach((c) => hr.append(el("th", "", c)));
  thead.append(hr);
  const tbody = el("tbody");
  for (const row of r.rows.slice(0, 200)) {
    const tr = el("tr");
    row.forEach((v) => tr.append(el("td", typeof v === "number" ? "num" : "", fmt(v))));
    tbody.append(tr);
  }
  table.append(thead, tbody);
  wrap.replaceChildren(table);
  $("#resultMeta").textContent = `${r.row_count.toLocaleString()} row${r.row_count === 1 ? "" : "s"}${r.truncated ? " (capped)" : ""} · ${r.elapsed_ms} ms`;
}

// ---------- chart ----------
const PALETTE = ["#e8ae5c", "#8fb8e0", "#86c2a1", "#e9785c", "#c9a0dc", "#d8c47a", "#6fb3b8", "#f0a3b5"];
function showChart(c) {
  if (typeof Chart === "undefined") return;
  $("#chartCard").hidden = false;
  $("#chartTitle").textContent = c.title || `${c.y} by ${c.x}`;
  if (chart) chart.destroy();
  Chart.defaults.color = "#c9c2b6";
  Chart.defaults.font.family = "Hanken Grotesk, system-ui, sans-serif";
  const ctx = $("#chart").getContext("2d");
  const grad = ctx.createLinearGradient(0, 0, 0, 320);
  grad.addColorStop(0, "rgba(232,174,92,.95)"); grad.addColorStop(1, "rgba(196,122,54,.55)");
  const isPie = c.kind === "pie";
  const data = c.kind === "scatter"
    ? { datasets: [{ label: c.y, data: c.labels.map((x, i) => ({ x: Number(x), y: c.values[i] })), backgroundColor: "#e8ae5c" }] }
    : { labels: c.labels.map(String), datasets: [{
        label: c.y, data: c.values,
        backgroundColor: isPie ? PALETTE : c.kind === "line" ? "rgba(232,174,92,.15)" : grad,
        borderColor: isPie ? "#161b1f" : "#e8ae5c", borderWidth: isPie ? 2 : c.kind === "line" ? 2.5 : 0,
        borderRadius: c.kind === "bar" ? 8 : 0, fill: c.kind === "line", tension: .35, pointRadius: c.kind === "line" ? 3 : 0,
      }] };
  chart = new Chart(ctx, {
    type: c.kind, data,
    options: {
      responsive: true, maintainAspectRatio: false,
      animation: { duration: 900, easing: "easeOutQuart" },
      plugins: { legend: { display: isPie, position: "right" } },
      scales: isPie ? {} : {
        x: { grid: { display: false }, ticks: { maxRotation: 40, autoSkip: true } },
        y: { grid: { color: "rgba(242,237,228,.07)" }, beginAtZero: c.kind !== "scatter" },
      },
    },
  });
}

// ---------- dialogs ----------
document.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));
$("#schemaBtn").addEventListener("click", async () => {
  const body = $("#schemaBody");
  if (!body.childElementCount) {
    const tables = await fetch("/api/schema").then((r) => r.json());
    for (const t of tables) {
      const d = el("div", "tbl");
      d.append(el("h3", "", `${t.table}  ·  ${t.rows.toLocaleString()} rows`));
      if (t.description) d.append(el("p", "", t.description));
      const ul = el("ul");
      for (const c of t.columns) {
        const li = el("li"); li.append(el("code", "", c.name), document.createTextNode(`  ${c.type}${c.description ? " — " + c.description : ""}`));
        ul.append(li);
      }
      d.append(ul); body.append(d);
    }
  }
  $("#schemaDlg").showModal();
});
$("#runsBtn").addEventListener("click", async () => {
  const body = $("#runsBody");
  const runs = await fetch("/api/runs").then((r) => r.json()).catch(() => []);
  body.replaceChildren();
  if (!runs.length) body.append(el("p", "muted", "No runs yet."));
  for (const r of runs) {
    const row = el("div", "run-row");
    row.append(el("span", "run-q", r.question), el("span", "run-m", `${(r.elapsed_ms / 1000).toFixed(1)}s`),
      el("span", "run-m", `${r.model} · ${r.steps} steps${r.repairs ? ` · ${r.repairs} repairs` : ""}${r.ok ? "" : " · no result"}`),
      el("span", "run-m", r.run_id));
    row.addEventListener("click", () => { $("#runsDlg").close(); replay(r.run_id); });
    body.append(row);
  }
  $("#runsDlg").showModal();
});
async function replay(id) {
  const r = await fetch(`/api/runs/${encodeURIComponent(id)}`).then((x) => x.json());
  $("#q").value = r.question;
  const start = (r.trace || [])[0] || {};
  if (typeof start.retrieval === "boolean") $("#retrieval").checked = start.retrieval;
  if (r.model && [...$("#model").options].some((o) => o.value === r.model)) $("#model").value = r.model;
  resetWork();
  $("#work").hidden = false;
  for (const ev of r.trace) onEvent(ev);
  document.querySelectorAll(".step.live").forEach((s) => s.classList.remove("live"));
  if (r.chart) showChart(r.chart);
}

// Shareable links: #run=<id> replays a saved run, #schema opens the schema sheet.
boot().then(() => {
  const m = /^#run=([0-9]{8}-[0-9]{6}-[0-9a-f]{6})$/.exec(location.hash);
  if (m) { document.body.classList.add("compact"); replay(m[1]); }
  else if (location.hash === "#schema") $("#schemaBtn").click();
});
