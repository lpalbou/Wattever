/* UI logic. Plain DOM, no framework: one file, no build step, works offline. */

const state = {
  env: null,
  runs: [],              // summaries
  runCache: {},          // id -> full run
  selected: null,        // results tab
  compare: new Set(),
  metrics: new Set(["prefill_tps"]),
  visibleModels: null,
  colorBy: "models",
  mtpFilter: "all",
  sizes: new Set(),
  concurrencyLevels: new Set([1]),
  comparisonConcurrency: 1,
  resultView: "summary",
  compareView: "charts",
  selectionInitialized: false,
  resultsRender: 0,
  detailSignature: null,
  models: new Set(),
  metadataOverrides: {label: false, notes: false},
  cursor: 0,
  liveResults: [],
  poller: null,
  pollBusy: false,
  liveRunId: null,
  liveSignature: null,
  idleWatcher: null,
  benchmarkRunning: false,
  extension: null,
};

const CONCURRENCY_PRESETS = [...Array.from({length: 10}, (_, i) => i + 1), 15, 20, 30, 40, 50, 75, 100];
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

function browserMode() { return typeof window !== "undefined" && !!window.WatteverBrowser?.enabled; }

function updateBrowserStorageWarning(data) {
  if (!browserMode() || !data || typeof data !== "object") return;
  const source = Object.prototype.hasOwnProperty.call(data, "storage_error") ? data : data.status;
  if (!source || !Object.prototype.hasOwnProperty.call(source, "storage_error")) return;
  const node = $("#browser-storage-warning");
  if (!node) return;
  node.hidden = !source.storage_error;
  node.innerHTML = source.storage_error ? banner("warn", "Browser storage is full or unavailable", "Download your results before closing this page.") : "";
}
async function api(path, opts = {}) {
  if (browserMode()) {
    const result = await window.WatteverBrowser.request(path, opts);
    updateBrowserStorageWarning(result);
    return result;
  }
  const res = await fetch(path, opts);
  const text = await res.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch (e) { data = { error: text }; }
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

/* ---------------- metrics ---------------- */
const METRICS = {
  aggregate_completion_tps: { label: "Aggregate output throughput", kind: "line", unit: "tk/s", better: "high", get: (r) => r.aggregate_completion_tps, blurb: "Total output tokens divided by group wall time." },
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
  if (idx === null) return "var(--text-muted)";
  return idx < SERIES_SLOTS ? `var(--series-${idx + 1})` : `hsl(${Math.round(idx * 137.508) % 360} 65% 60%)`;
}

/* Field help is available on hover, keyboard focus, and touch. */
const FIELD_HELP = {
  "mtp-filter": "Compare ordinary decoding, speculative decoding, or both.",
  "comparison-concurrency": "Compare runs at the same number of simultaneous queries.",
};
function applyFieldTooltips() {
  $$("input, select, textarea").forEach((input) => {
    if (input.title) return;
    const label = input.closest("label") || (input.id ? document.querySelector(`label[for="${input.id}"]`) : null);
    const field = input.closest(".field");
    const help = field && field.querySelector("[data-help]");
    input.title = (label && label.dataset.help) || (help && help.dataset.help) || FIELD_HELP[input.id] || (input.classList.contains("comparison-concurrency") ? FIELD_HELP["comparison-concurrency"] : "Include " + ((label && label.textContent.trim()) || "this option") + " in the selection.");
    if (label && !label.title) label.title = input.title;
  });
}
function decorateHelp() {
  $$("[data-help]").forEach((label) => {
    const help = document.createElement("span");
    help.className = "field-help";
    help.tabIndex = 0;
    help.setAttribute("role", "note");
    help.setAttribute("aria-label", label.dataset.help);
    help.dataset.tooltip = label.dataset.help;
    help.textContent = "?";
    help.addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); help.focus(); });
    label.appendChild(help);
  });
  applyFieldTooltips();
  new MutationObserver(applyFieldTooltips).observe(document.body, {childList: true, subtree: true});
}

/* ---------------- boot ---------------- */
async function connectBrowser() {
  const button = $("#browser-connect-button"), status = $("#browser-connect-status");
  button.disabled = true; status.textContent = "Connecting...";
  try {
    window.WatteverBrowser.configure({endpoint: $("#browser-endpoint").value.trim(), apiKey: $("#browser-key").value,
      hardware: {gpu: $("#browser-gpu").value.trim(), cpu: $("#browser-cpu").value.trim(), ram_gb: Number($("#browser-ram").value) || null, vram_gb: Number($("#browser-vram").value) || null}});
    await window.WatteverBrowser.connect();
    $("#browser-key").value = "";
    state.env = await api("/api/env"); renderEnv();
    status.textContent = "Connected. Select models and start a benchmark.";
  } catch (error) { status.textContent = "Connection failed: " + error.message + ". Check the endpoint, CORS and local network permission."; }
  finally { button.disabled = false; }
}
function downloadBlob(content, name, type) {
  const url = URL.createObjectURL(new Blob([content], {type}));
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = name; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function browserDownload(event) {
  if (!browserMode()) return;
  const anchor = event.target.closest("a[href]");
  if (!anchor) return;
  const path = anchor.getAttribute("href");
  if (!path || !(/^\/api\/runs\/[^/]+\/download$/.test(path) || path.startsWith("/api/export.csv"))) return;
  event.preventDefault();
  try {
    const result = await api(path);
    const csv = path.startsWith("/api/export.csv");
    downloadBlob(csv ? result : JSON.stringify(result, null, 2), csv ? "wattever.csv" : "benchmark.json", csv ? "text/csv" : "application/json");
  } catch (error) { alert("Could not export: " + error.message); }
}
async function importBrowserRun(event) {
  const file = event.target.files[0];
  if (!file) return;
  const status = $("#browser-import-status");
  try {
    const run = JSON.parse(await file.text());
    await api("/api/import", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(run)});
    state.runCache = {}; state.selectionInitialized = false; await loadRuns();
    status.textContent = "Imported successfully.";
  } catch (error) { status.textContent = "Import failed: " + error.message; }
  finally { event.target.value = ""; }
}

async function init() {
  if (browserMode()) {
    $("#browser-connect").hidden = false;
    $("#browser-import").hidden = false;
    $("#browser-connect-button").addEventListener("click", connectBrowser);
    $("#browser-import-file").addEventListener("change", importBrowserRun);
    document.addEventListener("click", browserDownload);
    ["#offload", "#mtp-chips", "#load-timeout", "#headroom", "#gpu-index", "#runtime-gpu-placement", "#fallback", "#warmup", "#no-think"].forEach(selector => {
      const node = $(selector); if (node) (node.closest(".field") || node.closest("label" ) || node).hidden = true;
    });
  }
  $("#extend-close").addEventListener("click", () => $("#extend-dialog").close());
  $("#extend-dialog").addEventListener("close", () => { state.extension = null; });
  $("#extend-start").addEventListener("click", startExtension);
  decorateHelp();
  Tables.init();
  api("/api/version").then((info) => {
    $("#app-version").textContent = "v" + info.version;
  }).catch(() => { $("#app-version").textContent = "Server unavailable"; });
  $$(".tab").forEach((tab) => tab.addEventListener("click", () => showPanel(tab.dataset.panel)));
  $("#theme-toggle").addEventListener("click", toggleTheme);
  $("#clean-run").addEventListener("click", prepareCleanRun);
  $$(".comparison-concurrency").forEach((select) => {
    select.innerHTML = CONCURRENCY_PRESETS.map((value) => `<option value="${value}">${value}</option>`).join("");
    select.addEventListener("change", () => {
      state.comparisonConcurrency = Number(select.value);
      if (state.metrics.size === 1 && (state.metrics.has("prefill_tps") || state.metrics.has("aggregate_completion_tps"))) {
        state.metrics = new Set([state.comparisonConcurrency > 1 ? "aggregate_completion_tps" : "prefill_tps"]);
      }
      renderMetricSeg(); renderResultsView();
    });
  });
  $$("[data-result-view]").forEach((button) => button.addEventListener("click", () => showResultsView(button.dataset.resultView)));
  ["[data-result-view]", "[data-compare-view]"].forEach((selector) => {
    $$(selector).forEach((button, index, buttons) => button.addEventListener("keydown", (event) => {
      let next;
      if (event.key === "ArrowRight") next = (index + 1) % buttons.length;
      if (event.key === "ArrowLeft") next = (index + buttons.length - 1) % buttons.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = buttons.length - 1;
      if (next === undefined) return;
      event.preventDefault(); buttons[next].focus(); buttons[next].click();
    }));
  });
  $("#results-all-measurements").parentElement.addEventListener("toggle", renderAllMeasurements);
  $$("[data-compare-view]").forEach((button) => button.addEventListener("click", () => {
    state.compareView = button.dataset.compareView;
    $$("[data-compare-view]").forEach((item) => item.setAttribute("aria-selected", String(item === button)));
    renderResultsView();
  }));
  $("#start-btn").addEventListener("click", startBench);
  $("#cancel-btn").addEventListener("click", cancelBench);
  $("#refresh-runs").addEventListener("click", loadRuns);
  $("#goto-results").addEventListener("click", () => {
    showPanel("results");
    const id = $("#live-card").dataset.runId;
    if (id) selectRun(id);
  });
  $$("[data-select]").forEach((btn) => btn.addEventListener("click", () => selectModels(btn.dataset.select)));
  ["#max-tokens", "#repeats", "#no-think", "#offload", "#gpu-index",
    "#runtime-gpu-placement", "#fallback", "#stop-after-fail", "#warmup",
    "#per-test-timeout", "#load-timeout", "#headroom", "#context-policy"].forEach((sel) => {
    const node = $(sel);
    if (node) {
      node.addEventListener("change", updatePlan);
      if (node.type === "text" || node.type === "number") node.addEventListener("input", updatePlan);
    }
  });
  [["#run-label", "label"], ["#run-notes", "notes"]].forEach(([selector, key]) => {
    $(selector).addEventListener("input", () => { state.metadataOverrides[key] = true; });
    $(selector).addEventListener("blur", () => {
      if (!$(selector).value.trim()) {
        state.metadataOverrides[key] = false;
        updateGeneratedMetadata();
      }
    });
  });
  $("#show-all-models").addEventListener("click", () => { state.visibleModels = null; redrawResultsWithoutScrolling(); });
  $("#hide-all-models").addEventListener("click", () => { state.visibleModels = new Set(); redrawResultsWithoutScrolling(); });
  $$("[data-color-by]").forEach(button => button.addEventListener("click", () => {
    state.colorBy = button.dataset.colorBy;
    $$("[data-color-by]").forEach(item => item.setAttribute("aria-pressed", String(item === button)));
    renderResultsView();
  }));
  $("#mtp-filter").addEventListener("change", (e) => { state.mtpFilter = e.target.value; renderResultsView(); });
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
  }
  const status = await api("/api/status");
  if (status.running) { enterRunningUi(); startPolling(); }
  state.idleWatcher = setInterval(checkForActiveBenchmark, 3000);
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
  if ($("#panel-results").classList.contains("active")) renderResultsView();
}

