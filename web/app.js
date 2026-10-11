/* UI logic. Plain DOM, no framework: one file, no build step, works offline. */

const state = {
  env: null,
  runs: [],              // summaries
  runCache: {},          // id -> full run
  selected: null,        // results tab
  compare: new Set(),
  metric: "prefill_tps",
  summaryMetrics: new Set(["prefill_tps", "decode_tps"]),
  summaryModels: new Set(),
  modelFilter: "",
  mtpFilter: "all",
  sizes: new Set(),
  models: new Set(),
  cursor: 0,
  liveResults: [],
  poller: null,
};

const SERIES_SLOTS = 8;
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const esc = (s) => String(s === null || s === undefined ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const fmtTokens = (v) => (v >= 1000 ? (v / 1000) + "k" : String(v));
const n = Charts.fmtNum;

function fmt(value, digits = 1, dash = "—") {
  if (value === null || value === undefined || Number.isNaN(value)) return dash;
  return Number(value).toFixed(digits);
}
function fmtInt(value, dash = "—") {
  if (value === null || value === undefined || Number.isNaN(value)) return dash;
  return Math.round(value).toLocaleString();
}
function fmtDuration(seconds) {
  if (!seconds && seconds !== 0) return "—";
  if (seconds < 90) return fmt(seconds, 1) + " s";
  const m = Math.floor(seconds / 60);
  if (m < 90) return m + " min";
  return (seconds / 3600).toFixed(1) + " h";
}

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch (e) { data = { error: text }; }
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

/* ---------------- metrics ---------------- */
const METRICS = {
  prefill_tps: {
    label: "Prefill speed", kind: "line", unit: "tk/s", better: "high",
    get: (r) => r.prefill_tps,
    blurb: "Prompt tokens ingested per second. The number that decides whether an agent loop feels usable, because the context is re-read on every step.",
  },
  decode_tps: {
    label: "Decode speed", kind: "line", unit: "tk/s", better: "high",
    get: (r) => r.decode_tps,
    blurb: "Output tokens generated per second, timed after the first token so prefill cost does not leak in.",
  },
  ttft_s: {
    label: "Time to first token", kind: "line", unit: "s", better: "low",
    get: (r) => r.ttft_s,
    blurb: "Wall-clock wait before anything appears: tokenisation plus the whole prefill.",
  },
  j_per_1k_prefill: {
    label: "Energy / 1k prefill", kind: "bars", unit: "J", better: "low",
    get: (r) => r.power && r.power.j_per_1k_prefill,
    blurb: "Measured GPU joules per thousand prompt tokens ingested.",
  },
  j_per_token_decode: {
    label: "Energy / output token", kind: "bars", unit: "J", better: "low",
    get: (r) => r.power && r.power.j_per_token_decode,
    blurb: "Measured GPU joules per generated token.",
  },
  prefill_gpu_w: {
    label: "GPU watts (prefill)", kind: "bars", unit: "W", better: null,
    get: (r) => r.power && r.power.prefill_gpu_w,
    blurb: "Average measured board power while ingesting the prompt.",
  },
  vram_peak_mb: {
    label: "VRAM peak", kind: "line", unit: "MB", better: null,
    get: (r) => r.vram_peak_mb,
    blurb: "Peak device memory in use. A flat ceiling means the runtime started spilling to system RAM.",
  },
};

/* colour follows the run, fixed by creation order over every known run, so
 * selecting or deselecting runs never repaints the survivors */
function runColorIndex(runId) {
  const order = state.runs.slice().sort((a, b) => (a.created_at || "").localeCompare(b.created_at || ""));
  const idx = order.findIndex((r) => r.id === runId);
  return idx < 0 ? null : idx;
}
function runColor(runId) {
  const idx = runColorIndex(runId);
  if (idx === null || idx >= SERIES_SLOTS) return "var(--text-muted)";
  return `var(--series-${idx + 1})`;
}

/* ---------------- boot ---------------- */
async function init() {
  $$(".tab").forEach((tab) => tab.addEventListener("click", () => showPanel(tab.dataset.panel)));
  $("#theme-toggle").addEventListener("click", toggleTheme);
  $("#start-btn").addEventListener("click", startBench);
  $("#cancel-btn").addEventListener("click", cancelBench);
  $("#refresh-runs").addEventListener("click", loadRuns);
  $("#goto-results").addEventListener("click", () => {
    showPanel("results");
    const id = $("#live-card").dataset.runId;
    if (id) selectRun(id);
  });
  $$("[data-select]").forEach((btn) => btn.addEventListener("click", () => selectModels(btn.dataset.select)));
  $("#model-filter").addEventListener("change", (e) => { state.modelFilter = e.target.value; renderCompare(); });
  $("#mtp-filter").addEventListener("change", (e) => { state.mtpFilter = e.target.value; renderCompare(); });
  $$("#mtp-chips input").forEach((input) => input.addEventListener("change", () => {
    $$("#mtp-chips .chip").forEach((chip) => {
      chip.dataset.on = chip.querySelector("input").checked ? "1" : "0";
    });
    updatePlan();
  }));
  $$(".chips .chip input[type=checkbox]").forEach(syncChip);
  const saved = localStorage.getItem("gpu-bench-theme");
  if (saved) document.documentElement.dataset.theme = saved;

  try {
    state.env = await api("/api/env");
  } catch (e) {
    $("#env-banners").innerHTML = banner("err", "Cannot reach the local server", esc(e.message));
    return;
  }
  renderEnv();
  renderMetricSeg();
  await loadRuns();
  const hash = location.hash.slice(1);
  if (["summary", "run", "results", "compare"].includes(hash)) {
    showPanel(hash, false);
  } else if (state.runs.length) {
    // there is something to look at: land on it rather than on the setup form
    showPanel("summary", false);
  }
  const status = await api("/api/status");
  if (status.running) { enterRunningUi(); startPolling(); }
}

function syncChip(input) {
  const chip = input.closest(".chip");
  const apply = () => { chip.dataset.on = input.checked ? "1" : "0"; };
  input.addEventListener("change", apply);
  apply();
}

function toggleTheme() {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("gpu-bench-theme", next);
  if ($("#panel-compare").classList.contains("active")) renderCompare();
  if ($("#panel-summary").classList.contains("active")) renderSummary();
  if (state.selected) renderRunDetail(state.runCache[state.selected]);
}

function showPanel(name, pushHash = true) {
  $$(".tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.panel === name)));
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === "panel-" + name));
  if (pushHash && location.hash.slice(1) !== name) history.replaceState(null, "", "#" + name);
  if (name === "compare") renderCompare();
  if (name === "summary") renderSummary();
  Charts.hideTip();
}

function banner(kind, title, body) {
  return `<div class="banner ${kind}"><div><b>${title}</b>${body ? " — " + body : ""}</div></div>`;
}