function showPanel(name, pushHash = true) {
  if (name === "summary" || name === "compare") {
    state.resultView = name;
    name = "results";
  }
  $$(".tab").forEach((tab) => tab.setAttribute("aria-selected", String(tab.dataset.panel === name)));
  $$(".panel").forEach((panel) => panel.classList.toggle("active", panel.id === "panel-" + name));
  if (pushHash && location.hash.slice(1) !== name) history.replaceState(null, "", "#" + name);
  if (name === "results") {
    showResultsView(state.resultView);
    checkForActiveBenchmark();
  }
  Charts.hideTip();
}

function showResultsView(view) {
  if (state.resultView !== view) $$(".result-view").forEach(panel => { panel.style.minHeight = ""; });
  state.resultView = view;
  const ids = {summary: "panel-summary", table: "result-table-view", compare: "panel-compare"};
  $$("[data-result-view]").forEach((button) => button.setAttribute("aria-selected", String(button.dataset.resultView === view)));
  $$(".result-view").forEach((panel) => {
    panel.hidden = panel.id !== ids[view];
    panel.classList.toggle("active", !panel.hidden);
  });
  renderResultsView();
}

async function renderResultsView() {
  const generation = ++state.resultsRender;
  const ids = [...state.compare];
  await ensureRuns(ids);
  if (generation !== state.resultsRender) return;
  const runs = ids.map((id) => state.runCache[id]).filter(Boolean);
  const models = allModelsOf(runs);
  renderVisibleModels(models);
  const shown = shownModels(models);
  $("#summary-export").href = "/api/export.csv?ids=" + ids.map(encodeURIComponent).join(",");
  $("#summary-export").hidden = !runs.length;
  const detailSignature = JSON.stringify([state.selected, state.comparisonConcurrency, runs.map(run => [run.id, run.status, run.label, run.notes, (run.results || []).length])]);
  if (detailSignature !== state.detailSignature) {
    const selection = JSON.stringify([...state.compare]);
    const keepDetailsOpen = state.detailSelection === selection && $("#run-detail details")?.open;
    renderRunDetail(runs.find((run) => run.id === state.selected) || runs[0]);
    if (keepDetailsOpen && $("#run-detail details")) $("#run-detail details").open = true;
    state.detailSelection = selection;
    state.detailSignature = detailSignature;
  }
  if (state.resultView === "summary") await renderSummary();
  if (state.resultView === "compare") await renderCompare();
  if (state.resultView === "table") {
    const keys = selectedMetricKeys();
    $("#results-table-heading").textContent = keys.length === 1 ? METRICS[keys[0]].label : "Measurements";
    $("#results-table").innerHTML = !runs.length ? emptyState("Select a benchmark", "Choose a benchmark from the list on the left.")
      : !keys.length ? emptyState("Choose measures to display", "Select one or more measure badges above.")
      : compareTable(tracksFor(runs), shown, keys);
    renderAllMeasurements();
  }
}

function renderAllMeasurements() {
  const target = $("#results-all-measurements");
  const runs = [...state.compare].map(id => state.runCache[id]).filter(Boolean);
  target.parentElement.hidden = !runs.length;
  target.innerHTML = target.parentElement.open
    ? runs.map(run => `<h4>${esc(run.label || run.id)}</h4>${detailedResultsTable({...run, results: (run.results || []).filter(row => state.visibleModels === null || state.visibleModels.has(row.model))})}`).join("") : "";
}

function banner(kind, title, body) {
  return `<div class="banner ${kind}"><div><b>${title}</b>${body ? " — " + body : ""}</div></div>`;
}