/* ---------------- run tab: environment ---------------- */
function renderEnv() {
  const env = state.env;
  const gpu = env.gpu || {};
  const host = env.host || {};

  const gpuPill = $("#pill-gpu");
  gpuPill.querySelector(".dot").className = "dot " + (gpu.name ? "on" : "off");
  gpuPill.querySelector("span:last-child").textContent = gpu.name
    ? gpu.name.replace(/^NVIDIA\s+/, "") : "no NVIDIA GPU";
  const lmsPill = $("#pill-lms");
  lmsPill.querySelector(".dot").className = "dot " + (env.lms.server ? "on" : "off");
  lmsPill.querySelector("span:last-child").textContent = env.lms.server ? "LM Studio ready" : "LM Studio off";

  const unified = gpu.memory_architecture === "unified";
  const src = (env.power || {}).source;
  $("#machine-stats").innerHTML = [
    stat("GPU", gpu.name ? gpu.name.replace(/^NVIDIA GeForce\s+/, "") : "none",
      gpu.compute_cap ? (unified ? gpu.compute_cap : "SM " + gpu.compute_cap) : ""),
    stat(unified ? "Unified memory" : "VRAM",
      gpu.vram_total_mb ? (gpu.vram_total_mb / 1024).toFixed(0) : "—",
      unified ? "GB shared with CPU" : "GB"),
    stat("Power", (env.power || {}).readable ? "measured" : "not available",
      src && src !== "none" ? "via " + src : "speed only"),
    stat(unified ? "macOS" : "Driver", gpu.driver || "—",
      gpu.pcie_gen ? "PCIe gen " + gpu.pcie_gen : ""),
    stat("System RAM", host.ram_gb ? Math.round(host.ram_gb) : "—", "GB"),
    stat("Corpus", Math.round((env.corpus.approx_tokens || 0) / 1000), "k tokens available"),
  ].join("");
  $("#machine-sub").textContent = [host.cpu, host.cores_logical ? host.cores_logical + " threads" : "", host.os]
    .filter(Boolean).join("  ·  ");

  const banners = [];
  if (!env.lms.cli) {
    banners.push(banner("err", "The lms CLI was not found",
      "Install LM Studio, then run <code>lms bootstrap</code> once and relaunch. " +
      "Context length and GPU offload can only be set through the CLI."));
  } else if (!env.lms.server) {
    banners.push(banner("warn", "LM Studio's local server is not running",
      "It will be started automatically when the benchmark begins."));
  }
  if (!gpu.name) {
    banners.push(banner("warn", "No supported accelerator detected",
      "The benchmark still runs on CPU, but wattage and VRAM will be blank."));
  } else if ((env.power || {}).needs_sudo) {
    banners.push(banner("warn", "Watts will not be recorded",
      "macOS exposes power only through <code>powermetrics</code>, which needs administrator " +
      "rights. Quit, relaunch with <b>Launch GPU Benchmark.command</b> and answer yes to the " +
      "power prompt. Prefill and decode speed are measured either way." +
      ((env.power || {}).note ? ` <span class="dim">(${esc(env.power.note)})</span>` : "")));
  } else if (!gpu.power_readable) {
    banners.push(banner("warn", "This GPU does not report power draw",
      "Speed will be measured; watts and joules will be blank."));
  }
  if (unified) {
    banners.push(banner("info", "Unified memory",
      "There is no separate VRAM pool on this machine, so the VRAM column stays blank and " +
      "GPU-offload settings have little effect. Prefill speed, decode speed and watts are " +
      "measured exactly as they are on a discrete card, and compare directly."));
  }
  if ((env.missing_defaults || []).length) {
    banners.push(banner("info", "Not installed yet: " + env.missing_defaults.map(esc).join(", "),
      "These appear in the list automatically once LM Studio finishes downloading them."));
  }
  if (!env.models.length) {
    banners.push(banner("err", "LM Studio has no models indexed", "Download at least one model first."));
  }
  $("#env-banners").innerHTML = banners.join("");

  renderModels();
  renderSizes();
  applyLastConfig();
  updatePlan();
}

function stat(key, value, unit) {
  return `<div class="stat"><div class="k">${esc(key)}</div>
    <div class="v">${esc(value)} <span class="u">${esc(unit || "")}</span></div></div>`;
}

function renderModels() {
  const env = state.env;
  const llms = env.models.filter((m) => m.type !== "embeddings");
  if (!state.models.size) (env.default_models || []).forEach((id) => state.models.add(id));
  $("#model-list").innerHTML = llms.map((m) => {
    const on = state.models.has(m.id);
    const isDefault = (env.default_models || []).includes(m.id);
    return `<label class="model" data-on="${on ? 1 : 0}">
      <input type="checkbox" value="${esc(m.id)}" ${on ? "checked" : ""}>
      <span class="name">${esc(m.id)}</span>
      <span class="meta">
        ${isDefault ? '<span class="badge accent">default</span>' : ""}
        ${m.quantization ? `<span class="badge">${esc(m.quantization)}</span>` : ""}
        ${m.arch ? `<span class="badge">${esc(m.arch)}</span>` : ""}
        <span class="badge">ctx ${fmtTokens(m.max_context_length || 0)}</span>
      </span></label>`;
  }).join("") || '<p class="dim">No language models indexed.</p>';
  $$("#model-list input").forEach((input) => input.addEventListener("change", () => {
    if (input.checked) state.models.add(input.value); else state.models.delete(input.value);
    input.closest(".model").dataset.on = input.checked ? "1" : "0";
    updatePlan();
  }));
}

function selectModels(which) {
  const llms = state.env.models.filter((m) => m.type !== "embeddings");
  state.models = new Set(
    which === "all" ? llms.map((m) => m.id)
      : which === "defaults" ? (state.env.default_models || [])
        : []);
  renderModels();
  updatePlan();
}

function renderSizes() {
  const presets = state.env.size_presets || [512, 5000, 10000, 20000, 40000, 80000];
  if (!state.sizes.size) (state.env.defaults.sizes || []).forEach((s) => state.sizes.add(s));
  $("#size-chips").innerHTML = presets.map((p) => `
    <label class="chip" data-on="${state.sizes.has(p) ? 1 : 0}">
      <input type="checkbox" value="${p}" ${state.sizes.has(p) ? "checked" : ""}>${fmtTokens(p)}</label>`).join("");
  $$("#size-chips input").forEach((input) => input.addEventListener("change", () => {
    const v = Number(input.value);
    if (input.checked) state.sizes.add(v); else state.sizes.delete(v);
    input.closest(".chip").dataset.on = input.checked ? "1" : "0";
    updatePlan();
  }));
  // shortcuts, not a second setting: each one just ticks the chips for you
  $("#size-quick").innerHTML = presets.slice(1).map((p) =>
    `<button class="btn-sm" data-upto="${p}">up to ${fmtTokens(p)}</button>`).join("");
  $$("#size-quick button").forEach((btn) => btn.addEventListener("click", () => {
    state.sizes = new Set(presets.filter((p) => p <= Number(btn.dataset.upto)));
    renderSizes();
    updatePlan();
  }));
}