/* ---------------- run tab: environment ---------------- */
function renderEnv() {
  const env = state.env;
  $("#context-policy-field").hidden = !env.capabilities?.context_planning;
  const gpu = env.gpu || {};
  const host = env.host || {};

  const gpuPill = $("#pill-gpu");
  gpuPill.querySelector(".dot").className = "dot " + (gpu.name ? "on" : "off");
  gpuPill.querySelector("span:last-child").textContent = gpu.name
    ? gpu.name.replace(/^NVIDIA\s+/, "") : "no NVIDIA GPU";
  const lmsPill = $("#pill-lms");
  lmsPill.querySelector(".dot").className = "dot " + (env.lms.server ? "on" : "off");
  lmsPill.querySelector("span:last-child").textContent = env.lms.server ? (browserMode() ? "Endpoint connected" : "LM Studio ready") : (browserMode() ? "Not connected" : "LM Studio off");

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
    stat("Corpus", (env.corpus || {}).approx_tokens == null ? "unknown" : Math.round(env.corpus.approx_tokens / 1000), (env.corpus || {}).approx_tokens == null ? "" : "k tokens available"),
  ].join("");
  $("#machine-name").textContent = gpu.name ? gpu.name.replace(/^NVIDIA GeForce\s+/, "") : "CPU benchmark";
  $("#machine-memory").textContent = gpu.vram_total_mb ? `${(gpu.vram_total_mb / 1024).toFixed(0)} GB ${unified ? "unified memory" : "VRAM"}` : "";
  $("#machine-sub").textContent = [host.cpu, host.cores_logical ? host.cores_logical + " threads" : "", host.os]
    .filter(Boolean).join("  ·  ");

  const banners = [];
  if (browserMode()) {
    // Browser connections are made explicitly and do not control LM Studio.
  } else if (!env.lms.cli) {
    banners.push(banner("err", "The lms CLI was not found",
      "Install LM Studio, then run <code>lms bootstrap</code> once and relaunch. " +
      "Context length and GPU offload can only be set through the CLI."));
  } else if (!env.lms.server) {
    banners.push(banner("warn", "LM Studio's local server is not running",
      "It will be started automatically when the benchmark begins."));
  }
  if (!browserMode() && !gpu.name) {
    banners.push(banner("warn", "No supported accelerator detected",
      "The benchmark still runs on CPU, but wattage and VRAM will be blank."));
  } else if (!browserMode() && (env.power || {}).needs_sudo) {
    banners.push(banner("warn", "Watts will not be recorded",
      "macOS exposes power only through <code>powermetrics</code>, which needs administrator " +
      "rights. Quit, relaunch with <b>Launch GPU Benchmark.command</b> and answer yes to the " +
      "power prompt. Prefill and decode speed are measured either way." +
      ((env.power || {}).note ? ` <span class="dim">(${esc(env.power.note)})</span>` : "")));
  } else if (!browserMode() && !gpu.power_readable) {
    banners.push(banner("warn", "This GPU does not report power draw",
      "Speed will be measured; watts and joules will be blank."));
  }
  if (!browserMode() && !env.models.length) {
    banners.push(banner("err", "LM Studio has no models indexed", "Download at least one model first."));
  }
  $("#env-banners").innerHTML = banners.join("");

  const last = env.last_config || env.defaults || {};
  const availableModels = new Set(env.models.filter((model) => model.type !== "embeddings").map((model) => model.id));
  state.models = new Set((env.last_config && Array.isArray(last.models) ? last.models : (env.default_models || [])).filter((id) => availableModels.has(id)));
  state.sizes = new Set(Array.isArray(last.sizes) ? last.sizes : (env.defaults.sizes || []));
  state.concurrencyLevels = new Set(Array.isArray(last.concurrency_levels) ? last.concurrency_levels : [1]);
  if (state.sizes.size) state.sizes = through([...new Set([...(env.size_presets || [512, 5000, 10000, 20000, 40000, 80000, 100000, 120000, 150000, 200000, 260000]), ...state.sizes])], Math.max(...state.sizes));
  if (state.concurrencyLevels.size) state.concurrencyLevels = through([...new Set([...CONCURRENCY_PRESETS, ...state.concurrencyLevels])], Math.max(...state.concurrencyLevels));
  renderHardware();
  renderModels();
  renderSizes();
  renderConcurrency();
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
  $("#model-list").innerHTML = llms.map((m) => {
    const on = state.models.has(m.id);
    const isDefault = (env.default_models || []).includes(m.id);
    return `<label class="model" data-on="${on ? 1 : 0}" title="Include ${esc(m.id)} in this benchmark">
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

function through(presets, maximum) {
  return new Set(presets.filter((value) => value <= Number(maximum)));
}

function renderSizes() {
  const presets = [...new Set([...(state.env.size_presets || [512, 5000, 10000, 20000, 40000, 80000]), 100000, 120000, 150000, 200000, 260000, ...state.sizes])].sort((a, b) => a - b);
  renderRange("#size-chips", presets, state.sizes, (value) => {
    state.sizes = through(presets, value);
    renderSizes(); updatePlan();
  }, (value) => fmtTokens(value), "prompt tokens");
}

function renderRange(selector, presets, selected, onSelect, format, unit) {
  $(selector).innerHTML = presets.map((value) => `<label class="chip" data-on="${selected.has(value) ? 1 : 0}" title="Test every smaller value through ${format(value)} ${unit}"><input type="checkbox" value="${value}" aria-label="${format(value)} ${unit}" ${selected.has(value) ? "checked" : ""}>${format(value)}</label>`).join("");
  $$(selector + " input").forEach((input) => input.addEventListener("change", () => {
    const value = Number(input.value);
    onSelect(value);
    const replacement = $$(selector + " input").find((next) => Number(next.value) === value);
    if (replacement) replacement.focus({preventScroll: true});
  }));
}

function hardwareMarkup(hw) {
  hw = hw || {};
  const cpu = hw.cpu || {}, system = hw.system || {}, os = hw.os || {};
  const gpus = hw.gpus || [];
  const board = hw.motherboard || {}, firmware = hw.firmware || {};
  return `<p class="hint">${esc([os.name, os.release, os.architecture].filter(Boolean).join(" "))} - ${esc(cpu.name || "CPU unknown")} - ${esc([system.manufacturer, system.model].filter(Boolean).join(" "))}</p>
    <p class="hint">${cpu.physical_cores != null ? `${esc(cpu.physical_cores)} physical cores / ` : ""}${esc(cpu.logical_cores ?? "unknown")} threads${system.memory_bytes ? ` - ${(system.memory_bytes / 1073741824).toFixed(1)} GiB RAM` : ""}</p>`
    + gpus.map((g, i) => `<p class="hint"><b>GPU ${i + 1}:</b> ${esc(g.name || "Unknown")} ${g.memory_bytes ? `- ${(g.memory_bytes / 1073741824).toFixed(1)} GiB` : ""} ${g.driver ? `- driver ${esc(g.driver)}` : ""} ${g.sensor_index != null ? `- sensor ${esc(g.sensor_index)}` : ""}</p>`).join("")
    + (board.model ? `<p class="hint"><b>Motherboard:</b> ${esc([board.manufacturer, board.model].filter(Boolean).join(" "))}</p>` : "")
    + (firmware.version ? `<p class="hint"><b>Firmware:</b> ${esc([firmware.vendor, firmware.version].filter(Boolean).join(" "))}</p>` : "")
    + (hw.storage || []).map((disk) => `<p class="hint"><b>Storage:</b> ${esc(disk.model || "Unknown")}${disk.capacity_bytes ? ` - ${(disk.capacity_bytes / 1000000000).toFixed(0)} GB` : ""} ${esc(disk.interface || "")}</p>`).join("");
}
function renderHardware() {
  const gpus = (state.env.hardware || {}).gpus || [];
  $("#hardware-inventory").innerHTML = (state.env.hardware?.source === "manual" ? '<p class="hint">Hardware details entered manually; not detected or measured.</p>' : "") + hardwareMarkup(state.env.hardware);
  const sensors = gpus.filter((g) => g.sensor_index != null);
  $("#gpu-index").innerHTML = sensors.length ? sensors.map((g) => `<option value="${esc(g.sensor_index)}">${esc(g.sensor_index)}: ${esc(g.name)}</option>`).join("") : '<option value="0">0 (default sensor)</option>';
}
function renderConcurrency() {
  const levels = [...new Set([...CONCURRENCY_PRESETS, ...state.concurrencyLevels])].sort((a, b) => a - b);
  renderRange("#concurrency-chips", levels, state.concurrencyLevels, (value) => {
    state.concurrencyLevels = through(levels, value);
    renderConcurrency(); updatePlan();
  }, String, "simultaneous queries");
}
function prepareCleanRun() {
  state.models = new Set(state.env.models.filter((m) => m.type !== "embeddings").map((m) => m.id));
  state.sizes = new Set((state.env.defaults || {}).sizes || [512, 5000, 10000, 20000, 40000, 80000]);
  state.concurrencyLevels = new Set([1]);
  const defaults = state.env.defaults || {};
  $("#max-tokens").value = defaults.max_tokens ?? 256;
  $("#repeats").value = defaults.repeats ?? 1;
  $("#offload").value = defaults.gpu_offload || "max";
  $("#context-policy").value = defaults.context_policy || "per_test";
  $("#no-think").checked = defaults.disable_thinking !== false;
  $("#no-think").closest(".chip").dataset.on = $("#no-think").checked ? "1" : "0";
  const mtp = defaults.mtp_modes || ["off"];
  const mode = mtp.length > 1 ? "both" : (mtp[0] || "off");
  const radio = $(`#mtp-chips input[value="${mode}"]`);
  if (radio) radio.checked = true;
  $$("#mtp-chips .chip").forEach((chip) => { chip.dataset.on = chip.querySelector("input").checked ? "1" : "0"; });
  state.metadataOverrides = {label: false, notes: false};
  ["fallback", "stop-after-fail", "warmup"].forEach((id) => {
    const key = {fallback: "fallback_on_failure", "stop-after-fail": "stop_model_after_failure", warmup: "warmup"}[id];
    $("#" + id).checked = defaults[key] !== false;
    $("#" + id).closest(".chip").dataset.on = $("#" + id).checked ? "1" : "0";
  });
  renderModels(); renderSizes(); renderConcurrency(); updatePlan();
}

function applyLastConfig() {
  // fall back to the server's defaults, which are platform-aware (the system
  // power constants differ a lot between a desktop PC and a Mac)
  const last = state.env.last_config || state.env.defaults;
  if (!last) return;
  const set = (id, value) => { if (value !== undefined && value !== null && $(id)) $(id).value = value; };
  set("#gpu-index", last.gpu_index);
  set("#runtime-gpu-placement", last.runtime_gpu_placement);
  set("#max-tokens", last.max_tokens);
  set("#repeats", last.repeats);
  set("#offload", last.gpu_offload);
  set("#per-test-timeout", last.per_test_timeout_s);
  set("#load-timeout", last.load_timeout_s);
  set("#headroom", last.context_headroom);
  set("#context-policy", last.context_policy || "per_test");
  ["fallback", "stop-after-fail", "warmup"].forEach((id) => {
    const key = {fallback: "fallback_on_failure", "stop-after-fail": "stop_model_after_failure", warmup: "warmup"}[id];
    if (last[key] !== undefined) {
      $("#" + id).checked = !!last[key];
      $("#" + id).closest(".chip").dataset.on = last[key] ? "1" : "0";
    }
  });
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

/* Guard the one thing the whole dataset rests on.
 *
 * Output length and the reasoning switch change what decode tk/s *means*, so a
 * run taken with different values cannot be compared with the ones already
 * cached — and nothing about the form makes that obvious, because the field
 * remembers whatever was used last. Say so before the benchmark runs, not
 * after, and offer to put it back in one click. */
function checkComparability() {
  if (browserMode()) { $("#comparability").innerHTML = ""; return; }
  const box = $("#comparability");
  if (!box) return;
  const prior = state.runs.filter((r) => r.n_ok > 0 && r.max_tokens);
  if (!prior.length) { box.innerHTML = ""; return; }
  const ref = prior[0];                       // most recent run with results
  const mine = { max_tokens: Number($("#max-tokens").value), no_think: $("#no-think").checked,
    offload: $("#offload").value };
  const diffs = [];
  if (ref.max_tokens && mine.max_tokens !== ref.max_tokens) {
    diffs.push(`output tokens <b>${mine.max_tokens}</b> vs <b>${ref.max_tokens}</b> in your existing runs`);
  }
  if (ref.reasoning_disabled !== undefined && ref.reasoning_disabled !== null
      && mine.no_think !== !!ref.reasoning_disabled) {
    diffs.push(`reasoning <b>${mine.no_think ? "off" : "on"}</b> vs <b>${ref.reasoning_disabled ? "off" : "on"}</b>`);
  }
  if (ref.gpu_offload && mine.offload !== ref.gpu_offload) {
    diffs.push(`GPU offload <b>${esc(mine.offload)}</b> vs <b>${esc(ref.gpu_offload)}</b>`);
  }
  if (!diffs.length) { box.innerHTML = ""; return; }
  box.innerHTML = `<div class="banner warn"><div>
    <b>Different settings from “${esc(ref.label || ref.id)}”</b> —
    ${diffs.join(", ")}. Match the settings for a more useful comparison.
    <div style="margin-top:9px"><button class="btn-sm" id="match-prior">Match existing runs</button></div>
  </div></div>`;
  $("#match-prior").addEventListener("click", () => {
    if (ref.max_tokens) $("#max-tokens").value = ref.max_tokens;
    if (ref.gpu_offload) $("#offload").value = ref.gpu_offload;
    if (ref.reasoning_disabled !== undefined && ref.reasoning_disabled !== null) {
      $("#no-think").checked = !!ref.reasoning_disabled;
      $("#no-think").closest(".chip").dataset.on = ref.reasoning_disabled ? "1" : "0";
    }
    updatePlan();
  });
}

function generatedMetadata(env, cfg) {
  const hw = env.hardware || {}, host = env.host || {};
  const memory = (value, unit) => Number.isFinite(value) && value > 0 ? `${Number(value.toFixed(1))} GB ${unit}` : "";
  const cpu = (hw.cpu || {}).name || host.cpu || "";
  const ram = memory((hw.system || {}).memory_bytes ? hw.system.memory_bytes / 1073741824 : host.ram_gb, "RAM");
  const cards = (hw.gpus || []).length ? hw.gpus : (env.gpu && env.gpu.name ? [{name: env.gpu.name, memory_bytes: env.gpu.vram_total_mb * 1048576, memory_architecture: env.gpu.memory_architecture}] : []);
  const gpuNames = cards.map((gpu) => [String(gpu.name || "Unknown GPU").replace(/^NVIDIA (?:GeForce\s+)?/, ""), memory(gpu.memory_bytes / 1073741824, gpu.memory_architecture === "unified" ? "unified" : "VRAM")].filter(Boolean).join(" "));
  const label = [cfg.gpu_offload === "off" ? "CPU only" : gpuNames.join(" + "), ram, cpu].filter(Boolean).join(" / ") || "Local benchmark";
  const allModels = (env.models || []).filter((model) => model.type !== "embeddings").map((model) => model.id);
  const selected = cfg.models || [];
  const models = selected.length && selected.length === allModels.length && allModels.every((id) => selected.includes(id))
    ? `all ${selected.length} installed models` : selected.join(", ") || "none";
  const placement = cfg.gpu_offload === "provider_managed" ? "provider controlled" : {max: "prefer GPU", auto: "automatic", off: "CPU only"}[cfg.gpu_offload] || `${Number(cfg.gpu_offload) * 100}% on GPU`;
  const notes = [
    `Models: ${models}`,
    `Prompt tokens: ${[...cfg.sizes].sort((a, b) => a - b).map(fmtTokens).join(", ") || "none"}`,
    `Simultaneous queries: ${cfg.concurrency_levels.join(", ") || "none"}`,
    `Answer: ${cfg.max_tokens} tokens; repeats: ${cfg.repeats}`,
    ...(cfg.context_policy === "provider_managed" ? ["Reasoning, context allocation, placement and speculative decoding: provider controlled"] : [
      `Placement: ${placement}; CPU fallback: ${cfg.fallback_on_failure ? "on" : "off"}`,
      `Reasoning: ${cfg.disable_thinking ? "off" : "on"}; speculative decoding: ${cfg.mtp_modes.join(" + ")}`,
      `Warmup: ${cfg.warmup ? "on" : "off"}; skip larger sizes after failure: ${cfg.stop_model_after_failure ? "on" : "off"}`,
      `Context allocation: ${cfg.context_policy === "fixed" ? "fixed largest context and query capacity" : "matched to each test"}`,
    ]),
  ];
  if (cards.length > 1) notes.push(`Power sensor: ${cards.find((gpu) => gpu.sensor_index === cfg.gpu_index)?.name || cfg.gpu_index} (monitoring only)`);
  if (cfg.runtime_gpu_placement) notes.push(`Runtime GPU setup: ${cfg.runtime_gpu_placement}`);
  return {label, notes: notes.join("\n")};
}

function updateGeneratedMetadata() {
  if (!state.env) return;
  const generated = generatedMetadata(state.env, collectConfig());
  if (!state.metadataOverrides.label) $("#run-label").value = generated.label;
  if (!state.metadataOverrides.notes) $("#run-notes").value = generated.notes;
}

function updatePlan() {
  updateGeneratedMetadata();
  const modes = mtpModes();
  const base = state.models.size * state.sizes.size * modes.length * Number($("#repeats").value || 1);
  const tests = base * state.concurrencyLevels.size;
  const requests = base * [...state.concurrencyLevels].reduce((a, b) => a + b, 0);
  $("#plan-summary").textContent = tests
    ? `${state.models.size} model${state.models.size === 1 ? "" : "s"} / ${tests} tests / ${requests} queries`
    : "Choose at least one model and workload.";
  $("#start-btn").disabled = !tests;
  checkComparability();
}

/* ---------------- run control ---------------- */
function collectConfig() {
  return {
    models: [...state.models],
    sizes: [...state.sizes],
    concurrency_levels: [...state.concurrencyLevels].sort((a, b) => a - b),
    gpu_index: Number($("#gpu-index").value),
    runtime_gpu_placement: $("#runtime-gpu-placement").value.trim(),
    max_tokens: Number($("#max-tokens").value) || 256,
    repeats: Number($("#repeats").value) || 1,
    gpu_offload: browserMode() ? "provider_managed" : $("#offload").value,
    context_policy: browserMode() ? "provider_managed" : ($("#context-policy")?.value || "per_test"),
    mtp_modes: mtpModes(),
    fallback_on_failure: browserMode() ? false : $("#fallback").checked,
    stop_model_after_failure: $("#stop-after-fail").checked,
    warmup: browserMode() ? false : $("#warmup").checked,
    disable_thinking: browserMode() ? null : $("#no-think").checked,
    per_test_timeout_s: Number($("#per-test-timeout").value) || 1800,
    load_timeout_s: Number($("#load-timeout").value) || 900,
    context_headroom: Number($("#headroom").value) || 0,
    other_idle_w: state.env?.defaults?.other_idle_w ?? 60,
    cpu_tdp_w: state.env?.defaults?.cpu_tdp_w ?? 140,
    power_interval_ms: state.env?.defaults?.power_interval_ms ?? 150,
    label: $("#run-label").value.trim(),
    notes: $("#run-notes").value.trim(),
  };
}

async function startBench() {
  updateGeneratedMetadata();
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
  state.benchmarkRunning = true;
  updateExtendAvailability();
  $("#live-card").classList.remove("hidden");
  $("#live-results").classList.remove("hidden");
  $("#cancel-btn").classList.remove("hidden");
  $("#cancel-btn").disabled = false;
  $("#start-btn").disabled = true;
  $("#pill-power").hidden = false;
}

function leaveRunningUi() {
  state.benchmarkRunning = false;
  updateExtendAvailability();
  $("#cancel-btn").classList.add("hidden");
  updatePlan();
}

function startPolling() {
  if (state.poller) return;
  state.poller = setInterval(poll, 700);
  poll();
}

async function checkForActiveBenchmark() {
  if (state.poller || state.pollBusy || document.visibilityState === "hidden") return;
  try {
    const status = await api("/api/status");
    if (status.running) { enterRunningUi(); startPolling(); }
  } catch (error) { /* The next check retries if the local server is unavailable. */ }
}

async function syncLiveBenchmark(status, force = false) {
  const summary = status.run;
  const id = status.run_id || (summary || {}).id;
  if (!id || !summary) return;
  const signature = `${id}:${summary.n_results}:${summary.status}`;
  if (!force && signature === state.liveSignature) return;
  const run = await api("/api/runs/" + encodeURIComponent(id));
  // Include each newly discovered active run once; later refreshes respect manual selections.
  if (status.running && id !== state.liveRunId) {
    state.liveRunId = id;
    state.compare.add(id);
    state.selected = id;
    state.selectionInitialized = true;
  }
  state.runCache[id] = run;
  state.liveResults = (run.results || []).slice();
  $("#live-card").dataset.runId = id;
  renderLiveTable();
  await loadRuns();
  // If the file was read just before an atomic save finished, retry on the next poll.
  if ((run.results || []).length >= (summary.n_results || 0) && run.status === summary.status) {
    state.liveSignature = signature;
  }
}

async function poll() {
  if (state.pollBusy) return;
  state.pollBusy = true;
  try {
    const data = await api("/api/events?cursor=" + state.cursor);
    state.cursor = data.cursor;
    for (const event of data.events) {
      if (event.kind === "log") appendLog(event.message, event.level);
    }
    const changed = data.events.some(event => event.kind === "result" || event.kind === "done");
    await syncLiveBenchmark(data.status, changed);
    renderLive(data.status);
    if (!data.status.running && state.poller) {
      clearInterval(state.poller); state.poller = null; leaveRunningUi();
    }
  } catch (error) {
    // Do not stop polling after a transient fetch failure; the next tick retries.
  } finally {
    state.pollBusy = false;
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

  const deadline = $("#live-deadline");
  const hasDeadline = Number.isFinite(live.timeout_budget_s) && live.timeout_budget_s > 0;
  deadline.hidden = !hasDeadline;
  deadline.textContent = hasDeadline ? `Current test deadline: ${fmtDuration(live.timeout_elapsed_s ?? 0)} elapsed / ${fmtDuration(live.timeout_budget_s)} budget${live.timeout_reason ? " (" + live.timeout_reason + ")" : ""}. Adaptive timing is capped by the configured timeout; it is not a memory limit.` : "";

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

function estimatedMark(r, aggregate = false) { return (aggregate ? r.aggregate_completion_tokens_estimated : r.completion_tokens_estimated) ? "*" : ""; }
function requestDetails(r) {
  if (!Array.isArray(r.requests)) return "";
  return `<details><summary>${esc(r.completed_requests ?? 0)}/${r.requests.length} completed</summary>${r.requests.map((q, i) => `<div>Request ${i + 1}: ${esc(q.status || "unknown")} - ${fmt(q.decode_tps, 1)}${estimatedMark(q)} tk/s - TTFT ${fmt(q.ttft_s, 2)} s ${q.message ? esc(q.message) : ""}</div>`).join("")}</details>`;
}

function renderLiveTable() {
  const rows = state.liveResults.map((r) => `<tr>
    <td>${esc(r.model)}</td>
    <td class="num">${fmtTokens(r.target_tokens)}</td>
    <td>${browserMode() ? '<span class="dim">provider controlled</span>' : r.mtp ? '<span class="badge accent">MTP</span>' : '<span class="dim">off</span>'}</td>
    <td class="num">${fmtInt(r.prompt_tokens)}</td>
    <td>${statusCell(r)} ${requestDetails(r)}</td>
    <td class="num">${r.concurrency || 1}</td><td class="num">${fmt(r.aggregate_completion_tps, 1)}${r.aggregate_completion_tokens_estimated ? "*" : ""}</td>
    <td class="num">${fmt(r.prefill_tps, 0)}</td>
    <td class="num">${fmt(r.decode_tps, 1)}${estimatedMark(r)}</td>
    <td class="num">${fmt(r.ttft_s, 2)}</td>
    <td class="num">${fmt(r.power && r.power.prefill_gpu_w, 0)}</td>
    <td class="num">${fmt(r.power && r.power.j_per_1k_prefill, 1)}</td>
    <td class="num">${fmtInt(r.vram_peak_mb)}</td>
  </tr>`).join("");
  $("#live-table").innerHTML = `<caption>* Output tokens and derived throughput are estimated.</caption><thead><tr>
    <th>Model</th><th class="num">Target</th><th>MTP</th><th class="num">Actual tk</th><th>Status / requests</th><th>Concurrency</th><th>Aggregate tk/s</th>
    <th class="num">Prefill tk/s</th><th class="num">Decode tk/s</th><th class="num">TTFT s</th>
    <th class="num">GPU W</th><th class="num">J/1k</th><th class="num">VRAM MB</th>
    </tr></thead><tbody>${rows}</tbody>`;
}

/* ---------------- results tab ---------------- */
async function loadRuns() {
  const {runs} = await api("/api/runs");
  state.runs = runs;
  for (const summary of runs) {
    const cached = state.runCache[summary.id];
    if (cached && ((cached.results || []).length !== summary.n_results || cached.status !== summary.status)) delete state.runCache[summary.id];
  }
  state.compare = new Set([...state.compare].filter((id) => runs.some((run) => run.id === id)));
  if ((!state.selectionInitialized || (state.selected && !runs.some((run) => run.id === state.selected))) && runs.length) state.compare.add(runs[0].id);
  state.selectionInitialized = true;
  if (!state.compare.has(state.selected)) state.selected = [...state.compare][0] || null;
  renderRunsList();
  checkComparability();
  await renderResultsView();
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
      <div class="run-item" style="--benchmark-color:${runColor(run.id)}"><input class="run-select" type="checkbox" data-run-select="${esc(run.id)}" aria-label="Include ${esc(run.label || run.id)}" title="Include this benchmark in all result views" ${state.compare.has(run.id) ? "checked" : ""}><button class="run" data-id="${esc(run.id)}" aria-current="${state.compare.has(run.id)}" style="--benchmark-color:${runColor(run.id)}" title="Open ${esc(run.label || run.id)}">
        <span class="swatch" style="background:${runColor(run.id)}"></span>
        <span style="flex:1;min-width:0">
          <span class="t">${esc(run.label || run.id)}</span>
          <span class="s">${esc((run.created_at || "").replace("T", " "))} · ${run.n_results} test${run.n_results === 1 ? "" : "s"}</span>
          <span class="counts">
            <span class="badge">${run.n_ok} ok</span>
            ${run.n_failed ? `<span class="badge">${run.n_failed} failed</span>` : ""}
            ${(run.mtp_modes || []).includes("on") ? '<span class="badge accent">MTP</span>' : ""}
            ${run.status === "running" ? '<span class="badge badge-live">Live</span>' : run.status !== "completed" ? `<span class="badge">${esc(run.status)}</span>` : ""}
          </span>
        </span>
      </button><button class="run-delete" data-delete="${esc(run.id)}" title="${run.status === "running" ? "This benchmark is still running" : "Delete this benchmark from this computer"}" ${run.status === "running" ? "disabled" : ""} aria-label="Delete ${esc(run.label || run.id)}"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M3 5h14M8 3h4m-7 2 1 12h8l1-12M8 8v6m4-6v6"/></svg><span>Delete</span></button></div>`).join("")}`).join("");
  $$("#runs-list .run").forEach((btn) => btn.addEventListener("click", () => selectRun(btn.dataset.id)));
  $$("#runs-list [data-run-select]").forEach((input) => input.addEventListener("change", async () => {
    if (input.checked) state.compare.add(input.dataset.runSelect); else state.compare.delete(input.dataset.runSelect);
    if (!state.compare.has(state.selected)) state.selected = [...state.compare][0] || null;
    renderRunsList(); await renderResultsView();
  }));
  $$("#runs-list [data-delete]").forEach((btn) => btn.addEventListener("click", () => deleteRun(state.runs.find((run) => run.id === btn.dataset.delete))));
}

async function deleteRun(run) {
  if (!run || !confirm(browserMode() ? `Delete benchmark "${run.label || run.id}" from this browser?` : `Delete benchmark "${run.label || run.id}" from this computer? A copy will remain in the local archive.`)) return;
  try {
    await api("/api/runs/" + encodeURIComponent(run.id), {method: "DELETE"});
    delete state.runCache[run.id];
    state.compare.delete(run.id);
    if (state.selected === run.id) state.selected = null;
    if (!state.compare.size) state.selectionInitialized = false;
    await loadRuns();
  } catch (error) {
    alert("Could not delete benchmark: " + error.message);
  }
}

async function selectRun(id) {
  state.selected = id;
  state.compare = new Set([id]);
  renderRunsList();
  await renderResultsView();
}

function sessionConfig(session) { return session.config || session.execution_config || {}; }
function allocationKey(cfg) {
  return JSON.stringify([cfg.context_policy || "per_test", cfg.context_planner_version || 0,
    cfg.context_headroom ?? 512, cfg.context_safety_fraction ?? 0]);
}
function allocationConfigs(run) {
  return (run.sessions || []).length ? run.sessions.map(sessionConfig) : [run.config || {}];
}
function sessionLabel(run, row) {
  const sessions = run.sessions || [];
  const index = sessions.findIndex(session => (session.id || session.session_id) === row.session_id);
  if (index < 0) return row.session_id ? String(row.session_id) : "Legacy";
  const session = sessions[index], cfg = sessionConfig(session);
  return `Session ${index + 1}${session.started_at ? " (" + session.started_at + ")" : ""} / planner ${cfg.context_planner_version ?? "legacy"}`;
}
function sessionSettings(run) {
  const sessions = run.sessions || [];
  if (!sessions.length) return "";
  return `<details class="adv"><summary>Sessions (${sessions.length})</summary><div>${sessions.map((session, index) => {
    const cfg = sessionConfig(session);
    const modelCount = cfg.model_modes ? Object.keys(cfg.model_modes).length : (cfg.models || []).length;
    const modes = cfg.model_modes ? [...new Set(Object.values(cfg.model_modes).flat())] : (cfg.mtp_modes || []);
    return `<p class="hint"><b>Session ${index + 1}</b>${session.started_at ? ` / ${esc(session.started_at)}` : ""} / ${modelCount} models / ${esc(modes.join(" + ") || "unknown technique")} / planner ${esc(cfg.context_planner_version ?? "legacy")} / ${esc(cfg.context_policy || "per_test")} / reserve ${esc(cfg.context_headroom ?? 512)} tokens + ${esc(100 * (cfg.context_safety_fraction ?? 0))}%</p>`;
  }).join("")}</div></details>`;
}

function renderRunDetail(run) {
  if (!run) {
    $("#run-detail").innerHTML = emptyState("Select a benchmark", "Click a name or tick benchmarks in the list on the left.");
    return;
  }
  const active = [...state.compare].map(id => state.runCache[id]).filter(item => item && item.status === "running");
  const selectedRuns = [...state.compare].map(id => state.runCache[id]).filter(Boolean);
  const allocationKeys = new Set(selectedRuns.flatMap(allocationConfigs).map(allocationKey));
  const measurementModes = new Set(selectedRuns.map(item => item.measurement_mode || "helper"));
  const measurementNotice = measurementModes.size > 1 ? '<p class="banner warn measurement-notice">These benchmarks use different measurement modes. Browser timings and desktop helper measurements may not be directly comparable.</p>' : selectedRuns.length ? `<p class="hint measurement-notice">Measurement mode: ${measurementModes.has("browser") ? "Browser timing" : "Desktop helper"}</p>` : "";
  const allocationNotice = allocationKeys.size > 1 ? '<p class="hint">Different context allocation settings: these benchmarks reserve different amounts of memory, which can affect speed. Check benchmark settings before comparing.</p>' : "";
  const liveNotice = active.length ? `<p class="live-result-notice" role="status"><span class="badge badge-live">Live</span> ${active.reduce((count, item) => count + (item.results || []).length, 0)} completed tests in the ongoing benchmark${active.length > 1 ? "s" : ""}. Results update as each test finishes.</p>` : "";
  if (state.compare.size > 1) {
    $("#run-detail").innerHTML = `<div class="card selection-heading"><header><h3>${state.compare.size} benchmarks selected</h3></header><p class="hint">Your selection and filters apply to Summary, Table and Compare.</p>${liveNotice}${measurementNotice}${allocationNotice}<details class="adv"><summary>Selected benchmarks</summary><ul>${[...state.compare].map((id) => `<li>${esc((state.runCache[id] || {}).label || id)}</li>`).join("")}</ul></details></div>`;
    return;
  }
  const sessionAllocationNotice = new Set(allocationConfigs(run).map(allocationKey)).size > 1
    ? '<p class="hint" role="status">Sessions in this benchmark used different context allocation settings. Memory use and speed may differ; see session settings below.</p>' : "";
  const gpu = run.gpu || {}, host = run.host || {}, cfg = run.config || {};
  const ok = (run.results || []).filter((row) => row.status === "ok");
  const best = (key) => Math.max(0, ...ok.map((row) => row[key] || 0));
  const levelRows = (run.results || []).filter(row => (row.concurrency || 1) === state.comparisonConcurrency);
  const failureNotice = levelRows.length && !levelRows.some(row => row.status === "ok")
    ? `<p class="hint" role="status">No successful tests at concurrency ${state.comparisonConcurrency}. Check All measurements for failure details.</p>` : "";
  $("#run-detail").innerHTML = `<div class="card selection-heading"><header><h3>${esc(run.label || run.id)}</h3>${state.env?.capabilities?.extend ? `<button class="btn-sm" id="extend-run" ${state.benchmarkRunning || run.status === "running" ? "disabled" : ""} title="${state.benchmarkRunning || run.status === "running" ? "Wait for the active benchmark to finish" : "Add or rerun tests in this benchmark"}">Add / rerun tests</button>` : ""}<button class="btn-sm" id="rename-run" title="Edit this benchmark's name and notes" ${run.status === "running" ? "disabled" : ""}>Rename</button><a class="btn-sm" href="/api/runs/${encodeURIComponent(run.id)}/download" title="Download this benchmark's JSON file">JSON</a><button class="btn-sm btn-danger" id="delete-run" title="Delete this benchmark from this computer" ${run.status === "running" ? "disabled" : ""}>Delete</button></header>${liveNotice}${measurementNotice}${failureNotice}${sessionAllocationNotice}<div class="result-stats">${stat("Best prefill", best("prefill_tps") ? fmt(best("prefill_tps"), 0) : "?", "tk/s")}${stat("Best output", best("decode_tps") ? fmt(best("decode_tps"), 1) : "?", "tk/s")}${stat("Successful tests", `${ok.length}/${(run.results || []).length}`, "")}</div><details class="adv"><summary>Machine, settings &amp; notes</summary><div><p class="hint">${esc(gpu.name || "CPU")} / ${esc(host.cpu || "")} / ${esc(run.status)} / ${esc(run.created_at || "")}</p>${run.hardware ? hardwareMarkup(run.hardware) : ""}<p class="saved-notes">${esc(run.notes || "")}</p><p class="hint">Output ${esc(cfg.max_tokens)} tokens / reasoning ${cfg.disable_thinking == null ? "provider controlled" : cfg.disable_thinking === false ? "on" : "off"} / placement ${esc(cfg.gpu_offload === "provider_managed" ? "provider controlled" : cfg.gpu_offload || "auto")} / ${cfg.context_policy === "provider_managed" ? "context and speculative decoding provider controlled" : `context allocation ${cfg.context_policy === "fixed" ? "one fixed load per model" : "matched to each test"} / context reserve ${esc(cfg.context_headroom ?? 512)} tokens + ${esc(100 * (cfg.context_safety_fraction ?? 0))}% / planner ${esc(cfg.context_planner_version ?? "legacy")}`}</p>${run.interrupted_note ? `<p class="hint">${esc(run.interrupted_note)}</p>` : ""}${sessionSettings(run)}</div></details></div>`;
  $("#extend-run")?.addEventListener("click", () => openExtendDialog(run));
  $("#delete-run").addEventListener("click", () => deleteRun(run));
  $("#rename-run").addEventListener("click", async () => {
    const label = prompt("Benchmark name", run.label || "");
    if (label === null) return;
    const notes = prompt("Notes", run.notes || "");
    if (notes === null) return;
    await api(`/api/runs/${encodeURIComponent(run.id)}/meta`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({label, notes})});
    delete state.runCache[run.id];
    await loadRuns();
  });
}

function updateExtendAvailability() {
  const button = $("#extend-run");
  if (button) {
    button.disabled = state.benchmarkRunning;
    button.title = state.benchmarkRunning ? "Wait for the active benchmark to finish" : "Add or rerun tests in this benchmark";
  }
  const start = $("#extend-start");
  if (start && state.benchmarkRunning) start.disabled = true;
}

async function openExtendDialog(run) {
  if (state.benchmarkRunning) return;
  const dialog = $("#extend-dialog");
  const pending = {};
  state.extension = pending;
  $("#extend-content").innerHTML = '<p class="hint">Loading installed models...</p>';
  $("#extend-preview").textContent = "";
  $("#extend-start").disabled = true;
  $("#extend-start").textContent = "Add tests";
  dialog.showModal();
  try {
    const [plan, source] = await Promise.all([
      api("/api/bench/extend-plan?run_id=" + encodeURIComponent(run.id)),
      api("/api/runs/" + encodeURIComponent(run.id)),
    ]);
    if (!dialog.open || state.extension !== pending) return;
    const cfg = source.config || {}, installed = (plan.models || []).filter(m => m.type !== "embeddings");
    const existing = cfg.models || [];
    const models = [...existing.map(id => installed.find(model => model.id === id) || {id, unavailable: true}),
      ...installed.filter(model => !existing.includes(model.id))];
    state.extension = {runId: run.id, source, sourceConfig: cfg, models,
      modelModes: originalModelSelection(cfg, models), sizes: new Set(plan.sizes || []), concurrency: new Set(plan.concurrency_levels || [1]), action: "add", revision: 0};
    renderExtendForm();
    previewExtension();
  } catch (error) {
    if (state.extension === pending) $("#extend-content").innerHTML = banner("err", "Could not load available tests", esc(error.message));
  }
}

function benchmarkTechniques(cfg, model) {
  return [...new Set((cfg.model_modes?.[model] || cfg.mtp_modes || ["off"]).map(mode =>
    mode === true || ["on", "true", "1", "mtp"].includes(String(mode).toLowerCase()) ? "on" : "off"))];
}

function originalModelSelection(cfg, models) {
  return Object.fromEntries(models.filter(model => !model.unavailable && (cfg.models || []).includes(model.id))
    .map(model => [model.id, benchmarkTechniques(cfg, model.id)]));
}

function extensionModelProgress(extension, model) {
  const cfg = extension.sourceConfig, original = (cfg.models || []).includes(model.id);
  const modes = [...new Set([...(original ? benchmarkTechniques(cfg, model.id) : []),
    ...(extension.modelModes[model.id] || [])])];
  if (!modes.length) modes.push("off");
  const repeats = Math.max(1, Number(cfg.repeats) || 1), cases = new Map();
  const rank = {ok: 4, failed: 3, skipped: 2, cancelled: 1};
  for (const row of extension.source.results || []) {
    if (row.model !== model.id || !extension.sizes.has(row.target_tokens) || !extension.concurrency.has(row.concurrency || 1)) continue;
    const mode = row.mtp ? "on" : "off";
    if (!modes.includes(mode)) continue;
    for (const repeat of row.repeat == null ? Array.from({length: repeats}, (_, i) => i) : [row.repeat]) {
      if (!Number.isInteger(repeat) || repeat < 0 || repeat >= repeats) continue;
      const key = [mode, row.target_tokens, row.concurrency || 1, repeat].join(":");
      if ((rank[row.status] || 0) > (rank[cases.get(key)] || 0)) cases.set(key, row.status);
    }
  }
  const counts = {ok: 0, failed: 0, skipped: 0, unfinished: 0,
    total: modes.length * extension.sizes.size * extension.concurrency.size * repeats};
  for (const status of cases.values()) if (status in counts) counts[status]++;
  counts.unfinished = counts.total - counts.ok - counts.failed - counts.skipped;
  return counts;
}

function extensionProgressMarkup(extension, model) {
  const progress = extensionModelProgress(extension, model);
  const original = (extension.sourceConfig.models || []).includes(model.id);
  const parts = [`<span class="extend-model-origin">${original ? "In this benchmark" : "New model"}${model.unavailable ? " / Not installed" : ""}</span>`];
  if (progress.ok) parts.push(`<span class="extend-count-ok">${progress.ok} successful</span>`);
  if (progress.failed) parts.push(`<span>${progress.failed} failed</span>`);
  if (progress.skipped) parts.push(`<span>${progress.skipped} skipped</span>`);
  parts.push(`<span class="extend-count-pending">${progress.unfinished} unfinished</span>`);
  return `<div class="extend-model-progress" title="For the prompt sizes, query counts and techniques shown here. Successful tests are retained unless you choose Rerun selected tests.">${parts.join("<span aria-hidden=\"true\"> · </span>")}</div>`;
}

function extendBody(extension) {
  return {run_id: extension.runId, model_modes: extension.modelModes,
    sizes: [...extension.sizes].sort((a, b) => a - b),
    concurrency_levels: [...extension.concurrency].sort((a, b) => a - b), retry_failed: extension.action === "retry" || extension.action === "rerun", rerun_existing: extension.action === "rerun"};
}

function renderExtendForm() {
  const extension = state.extension;
  const sizes = [...new Set([...(state.env.size_presets || []), ...extension.sizes])].sort((a,b) => a-b);
  const levels = [...new Set([...CONCURRENCY_PRESETS, ...extension.concurrency])].sort((a,b) => a-b);
  $("#extend-content").innerHTML = `<div class="field"><label>Models and techniques</label><p class="hint">Your benchmark models are selected. Add missing tests keeps saved results and continues unfinished work. New models are unchecked.</p><p class="extend-continuity" role="status">Successful tests will not run again unless you choose Rerun selected tests.</p><div class="btn-row"><button type="button" class="btn-sm" id="extend-select-existing" title="Restore the benchmark models and techniques; saved measurements remain untouched">Restore benchmark selection</button><button type="button" class="btn-sm" id="extend-clear-selection" title="Clear which models will run; saved results are retained">Clear selection</button></div><p class="hint extend-progress-caption">Progress below covers saved techniques and selected additions at the prompt sizes and query counts shown below. Unchecking a model keeps its results.</p><div class="extend-models">${extension.models.map((m, i) => `<div class="extend-model" data-progress-model="${i}"><div class="extend-model-info"><strong>${esc(m.id)}</strong>${extensionProgressMarkup(extension, m)}</div><label title="Standard decoding without MTP"><input type="checkbox" data-model="${i}" data-mode="off" ${(extension.modelModes[m.id] || []).includes("off") ? "checked" : ""} ${m.unavailable ? "disabled" : ""}> Standard</label><label title="Speculative decoding; model and runtime support is not guaranteed"><input type="checkbox" data-model="${i}" data-mode="on" ${(extension.modelModes[m.id] || []).includes("on") ? "checked" : ""} ${m.unavailable ? "disabled" : ""}> MTP</label></div>`).join("") || '<p class="hint">No installed language models found.</p>'}</div></div><div class="field"><label>Prompt sizes</label><p class="hint">Select a maximum to include every smaller size.</p><div class="chips" id="extend-sizes"></div></div><div class="field"><label>Concurrent queries</label><p class="hint">Select a maximum to include every smaller level.</p><div class="chips" id="extend-concurrency"></div></div><fieldset class="extend-actions"><legend>Action</legend><label><input type="radio" name="extend-action" value="add" ${extension.action === "add" ? "checked" : ""}> Add missing tests</label><label><input type="radio" name="extend-action" value="retry" ${extension.action === "retry" ? "checked" : ""}> Retry failed tests <span class="hint">(includes missing and skipped)</span></label><label><input type="radio" name="extend-action" value="rerun" ${extension.action === "rerun" ? "checked" : ""}> Rerun selected tests <span class="hint">(includes successful, failed, skipped and missing)</span></label>${extension.action === "rerun" ? '<p class="banner warn">Selected measurements will be replaced; previous attempts are kept.</p>' : ""}</fieldset>`;
  $("#extend-select-existing").addEventListener("click", () => {
    extension.modelModes = originalModelSelection(extension.sourceConfig, extension.models);
    renderExtendForm(); previewExtension();
  });
  $("#extend-clear-selection").addEventListener("click", () => { extension.modelModes = {}; renderExtendForm(); previewExtension(); });
  $$("#extend-content input[data-model]").forEach(input => input.addEventListener("change", () => {
    const id = extension.models[Number(input.dataset.model)].id;
    const modes = new Set(extension.modelModes[id] || []);
    if (input.checked) modes.add(input.dataset.mode); else modes.delete(input.dataset.mode);
    if (modes.size) extension.modelModes[id] = [...modes]; else delete extension.modelModes[id];
    const info = input.closest(".extend-model").querySelector(".extend-model-info");
    info.querySelector(".extend-model-progress").outerHTML = extensionProgressMarkup(extension, extension.models[Number(input.dataset.model)]);
    previewExtension();
  }));
  renderRange("#extend-sizes", sizes, extension.sizes, value => { extension.sizes = through(sizes, value); renderExtendForm(); previewExtension(); }, fmtTokens, "prompt tokens");
  renderRange("#extend-concurrency", levels, extension.concurrency, value => { extension.concurrency = through(levels, value); renderExtendForm(); previewExtension(); }, String, "simultaneous queries");
  $$("#extend-content input[name=extend-action]").forEach(input => input.addEventListener("change", () => { extension.action = input.value; renderExtendForm(); previewExtension(); }));
}

async function previewExtension() {
  const extension = state.extension;
  const revision = ++extension.revision;
  const start = $("#extend-start");
  start.disabled = true;
  start.textContent = extension.action === "rerun" ? "Rerun tests" : "Add tests";
  if (!Object.values(extension.modelModes).some(modes => modes.length)) {
    $("#extend-preview").innerHTML = '<p class="hint">Choose models and techniques to preview tests.</p>';
    return;
  }
  $("#extend-preview").innerHTML = '<p class="hint">Updating preview...</p>';
  try {
    const preview = await api("/api/bench/extend-plan", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(extendBody(extension))});
    if (state.extension !== extension || revision !== extension.revision) return;
    $("#extend-preview").innerHTML = `<div class="extend-preview"><b>${esc(preview.new_tests)} new tests</b> / ${esc(preview.retry_tests)} retries / ${esc(preview.rerun_tests ?? 0)} successful tests to rerun / ${esc(preview.existing)} already present / ${esc(preview.total_tests)} tests to run${(preview.warnings || []).map(w => `<p class="hint">${esc(w)}</p>`).join("")}</div>`;
    start.textContent = extension.action === "rerun" ? `Rerun ${preview.total_tests} tests` : `Run ${preview.total_tests} tests`;
    start.disabled = state.benchmarkRunning || !(preview.total_tests > 0);
  } catch (error) {
    if (state.extension === extension && revision === extension.revision) $("#extend-preview").innerHTML = banner("err", "Could not preview tests", esc(error.message));
  }
}

async function startExtension() {
  if (!state.extension || state.benchmarkRunning) return;
  $("#extend-start").disabled = true;
  try {
    const response = await api("/api/bench/extend", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(extendBody(state.extension))});
    $("#extend-dialog").close(); state.extension = null;
    state.cursor = 0; state.liveSignature = null; state.liveRunId = null;
    $("#live-card").dataset.runId = response.run_id;
    enterRunningUi(); startPolling();
  } catch (error) {
    const message = error.message;
    await previewExtension();
    $("#extend-preview").insertAdjacentHTML("afterbegin", banner("err", "Could not add tests", esc(message)));
  }
}

function detailedResultsTable(run) {
  const fields = [
    ["Status", row => row.status], ["Repeat", row => (row.repeat || 0) + 1],
    ["Actual prompt tokens", row => row.prompt_tokens], ["Query context", row => row.per_request_context_length || row.context_length],
    ["Placement", row => row.gpu_offload], ["Time to first token (s)", row => row.ttft_s],
    ["Prefill (tk/s)", row => row.prefill_tps], ["Output (tk/s)", row => row.decode_tps],
    ["Group output (tk/s)", row => row.aggregate_completion_tps],
    ["Output tokens", row => row.completion_tokens], ["Wall time (s)", row => row.wall_s],
    ["GPU power (W)", row => (row.power || {}).prefill_gpu_w ?? (row.group_power || {}).gpu_w_avg],
    ["GPU decode power (W)", row => (row.power || {}).decode_gpu_w],
    ["Estimated system power (W)", row => (row.power || {}).prefill_system_w_est ?? (row.group_power || {}).system_w_est],
    ["GPU prefill energy (J)", row => (row.power || {}).prefill_energy_j],
    ["GPU decode energy (J)", row => (row.power || {}).decode_energy_j],
    ["Group GPU energy (J)", row => (row.group_power || {}).gpu_energy_j],
    ["Energy per 1k prompt tokens (J)", row => (row.power || {}).j_per_1k_prefill],
    ["Energy per output token (J)", row => (row.power || {}).j_per_token_decode],
    ["Load time (s)", row => row.load_s],
    ["Quantization", row => row.quantization],
    ["Speculative decoding", row => run.measurement_mode === "browser" ? "provider controlled" : row.mtp ? "on" : "off"],
    ["VRAM peak (MB)", row => row.vram_peak_mb], ["Failure", row => row.message],
  ];
  const rows = (run.results || []).flatMap(row => [...fields.map(([label, get]) => {
    const value = get(row);
    if (value == null || value === "") return "";
    return `<tr class="colored-result-row" style="--table-color:${tableRowColor(run, row.model, modelUniverse())}"><td>${esc(row.model)}</td><td>${fmtTokens(row.target_tokens)}</td><td>${row.concurrency || 1}</td><td>${esc(sessionLabel(run, row))}</td><td>${esc(label)}</td><td class="measure-cell">${esc(typeof value === "number" ? n(value) : value)}${label.includes("tk/s") ? estimatedMark(row, label.startsWith("Group")) : ""}</td></tr>`;
  }), ...(row.requests || []).flatMap((request, index) => [
    ["Status", request.status], ["Prompt tokens", request.prompt_tokens],
    ["Output tokens", request.completion_tokens], ["Time to first token (s)", request.ttft_s],
    ["Output (tk/s)", request.decode_tps], ["Wall time (s)", request.wall_s], ["Failure", request.message],
  ].filter(([, value]) => value != null).map(([label, value]) =>
    `<tr class="colored-result-row" style="--table-color:${tableRowColor(run, row.model, modelUniverse())}"><td>${esc(row.model)}</td><td>${fmtTokens(row.target_tokens)}</td><td>${row.concurrency || 1}</td><td>${esc(sessionLabel(run, row))}</td><td>Request ${index + 1}: ${esc(label)}</td><td class="measure-cell">${esc(typeof value === "number" ? n(value) : value)}${label.startsWith("Output") ? estimatedMark(request) : ""}</td></tr>`))]).join("");
  return `<table><thead><tr><th>Model</th><th>Prompt</th><th>Queries</th><th>Session / planner</th><th>Measurement</th><th>Value</th></tr></thead><tbody>${rows}</tbody></table>`;
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

function markerForRun(runId) {
  const idx = runColorIndex(runId);
  return Charts.MARKERS[(idx === null ? 0 : idx) % Charts.MARKERS.length];
}

function allModelsOf(runs) {
  return [...new Set(runs.flatMap(run => [
    ...(run.results || []).map(row => row.model),
    ...(run.status === "running" ? (run.config || {}).models || [] : []),
  ]))].sort();
}

function modelUniverse(models = []) {
  return [...new Set([...state.runs.flatMap(run => run.models || []), ...allModelsOf(Object.values(state.runCache)), ...models])].sort();
}
function modelColor(model, allModels) {
  const index = modelUniverse(allModels).indexOf(model);
  if (index < 0) return resolveColor("var(--text-muted)");
  return index < SERIES_SLOTS ? resolveColor(`var(--series-${index + 1})`) : `hsl(${Math.round(index * 137.508) % 360} 65% 60%)`;
}
function shownModels(models) {
  return state.visibleModels === null ? models : models.filter(model => state.visibleModels.has(model));
}
async function redrawResultsWithoutScrolling() {
  const view = $(".result-view.active");
  const left = window.scrollX, top = window.scrollY;
  // Keep enough space for the current viewport when filtering removes chart panels.
  if (view && !view.hidden) view.style.minHeight = `${view.getBoundingClientRect().height}px`;
  try { await renderResultsView(); }
  finally { window.scrollTo({left, top, behavior: "instant"}); }
}

function renderVisibleModels(models) {
  const shown = shownModels(models);
  $("#visible-model-count").textContent = `${shown.length} of ${models.length}`;
  const container = $("#visible-models");
  const signature = JSON.stringify(models);
  if (container.dataset.models !== signature) {
    container.dataset.models = signature;
    container.innerHTML = models.map(model => `<label class="chip" title="Show or hide ${esc(model)}"><input type="checkbox" value="${esc(model)}"><span class="model-key" aria-hidden="true"></span>${esc(model)}</label>`).join("") || '<span class="dim">Select a benchmark to see its models.</span>';
    $$("#visible-models input").forEach(input => input.addEventListener("change", async () => {
      if (state.visibleModels === null) state.visibleModels = new Set(models);
      if (input.checked) state.visibleModels.add(input.value); else state.visibleModels.delete(input.value);
      await redrawResultsWithoutScrolling();
    }));
  }
  const shapes = {circle: "&#9679;", square: "&#9632;", triangle: "&#9650;", diamond: "&#9670;"};
  $$("#visible-models input").forEach(input => {
    const selected = shown.includes(input.value);
    input.checked = selected;
    input.closest(".chip").dataset.on = selected ? "1" : "0";
    const symbol = input.closest(".chip").querySelector(".model-key");
    if (state.colorBy === "models") {
      symbol.innerHTML = "";
      symbol.style.background = modelColor(input.value, models);
    } else {
      symbol.innerHTML = shapes[chartAppearance({id: ""}, input.value, models, false).marker] || "&#9679;";
      symbol.style.background = "transparent";
    }
  });
}
function chartAppearance(run, model, models, mtp) {
  const byModel = state.colorBy === "models";
  const modelIndex = modelUniverse(models).indexOf(model);
  const shapeIndex = byModel ? runColorIndex(run.id) || 0 : Math.max(0, modelIndex);
  return {
    color: byModel ? modelColor(model, models) : resolveColor(runColor(run.id)),
    marker: Charts.MARKERS[shapeIndex % Charts.MARKERS.length],
    dash: mtp ? "2 2" : Charts.DASHES[shapeIndex % Charts.DASHES.length],
  };
}
function encodingLegend(metricKeys, runs) {
  const shapes = {circle: "&#9679;", square: "&#9632;", triangle: "&#9650;", diamond: "&#9670;"};
  const benchmarkItems = runs.map(run => `<span>${state.colorBy === "benchmarks" ? `<i style="background:${runColor(run.id)}"></i>` : `<b>${shapes[markerForRun(run.id)] || "&#9679;"}</b>`}${esc(run.label || run.id)}</span>`).join("");
  const measures = metricKeys.length > 1 ? `<div class="legend-group measure-legend"><span class="legend-key">Measures</span>${metricKeys.map(key => `<span><svg width="28" height="10" aria-hidden="true"><line x1="0" y1="5" x2="28" y2="5" stroke="currentColor" stroke-width="2" stroke-dasharray="${measureAppearance({}, key, metricKeys, false).dash || ""}"/></svg>${esc(METRICS[key].label)}</span>`).join("")}</div>` : "";
  return `<div class="legend-stack">${runs.length > 1 || state.colorBy === "benchmarks" ? `<div class="legend-group"><span class="legend-key">Benchmarks</span>${benchmarkItems}</div>` : ""}${measures}</div>`;
}

async function renderSummary() {
  const generation = state.resultsRender;
  const body = $("#summary-body");
  const ids = [...state.compare];
  if (!state.runs.length) {
    body.innerHTML = emptyState("Nothing to summarise yet",
      "Run a benchmark first. This tab then overlays every model, setup and measure you tick.");
    return;
  }
  await ensureRuns(ids);
  if (generation !== state.resultsRender) return;
  const runs = ids.map((id) => state.runCache[id]).filter(Boolean);
  const allModels = allModelsOf(runs);
  $("#summary-export").href = "/api/export.csv?ids=" + ids.map(encodeURIComponent).join(",");

  if (!runs.length) {
    body.innerHTML = emptyState("Select a benchmark", "Tick one or more benchmarks in the list on the left.");
    return;
  }
  const models = shownModels(allModels);
  const metricKeys = selectedMetricKeys();
  if (!metricKeys.length || !models.length) {
    body.innerHTML = !metricKeys.length ? emptyState("Choose measures to display", "Select one or more measure badges above.") : emptyState("Choose models to display", "Select one or more model badges above, or click All.");
    return;
  }

  const units = metricGroups(metricKeys);

  body.innerHTML = units.map((u, i) => `
    <div class="card" style="margin-bottom:16px">
      <header><h3>${esc(u.keys.map((k) => METRICS[k].label).join("  ·  "))}</h3>
        <div class="spacer"></div><span class="dim" style="font-size:12px">${esc(u.unit)} / ${state.comparisonConcurrency} simultaneous queries</span>
      </header>
      <div id="summary-legend-${i}"></div>
      <div class="plot" id="summary-plot-${i}" style="margin-top:4px"></div>
      <p class="hint" id="summary-note-${i}" style="margin:8px 0 0"></p>
    </div>`).join("");

  units.forEach((u, i) => {
    $(`#summary-legend-${i}`).innerHTML = encodingLegend(u.keys, runs);
    const series = [];
    const sizes = new Set();
    for (const key of u.keys) {
      const metric = METRICS[key];
      for (const run of runs) {
        for (const mtp of (state.mtpFilter === "all" ? [false, true] : [state.mtpFilter === "on"])) {
          if (run.measurement_mode === "browser" && (state.mtpFilter !== "all" || mtp)) continue;
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
              ...measureAppearance(chartAppearance(run, model, allModels, mtp), key, u.keys, mtp),
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
      height: 360,
      ariaLabel: u.keys.map((k) => METRICS[k].label).join(", "),
      series,
    });
    const values = series.flatMap((s) => s.points.map((p) => p.y)).filter((v) => v > 0);
    const spread = values.length ? Math.max(...values) / Math.min(...values) : 1;
    $(`#summary-note-${i}`).innerHTML = series.length
      ? `${series.length} series. ` + (spread >= 40
        ? "A log scale is in use because these measures span " + Math.round(spread) +
          "× — on a linear axis the smaller one would sit flat against the baseline."
        : "Linear scale.") + " Estimated output throughput is identified in point details." +
        " ✕ on the baseline marks a size that failed; hover any point for the exact figure."
      : "No measurements for this combination.";
  });
}

/* ---------------- compare tab ---------------- */
function selectedMetricKeys() {
  return Object.keys(METRICS).filter(key => state.metrics.has(key));
}
function metricGroups(keys = selectedMetricKeys()) {
  const groups = [];
  for (const key of keys) {
    const unit = METRICS[key].unit;
    // Energy per prompt workload and energy per output token have different denominators.
    const identity = unit === "J" ? key : unit;
    const group = groups.find(item => item.identity === identity);
    if (group) group.keys.push(key); else groups.push({identity, unit, keys: [key]});
  }
  return groups;
}
function measureAppearance(base, key, keys, mtp) {
  if (keys.length < 2) return base;
  const styles = [null, "7 4", "2 3", "10 3 2 3", "12 5"];
  const dash = styles[keys.indexOf(key) % styles.length];
  return {...base, dash: mtp ? (dash ? dash + " 2 2" : "2 2") : dash};
}
function renderMetricSeg() {
  const inputs = $$("#result-metrics input");
  if (inputs.length === Object.keys(METRICS).length) {
    inputs.forEach(input => {
      input.checked = state.metrics.has(input.value);
      input.closest(".chip").dataset.on = input.checked ? "1" : "0";
    });
    return;
  }
  $("#result-metrics").innerHTML = Object.entries(METRICS).map(([key, metric]) => `<label class="chip" data-on="${state.metrics.has(key) ? 1 : 0}" title="${esc(metric.blurb)}"><input type="checkbox" value="${key}" ${state.metrics.has(key) ? "checked" : ""}>${esc(metric.label)}</label>`).join("");
  $$("#result-metrics input").forEach(input => input.addEventListener("change", async () => {
    const key = input.value;
    if (input.checked) state.metrics.add(key); else state.metrics.delete(key);
    renderMetricSeg();
    await redrawResultsWithoutScrolling();
  }));
}

async function ensureRuns(ids) {
  await Promise.all(ids.map(async (id) => {
    if (!state.runCache[id]) state.runCache[id] = await api("/api/runs/" + encodeURIComponent(id));
  }));
}

async function renderCompare() {
  const generation = state.resultsRender;
  const body = $("#compare-body");
  const ids = [...state.compare];
  if (!ids.length) {
    body.innerHTML = emptyState("Select a benchmark", "Tick one or more benchmarks in the list on the left.");
    return;
  }
  await ensureRuns(ids);
  if (generation !== state.resultsRender) return;
  const runs = ids.map(id => state.runCache[id]).filter(Boolean);
  const models = allModelsOf(runs), shown = shownModels(models), keys = selectedMetricKeys();
  if (!shown.length || !keys.length) {
    body.innerHTML = !keys.length ? emptyState("Choose measures to display", "Select one or more measure badges above.") : emptyState("Choose models to display", "Select one or more model badges above, or click All.");
    return;
  }
  const tracks = tracksFor(runs);
  const sources = [...new Set(runs.map(run => run.power_source).filter(Boolean))];
  const mixedPower = keys.some(key => ["j_per_1k_prefill", "j_per_token_decode", "prefill_gpu_w"].includes(key)) && sources.length > 1;
  const note = mixedPower ? banner("warn", "Different power sensors", "These readings have different measurement boundaries. Compare them with care.") : "";
  if (state.compareView === "table") {
    body.innerHTML = `<div class="card"><header><h3>Measurements</h3></header>${note}${compareTable(tracks, shown, keys)}</div>`;
    return;
  }
  body.innerHTML = `<div class="card"><header><h3>${esc(keys.map(key => METRICS[key].label).join(" + "))}</h3></header>${note}<div class="facets" id="facet-grid"></div></div>`;
  const grid = $("#facet-grid");
  for (const group of metricGroups(keys)) {
    for (const model of shown) {
      const facet = document.createElement("div");
      facet.className = "facet";
      facet.innerHTML = `<h4>${esc(model)}</h4><div class="sub">${esc(group.keys.map(key => METRICS[key].label).join(" + "))} / ${esc(group.unit)}</div>${encodingLegend(group.keys, runs)}<div class="plot"></div>`;
      grid.appendChild(facet);
      const sizes = [...new Set(tracks.flatMap(track => (track.run.results || []).filter(row => row.model === model && !!row.mtp === !!track.mtp && (row.concurrency || 1) === state.comparisonConcurrency).map(row => row.target_tokens)))].sort((a, b) => a - b);
      const series = group.keys.flatMap(key => tracks.map(track => ({
        id: track.key + "|" + key, name: `${track.name} / ${METRICS[key].label}`,
        ...measureAppearance(chartAppearance(track.run, model, models, track.mtp), key, group.keys, track.mtp),
        points: pointsFor(track.run, model, METRICS[key], track.mtp),
        failures: key === group.keys[0] ? failuresFor(track.run, model, track.mtp) : [],
      })));
      const plot = $(".plot", facet);
      if (group.keys.length === 1 && METRICS[group.keys[0]].kind === "bars") {
        const metric = METRICS[group.keys[0]];
        Charts.bars(plot, {unit: group.unit, yLabel: group.unit, xLabel: "prefill tokens", height: 250,
          groups: sizes.map(size => ({label: fmtTokens(size), bars: tracks.map(track => {
            const row = pick(track.run, model, size, track.mtp, metric);
            return {id: track.key, name: track.name, ...chartAppearance(track.run, model, models, track.mtp), value: row && row.status === "ok" ? metric.get(row) : null};
          })})),
        });
      } else {
        Charts.line(plot, {unit: group.unit, xLabel: "prefill tokens", yLabel: group.unit, xTicks: sizes, height: 250, yScale: "auto", ariaLabel: `${group.keys.map(key => METRICS[key].label).join(", ")} for ${model}`, series});
      }
    }
  }
}

function resolveColor(cssVar) {
  const match = /^var\((--[a-z0-9-]+)\)$/i.exec(cssVar);
  if (!match) return cssVar;
  return getComputedStyle(document.documentElement).getPropertyValue(match[1]).trim() || "#888";
}

function pick(run, model, size, mtp, metric) {
  const matches = (run.results || []).filter((r) => r.model === model
    && r.target_tokens === size && !!r.mtp === !!mtp && (r.concurrency || 1) === state.comparisonConcurrency);
  const ok = matches.filter((r) => r.status === "ok");
  if (!ok.length) return matches[0] || null;
  // several repeats: report the median so one hiccup does not define the point
  const measured = metric ? ok.filter((r) => metric.get(r) != null) : ok;
  if (!measured.length) return ok[0];
  const sorted = measured.slice().sort((a, b) => metric ? metric.get(a) - metric.get(b) : (a.prefill_tps || 0) - (b.prefill_tps || 0));
  return sorted[Math.floor(sorted.length / 2)];
}

function pointsFor(run, model, metric, mtp) {
  const sizes = [...new Set((run.results || []).filter((r) => r.model === model
    && !!r.mtp === !!mtp && (r.concurrency || 1) === state.comparisonConcurrency).map((r) => r.target_tokens))].sort((a, b) => a - b);
  const out = [];
  for (const size of sizes) {
    const r = pick(run, model, size, mtp, metric);
    if (!r || r.status !== "ok") continue;
    const value = metric.get(r);
    if (value === null || value === undefined) continue;
    const note = [
      r.prompt_tokens ? `${fmtInt(r.prompt_tokens)} actual tokens` : "",
      r.gpu_offload && r.gpu_offload !== "auto" ? `offload ${r.gpu_offload}` : "",
      r.vram_peak_mb ? `${fmtInt(r.vram_peak_mb)} MB VRAM` : "",
      ((metric === METRICS.decode_tps && r.completion_tokens_estimated) || (metric === METRICS.aggregate_completion_tps && r.aggregate_completion_tokens_estimated)) ? "Output tokens and throughput estimated" : "",
    ].filter(Boolean).join(" · ");
    out.push({ x: size, y: value, note });
  }
  return out;
}

function failuresFor(run, model, mtp) {
  return (run.results || [])
    .filter((r) => r.model === model && !!r.mtp === !!mtp && (r.concurrency || 1) === state.comparisonConcurrency
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
      if (run.measurement_mode === "browser" && (state.mtpFilter !== "all" || mtp)) continue;
      const rows = (run.results || []).filter((r) => !!r.mtp === !!mtp && (r.concurrency || 1) === state.comparisonConcurrency);
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

function metricEstimateMark(row, metric) {
  if (metric === METRICS.aggregate_completion_tps) return estimatedMark(row, true);
  return metric === METRICS.decode_tps || metric === METRICS.j_per_token_decode ? estimatedMark(row) : "";
}

function tableRowColor(run, model, models) {
  return state.colorBy === "models" ? modelColor(model, models) : resolveColor(runColor(run.id));
}

function compareTable(tracks, models, keys = selectedMetricKeys()) {
  const rows = [];
  for (const model of models) {
    const sizes = [...new Set(tracks.flatMap(track => (track.run.results || [])
      .filter(row => row.model === model && !!row.mtp === !!track.mtp && (row.concurrency || 1) === state.comparisonConcurrency)
      .map(row => row.target_tokens)))].sort((a, b) => a - b);
    for (const size of sizes) {
      for (const track of tracks) {
        const representative = pick(track.run, model, size, track.mtp, METRICS[keys[0]]);
        if (!representative) continue;
        const color = tableRowColor(track.run, model, models);
        const cells = keys.map(key => {
          const metric = METRICS[key];
          const row = pick(track.run, model, size, track.mtp, metric);
          const value = row && row.status === "ok" ? metric.get(row) : null;
          return `<td class="num measure-cell" data-measure="${key}">${value == null ? "?" : n(value) + metricEstimateMark(row, metric)}</td>`;
        }).join("");
        rows.push(`<tr class="colored-result-row" style="--table-color:${color}" data-model="${esc(model)}" data-benchmark="${esc(track.run.id)}"><td class="model-cell">${esc(model)}</td><td class="num">${fmtTokens(size)}</td><td class="benchmark-cell"><span class="table-benchmark-name" title="${esc(track.name)}">${esc(track.name)}</span></td>${cells}<td>${statusCell(representative)}</td></tr>`);
      }
    }
  }
  if (!rows.length) return '<p class="dim">No measurements for the selected models and query count.</p>';
  return `<table class="measurements-table"><thead><tr><th>Model</th><th>Prompt tokens</th><th>Benchmark</th>${keys.map(key => `<th class="num" data-measure="${key}">${esc(METRICS[key].label)} <small>(${esc(METRICS[key].unit)})</small></th>`).join("")}<th>Status</th></tr></thead><tbody>${rows.join("")}</tbody><tfoot><tr><td colspan="${4 + keys.length}">One row per model, prompt size and benchmark. Each measure reports its median successful repeat. * Estimated token counts. Cell colors follow the Color by selection.</td></tr></tfoot></table>`;
}

init();