function applyLastConfig() {
  // fall back to the server's defaults, which are platform-aware (the system
  // power constants differ a lot between a desktop PC and a Mac)
  const last = state.env.last_config || state.env.defaults;
  if (!last) return;
  const set = (id, value) => { if (value !== undefined && value !== null && $(id)) $(id).value = value; };
  set("#max-tokens", last.max_tokens);
  set("#repeats", last.repeats);
  set("#offload", last.gpu_offload);
  set("#per-test-timeout", last.per_test_timeout_s);
  set("#load-timeout", last.load_timeout_s);
  set("#headroom", last.context_headroom);
  set("#other-idle", last.other_idle_w);
  set("#cpu-tdp", last.cpu_tdp_w);
  set("#power-interval", last.power_interval_ms);
  if (last.notes) $("#run-notes").value = last.notes;
  if (Array.isArray(last.mtp_modes)) {
    const want = last.mtp_modes.length > 1 ? "both" : (last.mtp_modes[0] || "off");
    const radio = $(`#mtp-chips input[value="${want}"]`);
    if (radio) { radio.checked = true; }
    $$("#mtp-chips .chip").forEach((chip) => {
      chip.dataset.on = chip.querySelector("input").checked ? "1" : "0";
    });
  }
  if (last.disable_thinking !== undefined) {
    $("#no-think").checked = !!last.disable_thinking;
    $("#no-think").closest(".chip").dataset.on = last.disable_thinking ? "1" : "0";
  }
}

function mtpModes() {
  const choice = ($("#mtp-chips input:checked") || {}).value || "off";
  return choice === "both" ? ["off", "on"] : [choice];
}

function updatePlan() {
  const modes = mtpModes();
  const tests = state.models.size * state.sizes.size * modes.length
    * Number($("#repeats").value || 1);
  const biggest = state.sizes.size ? Math.max(...state.sizes) : 0;
  $("#plan-summary").textContent = tests
    ? `${tests} test${tests === 1 ? "" : "s"} · ${state.models.size} model${state.models.size === 1 ? "" : "s"} × ${state.sizes.size} size${state.sizes.size === 1 ? "" : "s"}${modes.length > 1 ? " × MTP off+on" : (modes[0] === "on" ? " · MTP on" : "")} · largest prefill ${fmtTokens(biggest)}`
    : "Select at least one model and one size.";
  $("#start-btn").disabled = !tests;
}

/* ---------------- run control ---------------- */
function collectConfig() {
  return {
    models: [...state.models],
    sizes: [...state.sizes],
    max_tokens: Number($("#max-tokens").value) || 256,
    repeats: Number($("#repeats").value) || 1,
    gpu_offload: $("#offload").value,
    mtp_modes: mtpModes(),
    fallback_on_failure: $("#fallback").checked,
    stop_model_after_failure: $("#stop-after-fail").checked,
    warmup: $("#warmup").checked,
    disable_thinking: $("#no-think").checked,
    per_test_timeout_s: Number($("#per-test-timeout").value) || 1800,
    load_timeout_s: Number($("#load-timeout").value) || 900,
    context_headroom: Number($("#headroom").value) || 0,
    other_idle_w: Number($("#other-idle").value) || 0,
    cpu_tdp_w: Number($("#cpu-tdp").value) || 0,
    power_interval_ms: Number($("#power-interval").value) || 150,
    label: $("#run-label").value.trim(),
    notes: $("#run-notes").value.trim(),
  };
}

async function startBench() {
  $("#start-btn").disabled = true;
  try {
    const { run_id } = await api("/api/bench/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectConfig()),
    });
    state.cursor = 0;
    state.liveResults = [];
    $("#log").innerHTML = "";
    $("#live-card").dataset.runId = run_id;
    enterRunningUi();
    startPolling();
  } catch (e) {
    $("#env-banners").innerHTML = banner("err", "Could not start", esc(e.message)) + $("#env-banners").innerHTML;
    $("#start-btn").disabled = false;
  }
}

async function cancelBench() {
  $("#cancel-btn").disabled = true;
  try { await api("/api/bench/cancel", { method: "POST" }); } catch (e) { /* ignore */ }
}

function enterRunningUi() {
  $("#live-card").classList.remove("hidden");
  $("#live-results").classList.remove("hidden");
  $("#cancel-btn").classList.remove("hidden");
  $("#cancel-btn").disabled = false;
  $("#start-btn").disabled = true;
  $("#pill-power").hidden = false;
}

function leaveRunningUi() {
  $("#cancel-btn").classList.add("hidden");
  updatePlan();
}

function startPolling() {
  if (state.poller) clearInterval(state.poller);
  state.poller = setInterval(poll, 700);
  poll();
}

async function poll() {
  let data;
  try {
    data = await api("/api/events?cursor=" + state.cursor);
  } catch (e) { return; }
  state.cursor = data.cursor;
  for (const ev of data.events) {
    if (ev.kind === "log") appendLog(ev.message, ev.level);
    if (ev.kind === "result") {
      state.liveResults.push(ev.result);
      renderLiveTable();
    }
    if (ev.kind === "done") {
      clearInterval(state.poller); state.poller = null;
      leaveRunningUi();
      loadRuns().then(() => { if (ev.run_id) state.selected = ev.run_id; });
    }
  }
  renderLive(data.status);
  if (!data.status.running && state.poller) {
    clearInterval(state.poller); state.poller = null; leaveRunningUi();
  }
}

function appendLog(message, level) {
  const log = $("#log");
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 24;
  const div = document.createElement("div");
  div.className = level || "info";
  div.textContent = message;
  log.appendChild(div);
  while (log.children.length > 500) log.removeChild(log.firstChild);
  if (atBottom) log.scrollTop = log.scrollHeight;
}

function renderLive(status) {
  const live = status.live || {};
  const total = live.total || 0;
  const done = live.done || 0;
  $("#progress-bar").style.width = total ? (100 * done / total).toFixed(1) + "%" : "0%";
  const phase = status.cancelling ? "stopping…" : (live.phase || "idle");
  $("#live-phase").textContent = `${phase}${live.model ? " · " + live.model : ""}${live.size ? " @ " + fmtTokens(live.size) : ""}${total ? `  (${done}/${total})` : ""}`;

  const t = live.telemetry || {};
  if (t.gpu_w !== null && t.gpu_w !== undefined) {
    $("#pill-power").hidden = false;
    $("#pill-power").innerHTML = `<span class="num">${Math.round(t.gpu_w)}</span> W GPU`;
  }
  $("#live-stats").innerHTML = [
    stat("GPU power", t.gpu_w !== null && t.gpu_w !== undefined ? Math.round(t.gpu_w) : "—", "W measured"),
    stat("System", t.system_w_est ? Math.round(t.system_w_est) : "—", "W estimated"),
    stat("GPU load", t.gpu_util !== null && t.gpu_util !== undefined ? Math.round(t.gpu_util) : "—", "%"),
    stat("VRAM", t.vram_mb ? (t.vram_mb / 1024).toFixed(1) : "—", "GB in use"),
    stat("CPU load", t.cpu_util !== null && t.cpu_util !== undefined ? Math.round(t.cpu_util * 100) : "—", "%"),
    stat("TTFT", live.ttft_s ? fmt(live.ttft_s, 2) : "—", "s this test"),
    stat("Decoding", live.decode_tps_live ? fmt(live.decode_tps_live, 1) : "—", "tk/s live"),
    stat("Temp", t.temp_c ? Math.round(t.temp_c) : "—", "°C"),
  ].join("");
}

const STATUS_MARK = { ok: "✓", failed: "✕", skipped: "–", cancelled: "⊘" };
function statusCell(r) {
  const mark = STATUS_MARK[r.status] || "?";
  const title = r.fail_kind ? ` title="${esc(r.message || r.fail_kind)}"` : "";
  return `<span class="st st-${esc(r.status)}"${title}>${mark} ${esc(r.status === "failed" ? (r.fail_kind || "failed").replace(/_/g, " ") : r.status)}</span>`;
}

function renderLiveTable() {
  const rows = state.liveResults.map((r) => `<tr>
    <td>${esc(r.model)}</td>
    <td class="num">${fmtTokens(r.target_tokens)}</td>
    <td>${r.mtp ? '<span class="badge accent">MTP</span>' : '<span class="dim">off</span>'}</td>
    <td class="num">${fmtInt(r.prompt_tokens)}</td>
    <td>${statusCell(r)}</td>
    <td class="num">${fmt(r.prefill_tps, 0)}</td>
    <td class="num">${fmt(r.decode_tps, 1)}</td>
    <td class="num">${fmt(r.ttft_s, 2)}</td>
    <td class="num">${fmt(r.power && r.power.prefill_gpu_w, 0)}</td>
    <td class="num">${fmt(r.power && r.power.j_per_1k_prefill, 1)}</td>
    <td class="num">${fmtInt(r.vram_peak_mb)}</td>
  </tr>`).join("");
  $("#live-table").innerHTML = `<thead><tr>
    <th>Model</th><th class="num">Target</th><th>MTP</th><th class="num">Actual tk</th><th>Status</th>
    <th class="num">Prefill tk/s</th><th class="num">Decode tk/s</th><th class="num">TTFT s</th>
    <th class="num">GPU W</th><th class="num">J/1k</th><th class="num">VRAM MB</th>
    </tr></thead><tbody>${rows}</tbody>`;
}

/* ---------------- results tab ---------------- */
async function loadRuns() {
  const { runs } = await api("/api/runs");
  state.runs = runs;
  renderRunsList();
  renderCompareRuns();
  if ($("#panel-summary").classList.contains("active")) renderSummary();
  if (state.selected) selectRun(state.selected);
  else if (runs.length) selectRun(runs[0].id);
  else $("#run-detail").innerHTML = emptyState("No runs yet",
    "Configure a benchmark on the Run tab and press Start. Results are cached here, one entry per session.");
  return runs;
}

function emptyState(title, body) {
  return `<div class="empty"><h3>${esc(title)}</h3><div>${body}</div></div>`;
}

function renderRunsList() {
  if (!state.runs.length) {
    $("#runs-list").innerHTML = '<p class="dim">Nothing cached yet.</p>';
    return;
  }
  const groups = {};
  for (const run of state.runs) {
    const key = (run.gpu && run.gpu.name) || "No GPU";
    (groups[key] = groups[key] || []).push(run);
  }
  $("#runs-list").innerHTML = Object.entries(groups).map(([gpu, runs]) => `
    <div class="run-group-label">${esc(gpu.replace(/^NVIDIA\s+/, ""))}</div>
    ${runs.map((run) => `
      <button class="run" data-id="${esc(run.id)}" aria-current="${run.id === state.selected}">
        <span class="swatch" style="background:${runColor(run.id)}"></span>
        <span style="flex:1;min-width:0">
          <span class="t">${esc(run.label || run.id)}</span>
          <span class="s">${esc((run.created_at || "").replace("T", " "))} · ${run.n_results} test${run.n_results === 1 ? "" : "s"}</span>
          <span class="counts">
            <span class="badge">${run.n_ok} ok</span>
            ${run.n_failed ? `<span class="badge">${run.n_failed} failed</span>` : ""}
            ${(run.mtp_modes || []).includes("on") ? '<span class="badge accent">MTP</span>' : ""}
            ${run.status !== "completed" ? `<span class="badge">${esc(run.status)}</span>` : ""}
          </span>
        </span>
      </button>`).join("")}`).join("");
  $$("#runs-list .run").forEach((btn) => btn.addEventListener("click", () => selectRun(btn.dataset.id)));
}

async function selectRun(id) {
  state.selected = id;
  $$("#runs-list .run").forEach((b) => b.setAttribute("aria-current", String(b.dataset.id === id)));
  let run = state.runCache[id];
  if (!run || run.status === "running") {
    run = await api("/api/runs/" + encodeURIComponent(id));
    state.runCache[id] = run;
  }
  renderRunDetail(run);
}

function renderRunDetail(run) {
  if (!run) return;
  const gpu = run.gpu || {};
  const host = run.host || {};
  const cfg = run.config || {};
  const ok = (run.results || []).filter((r) => r.status === "ok");
  const bestPrefill = ok.reduce((a, r) => (r.prefill_tps > (a ? a.prefill_tps : 0) ? r : a), null);
  const bestDecode = ok.reduce((a, r) => (r.decode_tps > (a ? a.decode_tps : 0) ? r : a), null);
  const maxCtx = ok.reduce((a, r) => Math.max(a, r.prompt_tokens || 0), 0);
  const peakW = ok.reduce((a, r) => Math.max(a, (r.power && r.power.prefill_gpu_w_max) || 0), 0);

  $("#run-detail").innerHTML = `
    <div class="card" style="margin-bottom:16px">
      <header>
        <h3>${esc(run.label || run.id)}</h3><div class="spacer"></div>
        <button class="btn-sm" id="rename-run">Rename</button>
        <a class="btn-sm" style="text-decoration:none;padding:5px 10px;border:1px solid var(--border);border-radius:8px"
           href="/api/export.csv?ids=${encodeURIComponent(run.id)}">CSV</a>
        <button class="btn-sm btn-danger" id="delete-run">Delete</button>
      </header>
      <p class="hint">${esc(gpu.name || "no GPU")}${gpu.vram_total_mb ? ` · ${(gpu.vram_total_mb / 1024).toFixed(0)} GB` : ""}${gpu.driver ? ` · driver ${esc(gpu.driver)}` : ""}
        · ${esc(host.cpu || "")} · ${host.ram_gb ? Math.round(host.ram_gb) + " GB RAM" : ""}
        · ${esc((run.created_at || "").replace("T", " "))} · status ${esc(run.status)}
        ${run.notes ? `<br><b>Notes:</b> ${esc(run.notes)}` : ""}
        ${run.interrupted_note ? `<br><span class="st st-interrupted">⚠ interrupted</span> ${esc(run.interrupted_note)}` : ""}</p>
      <div class="live-stats">
        ${stat("Best prefill", bestPrefill ? fmt(bestPrefill.prefill_tps, 0) : "—", bestPrefill ? `tk/s @ ${fmtTokens(bestPrefill.target_tokens)}` : "tk/s")}
        ${stat("Best decode", bestDecode ? fmt(bestDecode.decode_tps, 1) : "—", "tk/s")}
        ${stat("Largest prefill that ran", maxCtx ? fmtTokens(Math.round(maxCtx / 1000) * 1000 || maxCtx) : "—", "tokens")}
        ${stat("Peak GPU power", peakW ? Math.round(peakW) : "—", "W measured")}
        ${stat("Idle GPU power", run.idle && run.idle.gpu_w ? fmt(run.idle.gpu_w, 1) : "—", "W baseline")}
        ${stat("Tests", `${ok.length}/${(run.results || []).length}`, "succeeded")}
      </div>
      <p class="hint" style="margin:0">Watts via <b>${esc(run.power_source || "n/a")}</b>
        ${(run.gpu || {}).memory_architecture === "unified" ? "· unified memory" : ""}
        · offload policy <b>${esc(cfg.gpu_offload || "auto")}</b>
        · MTP ${esc((cfg.mtp_modes || ["off"]).join(" + "))}
        · reasoning ${cfg.disable_thinking === false ? "on" : "off"}
        · output ${cfg.max_tokens} tokens · ${cfg.repeats}× repeats
        · system power estimated with ${cfg.other_idle_w} W base + ${cfg.cpu_tdp_w} W CPU TDP × load</p>
    </div>
    <div class="card">
      <header><h3>Measurements</h3></header>
      <div class="table-wrap">${resultsTable(run)}</div>
      ${failureNotes(run)}
    </div>`;

  $("#delete-run").addEventListener("click", async () => {
    if (!confirm(`Delete run "${run.label || run.id}" permanently?`)) return;
    await api("/api/runs/" + encodeURIComponent(run.id), { method: "DELETE" });
    delete state.runCache[run.id];
    state.compare.delete(run.id);
    state.selected = null;
    loadRuns();
  });
  $("#rename-run").addEventListener("click", async () => {
    const label = prompt("Run label", run.label || "");
    if (label === null) return;
    const notes = prompt("Notes", run.notes || "") || "";
    await api(`/api/runs/${encodeURIComponent(run.id)}/meta`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label, notes }),
    });
    delete state.runCache[run.id];
    loadRuns();
  });
}

function resultsTable(run) {
  const results = (run.results || []).slice()
    .sort((a, b) => (a.model || "").localeCompare(b.model || "") || a.target_tokens - b.target_tokens
      || (a.repeat || 0) - (b.repeat || 0));
  if (!results.length) return '<p class="dim">No measurements.</p>';
  const rows = results.map((r) => {
    const p = r.power || {};
    return `<tr>
      <td>${esc(r.model)}${r.quantization ? ` <span class="badge">${esc(r.quantization)}</span>` : ""}</td>
      <td class="num">${fmtTokens(r.target_tokens)}</td>
      <td>${r.mtp ? '<span class="badge accent">MTP</span>' : '<span class="dim">off</span>'}</td>
      <td class="num">${fmtInt(r.prompt_tokens)}</td>
      <td>${statusCell(r)}</td>
      <td class="num">${fmtInt(r.context_length)}</td>
      <td>${esc(r.gpu_offload || "—")}</td>
      <td class="num">${fmt(r.ttft_s, 2)}</td>
      <td class="num">${fmt(r.prefill_tps, 0)}</td>
      <td class="num">${fmt(r.decode_tps, 1)}</td>
      <td class="num">${fmtInt(r.completion_tokens)}</td>
      <td class="num">${fmt(p.prefill_gpu_w, 0)}</td>
      <td class="num">${fmt(p.decode_gpu_w, 0)}</td>
      <td class="num">${fmt(p.prefill_system_w_est, 0)}</td>
      <td class="num">${fmt(p.j_per_1k_prefill, 1)}</td>
      <td class="num">${fmt(p.j_per_token_decode, 2)}</td>
      <td class="num">${fmtInt(r.vram_peak_mb)}</td>
      <td class="num">${fmt(r.load_s, 1)}</td>
    </tr>`;
  }).join("");
  return `<table><thead><tr>
    <th>Model</th><th class="num">Target</th><th>MTP</th><th class="num">Actual tk</th><th>Status</th>
    <th class="num">Load ctx</th><th>Offload</th>
    <th class="num">TTFT s</th><th class="num">Prefill tk/s</th><th class="num">Decode tk/s</th>
    <th class="num">Out tk</th>
    <th class="num">GPU W pre</th><th class="num">GPU W dec</th><th class="num">Sys W est</th>
    <th class="num">J/1k pre</th><th class="num">J/tok dec</th><th class="num">VRAM MB</th><th class="num">Load s</th>
    </tr></thead><tbody>${rows}</tbody>
    <tfoot><tr><td colspan="18">GPU watts measured via nvidia-smi; system watts estimated.
      Prefill tk/s = prompt tokens ÷ time to first token. Decode excludes the first token.</td></tr></tfoot></table>`;
}

function failureNotes(run) {
  const failed = (run.results || []).filter((r) => r.status === "failed" && r.message);
  if (!failed.length) return "";
  return `<details class="adv" open><summary>Failures (${failed.length})</summary><div>
    ${failed.map((r) => `<p class="hint"><b>${esc(r.model)} @ ${fmtTokens(r.target_tokens)}${r.mtp ? " +MTP" : ""}</b>
      — ${esc((r.fail_kind || "").replace(/_/g, " "))}: ${esc(r.message)}</p>`).join("")}
  </div></details>`;
}

/* ---------------- summary tab ----------------
 *
 * One figure, every selected model x setup x measure overlaid. Three encodings
 * carry the three dimensions, so identity never depends on colour alone and the
 * eight categorical hues are never cycled:
 *
 *   colour = model     (the dimension usually being compared here)
 *   dash   = measure   (prefill solid, decode dashed, ...)
 *   marker = setup     (circle, square, triangle, diamond)
 *
 * Each encoding indexes a fixed order rather than the current selection, so
 * toggling one series never restyles the survivors.
 */

function metricsByUnit(unit) {
  return Object.keys(METRICS).filter((k) => METRICS[k].unit === unit);
}

function dashForMetric(key) {
  const peers = metricsByUnit(METRICS[key].unit);
  return Charts.DASHES[peers.indexOf(key) % Charts.DASHES.length];
}

function markerForRun(runId) {
  const idx = runColorIndex(runId);
  return Charts.MARKERS[(idx === null ? 0 : idx) % Charts.MARKERS.length];
}

function allModelsOf(runs) {
  return [...new Set(runs.flatMap((r) => (r.results || []).map((x) => x.model)))].sort();
}

function modelColor(model, allModels) {
  const idx = allModels.indexOf(model);
  return idx < 0 || idx >= SERIES_SLOTS
    ? resolveColor("var(--text-muted)")
    : resolveColor(`var(--series-${idx + 1})`);
}

function renderSummaryControls(allModels) {
  $("#summary-metrics").innerHTML = Object.entries(METRICS).map(([key, m]) => `
    <label class="chip" data-on="${state.summaryMetrics.has(key) ? 1 : 0}">
      <input type="checkbox" value="${key}" ${state.summaryMetrics.has(key) ? "checked" : ""}>
      ${esc(m.label)} <span class="dim">${esc(m.unit)}</span></label>`).join("");
  $$("#summary-metrics input").forEach((input) => input.addEventListener("change", () => {
    if (input.checked) state.summaryMetrics.add(input.value);
    else state.summaryMetrics.delete(input.value);
    input.closest(".chip").dataset.on = input.checked ? "1" : "0";
    renderSummary();
  }));

  $("#summary-runs").innerHTML = state.runs.map((run) => `
    <label class="chip" data-on="${state.compare.has(run.id) ? 1 : 0}">
      <input type="checkbox" value="${esc(run.id)}" ${state.compare.has(run.id) ? "checked" : ""}>
      ${esc(run.label || run.id)}</label>`).join("")
    || '<p class="dim">No runs cached yet.</p>';
  $$("#summary-runs input").forEach((input) => input.addEventListener("change", () => {
    if (input.checked) state.compare.add(input.value); else state.compare.delete(input.value);
    input.closest(".chip").dataset.on = input.checked ? "1" : "0";
    renderCompareRuns();
    renderSummary();
  }));

  $("#summary-models").innerHTML = allModels.map((m) => `
    <label class="chip" data-on="${state.summaryModels.has(m) ? 1 : 0}">
      <input type="checkbox" value="${esc(m)}" ${state.summaryModels.has(m) ? "checked" : ""}>
      <i style="width:10px;height:10px;border-radius:3px;display:inline-block;background:${modelColor(m, allModels)}"></i>
      ${esc(m)}</label>`).join("") || '<p class="dim">No models in these runs.</p>';
  $$("#summary-models input").forEach((input) => input.addEventListener("change", () => {
    if (input.checked) state.summaryModels.add(input.value);
    else state.summaryModels.delete(input.value);
    input.closest(".chip").dataset.on = input.checked ? "1" : "0";
    renderSummary();
  }));
}

function encodingLegend(models, allModels, metricKeys, runs) {
  const parts = [];
  parts.push(`<div class="legend-group"><span class="legend-key">Model</span>
    ${models.map((m) => `<span><i style="background:${modelColor(m, allModels)}"></i>${esc(m)}</span>`).join("")}
  </div>`);
  if (metricKeys.length > 1) {
    parts.push(`<div class="legend-group"><span class="legend-key">Measure</span>
      ${metricKeys.map((k) => `<span><svg width="26" height="10" aria-hidden="true">
        <line x1="1" y1="5" x2="25" y2="5" stroke="currentColor" stroke-width="2"
              stroke-dasharray="${dashForMetric(k) || ""}"/></svg>${esc(METRICS[k].label)}</span>`).join("")}
    </div>`);
  }
  if (runs.length > 1) {
    const glyph = { circle: "●", square: "■", triangle: "▲", diamond: "◆" };
    parts.push(`<div class="legend-group"><span class="legend-key">Setup</span>
      ${runs.map((r) => `<span><b style="font-size:13px">${glyph[markerForRun(r.id)] || "●"}</b>${esc(r.label || r.id)}</span>`).join("")}
    </div>`);
  }
  return `<div class="legend-stack">${parts.join("")}</div>`;
}

async function renderSummary() {
  const body = $("#summary-body");
  const ids = [...state.compare];
  if (!state.runs.length) {
    $("#summary-runs").innerHTML = '<p class="dim">No runs cached yet.</p>';
    body.innerHTML = emptyState("Nothing to summarise yet",
      "Run a benchmark first. This tab then overlays every model, setup and measure you tick.");
    return;
  }
  await ensureRuns(ids);
  const runs = ids.map((id) => state.runCache[id]).filter(Boolean);
  const allModels = allModelsOf(runs);
  renderSummaryControls(allModels);
  $("#summary-export").href = "/api/export.csv?ids=" + ids.map(encodeURIComponent).join(",");

  if (!runs.length) {
    body.innerHTML = emptyState("Pick at least one setup", "Tick a run above.");
    return;
  }
  const models = allModels.filter((m) => !state.summaryModels.size || state.summaryModels.has(m));
  const metricKeys = Object.keys(METRICS).filter((k) => state.summaryMetrics.has(k));
  if (!metricKeys.length || !models.length) {
    body.innerHTML = emptyState("Pick at least one measure",
      "Tick Prefill speed and Decode speed to see them overlaid — they share a unit, so they land on one chart.");
    return;
  }

  // one chart per unit: two scales on one axis would fabricate a relationship
  const units = [];
  for (const key of metricKeys) {
    const unit = METRICS[key].unit;
    const found = units.find((u) => u.unit === unit);
    if (found) found.keys.push(key);
    else units.push({ unit, keys: [key] });
  }

  body.innerHTML = units.map((u, i) => `
    <div class="card" style="margin-bottom:16px">
      <header><h3>${esc(u.keys.map((k) => METRICS[k].label).join("  ·  "))}</h3>
        <div class="spacer"></div><span class="dim" style="font-size:12px">${esc(u.unit)}</span>
      </header>
      <div id="summary-legend-${i}"></div>
      <div class="plot" id="summary-plot-${i}" style="margin-top:4px"></div>
      <p class="hint" id="summary-note-${i}" style="margin:8px 0 0"></p>
    </div>`).join("");

  units.forEach((u, i) => {
    $(`#summary-legend-${i}`).innerHTML = encodingLegend(models, allModels, u.keys, runs);
    const series = [];
    const sizes = new Set();
    for (const key of u.keys) {
      const metric = METRICS[key];
      for (const run of runs) {
        for (const mtp of (state.mtpFilter === "all" ? [false, true] : [state.mtpFilter === "on"])) {
          for (const model of models) {
            const pts = pointsFor(run, model, metric, mtp);
            if (!pts.length) continue;
            pts.forEach((p) => sizes.add(p.x));
            const bits = [model, metric.label];
            if (runs.length > 1) bits.push(run.label || run.id);
            if (mtp) bits.push("MTP");
            series.push({
              id: `${run.id}|${model}|${key}|${mtp}`,
              name: bits.join("  ·  "),
              color: modelColor(model, allModels),
              dash: mtp ? "2 2" : dashForMetric(key),
              marker: markerForRun(run.id),
              points: pts.map((p) => ({ ...p, unit: metric.unit })),
              failures: u.keys.indexOf(key) === 0 ? failuresFor(run, model, mtp) : [],
            });
          }
        }
      }
    }
    const plot = $(`#summary-plot-${i}`);
    Charts.line(plot, {
      unit: u.unit,
      xLabel: "prefill tokens",
      yLabel: u.unit,
      yScale: "auto",
      xTicks: [...sizes].sort((a, b) => a - b),
      height: Math.max(300, 220 + series.length * 6),
      ariaLabel: u.keys.map((k) => METRICS[k].label).join(", "),
      series,
    });
    const values = series.flatMap((s) => s.points.map((p) => p.y)).filter((v) => v > 0);
    const spread = values.length ? Math.max(...values) / Math.min(...values) : 1;
    $(`#summary-note-${i}`).innerHTML = series.length
      ? `${series.length} series. ` + (spread >= 40
        ? "A log scale is in use because these measures span " + Math.round(spread) +
          "× — on a linear axis the smaller one would sit flat against the baseline."
        : "Linear scale.") +
        " ✕ on the baseline marks a size that failed; hover any point for the exact figure."
      : "No measurements for this combination.";
  });
}

/* ---------------- compare tab ---------------- */
function renderMetricSeg() {
  $("#metric-seg").innerHTML = Object.entries(METRICS).map(([key, m]) =>
    `<button data-metric="${key}" aria-pressed="${key === state.metric}">${esc(m.label)}</button>`).join("");
  $$("#metric-seg button").forEach((btn) => btn.addEventListener("click", () => {
    state.metric = btn.dataset.metric;
    $$("#metric-seg button").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
    renderCompare();
  }));
}

function renderCompareRuns() {
  $("#compare-runs").innerHTML = state.runs.map((run) => `
    <label class="chip" data-on="${state.compare.has(run.id) ? 1 : 0}">
      <input type="checkbox" value="${esc(run.id)}" ${state.compare.has(run.id) ? "checked" : ""}>
      <i style="width:10px;height:10px;border-radius:3px;background:${runColor(run.id)};display:inline-block"></i>
      ${esc(run.label || run.id)}
    </label>`).join("") || '<p class="dim">No runs cached yet.</p>';
  $$("#compare-runs input").forEach((input) => input.addEventListener("change", async () => {
    if (input.checked) state.compare.add(input.value); else state.compare.delete(input.value);
    input.closest(".chip").dataset.on = input.checked ? "1" : "0";
    await renderCompare();
  }));
  if (!state.compare.size && state.runs.length) {
    state.runs.slice(0, 3).forEach((r) => state.compare.add(r.id));
    renderCompareRuns();
  }
}

async function ensureRuns(ids) {
  await Promise.all(ids.map(async (id) => {
    if (!state.runCache[id]) state.runCache[id] = await api("/api/runs/" + encodeURIComponent(id));
  }));
}

async function renderCompare() {
  const body = $("#compare-body");
  const ids = [...state.compare];
  if (!ids.length) {
    body.innerHTML = emptyState("Pick at least one run",
      "Each run is one card in one session. Benchmark the 1070 Ti, swap the card, run it again — then both appear here side by side.");
    return;
  }
  await ensureRuns(ids);
  const runs = ids.map((id) => state.runCache[id]).filter(Boolean);
  const metric = METRICS[state.metric];

  const models = [...new Set(runs.flatMap((r) => (r.results || []).map((x) => x.model)))].sort();
  const filter = $("#model-filter");
  const keep = state.modelFilter;
  filter.innerHTML = '<option value="">All models</option>' +
    models.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join("");
  filter.value = models.includes(keep) ? keep : "";
  state.modelFilter = filter.value;
  const shown = state.modelFilter ? [state.modelFilter] : models;

  const tracks = tracksFor(runs);
  const legend = `<div class="legend">${tracks.map((t) => {
    const gpuName = ((t.run.gpu || {}).name || "").replace(/^NVIDIA\s+/, "");
    // only name the card when the run label does not already say it
    const showGpu = gpuName && !(t.run.label || "").toLowerCase().includes(gpuName.toLowerCase());
    return `<span><i style="background:${t.color}${t.mtp ? ";outline:1px dashed var(--text-muted);outline-offset:2px" : ""}"></i>${esc(t.name)}
      ${showGpu ? `<span class="dim">· ${esc(gpuName)}</span>` : ""}</span>`;
  }).join("")}</div>`;

  // Energy and wattage are only comparable when they came from the same sensor:
  // nvidia-smi reports board power for the card alone, powermetrics reports SoC
  // rails. Speed metrics are measured identically everywhere.
  const POWER_METRICS = ["j_per_1k_prefill", "j_per_token_decode", "prefill_gpu_w"];
  const sources = [...new Set(runs.map((r) => r.power_source).filter(Boolean))];
  const mixedPower = POWER_METRICS.includes(state.metric) && sources.length > 1;
  const note = mixedPower
    ? banner("warn", "Mixed power sources: " + sources.map(esc).join(" vs "),
      "<code>nvidia-smi</code> measures the graphics card's board power alone; " +
      "<code>powermetrics</code> measures the SoC's GPU and CPU rails. These are different " +
      "boundaries, so treat the gap as indicative, not exact — the estimated system watts " +
      "column is the fairer cross-platform figure. Speed metrics are unaffected.")
    : "";

  body.innerHTML = `
    <div class="card" style="margin-bottom:16px">
      <header><h3>${esc(metric.label)}</h3></header>
      <p class="hint">${esc(metric.blurb)}
        ${metric.better ? `Lower is better.`.replace("Lower", metric.better === "high" ? "Higher" : "Lower") : ""}</p>
      ${note}
      ${legend}
      <div class="facets" id="facet-grid"></div>
    </div>
    <div class="card">
      <header><h3>${esc(metric.label)} by model and prefill size</h3></header>
      <div class="table-wrap" id="compare-table"></div>
    </div>`;

  const grid = $("#facet-grid");
  for (const model of shown) {
    const facet = document.createElement("div");
    facet.className = "facet";
    const sub = modelSubtitle(runs, model);
    facet.innerHTML = `<h4>${esc(model)}</h4><div class="sub">${esc(sub)}</div><div class="plot"></div>`;
    grid.appendChild(facet);
    const plot = $(".plot", facet);
    const sizes = [...new Set(tracks.flatMap((t) => (t.run.results || [])
      .filter((x) => x.model === model && !!x.mtp === !!t.mtp)
      .map((x) => x.target_tokens)))].sort((a, b) => a - b);

    if (metric.kind === "line") {
      Charts.line(plot, {
        unit: metric.unit, xLabel: "prefill tokens", yLabel: metric.unit,
        xTicks: sizes, height: 250,
        ariaLabel: `${metric.label} for ${model}`,
        series: tracks.map((t) => ({
          id: t.key, name: t.name, color: t.color, dash: t.dash,
          points: pointsFor(t.run, model, metric, t.mtp),
          failures: failuresFor(t.run, model, t.mtp),
        })),
      });
    } else {
      Charts.bars(plot, {
        unit: metric.unit, yLabel: metric.unit, xLabel: "prefill tokens", height: 250,
        ariaLabel: `${metric.label} for ${model}`,
        groups: sizes.map((size) => ({
          label: fmtTokens(size),
          bars: tracks.map((t) => {
            const r = pick(t.run, model, size, t.mtp);
            return {
              id: t.key, name: t.name, color: t.color, dash: t.dash,
              value: r && r.status === "ok" ? (metric.get(r) || 0) : 0,
              note: r && r.status !== "ok" ? (r.fail_kind || r.status || "").replace(/_/g, " ") : null,
            };
          }),
        })),
      });
    }
  }
  $("#compare-table").innerHTML = compareTable(tracks, shown, metric);
  $("#export-csv").href = "/api/export.csv?ids=" + ids.map(encodeURIComponent).join(",");
}

function resolveColor(cssVar) {
  const match = /^var\((--[a-z0-9-]+)\)$/i.exec(cssVar);
  if (!match) return cssVar;
  return getComputedStyle(document.documentElement).getPropertyValue(match[1]).trim() || "#888";
}

function pick(run, model, size, mtp) {
  const matches = (run.results || []).filter((r) => r.model === model
    && r.target_tokens === size && !!r.mtp === !!mtp);
  const ok = matches.filter((r) => r.status === "ok");
  if (!ok.length) return matches[0] || null;
  // several repeats: report the median so one hiccup does not define the point
  const sorted = ok.slice().sort((a, b) => (a.prefill_tps || 0) - (b.prefill_tps || 0));
  return sorted[Math.floor(sorted.length / 2)];
}

function pointsFor(run, model, metric, mtp) {
  const sizes = [...new Set((run.results || []).filter((r) => r.model === model
    && !!r.mtp === !!mtp).map((r) => r.target_tokens))].sort((a, b) => a - b);
  const out = [];
  for (const size of sizes) {
    const r = pick(run, model, size, mtp);
    if (!r || r.status !== "ok") continue;
    const value = metric.get(r);
    if (value === null || value === undefined) continue;
    const note = [
      r.prompt_tokens ? `${fmtInt(r.prompt_tokens)} actual tokens` : "",
      r.gpu_offload && r.gpu_offload !== "auto" ? `offload ${r.gpu_offload}` : "",
      r.vram_peak_mb ? `${fmtInt(r.vram_peak_mb)} MB VRAM` : "",
    ].filter(Boolean).join(" · ");
    out.push({ x: size, y: value, note });
  }
  return out;
}

function failuresFor(run, model, mtp) {
  return (run.results || [])
    .filter((r) => r.model === model && !!r.mtp === !!mtp
      && (r.status === "failed" || r.status === "skipped")
      && r.fail_kind !== "mtp_unsupported")
    .map((r) => ({ x: r.target_tokens, reason: (r.fail_kind || r.status || "").replace(/_/g, " ") }));
}

/* A track is one line/bar colour group: a run in one MTP mode. Colour still
 * follows the run, so MTP is distinguished by a dashed stroke and by its name
 * rather than by stealing a second hue from the categorical order. */
function tracksFor(runs) {
  const modes = state.mtpFilter === "all" ? [false, true] : [state.mtpFilter === "on"];
  const tracks = [];
  for (const run of runs) {
    for (const mtp of modes) {
      const rows = (run.results || []).filter((r) => !!r.mtp === !!mtp);
      const meaningful = rows.some((r) => r.status === "ok" || r.fail_kind !== "mtp_unsupported");
      if (!rows.length || !meaningful) continue;
      const bothShown = modes.length > 1;
      tracks.push({
        run, mtp,
        key: run.id + (mtp ? "::mtp" : ""),
        name: (run.label || run.id) + (mtp ? "  · MTP" : (bothShown ? "  · no MTP" : "")),
        color: resolveColor(runColor(run.id)),
        dash: mtp ? "6 4" : null,
      });
    }
  }
  return tracks;
}

function modelSubtitle(runs, model) {
  for (const run of runs) {
    const hit = (run.results || []).find((r) => r.model === model && r.quantization);
    if (hit) return [hit.quantization, hit.params, hit.architecture].filter(Boolean).join(" · ");
  }
  return "";
}

function compareTable(tracks, models, metric) {
  const sizes = [...new Set(tracks.flatMap((t) => (t.run.results || [])
    .filter((x) => !!x.mtp === !!t.mtp).map((x) => x.target_tokens)))].sort((a, b) => a - b);
  const baseline = tracks[0];
  const head = `<thead><tr><th>Model</th><th class="num">Prefill</th>
    ${tracks.map((t) => `<th class="num">${esc(t.name)}</th>`).join("")}
    ${tracks.length > 1 ? `<th class="num">best vs ${esc(baseline.name)}</th>` : ""}
    </tr></thead>`;
  const rows = [];
  for (const model of models) {
    for (const size of sizes) {
      const cells = tracks.map((t) => {
        const r = pick(t.run, model, size, t.mtp);
        if (!r) return { html: '<td class="num dim">—</td>', value: null };
        if (r.status !== "ok") {
          return {
            html: `<td class="num"><span class="st st-${esc(r.status)}" title="${esc(r.message || "")}">${STATUS_MARK[r.status] || "?"} ${esc((r.fail_kind || r.status).replace(/_/g, " "))}</span></td>`,
            value: null,
          };
        }
        const value = metric.get(r);
        return {
          html: `<td class="num">${value === null || value === undefined ? "—" : n(value)}</td>`,
          value: value === null || value === undefined ? null : value,
        };
      });
      if (cells.every((c) => c.value === null && !c.html.includes("st-"))) continue;
      let delta = "";
      if (tracks.length > 1) {
        const base = cells[0].value;
        // compare the baseline against the best of the others, so the column
        // stays meaningful when a card fails at a size the baseline survived
        const others = cells.slice(1).map((c) => c.value).filter((v) => v !== null && v !== undefined);
        const best = others.length
          ? (metric.better === "low" ? Math.min(...others) : Math.max(...others))
          : null;
        if (base && best) {
          const ratio = metric.better === "low" ? base / best : best / base;
          const pct = (ratio - 1) * 100;
          delta = `<td class="num">${pct >= 0 ? "+" : ""}${pct.toFixed(0)}%</td>`;
        } else {
          delta = '<td class="num dim">—</td>';
        }
      }
      rows.push(`<tr><td>${esc(model)}</td><td class="num">${fmtTokens(size)}</td>
        ${cells.map((c) => c.html).join("")}${delta}</tr>`);
    }
  }
  if (!rows.length) return '<p class="dim">No overlapping measurements.</p>';
  return `<table>${head}<tbody>${rows.join("")}</tbody>
    <tfoot><tr><td colspan="${2 + tracks.length + (tracks.length > 1 ? 1 : 0)}">
      Values in ${esc(metric.unit)}.${metric.better ? ` ${metric.better === "high" ? "Higher" : "Lower"} is better.` : ""}
      ${tracks.length > 1 ? `The last column compares the best other column against ${esc(baseline.name)}, oriented so positive always means better.` : ""}
      Repeats are reduced to the median. Columns marked · MTP used MTP speculative decoding.</td></tr></tfoot></table>`;
}

init();
