/* UI logic. Plain DOM, no framework: one file, no build step, works offline. */

const state = {
  env: null,
  runs: [],              // summaries
  runCache: {},          // id -> full run
  selected: null,        // results tab
  compare: new Set(),
  metrics: new Set(["prefill_tps", "decode_tps"]),
  visibleModels: null,
  colorBy: "models",
  mtpFilter: "all",
  sizes: new Set(),
  concurrencyLevels: new Set([1]),
  comparisonConcurrency: 1,
  queryLevels: null,      // concurrent-query levels drawn as separate lines; null = the single comparisonConcurrency
  promptLevels: null,     // prompt lengths drawn as separate lines on the concurrency axis; null = best covered
  resultScope: null,
  colorTouched: false,
  promptsTouched: false,
  levelsTouched: false,   // until the user picks levels or metrics, the view chooses ones that show every test
  metricsTouched: false,
  resultView: "summary",
  selectionInitialized: false,
  resultsRender: 0,
  detailSignature: null,
  models: new Set(),
  metadataOverrides: {label: false, notes: false},
  cursor: 0,
  liveResults: [],
  openNotes: new Set(),
  // Curves switched on or off from the chart key, by run key. Unlisted keys follow the selection.
  curveChoice: new Map(),
  poller: null,
  pollBusy: false,
  liveRunId: null,
  liveSignature: null,
  idleWatcher: null,
  benchmarkRunning: false,
  extension: null,
  merge: null,            // the open merge dialog: ticked benchmarks, confirmations and the server's plan
  mergeSuggestions: [],   // same-machine groups found by the helper
  detectedEndpoint: null,
  detectedEndpointProvider: null,
  conditions: null,
  comparisonLayout: "overview",
  comparisonAxis: "context",
  scaling: {tab:"throughput",prompt:null,model:null,selectedGroup:null,n:null},
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

function browserMode() { return typeof window !== "undefined" && window.WATTEVER_STATIC === true; }
function genericHelper() { return !browserMode() && $("#helper-provider")?.value === "openai"; }
function providerManaged() { return browserMode() || genericHelper(); }
function runProviderManaged(run) { return run.measurement_mode === "browser" || run.config?.provider === "openai"; }
function executionGroup(run, row) {
  const session = (run.sessions || []).find(item => item.id === row.session_id);
  return (run.engine_groups || []).find(group => group.id === session?.engine_group_id);
}
function rowProviderManaged(run, row) {
  const group = executionGroup(run, row);
  return group ? group.config?.provider === "openai" : runProviderManaged(run);
}
function managedRunConfig(run) {
  return (run.engine_groups || []).find(group => group.config?.provider === "lmstudio")?.config || run.config || {};
}
function availableMeasurementRuns(runs) {
  return runs.flatMap(run => {
    if (run._measurement_view || (run.parent_run_id && !run.engine_groups?.length)) return [run];
    const primary = !run.engine_groups?.length ? [{...run,_measurement_view:true}] : run.engine_groups.map(group => ({
    ...run, ...group, id: run.id + "::" + group.id, parent_run_id: run.id,
    _measurement_view:true,
    label: (run.label || run.id) + " · " + group.label, engine_groups: undefined,
    sessions: (run.sessions || []).filter(session => session.engine_group_id === group.id),
    results: (run.results || []).filter(row => executionGroup(run, row)?.id === group.id),
    }));
    const comparison = run.storage_comparison;
    if (!comparison || comparison.protocol !== "wattever-storage-comparison-v1" || !comparison.alternate_results?.length) return primary;
    const alternates = [], baselines = [];
    for (const view of primary) {
      const rows = comparison.alternate_results.filter(row=>!view.parent_run_id || executionGroup(run,row)?.id === view.id.slice(run.id.length+2));
      if (!rows.length) continue;
      const ids = new Set(rows.map(row=>row.session_id));
      const originalRows = comparison.baseline_results || [];
      const models = new Set(rows.map(row=>row.model));
      const baselineRows = originalRows.filter(row=>models.has(row.model) && (!view.parent_run_id || executionGroup(run,row)?.id === view.id.slice(run.id.length+2)));
      let baselineId = view.id;
      if (baselineRows.length) {
        // Storage labels describe only the model cases present in this experiment.
        const selected = new Set(baselineRows.map(row=>JSON.stringify([row.model,!!row.mtp,row.concurrency||1,row.target_tokens,row.repeat||0])));
        const covered = view.results.filter(row=>selected.has(JSON.stringify([row.model,!!row.mtp,row.concurrency||1,row.target_tokens,row.repeat||0])));
        const signature = row=>JSON.stringify([row.model,!!row.mtp,row.concurrency||1,row.target_tokens,row.repeat||0,row.session_id,row.status,row.wall_s,row.prompt_tokens,row.completion_tokens,row.prefill_tps,row.decode_tps,row.ttft_s]);
        const recorded = new Set(covered.map(signature));
        const freshBaseline = baselineRows.some(row=>!recorded.has(signature(row)));
        if (freshBaseline) {
          const sessionIds = new Set(baselineRows.map(row=>row.session_id));
          const sessions = (run.sessions || []).filter(session=>sessionIds.has(session.id));
          const last = sessions[sessions.length-1];
          baselineId = view.id+"::storage:baseline";
          baselines.push({...view,id:baselineId,parent_run_id:run.id,
            label:view.label+" · "+(comparison.baseline_label || "Baseline storage"),
            storage_condition:comparison.baseline_label || "Baseline storage",storage_baseline:true,
            config:last?.config || last?.execution_config || view.config,
            hardware:last?.hardware || view.hardware,sessions,results:baselineRows});
        } else if (covered.length === view.results.length) {
          view.label = view.label.replace(/\s*\|\s*Samsung SATA$/i,"") + " · " + (comparison.baseline_label || "Primary storage");
          view.storage_condition = comparison.baseline_label || "Primary storage";
        }
        const sessions = new Set(view.results.map(row=>row.session_id));
        view.sessions = (run.sessions || []).filter(session=>sessions.has(session.id));
      }
      const actualSessions = (run.sessions || []).filter(session=>ids.has(session.id));
      const last = actualSessions[actualSessions.length-1];
      alternates.push({...view,id:view.id+"::storage:alternate",parent_run_id:run.id,
        label:view.label.replace(" · " + (comparison.baseline_label || "Primary storage"),"") + " · " + (comparison.alternative_label || "Alternative storage"),
        storage_condition:comparison.alternative_label || "Alternative storage",storage_alternative:true,
        storage_baseline_id:baselineId,
        config:last?.config || last?.execution_config || view.config,
        hardware:last?.hardware || view.hardware,
        sessions:actualSessions,results:rows});
    }
    return [...primary,...baselines,...alternates];
  });
}
function measurementRuns(runs) {
  const variants = availableMeasurementRuns(runs);
  // A live single-engine benchmark initially has its root ID as its condition.
  // Consolidation keeps that benchmark ID but replaces its primary view with
  // engine-specific views. Preserve the explicitly selected whole benchmark.
  // Empty selections and already selected engine/storage subsets stay intact.
  if (state.conditions?.size) {
    for (const run of runs) {
      if (run._measurement_view || !state.conditions.has(run.id) || variants.some(view=>view.id === run.id)) continue;
      const replacements = variants.filter(view=>view.parent_run_id === run.id && !view.storage_alternative && !view.storage_baseline);
      if (!replacements.length) continue;
      state.conditions.delete(run.id);
      replacements.forEach(view=>state.conditions.add(view.id));
    }
  }
  return variants.filter(run=>state.conditions === null ? !run.storage_alternative && !run.storage_baseline : state.conditions.has(run.id));
}

/* Results can show several concurrent-query levels at once. With no explicit
 * choice the single comparisonConcurrency level applies. */
function activeQueryLevels() {
  return state.queryLevels?.size ? [...state.queryLevels].sort((a,b)=>a-b) : [state.comparisonConcurrency];
}
function levelOn(count) {
  return state.queryLevels?.size ? state.queryLevels.has(count) : count === state.comparisonConcurrency;
}
function activePromptLevels() {
  if (state.promptLevels?.size) return [...state.promptLevels].sort((a,b)=>a-b);
  const best = state.resultScope?.bestPrompt;
  return best == null ? [] : [best];
}
/* What the ticked benchmarks actually recorded: the only values worth offering. */
function resultLevels(runs) {
  const queries = new Set(), prompts = new Set(), coverage = new Map();
  for (const run of runs) for (const row of run.results || []) {
    const count = row.concurrency || 1;
    if (Number.isInteger(count) && count > 0) queries.add(count);
    if (!Number.isFinite(row.target_tokens)) continue;
    prompts.add(row.target_tokens);
    if (row.status !== "ok") continue;
    if (!coverage.has(row.target_tokens)) coverage.set(row.target_tokens, new Set());
    coverage.get(row.target_tokens).add(count);
  }
  const sorted = values => [...values].sort((a,b)=>a-b);
  // The prompt length measured at the most query levels makes the fullest scaling curve.
  const bestPrompt = sorted(prompts).reduce((best, size) => best === null || (coverage.get(size)?.size || 0) >= (coverage.get(best)?.size || 0) ? size : best, null);
  return {queries: sorted(queries), prompts: sorted(prompts), bestPrompt};
}
const AUTO_LINE_LIMIT = 8;
/* Until the user chooses for themselves, pick metrics and colours that show every passed test legibly. */
function autoResultDefaults(measured, models) {
  if (state.resultView !== "compare") return;
  const runs = splitRowConditions(measured), shown = shownModels(models);
  const axis = state.comparisonAxis, levels = axis === "context" ? activeQueryLevels() : activePromptLevels();
  if (!state.metricsTouched) {
    const missing = graphCoverage(runs, shown, selectedMetricKeys(), axis, levels);
    if (missing.unrecorded.length) missing.suggest.slice(0, 2).forEach(key => state.metrics.add(key));
  }
  if (!state.colorTouched) {
    // One model on one benchmark leaves colour free to tell the levels apart.
    const lineups = new Set(runs.flatMap(run => (run.results || []).filter(row => shown.includes(row.model)).map(row => runKey(run) + "|" + row.model)));
    state.colorBy = lineups.size === 1 && levels.length > 1 ? "levels" : state.colorBy === "levels" ? "models" : state.colorBy;
  }
}
function syncResultLevels(runs) {
  const scope = state.resultScope = resultLevels(runs);
  if (state.queryLevels?.size) {
    const kept = scope.queries.filter(value => state.queryLevels.has(value));
    state.queryLevels = kept.length ? new Set(kept) : null;
  }
  if (!state.levelsTouched) {
    const shown = shownModels(allModelsOf(runs));
    const lineups = new Set(runs.flatMap(run => (run.results || []).filter(row => shown.includes(row.model)).map(row => run.id + "|" + row.model + "|" + !!row.mtp)));
    const lines = lineups.size * scope.queries.length;
    // The other axis gets the same treatment: every prompt length while the chart stays readable.
    if (!state.promptsTouched) state.promptLevels = scope.prompts.length > 1 && lineups.size * scope.prompts.length <= AUTO_LINE_LIMIT ? new Set(scope.prompts) : null;
    state.queryLevels = scope.queries.length > 1 && lines <= AUTO_LINE_LIMIT ? new Set(scope.queries) : null;
    if (!state.queryLevels && scope.queries.length) state.comparisonConcurrency = scope.queries[0];
  }
  if (state.queryLevels?.size) state.comparisonConcurrency = Math.min(...state.queryLevels);
  else if (scope.queries.length && !scope.queries.includes(state.comparisonConcurrency)) state.comparisonConcurrency = scope.queries[0];
  if (state.promptLevels?.size) {
    const kept = scope.prompts.filter(value => state.promptLevels.has(value));
    state.promptLevels = kept.length ? new Set(kept) : null;
  }
  if (scope.queries.length < 2) state.comparisonAxis = "context";
  return scope;
}

function conditionRows(run,models=null,allConcurrency=false) {
  return (run?.results || []).filter(row=>(models ? models.includes(row.model) : state.visibleModels === null || state.visibleModels.has(row.model)) && (allConcurrency || levelOn(row.concurrency || 1)) &&
    (state.mtpFilter === "all" || (!runProviderManaged(run) && !!row.mtp === (state.mtpFilter === "on"))));
}
function chartConditions(runs,models,keys) {
  return runs.filter(run=>conditionRows(run,models).some(row=>row.status === "ok" ? keys.some(key=>Number.isFinite(METRICS[key].get(row))) : ["failed","skipped"].includes(row.status) && row.fail_kind !== "mtp_unsupported"));
}

function storageExperimentSummary(run) {
  const comparison = run.storage_comparison;
  if (!comparison?.alternate_results?.length || comparison.protocol !== "wattever-storage-comparison-v1") return "";
  const summaries = new Map();
  const baseline = new Map((comparison.baseline_results || []).map(row=>[JSON.stringify([row.model,!!row.mtp,row.concurrency||1,row.target_tokens,row.repeat||0]),row]));
  for (const row of comparison.alternate_results) {
    const original = baseline.get(JSON.stringify([row.model,!!row.mtp,row.concurrency||1,row.target_tokens,row.repeat||0]));
    const values = summaries.get(row.model) || [];
    if (row.status === "ok" && original?.status === "ok" && row.wall_s > 0 && original.wall_s > 0) values.push(original.wall_s/row.wall_s);
    summaries.set(row.model,values);
  }
  const cards = [...summaries].sort((a,b)=>(storageMedian(b[1])||0)-(storageMedian(a[1])||0)).map(([model,values])=>`<div class="storage-highlight"><span>${esc(model)}</span><strong>${values.length ? storageMedian(values).toFixed(2)+"×" : "No matched successes"}</strong><small>median completion speedup · ${values.length} matched cases</small></div>`).join("");
  return `<section class="recorded-experiment"><header><h3>Recorded storage experiment</h3><button type="button" class="btn-sm" data-compare-storage="${esc(run.id)}">Compare recorded storage</button><button type="button" class="btn-sm" data-storage-export="${esc(run.id)}">Export both conditions CSV</button></header><div class="condition-badges">${conditionBadge(comparison.baseline_label || "Primary storage")}${conditionBadge(comparison.alternative_label || "Alternative storage")}${conditionBadge(`${comparison.alternate_results.length} alternative observations`)}</div><div class="storage-highlights">${cards}</div><details class="adv"><summary>Experiment settings</summary><p class="hint">${esc(comparison.cache_policy || "Cache state not recorded")} Speedups use matching successful case ratios; failures and skipped cases have no ratio. Compare and Table retain each condition separately.</p></details></section>`;
}

function compareRecordedStorage(runId) {
  const run = state.runCache[runId];
  if (!run?.storage_comparison?.alternate_results?.length) return;
  const models = new Set(run.storage_comparison.alternate_results.map(row=>row.model));
  const available = availableMeasurementRuns([run]), alternatives = available.filter(view=>view.storage_alternative);
  const experimentIds = new Set(alternatives.flatMap(view=>[view.id,view.storage_baseline_id || view.id.replace(/::storage:alternate$/,"")]));
  const variants = available.filter(view=>experimentIds.has(view.id));
  state.compare = new Set([runId]); state.selected = runId;
  state.conditions = new Set(variants.map(view=>view.id)); state.visibleModels = models; state.curveChoice.clear();
  state.metrics = new Set(["wall_s"]); state.mtpFilter = "all";
  state.comparisonConcurrency = 1; state.queryLevels = null; state.promptLevels = null; state.levelsTouched = true; state.metricsTouched = true;
  state.comparisonLayout = "models"; state.comparisonAxis = "context"; state.colorBy = "benchmarks";
  renderMetricSeg(); renderRunsList(); showResultsView("compare");
}

function renderBenchmarkSummary(runs) {
  const container = $("#summary-body");
  if (!container) return;
  if (!runs.length) {container.innerHTML="";return;}
  const profiles = runs.map(displayBandwidthProfile);
  const samples = profiles.map(profile=>[profile?.storage_to_ram,...(profile?.storage_volumes||[])].filter(item=>item?.status === "ok" && item.bytes_per_second > 0));
  const key = sample=>JSON.stringify([storageKind(sample),sample.volume || sample.device_model || "unreported volume"]);
  const storage = new Map(samples.flatMap(list=>list.map(sample=>[key(sample),sample])));
  const descriptors = [...storage].map(([id,sample])=>({label:`${storageKind(sample) || "Storage"}${sample.volume ? ` (${sample.volume})` : ""} to RAM`,
    note:sample.device_model || (storageKind(sample) ? "Sequential transfer probe" : "Storage type unreported"),
    values:samples.map(list=>list.find(item=>key(item)===id)?.bytes_per_second)}));
  for (const [field,label] of [["ram_copy_single","RAM copy - 1 worker"],["ram_copy_parallel","RAM copy - parallel"]]) {
    const values=profiles.map(profile=>profile?.[field]?.status === "ok" ? profile[field].bytes_per_second : null);
    if(values.some(value=>value>0)) descriptors.push({label,note:field.startsWith("ram_copy") ? "Copied payload, not peak DRAM bus bandwidth" : "Pinned host-to-device transfer",values});
  }
  const devicePaths=profiles.map(gpuTransferPaths);
  for (const label of new Set(devicePaths.flatMap(paths=>paths.map(([label])=>label)))) {
    const values=devicePaths.map(paths=>{const item=paths.find(([name])=>name===label)?.[1];return item?.status === "ok" ? item.bytes_per_second : null;});
    descriptors.push({label,note:"Pinned RAM to the named installed GPU; physical slot wiring is unverified",values});
  }
  if (!descriptors.length) {container.innerHTML="";return;}
  const value=number=>number>0 ? (number/1e9).toFixed(2) : "Not measured";
  const ratios=samples.map(list=>{
    const ordered=list.slice().sort((a,b)=>a.bytes_per_second-b.bytes_per_second);
    return ordered.length>1 ? (ordered[ordered.length-1].bytes_per_second/ordered[0].bytes_per_second).toFixed(2)+"x" : "Not measured";
  });
  container.innerHTML=`<div class="card"><header><h3>Measured machine transfers</h3></header><p class="hint">GB/s - independent captures where available. Only recorded paths are shown.</p><div class="table-wrap"><table class="machine-transfers"><thead><tr><th>Measured path</th>${runs.map(run=>`<th>${esc(run.label || run.id)}</th>`).join("")}</tr></thead><tbody>${descriptors.map(item=>`<tr><td title="${esc(item.note)}">${esc(item.label)}</td>${item.values.map(number=>`<td>${esc(value(number))}</td>`).join("")}</tr>`).join("")}${samples.some(list=>list.length>1) ? `<tr><td>Fastest / slowest recorded storage read</td>${ratios.map(ratio=>`<td>${ratio}</td>`).join("")}</tr>` : ""}</tbody></table></div></div>`;
}

function helperEndpointConfig() {
  const provider=browserMode()?"openai":$("#helper-provider")?.value || "lmstudio";
  const override=$(browserMode() ? "#browser-name-override" : "#helper-name-override")?.value.trim() || "";
  const identity=state.detectedEndpointProvider && state.detectedEndpointProvider!==provider?null:state.detectedEndpoint;
  const publicIdentity={endpoint_id:identity?.id || "",endpoint_label:override || identity?.label || "",
    endpoint_identity:identity || undefined,endpoint_name_override:override};
  if (browserMode()) return {provider:"openai",...publicIdentity};
  if(provider==="lmstudio")return {provider,...publicIdentity,endpoint_inference_local:!!$("#helper-local")?.checked};
  // The key is typed here and travels only in same-origin request bodies to the
  // local helper. Nothing in this page stores it: see withoutCredentials().
  const key = $("#helper-key")?.value.trim() || "";
  return {provider,
    endpoint_base_url: $("#helper-url")?.value.trim() || "",
    ...(key ? {endpoint_api_key: key} : {}),
    ...publicIdentity,
    endpoint_inference_local: !!$("#helper-local")?.checked};
}
/* Remembered form state must never carry the typed credential. */
function withoutCredentials(config) {
  const {endpoint_api_key, ...rest} = config || {};
  return rest;
}
/* Detects that the key changed without keeping a copy of it in signatures. */
function credentialMark(value) {
  if (!value) return "";
  let hash = 2166136261;
  for (let index = 0; index < value.length; index++) hash = Math.imul(hash ^ value.charCodeAt(index), 16777619);
  return value.length + ":" + (hash >>> 0).toString(36);
}
function rememberEndpoint(payload,provider) {
  state.detectedEndpointProvider=provider;
  const identity=payload?.endpoint_identity || payload?.provider?.identity || payload?.lms?.endpoint_identity;
  state.detectedEndpoint=identity && typeof identity === "object" ? identity : provider === "lmstudio" ? {id:"lmstudio",label:"LM Studio",source:"declared_provider"} : null;
  return state.detectedEndpoint;
}
const OPENAI_URL_STORAGE = "wattever-openai-base-url";
const HELPER_PROVIDER_STORAGE = "wattever-helper-provider";
const PLAIN_ENDPOINT_STORAGE = "wattever-plain-endpoint";
/* The address the user chose to measure as a plain endpoint although it is this computer's LM Studio. */
function plainEndpoint() { return state.plainEndpoint ?? localStorage.getItem(PLAIN_ENDPOINT_STORAGE) ?? ""; }
function setPlainEndpoint(url) {
  state.plainEndpoint = url;
  if (url) localStorage.setItem(PLAIN_ENDPOINT_STORAGE, url); else localStorage.removeItem(PLAIN_ENDPOINT_STORAGE);
}

function helperConnectionSignature() {
  const config = helperEndpointConfig();
  return JSON.stringify({provider:config.provider,base:config.endpoint_base_url,key:credentialMark(config.endpoint_api_key),
    model:$("#helper-model")?.value.trim() || "",local:config.endpoint_inference_local});
}
function addComputeClaim() {
  const node = document.createElement("div"); node.className = "row compute-claim";
  node.innerHTML = '<div class="field"><label>Precision<select data-claim="precision"><option>FP4</option><option>INT4</option><option>FP8</option><option>INT8</option><option>FP16</option><option>BF16</option><option>FP32</option></select></label></div><div class="field"><label>Throughput<input data-claim="value" type="number" min="0" step="any"></label></div><div class="field"><label>Evidence<select data-claim="kind"><option value="advertised_peak">Advertised peak</option><option value="measured">Measured separately</option><option value="estimate">Estimate</option></select></label></div><div class="field"><label>Sparsity<select data-claim="sparsity"><option value="unknown">Unknown</option><option value="dense">Dense</option><option value="sparse">Sparse</option></select></label></div><div class="field"><label>Execution path<input data-claim="execution_path" maxlength="120" placeholder="Tensor cores / CUDA cores"></label></div><div class="field"><label>Public source / method<input data-claim="source" maxlength="300"></label></div><button type="button" class="btn-sm">Remove</button>';
  node.querySelector("button").addEventListener("click", () => node.remove());
  $("#compute-claims").appendChild(node);
}
function publicRunMetadata() {
  const reproducibility = {};
  for (const [field, id] of Object.entries({engine:"meta-engine", engine_version:"meta-engine-version", quantization:"meta-quant", model_revision:"meta-revision", context_tokens:"meta-context", kv_cache_dtype:"meta-kv", mtp_draft_tokens:"meta-mtp"})) {
    const value = $("#" + id)?.value.trim();
    if (value) reproducibility[field] = ["context_tokens", "mtp_draft_tokens"].includes(field) ? Number(value) : value;
  }
  const performance_claims = $$(".compute-claim").filter(node => node.querySelector('[data-claim="value"]').value !== "").map(node => {
    const claim = Object.fromEntries(Array.from(node.querySelectorAll("[data-claim]")).map(input => [input.dataset.claim, input.value.trim()]));
    claim.value = Number(claim.value); claim.unit = claim.precision.startsWith("INT") ? "TOPS" : "TFLOP/s";
    return claim;
  });
  return {reproducibility, performance_claims};
}
function updateProviderControls() {
  const managed = providerManaged();
  ["#offload", "#load-timeout", "#headroom", "#context-policy", "#fallback", "#skip-after-timeout", "#no-think"].forEach(selector => {
      const node = $(selector); if (node) (node.closest(".field") || node.closest("label") || node).hidden = selector === "#no-think" || managed;
  });
  // MTP can always be chosen; the benchmark resolves whether a model can be launched with it.
  // A model Wattever loads is tried and skipped when it has no MTP head. An endpoint serves its
  // models as it decides, so only its standard pass runs: see mtpPlan().
  const mtpField = $("#mtp-field");
  if (mtpField) {
    mtpField.hidden = browserMode();
    $("#mtp-note").textContent = genericHelper()
      ? "This endpoint decides MTP itself, so Wattever cannot switch it: Compare both runs the standard pass and records that the MTP pass was not run. To launch models with MTP, choose Provider: LM Studio (managed) and click Connect."
      : "Off is the standard benchmark. On and Compare both try each selected model with MTP and skip the ones without a bundled MTP head; Compare both runs every test twice.";
  }
  if (!browserMode()) {
    const generic = genericHelper();
    ["#helper-url", "#helper-key", "#helper-model", "#helper-local"].forEach(selector => { const node = $(selector); if (node) (node.closest(".field") || node.closest("label") || node).hidden = !generic; });
    if ($("#helper-name-override")) $("#helper-name-override").disabled = false;
    if ($("#helper-local-note")) $("#helper-local-note").hidden = !generic;
  }
}
async function refreshOpenAiCatalog(config = helperEndpointConfig()) {
  if (browserMode() || config.provider !== "openai" || !config.endpoint_base_url) return false;
  const attempt = state.connectionAttempt;
  const found = await api("/api/endpoint/check", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(config)});
  if (attempt !== state.connectionAttempt) return false;  // the user connected something else meanwhile
  // This computer's own LM Studio is not an external endpoint: connected as managed, its models
  // can be loaded with a chosen context, GPU placement and MTP. The switch is shown and reversible.
  if (found.helper_lmstudio && plainEndpoint() !== config.endpoint_base_url) {
    let switched = null;
    try { switched = await connectLmStudio(config.endpoint_base_url); }
    catch (_) { /* LM Studio's own catalog did not answer: it stays a plain endpoint */ }
    if (switched !== null) return switched;
  }
  rememberEndpoint(found, config.provider);
  const models = Array.isArray(found) ? found : found.models || found.data || [];
  if (!models.length) throw new Error(found.error || "No model IDs found.");
  state.env = {...state.env, models, default_models: models.map((model) => model.id),
    last_config: {...state.env.defaults, ...state.env.last_config, ...workloadChoices(), ...withoutCredentials(config), models: models.map(model => model.id)},
    lms: {server: true, cli: false}, capabilities: {...state.env.capabilities, context_planning: false}};
  state.providerNeedsConnection = false;
  state.endpointSignature = helperConnectionSignature();
  renderEnv();
  updateProviderControls();
  const status = $("#helper-status");
  if (status) {
    status.textContent = "Ready · " + (helperEndpointConfig().endpoint_label || "OpenAI-compatible endpoint") + " · " + models.length + " model(s)";
    status.dataset.state = "ready";
  }
  return true;
}

/* The workload already chosen in the form. Connecting changes where models come from, not what to measure. */
function workloadChoices() {
  if (!state.env || !state.sizes || !state.concurrencyLevels) return {};
  return {sizes: [...state.sizes], concurrency_levels: [...state.concurrencyLevels].sort((a, b) => a - b),
    repeats: Number($("#repeats")?.value) || 1, max_tokens: Number($("#max-tokens")?.value) || 256, mtp_modes: mtpModes()};
}
/* Connect LM Studio as managed. `switchedFrom` is the endpoint address that turned out to be this computer's LM Studio. */
async function connectLmStudio(switchedFrom = "") {
  const status = $("#helper-status"), keep = workloadChoices(), chosen = [...(state.models || [])];
  const attempt = state.connectionAttempt;
  // Ask for LM Studio's own catalog: plain /api/env answers for the last saved connection,
  // so a saved endpoint that is offline would report LM Studio as having no models.
  const env = await api("/api/env?provider=lmstudio");
  if (attempt !== state.connectionAttempt) return false;  // the user connected something else meanwhile
  if (switchedFrom) $("#helper-provider").value = "lmstudio";  // only once LM Studio has answered
  rememberEndpoint(env,"lmstudio");
  const served = new Set(env.models.filter(model => model.type !== "embeddings").map(model => model.id));
  const models = chosen.filter(id => served.has(id));
  env.last_config = {...env.defaults, ...keep, provider: "lmstudio", models: models.length ? models : env.default_models || []};
  localStorage.setItem(HELPER_PROVIDER_STORAGE, "lmstudio");
  state.env = env; renderEnv(); updateProviderControls();
  state.providerNeedsConnection = false;
  state.endpointSignature = helperConnectionSignature(); updatePlan();
  status.textContent = "Ready · LM Studio · " + env.models.length + " model(s)";
  status.dataset.state = "ready";
  const note = $("#helper-switch-note");
  if (note) {
    note.hidden = !switchedFrom;
    if (switchedFrom) {
      note.dataset.endpoint = switchedFrom;
      $("#helper-switch-text").textContent = switchedFrom + " is the LM Studio on this computer, so Wattever connected it as LM Studio (managed): context, GPU placement and MTP are yours to set.";
    }
  }
  return true;
}
/* Opt out of the managed switch: measure this computer's LM Studio as served, like any other endpoint.
 * Remembered for that address until LM Studio (managed) is connected by hand. */
async function usePlainEndpoint() {
  const note = $("#helper-switch-note"), chosen = [...state.models];
  setPlainEndpoint(note.dataset.endpoint || "");
  note.hidden = true;
  $("#helper-provider").value = "openai"; $("#helper-url").value = plainEndpoint();
  updateProviderControls();
  await connectHelperEndpoint();
  const served = chosen.filter(id => state.env.models.some(model => model.id === id));
  if (genericHelper() && served.length) { state.models = new Set(served); renderModels(); updatePlan(); }
}

async function connectHelperEndpoint() {
  const status = $("#helper-status"), button = $("#helper-connect");
  if ($("#helper-switch-note")) $("#helper-switch-note").hidden = true;
  state.connectionAttempt = (state.connectionAttempt || 0) + 1;  // retires a boot-time connection still in flight
  status.dataset.state = "connecting";
  state.detectedEndpoint = null; state.providerNeedsConnection = true;
  button.disabled = true; status.textContent = "Connecting…";
  try {
    const config = helperEndpointConfig();
    if (config.provider === "openai" && !config.endpoint_base_url) throw new Error("Enter your endpoint URL.");
    if (config.provider === "openai") {
      const manual = $("#helper-model").value.trim();
      try {
        // Nothing more to do when it was this computer's LM Studio, now connected as managed.
        if (!(await refreshOpenAiCatalog(config)) || !genericHelper()) return;
      } catch (error) {
        if (manual) {
          state.env = {...(state.env || {}), models: [{id: manual, type: "llm", state: "manual model ID; discovery unavailable"}],
            last_config: {...state.env.defaults, ...state.env.last_config, ...workloadChoices(), ...withoutCredentials(config), models: [manual]},
            default_models: [manual], lms: {server: true, cli: false}};
          state.providerNeedsConnection = false;
          state.endpointSignature = helperConnectionSignature();
          renderEnv();
          updateProviderControls();
          status.textContent = "Manual model ID ready; discovery unavailable. Availability is checked when a request is sent.";
          status.dataset.state = "ready";
        } else throw error;
      }
      state.env.last_config = {...state.env.defaults, ...withoutCredentials(config), models: state.env.default_models};
      localStorage.setItem(OPENAI_URL_STORAGE, config.endpoint_base_url);
      localStorage.setItem(HELPER_PROVIDER_STORAGE, "openai");
      updatePlan();
      return;
    }
    setPlainEndpoint("");  // choosing LM Studio (managed) by hand ends the plain-endpoint choice
    await connectLmStudio();
  } catch (error) { status.textContent = "Connection failed: " + error.message; status.dataset.state = "error"; }
  finally { button.disabled = false; }
}

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
    if (!window.WatteverBrowser?.enabled || typeof window.WatteverBrowser.request !== "function") throw new Error("Browser application failed to load. Reload this page.");
    const result = await window.WatteverBrowser.request(path, opts);
    updateBrowserStorageWarning(result);
    if (Object.prototype.hasOwnProperty.call(result || {}, "community_error")) {
      const warning = $("#community-warning");
      if (warning) { warning.hidden = !result.community_error; warning.innerHTML = result.community_error ? banner("warn", "Shared library unavailable", esc(result.community_error)) : ""; }
    }
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
  wall_s: {label:"Completion time",kind:"line",unit:"s",better:"low",get:r=>r.wall_s,blurb:"Elapsed time until all queries in the case complete."},
  prefill_tps: {
    label: "Prefill speed", kind: "line", unit: "tk/s", better: "high",
    get: (r) => r._metricValues?.prefill_tps ?? collectiveRate(r, "prefill_tps"),
    blurb: "One stream: client prefill rate. Concurrent group: total prompt tokens ÷ latest time to first token (queueing and serial load included).",
  },
  group_output_tps: {
    label: "End-to-end output throughput", kind: "line", unit: "tk/s", better: "high",
    get: r => r._metricValues?.group_output_tps ?? phaseMetrics()?.outputRate(r) ?? null,
    blurb: "Total output tokens divided by group wall time, including prefill and queueing, for every request count.",
  },
  decode_tps: {
    label: "Total decode throughput", kind: "line", unit: "tk/s", better: "high",
    get: r => r._metricValues?.decode_tps ?? decodeRate(r),
    blurb: "Scoped decode tokens divided by the union of post-first-token decode intervals. Excludes TTFT and gaps with no decoding request; overlapping intervals count once.",
  },
  per_request_decode_tps: {
    label: "Decode per request (median)",kind:"line",unit:"tk/s",better:"high",get:r=>r._metricValues?.per_request_decode_tps ?? requestDecodeRate(r),
    blurb:"Median recorded per-request generation speed after the first token. Time to first token is excluded.",
  },
  decode_s: {label:"Decode time",kind:"line",unit:"s",better:"low",get:r=>r._metricValues?.decode_s ?? phaseMetrics()?.decodePhase(r)?.decode_s ?? null,
    blurb:"Duration of post-first-token decode intervals, merged across concurrent requests. TTFT and gaps outside all decode intervals are excluded."},
  prefill_s: {label:"Prefill time (client TTFT proxy)",kind:"line",unit:"s",better:"low",get:r=>r._metricValues?.prefill_s ?? phaseMetrics()?.prefillPhase(r)?.prefill_s ?? null,
    blurb:"Client-observed request-start to first-output intervals, merged across requests. Includes network and queueing; isolated engine prefill timing is separate."},
  ttft_s: {
    label: "Time to first token", kind: "line", unit: "s", better: "low",
    get: (r) => r.ttft_s ?? storageMetricValue(r,"ttft_s"),
    blurb: "Client wait until the first output token, including transport, queueing and prompt processing.",
  },
  j_per_1k_prefill: {
    label: "Energy / 1k prefill", kind: "bars", unit: "J/1k tk", better: "low",
    get: (r) => r.power && r.power.j_per_1k_prefill,
    blurb: "Measured GPU joules per thousand prompt tokens ingested.",
  },
  j_per_token_decode: {
    label: "GPU decode energy / decode token", kind: "bars", unit: "J/tk", better: "low",
    get: (r) => r.power && r.power.j_per_token_decode,
    blurb: "Single-request GPU decode-phase joules per scoped decode token; excludes prefill energy. Concurrent whole-case energy is a separate metric.",
  },
  gpu_energy_per_output_token: {label:"Whole-case GPU energy / output token",kind:"bars",unit:"J/tk",better:"low",
    get:r=>globalThis.WatteverConcurrency?.measurements(r).energy ?? null,
    blurb:"Verified whole-case GPU energy, including prefill, divided by measured completion tokens. Separate from decode-phase energy."},
  prefill_gpu_w: {
    label: "GPU watts (prefill)", kind: "bars", unit: "W", better: null,
    get: (r) => r.power && r.power.prefill_gpu_w,
    blurb: "Average measured board power while ingesting the prompt.",
  },
  vram_peak_mb: {
    label: "VRAM peak", kind: "line", unit: "MiB", better: null,
    get: (r) => r.vram_peak_mb,
    blurb: "Peak simultaneous monitored GPU memory sampled during the test. Per-card capacities remain separate; this does not establish engine placement.",
  },
};
/* Control-panel wording: short names on the toggle buttons, grouped by what they answer. */
const METRIC_GROUPS = [["speed", "Speed", "tokens per second"], ["time", "Time", "seconds"], ["hardware", "Energy & memory", ""]];
const METRIC_CHIPS = {
  prefill_tps: ["Prefill speed", "speed"], decode_tps: ["Decode speed", "speed"],
  per_request_decode_tps: ["Decode per request", "speed"], group_output_tps: ["End-to-end output", "speed"],
  ttft_s: ["Time to first token", "time"], wall_s: ["Completion time", "time"],
  prefill_s: ["Prefill time", "time"], decode_s: ["Decode time", "time"],
  j_per_1k_prefill: ["Energy / 1k prefill", "hardware"], j_per_token_decode: ["Decode energy / token", "hardware"],
  gpu_energy_per_output_token: ["Whole-case energy / token", "hardware"],
  prefill_gpu_w: ["GPU watts (prefill)", "hardware"], vram_peak_mb: ["VRAM peak", "hardware"],
};
for(const [key,metric] of Object.entries(METRICS)) {
  [metric.short, metric.group] = METRIC_CHIPS[key] || [metric.label, "hardware"];
  const get=metric.get;
  metric.get=row=>row?._metricValues && Object.prototype.hasOwnProperty.call(row._metricValues,key) ? row._metricValues[key] : get(row);
}

/* colour follows the run, fixed by creation order over every known run, so
 * selecting or deselecting runs never repaints the survivors */
function runColorIndex(runId) {
  runId = String(runId).split("::")[0];
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
const FIELD_HELP = {};
function applyFieldTooltips() {
  $$("input, select, textarea").forEach((input) => {
    if (input.title) return;
    const label = input.closest("label") || (input.id ? document.querySelector(`label[for="${input.id}"]`) : null);
    const field = input.closest(".field");
    const help = field && field.querySelector("[data-help]");
    input.title = (label && label.dataset.help) || (help && help.dataset.help) || FIELD_HELP[input.id] || "Include " + ((label && label.textContent.trim()) || "this option") + " in the selection.";
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
    if (!$("#browser-endpoint").value.trim()) throw new Error("Enter your endpoint URL.");
    window.WatteverBrowser.configure({endpoint: $("#browser-endpoint").value.trim(), apiKey: $("#browser-key").value,
      endpoint_name_override: $("#browser-name-override")?.value.trim() || undefined,
      hardware: {gpu: $("#browser-gpu").value.trim(), cpu: $("#browser-cpu").value.trim(), ram_gb: Number($("#browser-ram").value) || null, vram_gb: Number($("#browser-vram").value) || null}});
    state.detectedEndpoint = null;
    const found = await window.WatteverBrowser.connect();
    $("#browser-key").value = "";
    state.env = await api("/api/env"); rememberEndpoint(found,"openai"); if(!state.detectedEndpoint) rememberEndpoint(state.env,"openai"); renderEnv();
    status.textContent = "Ready · " + (helperEndpointConfig().endpoint_label || "OpenAI-compatible endpoint");
  } catch (error) { status.textContent = "Connection failed: " + error.message + ". Check the endpoint, CORS and local network permission."; }
  finally { button.disabled = false; }
}
function benchmarkFilename(id) {
  const value = typeof id === "string" && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$/.test(id) ? id : "benchmark";
  return value + ".json";
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
    let result = await api(path);
    const csv = path.startsWith("/api/export.csv");
    if (!csv && result.community) { result = {...result}; delete result.community; }
    downloadBlob(csv ? result : JSON.stringify(result, null, 2), csv ? "wattever.csv" : benchmarkFilename(result.id), csv ? "text/csv" : "application/json");
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
  if (!browserMode() && $("#helper-endpoint")) {
    $("#helper-endpoint").hidden = false;
    $("#helper-provider").addEventListener("change", () => { state.providerNeedsConnection = true; state.connectionAttempt = (state.connectionAttempt || 0) + 1; state.detectedEndpoint=null; state.detectedEndpointProvider=null; if ($("#helper-switch-note")) $("#helper-switch-note").hidden = true; updateProviderControls(); updatePlan(); });
    $("#helper-connect").addEventListener("click", connectHelperEndpoint);
    $("#helper-plain-endpoint")?.addEventListener("click", usePlainEndpoint);
    ["#helper-url", "#helper-key", "#helper-name-override", "#helper-local", "#helper-model"].forEach(selector => $(selector)?.addEventListener("input", updatePlan));
    updateProviderControls();
  }
  $("#add-compute-claim")?.addEventListener("click", addComputeClaim);
  if (browserMode()) {
    $("#browser-connect").hidden = false;
    $("#browser-import").hidden = false;
    $("#browser-connect-button").addEventListener("click", connectBrowser);
    $("#browser-import-file").addEventListener("change", importBrowserRun);
    document.addEventListener("click", browserDownload);
    ["#offload", "#mtp-chips", "#load-timeout", "#headroom", "#gpu-index", "#runtime-gpu-placement", "#fallback", "#skip-after-timeout", "#warmup", "#no-think"].forEach(selector => {
      const node = $(selector); if (node) (node.closest(".field") || node.closest("label" ) || node).hidden = true;
    });
  }
  $("#extend-close").addEventListener("click", () => $("#extend-dialog").close());
  if ($("#merge-dialog")) {
    for (const id of ["#merge-close", "#merge-cancel"]) $(id).addEventListener("click", () => $("#merge-dialog").close());
    $("#merge-dialog").addEventListener("close", () => { state.merge = null; });
    for (const type of ["change", "input", "click"]) $("#merge-content").addEventListener(type, mergeDialogChanged);
    $("#merge-start").addEventListener("click", startMerge);
  }
  $("#extend-dialog").addEventListener("close", () => {
    // A queued close event can arrive after the dialog has already reopened.
    if (!$("#extend-dialog").open) state.extension = null;
  });
  $("#extend-start").addEventListener("click", startExtension);
  decorateHelp();
  Tables.init();
  api("/api/version").then((info) => {
    $("#app-version").textContent = "v" + info.version;
  }).catch(() => { $("#app-version").textContent = "Server unavailable"; });
  $$(".tab").forEach((tab) => tab.addEventListener("click", () => showPanel(tab.dataset.panel)));
  document.addEventListener("visibilitychange", () => updateSystemPolling($("#panel-system").classList.contains("active")));
  $("#export-graphs")?.addEventListener("click", exportVisibleGraphs);
  $("#share-benchmarks")?.addEventListener("click", shareBenchmarks);
  $("#theme-toggle").addEventListener("click", toggleTheme);
  $("#clean-run").addEventListener("click", prepareCleanRun);
  // Control panel: toggles only. Each handler changes one piece of state and redraws in place.
  $("#result-metrics")?.addEventListener("click", event => {
    const chip = event.target.closest?.("[data-metric]");
    if (!chip) return;
    const key = chip.dataset.metric;
    state.metricsTouched = true;
    if (state.metrics.has(key)) state.metrics.delete(key); else state.metrics.add(key);
    redrawResultsWithoutScrolling();
  });
  $("#result-levels")?.addEventListener("click", event => {
    const every = event.target.closest?.("[data-level-all]");
    if (every) return applyCoverageAction({dataset: {coverage: "levels"}});
    const chip = event.target.closest?.("[data-level]");
    if (!chip) return;
    const value = Number(chip.dataset.level), prompts = chip.dataset.levelKind === "prompt";
    const picked = new Set(prompts ? activePromptLevels() : activeQueryLevels());
    if (picked.has(value)) picked.delete(value); else picked.add(value);
    if (!picked.size) return;   // one value always stays ticked; an empty chart answers nothing
    if (prompts) state.promptsTouched = true; else state.levelsTouched = true;
    if (prompts) state.promptLevels = picked;
    else { state.queryLevels = picked; state.comparisonConcurrency = Math.min(...picked); }
    redrawResultsWithoutScrolling();
  });
  $$("[data-comparison-layout]").forEach(button => button.addEventListener("click", () => {
    state.comparisonLayout = button.dataset.comparisonLayout; redrawResultsWithoutScrolling();
  }));
  $$("[data-mtp-filter]").forEach(button => button.addEventListener("click", () => {
    state.mtpFilter = button.dataset.mtpFilter; redrawResultsWithoutScrolling();
  }));
  $("#chart-key")?.addEventListener("mouseover", event => highlightSeries(event.target.closest?.("[data-hl-tag]")));
  $("#chart-key")?.addEventListener("mouseleave", () => highlightSeries(null));
  $("#chart-key")?.addEventListener("focusin", event => highlightSeries(event.target.closest?.("[data-hl-tag]")));
  $("#chart-key")?.addEventListener("focusout", () => highlightSeries(null));
  // A benchmark in the key is a switch for its curves.
  $("#chart-key")?.addEventListener("click", async event => {
    const entry = event.target.closest?.("[data-curve]");
    if (!entry) return;
    const key = entry.dataset.curve;
    state.curveChoice.set(key, entry.getAttribute("aria-pressed") !== "true");
    await redrawResultsWithoutScrolling();
    [...$$("#chart-key [data-curve]")].find(node => node.dataset.curve === key)?.focus();
  });
  document.addEventListener("click", event => {
    const link = event.target.closest?.("[data-open-graph]");
    if (link) openGraphFor(JSON.parse(link.dataset.openGraph));
    const remedy = event.target.closest?.("[data-coverage]");
    if (remedy) applyCoverageAction(remedy);
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
  $("#start-btn").addEventListener("click", startBench);
  $("#cancel-btn").addEventListener("click", cancelBench);
  $("#refresh-runs").addEventListener("click", async () => { if (browserMode()) { await window.WatteverBrowser.loadCommunity(true); for (const [id, run] of Object.entries(state.runCache)) if (run.community) delete state.runCache[id]; state.detailSignature = null; } await loadRuns(); });
  $("#live-issues")?.addEventListener("click", event => {
    const id = event.target.closest?.("[data-continue-run]")?.dataset.continueRun;
    if (!id) return;
    event.preventDefault();
    continueBenchmark(id);
  });
  // `toggle` does not bubble; capture it so every strip remembers whether it was unfolded.
  document.addEventListener("toggle", event => {
    const id = event.target?.dataset?.notes;
    if (id) state.openNotes[event.target.open ? "add" : "delete"](id);
  }, true);
  $("#goto-results").addEventListener("click", () => {
    showPanel("results");
    const id = $("#live-card").dataset.runId;
    if (id) selectRun(id);
  });
  $$("[data-select]").forEach((btn) => btn.addEventListener("click", () => selectModels(btn.dataset.select)));
  ["#max-tokens", "#repeats", "#no-think", "#offload", "#gpu-index",
    "#runtime-gpu-placement", "#fallback", "#stop-after-fail", "#skip-after-timeout", "#warmup",
    "#per-test-timeout", "#load-timeout", "#headroom", "#context-policy", "#helper-reasoning"].forEach((sel) => {
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
  $("#rerun-model").addEventListener("click", () => {
    const target = selectedModelRerun();
    if (target?.ready) openExtendDialog(target.run, target.model);
  });
  $("#remove-model").addEventListener("click", removeSelectedModel);
  document.addEventListener("click", event => {
    const button = event.target.closest?.("[data-storage-export]");
    if (!button) return;
    const run = state.runCache[button.dataset.storageExport];
    if (!run?.storage_comparison) return;
    const link = document.createElement("a"), url = URL.createObjectURL(new Blob([storageComparisonCsv(run)], {type:"text/csv;charset=utf-8"}));
    link.href = url; link.download = run.id + "-storage-comparison.csv"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  document.addEventListener("click", event => {
    const button = event.target.closest?.("[data-compare-storage]");
    if (button) compareRecordedStorage(button.dataset.compareStorage);
  });
  $$("[data-comparison-axis]").forEach(button=>button.addEventListener("click",()=>{
    if (button.getAttribute("aria-disabled") === "true") return;
    state.comparisonAxis=button.dataset.comparisonAxis;redrawResultsWithoutScrolling();
  }));
  $("#summary-export")?.addEventListener("click", event=>{
    event.preventDefault();
    const runs = [...state.compare].map(id=>state.runCache[id]).filter(Boolean);
    const url = URL.createObjectURL(new Blob([selectedResultsCsv(runs)],{type:"text/csv;charset=utf-8"}));
    const link = document.createElement("a"); link.href=url;link.download="selected-measurements.csv";link.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  $$("[data-color-by]").forEach(button => button.addEventListener("click", () => {
    state.colorBy = button.dataset.colorBy; state.colorTouched = true;
    redrawResultsWithoutScrolling();
  }));
  $$("#mtp-chips input").forEach((input) => input.addEventListener("change", () => {
    $$("#mtp-chips .chip").forEach((chip) => {
      chip.dataset.on = chip.querySelector("input").checked ? "1" : "0";
    });
    updatePlan();
  }));
  $$(".chips .chip input[type=checkbox]").forEach(syncChip);
  const saved = localStorage.getItem("gpu-bench-theme");
  if (saved) document.documentElement.dataset.theme = saved;

  // The helper remembers the last benchmark's connection; this browser remembers the last Connect.
  // When that was LM Studio, open on LM Studio instead of an endpoint that may no longer be running.
  const reopenLmStudio = !browserMode() && localStorage.getItem(HELPER_PROVIDER_STORAGE) === "lmstudio";
  try {
    state.env = await api(reopenLmStudio ? "/api/env?provider=lmstudio" : "/api/env");
  } catch (e) {
    $("#env-banners").innerHTML = banner("err", browserMode() ? "Browser application unavailable" : "Cannot reach the local server", esc(e.message));
    return;
  }
  const savedConfig = state.env.last_config;
  if (reopenLmStudio && savedConfig?.provider === "openai") {
    const workload = Object.fromEntries(["sizes", "concurrency_levels", "repeats", "max_tokens"].filter(key => key in savedConfig).map(key => [key, savedConfig[key]]));
    state.env.last_config = {...state.env.defaults, ...workload, provider: "lmstudio", models: state.env.default_models || []};
  }
  renderEnv();
  if (!browserMode()) {
    try { await refreshOpenAiCatalog(); } catch (_) { /* user can connect manually */ }
  }
  renderMetricSeg();
  await loadRuns();
  const hash = location.hash.slice(1);
  if (!hash && browserMode()) showPanel("results", false);
  if (["summary", "run", "system", "results", "compare", "capabilities", "share"].includes(hash)) {
    showPanel(hash, false);
  }
  await openComparisonLink(location.search);
  const status = await api("/api/status");
  if (status.running) { enterRunningUi(); startPolling(); }
  else if (status.run) renderLive(status);
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

let systemPollTimer = null, systemPollGeneration = 0, systemDashboard = null;
function updateSystemPolling(active) {
  systemPollGeneration++;
  clearTimeout(systemPollTimer); systemPollTimer = null;
  if (!active || document.hidden) return;
  if (browserMode()) {
    $("#system-status").textContent = "Live hardware readings require the desktop helper. Browser mode cannot access local telemetry.";
    $("#system-stats").innerHTML = "";
    return;
  }
  // The helper kept recording while System was hidden; fill that interval from its log.
  systemDashboard?.activate();
  pollSystem(systemPollGeneration);
}
function systemHistory(start, end, maxPoints) {
  return api(`/api/system/history?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&max_points=${encodeURIComponent(maxPoints)}`);
}
// A reading's clock time, with its date when it is not today.
function clockStamp(seconds) {
  const date = new Date(seconds * 1000);
  return (date.toDateString() === new Date().toDateString() ? "" : date.toLocaleDateString(undefined, {month: "short", day: "numeric"}) + " ") + date.toLocaleTimeString();
}
async function pollSystem(generation) {
  try {
    const sample = await api("/api/system");
    if (generation !== systemPollGeneration) return;
    const t = sample.telemetry;
    const age = t?.sample_age_s;
    const at = Number.isFinite(t?.t) ? t.t : Number.isFinite(sample.timestamp) ? sample.timestamp - (Number.isFinite(age) ? age : 0) : null;
    const stamp = at === null ? "" : clockStamp(at);
    const stale = Number.isFinite(age) && age > Math.max(3, 3 * (sample.sampling_interval_ms || 1000) / 1000);
    const log = sample.capture?.log;
    const capture = !log ? "" : log.mode === "owner" ? " \u00b7 Recording continuously; the last 24 h are kept on this computer" : log.reason ? " \u00b7 " + log.reason : "";
    $("#system-status").textContent = (t ? (stale ? "Stale sample; no new reading since " : "Sample at ") + stamp : "Warming up; waiting for the first sample.") + (sample.error ? " \u00b7 Some readings unavailable: " + sample.error : "") + capture;
    const val = (v, divisor=1) => Number.isFinite(v) ? fmt(v/divisor, 1) : "\u2014";
    updateCpuSensorSetup(t);
    if (globalThis.WatteverSystemDashboard) {
      if (!systemDashboard) { systemDashboard = new WatteverSystemDashboard.Dashboard($("#system-stats")); systemDashboard.loader = systemHistory; systemDashboard.activate(); }
      systemDashboard.update(sample, t ? liveGpuCards(t) : "");
    } else {
    $("#system-stats").innerHTML = t ? [stat(t.gpus?.length ? "Monitored NVIDIA power" : "GPU/SoC sensor power",val(t.gpu_w),"W"),stat("System power",val(t.system_w_est),"W estimated"),stat(t.gpus?.length ? "Monitored NVIDIA load" : "GPU/SoC sensor load",val(t.gpu_util),"%"),stat(t.gpus?.length ? "Monitored NVIDIA memory used" : "GPU/SoC memory used",val(t.vram_mb,1024),"GiB"),stat("RAM used",val(t.system_memory_used_bytes,1073741824),"GiB"),stat("CPU load",val(t.cpu_util_percent ?? (Number.isFinite(t.cpu_util) ? t.cpu_util * 100 : null)),"%"),stat("GPU temperature",val(t.temp_c),"\u00b0C"),stat("CPU temperature",val(t.cpu_temp_c),"\u00b0C")].join("") + liveGpuCards(t) : "";
    }
  } catch (error) {
    if (generation !== systemPollGeneration) return;
    $("#system-status").textContent = "Live readings unavailable: " + error.message;
    systemDashboard?.tick();
    if (!systemDashboard) $("#system-stats").innerHTML = "";
  }
  if (generation === systemPollGeneration) systemPollTimer = setTimeout(() => pollSystem(generation), 1000);
}
/* Optional Windows CPU sensor: offered only when no CPU temperature is readable; nothing installs until clicked. */
let cpuSensor = { status: null, checkedAt: 0, pending: false };
const CPU_SENSOR_BUSY = ["downloading", "waiting_for_approval"];
function cpuSensorMarkup(status, telemetry) {
  if (!status?.supported) return "";
  const phase = status.phase || "idle", busy = CPU_SENSOR_BUSY.includes(phase);
  const reading = Number.isFinite(telemetry?.cpu_temp_c), ours = String(telemetry?.cpu_temp_source || "").startsWith("windows_wattever_pawnio:");
  const outcome = ["done", "failed", "cancelled"].includes(phase) && status.message
    ? `<p class="hint ${phase === "failed" ? "error-text" : ""}" role="status">${esc(status.message)}</p>` : "";
  if (busy) return `<p class="hint" role="status">${phase === "downloading" ? "Downloading and verifying PawnIO…" : "Approve the Windows administrator prompt to continue…"}</p>`;
  if (status.installed) return `${outcome}<p class="hint">CPU temperature: ${ours && reading ? "read by the Wattever sensor (PawnIO driver, read-only)" : esc(telemetry?.cpu_temp_note || "the Wattever sensor is installed but has no reading yet.")} <button class="btn-sm" data-cpu-sensor="uninstall">Remove CPU sensor</button></p>`;
  if (reading) return outcome;
  return `${outcome}<div class="cpu-sensor-offer"><p><strong>CPU temperature is unavailable.</strong> Windows does not let ordinary programs read the CPU sensor.</p><p class="hint">Enabling it installs the signed <a href="https://pawnio.eu/" target="_blank" rel="noopener">PawnIO</a> driver and a small read-only Wattever task that reads only the CPU thermal registers. Windows asks for administrator approval once, and you can remove it here at any time.</p><button class="btn" data-cpu-sensor="install">Enable CPU temperature</button></div>`;
}
async function updateCpuSensorSetup(telemetry) {
  const node = $("#cpu-sensor-setup");
  if (!node || browserMode()) return;
  const busy = CPU_SENSOR_BUSY.includes(cpuSensor.status?.phase);
  if (!cpuSensor.pending && Date.now() - cpuSensor.checkedAt > (busy ? 1000 : 15000)) {
    cpuSensor.pending = true;
    try { cpuSensor.status = await api("/api/system/cpu-sensor"); } catch (e) { cpuSensor.status = null; }
    cpuSensor.checkedAt = Date.now(); cpuSensor.pending = false;
  }
  const markup = cpuSensorMarkup(cpuSensor.status, telemetry);
  node.hidden = !markup;
  if (node.dataset.markup !== markup) {
    node.dataset.markup = markup; node.innerHTML = markup;
    node.querySelectorAll("[data-cpu-sensor]").forEach(button => button.onclick = async () => {
      const action = button.dataset.cpuSensor;
      if (action === "uninstall" && !confirm("Remove the Wattever CPU sensor? PawnIO is also removed if Wattever installed it.")) return;
      button.disabled = true;
      try {
        cpuSensor.status = await api("/api/system/cpu-sensor", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }) });
      } catch (e) { cpuSensor.status = { ...(cpuSensor.status || {}), supported: true, phase: "failed", message: e.message }; }
      cpuSensor.checkedAt = Date.now(); updateCpuSensorSetup(telemetry);
    });
  }
}
function exportVisibleGraphs() {
  const graphs = [...document.querySelectorAll("#panel-compare svg")].filter(svg => svg.getBoundingClientRect().width > 0);
  if (!graphs.length) { alert("No visible graphs to export. Select a benchmark and measures first."); return; }
  const ns = "http://www.w3.org/2000/svg", output = document.createElementNS(ns,"svg");
  output.setAttribute("xmlns",ns);
  let y = 0, width = 0;
  graphs.forEach(graph => {
    const rect = graph.getBoundingClientRect(), copy = graph.cloneNode(true);
    copy.setAttribute("x",0); copy.setAttribute("y",y); copy.setAttribute("width",rect.width); copy.setAttribute("height",rect.height);
    output.appendChild(copy); y += rect.height; width = Math.max(width,rect.width);
  });
  output.setAttribute("width",width); output.setAttribute("height",y); output.setAttribute("viewBox",`0 0 ${width} ${y}`);
  const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(output)],{type:"image/svg+xml"}));
  const link = document.createElement("a"); link.href=url; link.download="wattever-graphs.svg"; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
}
async function shareBenchmarks() {
  const ids = [...state.compare];
  if (!ids.length) { $("#share-status").textContent = "Select one or more benchmarks in Results first."; return; }
  try { const bundle = await api("/api/export.json?ids=" + ids.map(encodeURIComponent).join(",")); downloadBlob(JSON.stringify(bundle, null, 2), "wattever-benchmarks.json", "application/json"); $("#share-status").textContent = "Bundle downloaded. For contributions, use the individual download buttons or split the bundle into separate files."; } catch (error) { $("#share-status").textContent = "Export failed: " + error.message; }
}

function renderShareRuns() {
  const node = $("#share-run-files"); if (!node) return;
  const ids = [...state.compare];
  node.innerHTML = ids.length ? ids.map(id => `<a class="btn-sm" href="/api/runs/${encodeURIComponent(id)}/download" download="${esc(benchmarkFilename(id))}">Download ${esc((state.runCache[id] || {}).label || id)} JSON</a>`).join(" ") : '<p class="hint">Select benchmarks in Results to download individual files.</p>';
}

let capabilityActivated = false;
function showPanel(name, pushHash = true) {
  if (name === "summary" || name === "compare") {
    state.resultView = name;
    name = "results";
  }
  $$(".tab").forEach((tab) => tab.setAttribute("aria-selected", String(tab.dataset.panel === name)));
  $$(".panel").forEach((panel) => panel.classList.toggle("active", panel.id === "panel-" + name));
  if (pushHash && location.hash.slice(1) !== name) history.replaceState(null, "", "#" + name);
  if (name === "share") {
    renderShareRuns();
    if (!capabilityActivated && window.WatteverCapabilities) { capabilityActivated = true; window.WatteverCapabilities.activate(); }
  }
  if (name === "results") {
    showResultsView(state.resultView);
    checkForActiveBenchmark();
  }
  updateSystemPolling(name === "system");
  Charts.hideTip();
  if (name === "capabilities") { window.scrollTo(0,0); if (window.WatteverCapabilities) { capabilityActivated = true; window.WatteverCapabilities.activate(); } }
}

function showResultsView(view) {
  if (state.resultView !== view) $$(".result-view").forEach(panel => { panel.style.minHeight = ""; });
  state.resultView = view;
  if ($("#results-controls")) $("#results-controls").hidden = view === "summary";
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
  if (ids.some(id => !state.runCache[id])) {
    // Figures from the benchmark shown before must not sit under the new name while it loads.
    const loading = emptyState("Loading benchmark", "Reading its measurements.");
    for (const selector of ["#run-detail", "#summary-body", "#compare-body", "#results-table"]) if ($(selector)) $(selector).innerHTML = loading;
    if ($("#chart-key")) $("#chart-key").innerHTML = "";
    state.detailSignature = null;
  }
  await ensureRuns(ids);
  if (generation !== state.resultsRender) return;
  const runs = ids.map((id) => state.runCache[id]).filter(Boolean);
  const measured = measurementRuns(runs);
  syncResultLevels(measured);
  if ($("#results-controls")) $("#results-controls").dataset.analysis = state.comparisonAxis;
  const models = allModelsOf(measured);
  if ($("#result-selection-summary")) $("#result-selection-summary").textContent = runs.map(run=>run.label || run.id).join(" · ") || "Select a benchmark";
  autoResultDefaults(measured, models);
  renderResultControls(runs, models);
  renderVisibleModels(models);
  const shown = shownModels(models);
  $("#summary-export").href = "/api/export.csv?ids=" + ids.map(encodeURIComponent).join(",");
  $("#summary-export").hidden = !runs.length;
  const detailSignature = JSON.stringify([state.selected, resultsConnectionKey(), !!state.env?.capabilities?.extend, runs.map(run => [run.id, run.status, run.label, run.notes, (run.results || []).length, run.hardware?.memory, run.hardware_follow_ups, state.runs.find(item => item.id === run.id)?.merge])]);
  if (detailSignature !== state.detailSignature) {
    const selection = JSON.stringify([...state.compare]);
    const keepDetailsOpen = state.detailSelection === selection && $("#run-detail details")?.open;
    renderRunDetail(runs.find((run) => run.id === state.selected) || runs[0]);
    if (keepDetailsOpen && $("#run-detail details")) $("#run-detail details").open = true;
    state.detailSelection = selection;
    state.detailSignature = detailSignature;
  }
  if (state.resultView === "summary") renderOverviewTab(runs);
  if (state.resultView === "compare") await renderCompare();
  if (state.resultView === "table") {
    const keys = selectedMetricKeys();
    $("#results-table-heading").textContent = keys.length === 1 ? METRICS[keys[0]].label : "Measurements";
    $("#results-table").innerHTML = !runs.length ? emptyState("Select a benchmark", "Choose a benchmark from the list on the left.")
      : !keys.length ? emptyState("Choose a metric", "Tick one or more metric buttons above.")
      : compareTable(tracksFor(splitRowConditions(measured)), shown, keys);
    renderAllMeasurements();
  }
}

function renderAllMeasurements() {
  const target = $("#results-all-measurements");
  const runs = measurementRuns([...state.compare].map(id => state.runCache[id]).filter(Boolean));
  target.parentElement.hidden = !runs.length;
  target.innerHTML = target.parentElement.open
    ? runs.map(run => `<h4>${esc(run.label || run.id)}</h4>${detailedResultsTable({...run,id:run.parent_run_id || run.id,community:run.community || run.storage_alternative || run.storage_baseline, results: (run.results || []).filter(row => (state.visibleModels === null || state.visibleModels.has(row.model)) && levelOn(row.concurrency || 1))})}`).join("") : "";
  target.querySelectorAll("button[data-repair-case]").forEach(button => button.addEventListener("click", () => {
    const run = state.runCache[button.dataset.runId];
    const selectedCase = JSON.parse(button.dataset.repairCase);
    if (run) openExtendDialog(run, selectedCase[0], selectedCase);
  }));
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
  lmsPill.querySelector("span:last-child").textContent = env.lms.server ? (providerManaged() ? "Endpoint connected" : "LM Studio ready") : (providerManaged() ? "Not connected" : "LM Studio off");

  const unified = gpu.memory_architecture === "unified";
  const src = (env.power || {}).source;
  $("#machine-stats").innerHTML = conditionBadge((env.power || {}).readable ? "Power telemetry available" : "Power telemetry unavailable",src || "No supported power source")
    + conditionBadge((env.corpus || {}).approx_tokens == null ? "Corpus unknown" : fmtTokens(env.corpus.approx_tokens)+" corpus tokens");

  const banners = [];
  if (providerManaged()) {
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
    banners.push(banner("err", genericHelper() ? "No served model selected" : "LM Studio has no models indexed", genericHelper() ? "Connect to an endpoint or enter its model ID." : "Download at least one model first."));
  }
  $("#env-banners").innerHTML = banners.join("");

  const last = env.last_config || env.defaults || {};
  const availableModels = new Set(env.models.filter((model) => model.type !== "embeddings").map((model) => model.id));
  state.models = new Set((env.last_config && Array.isArray(last.models) ? last.models : (env.default_models || [])).filter((id) => availableModels.has(id)));
  state.sizes = new Set(Array.isArray(last.sizes) ? last.sizes : (env.defaults.sizes || []));
  state.concurrencyLevels = new Set(Array.isArray(last.concurrency_levels) ? last.concurrency_levels : [1]);
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
    m=typeof window!=='undefined'&&window.WatteverModelMetadata?window.WatteverModelMetadata.normalize(m):m;
    const on = state.models.has(m.id);
    const isDefault = (env.default_models || []).includes(m.id);
    return `<label class="model" data-on="${on ? 1 : 0}" title="Include ${esc(m.id)} in this benchmark">
      <input type="checkbox" value="${esc(m.id)}" ${on ? "checked" : ""}>
      <span class="name">${esc(m.id)}</span>
      <span class="meta">
        ${isDefault ? '<span class="badge accent">default</span>' : ""}
        <span class="badge" title="${m.quantization_source==='model_identifier'?'Quantization label inferred from the model ID; weights are not independently verified':'Quantization reported by the endpoint, when available'}">${esc(quantizationLabel(m))}</span>
        ${m.arch ? `<span class="badge">${esc(m.arch)}</span>` : ""}
        <span class="badge">${m.max_context_length?'ctx '+fmtTokens(m.max_context_length):'Context unknown'}</span>
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

function toggleLevel(selected, value) {
  if (selected.has(value)) selected.delete(value); else selected.add(value);
}

function renderSizes() {
  const presets = [...new Set([...(state.env.size_presets || [512, 5000, 10000, 20000, 40000, 80000]), 100000, 120000, 150000, 200000, 260000, ...state.sizes])].sort((a, b) => a - b);
  renderRange("#size-chips", presets, state.sizes, (value) => {
    toggleLevel(state.sizes, value);
    renderSizes(); updatePlan();
  }, (value) => fmtTokens(value), "prompt tokens");
}

function renderRange(selector, presets, selected, onSelect, format, unit) {
  $(selector).innerHTML = presets.map((value) => `<label class="chip" data-on="${selected.has(value) ? 1 : 0}" title="Test ${format(value)} ${unit}"><input type="checkbox" value="${value}" aria-label="${format(value)} ${unit}" ${selected.has(value) ? "checked" : ""}>${format(value)}</label>`).join("");
  $$(selector + " input").forEach((input) => input.addEventListener("change", () => {
    const value = Number(input.value);
    onSelect(value);
    const replacement = $$(selector + " input").find((next) => Number(next.value) === value);
    if (replacement) replacement.focus({preventScroll: true});
  }));
}

function compactCpuName(name) {
  return String(name || "CPU unreported").replace(/\(R\)|\(TM\)/gi,"").replace(/\s+CPU\b/i,"").replace(/\s+@\s+.*$/,"").replace(/\s+/g," ").trim();
}
function machineHeaderMarkup(env) {
  const hw=env.hardware || {}, host=env.host || {};
  const cpu=hw.cpu?.name || host.cpu, gpu=env.gpu || {};
  const inventory=hw.gpus?.length ? hw.gpus : env.gpus?.length ? env.gpus : gpu.name ? [gpu] : [];
  const cards=inventory.map(card=>{
    const legacy=card.name===gpu.name ? gpu : {};
    return {name:card.name || "GPU unreported", bytes:card.memory_bytes || (card.vram_total_mb || legacy.vram_total_mb)*1048576,
      shared:(card.memory_architecture || legacy.memory_architecture)==="unified"};
  });
  const ram=hw.memory?.installed_capacity_bytes || hw.system?.memory_bytes || host.ram_gb*1073741824;
  const size=value=>value>0 ? Number(value.toFixed(1))+" GiB" : "Unreported";
  const deviceRows=(values)=>values.map(value=>`<span class="machine-device">${esc(value)}</span>`).join("\n");
  const allShared=cards.length>0 && cards.every(card=>card.shared), mixed=cards.some(card=>card.shared) && !allShared;
  const memoryLabel=(allShared ? "Shared memory" : mixed ? "GPU memory" : "VRAM")+(cards.length>1 ? " / card" : "");
  const fields=[
    ["CPU",esc(compactCpuName(cpu)),cpu,""],
    [cards.length>1 ? `GPUs (${cards.length})` : "GPU",deviceRows(cards.length ? cards.map(card=>card.name.replace(/^NVIDIA (?:GeForce\s+)?/,"")) : ["GPU unreported"]),cards.map(card=>card.name).join(" + "),"machine-name"],
    ["RAM",esc(size(ram/1073741824)+(hw.memory?.channel_count ? ` · ${hw.memory.channel_count} ch` : "")),memoryChannelBadges(hw.memory)[0].title,""],
    [memoryLabel,deviceRows(cards.length ? cards.map(card=>size(card.bytes/1073741824)+(mixed ? card.shared ? " shared" : " VRAM" : "")) : ["Unreported"]),
      cards.length>1 ? cards.map(card=>`${card.name}: ${size(card.bytes/1073741824)}${card.shared ? " shared with CPU" : " dedicated"}`).join("; ")+". Separate per-card capacities, not a pooled allocation." : allShared ? "Unified memory shared with CPU" : "Per-card GPU memory","machine-memory"]
  ];
  return fields.map(([label,value,title,id])=>`<span class="machine-header-item" title="${esc(title || label)}"><small>${esc(label)}</small><strong${id ? ` id="${id}"` : ""}>${value}</strong></span>`).join("");
}
function memoryChannelBadges(memory={}) {
  memory=memory || {};
  const topology=memory.channel_topology || {}, active=topology.active_channel_count;
  const count=active || topology.populated_channel_count || memory.channel_count;
  const source=topology.source || "Source unreported";
  const later=topology.provenance === "detected_later_on_matching_hardware";
  const title=[source,topology.method,topology.captured_at,topology.note || topology.reason,
    later && "Detected after this benchmark; historical inference conditions were not verified."].filter(Boolean).join(" · ");
  if(!count) return [{label:"Channels unreported",title:title || "No channel topology supplied. DIMM count and bandwidth do not establish channels."}];
  return [{label:`${count} ${active ? "active channels" : topology.populated_channel_count ? "populated channels" : "channels · reported"}`,title},
    {label:active ? "Controller detected" : topology.status === "inferred" ? "Firmware layout · inferred" : "OS / firmware reported",title},
    ...(later ? [{label:"Detected later",title}] : [])];
}
function hardwareTile(label,value,badges=[],icon="") {
  return `<article class="hardware-tile"><span class="hardware-tile-label">${icon ? hardwareGlyph(icon) : ""}${esc(label)}</span><strong>${esc(value || "Unreported")}</strong><div class="condition-badges">${badges.filter(Boolean).map(item=>typeof item === "string" ? conditionBadge(item) : conditionBadge(item.label,item.title)).join("")}</div></article>`;
}
function hardwareGlyph(name) {
  const shapes={cpu:'<rect x="5" y="5" width="14" height="14" rx="2"/><path d="M9 1v4m6-4v4M9 19v4m6-4v4M1 9h4m-4 6h4m14-6h4m-4 6h4"/>',memory:'<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10v4m4-4v4m4-4v4m4-4v4M6 18v3m4-3v3m4-3v3m4-3v3"/>',gpu:'<rect x="2" y="5" width="20" height="13" rx="2"/><circle cx="10" cy="11.5" r="4"/><path d="M17 9h3m-3 5h3M6 18v3"/>',storage:'<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="12" cy="10" r="4"/><path d="m12 10 5 7M6 18h3"/>',platform:'<rect x="2" y="3" width="20" height="15" rx="2"/><path d="M8 22h8m-4-4v4"/>',transfer:'<path d="M3 7h18m-5-5 5 5-5 5M21 17H3m5-5-5 5 5 5"/>'};
  return `<span class="hardware-glyph" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">${shapes[name] || shapes.platform}</svg></span>`;
}
function hardwareMarkup(hw) {
  hw=hw || {};
  const cpu=hw.cpu || {}, memory=hw.memory || {}, system=hw.system || {}, os=hw.os || {};
  const gib=bytes=>bytes>0 ? Number((bytes/1073741824).toFixed(1))+" GiB" : "Unreported";
  const ram=memory.installed_capacity_bytes || system.memory_bytes;
  const compute=[hardwareTile("CPU",compactCpuName(cpu.name),[cpu.physical_cores && `${cpu.physical_cores} cores`,cpu.logical_cores && `${cpu.logical_cores} threads`,cpu.socket_count && `${cpu.socket_count} socket${cpu.socket_count===1 ? "" : "s"}`],"cpu"),
    hardwareTile("System memory",gib(ram),[memory.module_count && `${memory.module_count} modules`,...(memory.configured_clock_mhz_reported || []).map(clock=>({label:`${clock} reported clock`,title:"Firmware/OS report, not an independent clock measurement"})),...memoryChannelBadges(memory)],"memory")];
  for(const [index,card] of (hw.gpus || []).entries()) {
    const link=card.pcie || {}, shared=card.memory_architecture==="unified";
    compute.push(hardwareTile(`GPU ${index+1}`,String(card.name || "GPU unreported").replace(/^NVIDIA (?:GeForce\s+)?/,""),[
      card.memory_bytes && `${gib(card.memory_bytes)} ${shared ? "shared" : "VRAM"}`,card.driver && `Driver ${card.driver}`,
      link.generation_current && {label:`PCIe ${link.generation_current} x${link.width_current ?? "?"} snapshot`,title:"May reflect an idle downshift; not a transfer measurement"},
      link.generation_max_reported && {label:`Driver ceiling Gen ${link.generation_max_reported} x${link.width_max_reported ?? "?"}`,title:"Driver-reported platform/adapter ceiling. Does not establish the card's physical interface, motherboard slot wiring, or transfer throughput."}],"gpu"));
  }
  const clean=value=>value && !/to be filled|default string|system product name/i.test(value) ? value : null;
  const board=[clean(hw.motherboard?.manufacturer),clean(hw.motherboard?.model)].filter(Boolean).join(" ");
  const platform=hardwareTile("Platform",[os.name,os.release,os.architecture].filter(Boolean).join(" "),[board,clean(hw.firmware?.version) && `Firmware ${hw.firmware.version}`],"platform");
  const disks=new Map();
  for(const disk of hw.storage || []) {const key=JSON.stringify([disk.model,disk.capacity_bytes,storageKind(disk),disk.bus_type]);const item=disks.get(key) || {...disk,count:0};item.count++;disks.set(key,item);}
  const capacity=bytes=>bytes>=1e12 ? Number((bytes/1e12).toFixed(1))+" TB" : bytes>=1e9 ? Number((bytes/1e9).toFixed(0))+" GB" : bytes>0 ? Number((bytes/1e6).toFixed(0))+" MB" : "Capacity unreported";
  const storage=[...disks.values()].map(disk=>hardwareTile(storageKind(disk) || "Storage type unreported",disk.model || "Model unreported",[capacity(disk.capacity_bytes)+(disk.count>1 ? " each" : ""),disk.count>1 && `${disk.count} devices`,disk.bus_type && disk.bus_type !== storageKind(disk) && disk.bus_type],"storage")).join("");
  return `<div class="hardware-visual"><section class="hardware-section"><h4>Compute &amp; memory</h4><div class="hardware-grid">${compute.join("")}</div></section>${storage ? `<section class="hardware-section"><h4>Storage inventory</h4><div class="hardware-grid storage-inventory">${storage}</div></section>` : ""}${transferBandwidthMarkup(hw.transfer_bandwidth)}${storageCoverageMarkup(hw)}<section class="hardware-section"><h4>Platform</h4><div class="hardware-grid">${platform}</div></section></div>`;
}

function conditionBadge(label, title = "", kind = "") {
  return `<span class="badge condition-badge ${esc(kind)}"${title ? ` title="${esc(title)}"` : ""}>${esc(label)}</span>`;
}

function storageCoverageMarkup(hw) {
  const count=(hw.storage || []).filter(disk=>storageKind(disk)==="NVMe").length;
  if(!count) return "";
  const coverage=hw.transfer_coverage;
  if(coverage?.status==="complete") return `<p class="hint nvme-coverage">NVMe transfer coverage: ${esc(coverage.nvme_measured)} of ${esc(coverage.nvme_detected)} detected drives measured.</p>`;
  if(coverage?.status==="unverified" && coverage.nvme_measured>0) return `<p class="hint nvme-coverage">This capture includes ${esc(coverage.nvme_measured)} NVMe drive measurement${coverage.nvme_measured===1?"":"s"}. Matching these observations to the current physical drives is unverified; inspect the device and capture labels.</p>`;
  return `<p class="banner warn nvme-coverage"><b>${esc(count)} NVMe drive${count===1?"":"s"} detected.</b> ${coverage?.status==="incomplete" || !hw.transfer_bandwidth ? "The transfer capture does not cover every detected NVMe drive." : "NVMe transfer coverage has not been verified for this capture."} Use Measure transfer bandwidth in System to probe mounted NVMe volumes. Locked, unmounted or unwritable volumes remain explicitly unavailable.</p>`;
}

function storageKind(disk) {
  const bus = String(disk.bus_type || disk.interface || "");
  const media = String(disk.media_type || "");
  if (/nvme/i.test(bus + " " + (disk.device_model || disk.model || ""))) return "NVMe";
  if (/hdd|hard disk/i.test(media) || disk.rotational === true) return "HDD";
  if (/ssd|solid state/i.test(media + " " + (disk.device_model || disk.model || "")) || disk.rotational === false) return "SSD";
  return null;
}

function hardwareBadgesMarkup(source) {
  const hw = source.hardware || {}, gpu = source.gpu || {}, host = source.host || {};
  const remote = source.config?.provider === "openai" && source.config.endpoint_inference_local === false;
  if (remote) return `<div class="condition-badges" aria-label="Machine">${conditionBadge("Remote hardware unknown")}</div>`;
  const cards = hw.gpus?.length ? hw.gpus : gpu.name ? [{name:gpu.name, memory_bytes:gpu.vram_total_mb * 1048576, memory_architecture:gpu.memory_architecture}] : [];
  const badges = cards.flatMap(card => [conditionBadge(String(card.name || "GPU").replace(/^NVIDIA (?:GeForce\s+)?/, ""), "Graphics card", "gpu-badge"),
    ...(card.memory_bytes > 0 ? [conditionBadge(`${Number((card.memory_bytes / 1073741824).toFixed(1))} GiB ${card.memory_architecture === "unified" ? "unified" : "VRAM"}`)] : [])]);
  const ram = hw.memory?.installed_capacity_bytes || hw.system?.memory_bytes || host.ram_gb * 1073741824;
  if (ram > 0) badges.push(conditionBadge(`${Number((ram / 1073741824).toFixed(1))} GiB RAM`));
  const clocks = hw.memory?.configured_clock_mhz_reported || [];
  if (clocks.length) badges.push(conditionBadge(`${clocks.join(" / ")} RAM clock`, "Firmware/OS reported value; not a measured memory clock"));
  badges.push(...memoryChannelBadges(hw.memory).map(item=>conditionBadge(item.label,item.title)));
  if ((source.hardware_follow_ups || []).some(item=>item.kind === "memory_topology" && item.status === "pending")) badges.push(conditionBadge("Memory check pending reconnect","Reconnect this benchmark's GPU to capture memory topology on its matching configuration."));
  const profile = hw.transfer_bandwidth;
  const disks = [...(hw.storage || []), ...(profile ? [profile.storage_to_ram, ...(profile.storage_volumes || [])].filter(Boolean) : [])];
  const kinds = new Map();
  for (const disk of disks) {
    const kind = storageKind(disk), names = kinds.get(kind) || [];
    if (!kind) continue;
    names.push(disk.device_model || disk.model || "Storage type unavailable"); kinds.set(kind,names);
  }
  for (const [kind,names] of kinds) badges.push(conditionBadge(kind, [...new Set(names)].join("; "), "storage-badge"));
  return `<div class="condition-badges" aria-label="Machine">${badges.join("")}</div>`;
}

function engineBadgesMarkup(run) {
  const groups = run.engine_groups?.length ? run.engine_groups : [{config:run.config || {}, label:run.provider?.label}];
  return `<div class="engine-badges" aria-label="Engine settings">${groups.map(group => {
    const cfg = group.config || {}, external = cfg.provider === "openai";
    const name = cfg.reproducibility?.engine || group.label || (external ? cfg.endpoint_label || "OpenAI endpoint" : "LM Studio");
    const configurations = (run.results || []).filter(row => !group.id || row.engine_group_id === group.id).map(row => row.model_configuration).filter(Boolean);
    const effective = [...new Set(configurations.map(item => item.mtp?.effective || "unknown"))];
    const modes = cfg.mtp_modes || [];
    const mtp = external ? (effective.length === 1 && effective[0] !== "unknown" ? `MTP ${effective[0]} · verified` : cfg.reported_mtp && cfg.reported_mtp !== "unknown" ? `MTP ${cfg.reported_mtp} · reported` : cfg.requested_mtp && cfg.requested_mtp !== "unknown" ? `MTP ${cfg.requested_mtp} · requested` : "MTP unknown")
      : `MTP ${modes.length > 1 ? "on + off" : modes[0] || "unknown"} · requested`;
    const context = cfg.reproducibility?.context_tokens;
    return `<div class="engine-condition">${conditionBadge(name,"Inference engine","engine-badge")}${conditionBadge(mtp, external ? "Provider reported/requested state does not establish effective MTP. Inspect per-model settings." : "Benchmark technique selection; inspect per-model effective settings.")}${context ? conditionBadge(`${fmtTokens(context)} context`,"Reported engine context") : conditionBadge(cfg.context_policy === "per_test" ? "Context per test" : "Context provider controlled")}${cfg.repeats ? conditionBadge(`${cfg.repeats} ${cfg.repeats === 1 ? "repeat" : "repeats"}`) : ""}</div>`;
  }).join("")}</div>`;
}

function bandwidthBadgesMarkup(profile) {
  if (!profile) return "";
  const values = [["Storage → RAM",profile.storage_to_ram],...(profile.storage_volumes || []).map(item => [`${storageKind(item) || "Storage"} → RAM`,item]),
    ["RAM copy · 1 worker",profile.ram_copy_single],["RAM copy · parallel",profile.ram_copy_parallel],...gpuTransferPaths(profile)];
  if (profile.storage_to_ram) values[0][0] = `${storageKind(profile.storage_to_ram) || "Storage"} → RAM`;
  return `<div class="condition-badges bandwidth-badges" aria-label="Measured bandwidth">${conditionBadge(profile.provenance === "measured_later_on_matching_hardware" ? "Independent probes" : "Transfer probes",`${profile.captured_at || "Unknown time"}; ${profile.provenance || "benchmark client"}. Separate microbenchmarks; not observed inference traffic.`)}${values.filter(([,item])=>item?.status === "ok" && item.bytes_per_second > 0).map(([label,item])=>conditionBadge(`${label}${item.volume ? ` (${item.volume})` : ""} · ${(item.bytes_per_second / 1e9).toFixed(2)} GB/s`,item.workers ? "Copied payload throughput; not peak DRAM bus bandwidth" : item.note || "Separate transfer microbenchmark")).join("")}</div>`;
}

function displayBandwidthProfile(run) {
  return (run.hardware_measurement_history || []).filter(capture=>capture.transfer_bandwidth).slice(-1)[0]?.transfer_bandwidth || run.hardware?.transfer_bandwidth;
}

function gpuTransferPaths(profile) {
  if (Array.isArray(profile?.ram_to_vram_devices) && profile.ram_to_vram_devices.length) {
    return profile.ram_to_vram_devices.map(item=>[
      `RAM → ${String(item.gpu_name || "GPU").replace(/^NVIDIA (?:GeForce\s+)?/,"")}${Number.isInteger(item.gpu_sensor_index) ? ` (GPU ${item.gpu_sensor_index+1})` : ""}`,item]);
  }
  return profile?.ram_to_vram ? [["RAM to VRAM",profile.ram_to_vram]] : [];
}

function transferBandwidthMarkup(profile) {
  if (!profile) return `<section class="hardware-section"><h4>Transfer measurements</h4><div class="probe-empty">${conditionBadge("No transfer capture attached")}<span class="hint">No transfer values were supplied for this snapshot.</span></div></section>`;
  const paths=[["Storage to RAM",profile.storage_to_ram],...(profile.storage_volumes || []).map(value=>["Storage to RAM",value]),["RAM copy - 1 worker",profile.ram_copy_single],["RAM copy - parallel",profile.ram_copy_parallel],...gpuTransferPaths(profile)].filter(([,value])=>value);
  const tiles=paths.map(([label,value])=>{
    const ok=value.status === "ok" && Number.isFinite(value.bytes_per_second) && value.bytes_per_second>0;
    const name=label.startsWith("Storage") ? `${storageKind(value) || "Storage"}${value.volume ? ` (${value.volume})` : ""} to RAM` : label;
    return hardwareTile(name,ok ? (value.bytes_per_second/1e9).toFixed(2)+" GB/s" : "Unavailable",[
      ok && `${(value.bytes_per_second/1e6).toFixed(0)} MB/s`,value.device_model,value.host_memory && `${value.host_memory} host memory`,
      value.workers && `${value.workers} worker${value.workers===1 ? "" : "s"}`,value.workers && "Copied payload",
      value.pcie_during?.generation_current && {label:`Gen ${value.pcie_during.generation_current} ×${value.pcie_during.width_current ?? "?"} during transfer`,title:"Driver link snapshot during a separate untimed transfer warmup; not a motherboard slot wiring measurement"},
      {label:`${value.samples?.length || 0} samples`,title:value.note || value.reason || "Separate transfer microbenchmark"},!ok && value.reason],"transfer");
  }).join("");
  const saved=profile.restored_from_saved_capture || profile.reused_saved_capture || profile.saved_capture || profile.provenance === "measured_later_on_matching_hardware";
  const timestamp=profile.captured_at ? profile.captured_at.replace("T"," ").slice(0,19)+" UTC" : "Capture time unreported";
  return `<section class="hardware-section transfer-profile"><div class="hardware-section-heading"><h4>Transfer measurements</h4><div class="condition-badges">${conditionBadge(saved ? "Saved capture" : "Session capture")}${conditionBadge(timestamp)}</div></div><div class="hardware-grid transfer-grid">${tiles}</div><details class="hw-methods"><summary>Methods &amp; provenance</summary><div class="condition-badges">${conditionBadge(profile.protocol || "Protocol unreported")}${conditionBadge(profile.provenance || "Measured on benchmark client")}</div><p class="hint">${profile.provenance === "measured_later_on_matching_hardware" ? "Measured after this benchmark on matching hardware; original inference conditions were not verified." : "Separate transfer microbenchmarks; not observed inference traffic."}</p><p class="hint">Median of ${esc(profile.storage_to_ram?.samples?.length ?? 0)} storage samples and ${esc(profile.ram_to_vram?.samples?.length ?? 0)} GPU samples. RAM copy rates count payload bytes.</p></details></section>`;
}

const STORAGE_METRICS = {
  wall_s: {label:"Completion time",unit:"s",lower:true},
  ttft_s: {label:"Time to first token / query",unit:"s",lower:true},
  prefill_tps: {label:"Prefill / query",unit:"tk/s",lower:false},
  decode_tps: {label:"Decode / query",unit:"tk/s",lower:false},
};
function storageMedian(values) {
  const sorted = values.filter(value => typeof value === "number" && Number.isFinite(value) && value >= 0).sort((a,b)=>a-b);
  return sorted.length ? (sorted[Math.floor((sorted.length-1)/2)] + sorted[Math.floor(sorted.length/2)]) / 2 : null;
}
function wallCollectiveMetrics(row, { partial = false } = {}) {
  return {
    prefill_tps: phaseMetrics()?.prefillPhase(row,{partial})?.tps ?? null,
    decode_tps: phaseMetrics()?.decodePhase(row,{partial})?.tps ?? null,
    group_output_tps: phaseMetrics()?.outputRate(row,{partial}) ?? null,
  };
}
function collectiveRate(row, field, { partial = false } = {}) {
  if(field === "decode_tps") return decodeRate(row, {partial});
  if(field === "prefill_tps") return phaseMetrics()?.prefillPhase(row,{partial})?.tps ?? null;
  const num = (v) => typeof v === "number" && Number.isFinite(v) ? v : null;
  const n = row.concurrency || 1;
  const stored = num(row[field]);
  if (n <= 1) {
    const reqs = Array.isArray(row.requests) && row.requests.length ? row.requests : [row];
    const ok = reqs.filter((q) => q && q.status === "ok");
    const vals = ok.map((q) => num(q[field]));
    if (vals.length && vals.every((v) => v !== null)) return vals[0];
    return stored;
  }
  const wall = wallCollectiveMetrics(row, { partial });
  return wall?.[field] ?? null;
}
function phaseMetrics() {return globalThis.WatteverPhaseMetrics || globalThis.WatteverConcurrency?.phaseMetrics;}
function decodeRate(row,{partial=false}={}) {
  const phase=phaseMetrics()?.decodePhase(row,{partial});
  if(phase) return phase.tps;
  return null;
}
function decodeApproximate(row) {
  const phase=phaseMetrics()?.decodePhase(row);
  return !phase || phase.estimated_boundary || phase.legacy_boundary;
}
function requestDecodeRate(row) {
  const requests = Array.isArray(row.requests) && row.requests.length ? row.requests : (row.concurrency || 1) === 1 ? [row] : [];
  const values = requests.filter(request => request.status === "ok" || (request === row && row.status === "ok"))
    .map(request => phaseMetrics()?.decodePhase({...request,concurrency:1})?.tps).filter(value => Number.isFinite(value) && value >= 0);
  if (values.length !== requests.length) return null;
  values.sort((a,b) => a-b);
  return values.length ? (values[Math.floor((values.length-1)/2)] + values[Math.floor(values.length/2)]) / 2 : null;
}
function storageMetricValue(row,metric) {
  if (row.status !== "ok") return null;
  if (metric === "wall_s") return Number.isFinite(row.wall_s) && row.wall_s > 0 ? row.wall_s : null;
  if (metric === "prefill_tps" || metric === "decode_tps") return collectiveRate(row, metric);
  return storageMedian((row.requests || [row]).map(request => request.status && request.status !== "ok" ? null : request[metric]));
}
function storageComparisonGroups(run, metric = "wall_s") {
  const comparison = run.storage_comparison || {};
  const baseline = comparison.baseline_results || run.results || [], alternate = comparison.alternate_results || [];
  const groupKey = row => JSON.stringify([row.model,!!row.mtp,row.concurrency || 1,row.target_tokens]);
  const caseKey = row => JSON.stringify([!!row.mtp,row.repeat || 0]);
  const groups = new Map((comparison.rows || []).map(row=>[groupKey(row),{...row,baseline:[],alternate:[]}]));
  for (const [condition,rows] of [["baseline",baseline],["alternate",alternate]]) for (const row of rows) {
    const key = groupKey(row);
    if (!groups.has(key)) groups.set(key,{model:row.model,mtp:row.mtp,quantization:row.quantization,quantization_method:row.quantization_method,target_tokens:row.target_tokens,concurrency:row.concurrency || 1,baseline:[],alternate:[]});
    groups.get(key)[condition].push(row);
  }
  return [...groups.values()].filter(group=>group.alternate.length || (comparison.rows || []).some(row=>groupKey(row)===groupKey(group))).map(group=>{
    const original = new Map(group.baseline.map(row=>[caseKey(row),row]));
    const paired = group.alternate.map(row=>[original.get(caseKey(row)),row]).filter(([a,b])=>a && storageMetricValue(a,metric) != null && storageMetricValue(b,metric) != null);
    const raw = group.baseline.length || group.alternate.length;
    const a = storageMedian((paired.length ? paired.map(pair=>pair[0]) : group.baseline).map(row=>storageMetricValue(row,metric)));
    const b = storageMedian((paired.length ? paired.map(pair=>pair[1]) : group.alternate).map(row=>storageMetricValue(row,metric)));
    const pairedCount = raw ? paired.length : metric === "wall_s" ? group.paired_successes || 0 : 0;
    const primary = raw ? a : metric === "wall_s" ? (pairedCount ? group.baseline_wall_s : group.baseline_success_wall_s_median) ?? null : null;
    const alternative = raw ? b : metric === "wall_s" ? (pairedCount ? group.alternative_wall_s : group.alternative_success_wall_s_median) ?? null : null;
    return {...group,primary,alternative,pairedCount,
      baselinePassed:raw ? group.baseline.filter(row=>row.status==="ok").length : group.baseline_successes,
      alternativePassed:raw ? group.alternate.filter(row=>row.status==="ok").length : group.alternative_successes,
      speedup:raw ? storageMedian(paired.map(([a,b])=>STORAGE_METRICS[metric].lower ? storageMetricValue(a,metric)/storageMetricValue(b,metric) : storageMetricValue(b,metric)/storageMetricValue(a,metric)))
        : pairedCount && primary > 0 && alternative > 0 ? group.wall_speedup ?? (STORAGE_METRICS[metric].lower ? primary/alternative : alternative/primary) : null};
  });
}
function storageComparisonCsv(run) {
  const comparison = run.storage_comparison || {}, alternatives = comparison.alternate_results || [];
  const key = row => JSON.stringify([row.model, !!row.mtp, row.concurrency || 1, row.target_tokens, row.repeat || 0]);
  const selected = new Set(alternatives.map(key));
  const columns = ["condition", "model", "quantization", "quantization_method", "target_tokens", "concurrency", "repeat", "status", "prompt_tokens", "completion_tokens", "wall_s", "ttft_s", "prefill_tps", "decode_tps", "session_id", "request_metrics_json", "group_output_tps", "per_request_decode_tps"];
  const quote = value => {
    if (value == null) return "";
    let text = String(value);
    if (typeof value === "string" && /^[=+\-@\t\r]/.test(text)) text = "'" + text;
    return '"' + text.replace(/"/g, '""') + '"';
  };
  const rows = [[comparison.baseline_label || "primary", (comparison.baseline_results || run.results || []).filter(row => selected.has(key(row)))],
    [comparison.alternative_label || "alternative", alternatives]].flatMap(([condition, values]) => values.map(row =>
      columns.map(column => {
        if (column === "group_output_tps") return quote(METRICS.group_output_tps.get(row));
        if (column === "per_request_decode_tps") return quote(METRICS.per_request_decode_tps.get(row));
        if (column === "request_metrics_json") {
          const metrics = ["status", "prompt_tokens", "completion_tokens", "cached_prompt_tokens", "wall_s", "decode_s", "start_offset_s", "ttft_s", "prefill_tps", "decode_tps", "completion_tokens_estimated", "reasoning_tokens", "decode_token_count", "timing_scope", "response_tail_s", "metric_sources"];
          return quote(JSON.stringify((row.requests || [row]).map(request => Object.fromEntries(metrics.filter(key => request[key] != null).map(key => [key, request[key]])))));
        }
        return quote(column === "condition" ? condition : column === "repeat" ? (row.repeat || 0) + 1 : ["prefill_tps", "decode_tps"].includes(column) ? collectiveRate(row, column) : row[column]);
      }).join(",")));
  return [columns.map(quote).join(","), ...rows].join("\r\n");
}

function selectedResultsCsv(runs) {
  const columns = ["benchmark","condition","model","quantization","quantization_method","target_tokens","concurrency","repeat","status","wall_s","ttft_s","prefill_tps","decode_tps","prompt_tokens","completion_tokens","request_metrics_json","gpu_power_scope","power_json","group_power_json","vram_peak_mb","group_output_tps","per_request_decode_tps"];
  const quote = value=>{
    if (value == null) return "";
    let text=String(value);if(typeof value === "string" && /^[=+\-@\t\r]/.test(text)) text="'"+text;
    return '"'+text.replace(/"/g,'""')+'"';
  };
  const rows = measurementRuns(runs).flatMap(run=>(run.results || []).filter(row=>
    (state.visibleModels === null || state.visibleModels.has(row.model)) && levelOn(row.concurrency || 1) &&
    (state.mtpFilter === "all" || (!runProviderManaged(run) && !!row.mtp === (state.mtpFilter === "on"))))
    .map(row=>columns.map(column=>{
      if(column === "group_output_tps") return quote(METRICS.group_output_tps.get(row));
      if(column === "per_request_decode_tps") return quote(METRICS.per_request_decode_tps.get(row));
      if(column === "benchmark") return quote(run.parent_run_id || run.id);
      if(column === "condition") return quote(run.label || run.id);
      if(column === "repeat") return quote((row.repeat || 0)+1);
      if(column === "gpu_power_scope") return quote((row.power || row.group_power || {}).scope || "legacy selected sensor");
      if(column === "power_json") return quote(JSON.stringify(row.power || {}));
      if(column === "group_power_json") return quote(JSON.stringify(row.group_power || {}));
      if(column === "request_metrics_json") {
        const keys=["status","prompt_tokens","completion_tokens","wall_s","decode_s","decode_token_count","start_offset_s","ttft_s","prefill_tps","decode_tps","completion_tokens_estimated","timing_scope","response_tail_s","metric_sources"];
        return quote(JSON.stringify((row.requests || [row]).map(request=>Object.fromEntries(keys.filter(key=>request[key]!=null).map(key=>[key,request[key]])))));
      }
      return quote(["prefill_tps", "decode_tps"].includes(column) ? collectiveRate(row, column) : row[column]);
    }).join(",")));
  return [columns.map(quote).join(","),...rows].join("\r\n");
}

const TELEMETRY_METRICS = {
  cpu_util_percent:["CPU load","%",1],gpu_util:["GPU load","%",1],system_memory_percent:["RAM used","%",1],vram_percent:["VRAM used","%",1],
  system_memory_used_bytes:["RAM used","GiB",1073741824],vram_mb:["VRAM used","GiB",1024],cpu_temp_c:["CPU temperature","°C",1],temp_c:["GPU temperature","°C",1],
  gpu_w:["GPU power","W",1],cpu_w:["CPU power","W",1],system_w_est:["Estimated system power","W",1],clock_mhz:["GPU clock","MHz",1],
};
function telemetryMarkup(run) {
  const telemetry=run.telemetry || {}, summary=telemetry.summary || {}, metrics=summary.metrics || {};
  const keys=Object.keys(TELEMETRY_METRICS).filter(key=>metrics[key] && typeof metrics[key] === "object");
  if(!keys.length) return "";
  const format=(value,divisor=1)=>Number.isFinite(value) ? fmt(value/divisor,1) : "—";
  const history=telemetry.history || {}, retention=history.retention || {};
  const boundary=summary.scope === "benchmark_client_all_nvidia_gpus" ? `All ${summary.gpu_count || summary.gpus?.length || ""} NVIDIA GPU boards · combined totals. GPU utilization is a mean; VRAM is a physical sum across separate cards.` : "Benchmark client and selected GPU/SoC sensor (legacy boundary).";
  const devices=Array.isArray(summary.gpus) && summary.gpus.length ? `<h4>Per-card observations</h4><div class="table-wrap"><table><thead><tr><th>GPU</th><th>Mean / peak W</th><th>Peak VRAM</th><th>Mean load</th><th>Peak temperature</th><th>Mean clock</th><th>Covered energy</th></tr></thead><tbody>${summary.gpus.map(card=>{
    const m=card.metrics || {};
    return `<tr><td>GPU ${esc(card.index+1)} · ${esc(card.name)}</td><td>${format(m.gpu_w?.time_weighted_mean)} / ${format(m.gpu_w?.max)} W</td><td>${format(m.vram_mb?.max,1024)} GiB</td><td>${format(m.gpu_util?.time_weighted_mean)}%</td><td>${format(m.temp_c?.max)} °C</td><td>${format(m.clock_mhz?.time_weighted_mean)} MHz</td><td>${format(card.gpu_energy_sampled_j)} J</td></tr>`;
  }).join("")}</tbody></table></div>` : "";
  return `<details class="adv telemetry-report"><summary>Run telemetry · ${esc(summary.observations ?? 0)} observations</summary><div><p class="hint">${esc(boundary)} CPU/RAM cover the whole client; unrelated applications are included. System power remains estimated.</p>${devices}<div class="table-wrap"><table><thead><tr><th>Metric</th><th>Mean</th><th>Peak</th><th>Final</th><th>Early 60 s</th><th>Late 60 s</th><th>Late − early</th><th>Coverage</th></tr></thead><tbody>${keys.map(key=>{
    const metric=metrics[key], [label,unit,divisor]=TELEMETRY_METRICS[key];
    return `<tr><td>${esc(label)} <small>${esc(unit)}</small></td>${[metric.mean,metric.max,metric.last,metric.early_60s_mean,metric.late_60s_mean,metric.late_minus_early].map(value=>`<td>${format(value,divisor)}</td>`).join("")}<td>${Number.isFinite(metric.coverage_fraction)?format(metric.coverage_fraction*100)+"%":"—"}</td></tr>`;
  }).join("")}</tbody></table></div><p class="hint">Early/late windows can overlap. Drift describes observations, not proof of throttling. Coverage excludes long sampling gaps.</p>${Array.isArray(history.points) && history.points.length ? `<p class="hint">History retains ${esc(retention.retained_points ?? history.points.length)} of ${esc(retention.total_observations ?? history.points.length)} observations (recent ring). Summary includes earlier observations. Covered energy includes loading and idle; per-test phase energy is available in All measurements.</p><div class="telemetry-history" data-telemetry-plot="power"></div><div class="telemetry-history" data-telemetry-plot="usage"></div><div class="telemetry-history" data-telemetry-plot="temperature"></div>`:""}</div></details>`;
}
function bindTelemetry(container,run) {
  const raw=run.telemetry?.history?.points;
  if(!Array.isArray(raw) || !raw.length || !container?.querySelectorAll) return;
  container.querySelectorAll(".telemetry-report").forEach(report=>{
    const draw=()=>{
      const stride=Math.max(1,Math.ceil(raw.length/300)), points=raw.filter((_,index)=>index%stride===0 || index===raw.length-1);
      for(const [kind,keys,unit,title] of [["power",["gpu_w","system_w_est"],"W","GPU board power and estimated system power"],["usage",["cpu_util_percent","gpu_util","system_memory_percent","vram_percent"],"%","CPU / GPU load and memory use"],["temperature",["cpu_temp_c","temp_c"],"°C","Observed temperatures"]]) {
        const target=report.querySelector(`[data-telemetry-plot="${kind}"]`); if(!target) continue;
        const series=keys.map((key,index)=>({id:key,name:TELEMETRY_METRICS[key][0],color:resolveColor(`var(--series-${index+1})`),points:points.filter(point=>Number.isFinite(point.elapsed_s) && Number.isFinite(point[key])).map(point=>({x:point.elapsed_s,y:point[key]}))})).filter(item=>item.points.length);
        const perCardFields={power:["gpu_w"],usage:["gpu_util","vram_percent"],temperature:["temp_c"]}[kind];
        for(const gpu of run.telemetry?.summary?.gpus || []) for(const field of perCardFields) {
          const data=points.map(point=>({point,gpu:point.gpus?.find(card=>card.index===gpu.index)})).filter(item=>Number.isFinite(item.gpu?.[field]));
          if(data.length)series.push({id:`gpu-${gpu.index}-${field}`,name:`GPU ${gpu.index+1} · ${gpu.name} · ${TELEMETRY_METRICS[field][0]}`,color:resolveColor(`var(--series-${series.length%12+1})`),points:data.map(({point,gpu})=>({x:point.elapsed_s,y:gpu[field]}))});
        }
        target.hidden = !series.length;
        if(series.length) Charts.line(target,{unit,xLabel:"Elapsed seconds",yLabel:unit,height:240,xScale:"linear",yScale:"linear",ariaLabel:title,series});
      }
    };
    draw();
    report.addEventListener("toggle",()=>{if(report.open)draw();});
  });
}
function modelConfigurationsMarkup(run) {
  const observed=(run.results || []).map(row=>row.model_configuration).filter(Boolean);
  const initial=Array.isArray(run.model_configurations) ? run.model_configurations : [];
  const configurations=[...new Map((observed.length ? observed : initial).filter(item=>item && typeof item === "object").map(item=>[item.configuration_id,item])).values()];
  if(!configurations.length) return "";
  return `<div class="table-wrap"><table><thead><tr><th>Model / configuration</th><th>Quantization</th><th>Reasoning</th><th>MTP</th></tr></thead><tbody>${configurations.map(item=>`<tr><td>${esc(item.model?.value || "unknown")}<small>${esc(item.request_model?.value || "")} · ${esc((item.configuration_id || "").slice(0,12))}</small></td><td>${esc(quantizationLabel({model:item.model?.value,model_configuration:item}))}<small>${esc(item.quantization?.source || "unknown")}</small></td><td>Requested: ${esc(item.reasoning?.requested || "unknown")}<small>Effective: ${esc(item.reasoning?.effective || "unknown")}<br>Reported: ${esc(item.reasoning?.user_reported || "unknown")}</small></td><td>Requested: ${esc(item.mtp?.requested || "unknown")}<small>Effective: ${esc(item.mtp?.effective || "unknown")}<br>Reported: ${esc(item.mtp?.user_reported || "unknown")}</small></td></tr>`).join("")}</tbody></table></div>`;
}
function scientificMetadata(run) {
  const reported = run.reproducibility?.settings || run.config?.reproducibility || {};
  const claims = run.performance_claims || run.config?.performance_claims || [];
  const provider = run.provider || {};
  const scope = run.measurement_scope || {};
  const tags = Array.isArray(run.tags) ? run.tags.filter(tag => typeof tag === "string").slice(0, 24) : [];
  return (provider.kind ? `<p class="hint"><b>Provider:</b> ${esc(provider.label || provider.kind)}; ${esc(provider.model_management || "unknown")} model management.</p>` : "")
    + (run.engine_groups?.length ? `<p class="hint"><b>Engines in this benchmark:</b> ${run.engine_groups.map(group => `${esc(group.label)} (${(group.config?.models || []).length} models)`).join("; ")}. Settings, transfer profiles and runtime observations are retained in their execution sessions.</p>` : "")
    + (tags.length ? `<p class="hint"><b>Tags:</b> ${tags.map(esc).join(" · ")}</p>` : "")
    + ((run.hardware_measurement_history || []).some(capture => capture.transfer_bandwidth) ? `<details class="adv"><summary>Independent hardware measurements</summary>${run.hardware_measurement_history.filter(capture => capture.transfer_bandwidth).map(capture => transferBandwidthMarkup(capture.transfer_bandwidth)).join("")}</details>` : "")
    + modelConfigurationsMarkup(run)
    + telemetryMarkup(run)
    + (scope.hardware_attribution ? `<p class="hint"><b>Inference hardware attribution:</b> ${esc(scope.hardware_attribution)}</p>` : "")
    + (Object.keys(reported).length ? `<p class="hint"><b>Operator-reported settings (unverified):</b> ${Object.entries(reported).map(([k,v]) => `${esc(k)}: ${esc(v)}`).join("; ")}</p>` : "")
    + (claims.length ? `<p class="hint">Compute figures below were supplied by the operator; Wattever did not verify them. Quantization bit width does not establish arithmetic precision.</p><div class="table-wrap"><table><thead><tr><th>Precision</th><th>Value</th><th>Kind</th><th>Sparsity</th><th>Execution path</th><th>Source / method</th></tr></thead><tbody>${claims.map(c => `<tr><td>${esc(c.precision)}</td><td>${esc(c.value)} ${esc(c.unit)}</td><td>${esc(c.kind)}</td><td>${esc(c.sparsity)}</td><td>${esc(c.execution_path)}</td><td>${esc(c.source)}</td></tr>`).join("")}</tbody></table></div>` : "")
    + (run.measurement_client ? `<details><summary>Benchmark client inventory (inference hardware unknown)</summary>${hardwareMarkup(run.measurement_client.hardware)}</details>` : "");
}
function renderHardware() {
  const gpus = (state.env.hardware || {}).gpus || [];
  if ($("#machine-badges")) $("#machine-badges").innerHTML = machineHeaderMarkup(state.env);
  $("#hardware-inventory").innerHTML = (state.env.hardware?.source === "manual" ? '<p class="hint">Hardware details entered manually; not detected or measured.</p>' : "") + hardwareMarkup(state.env.hardware);
  if (!browserMode()) {
    if(state.env.capabilities?.memory_topology) {
      const followUps=state.env.hardware_follow_ups || [];
      $("#hardware-inventory").insertAdjacentHTML("beforeend", memoryFollowUpMarkup(followUps));
      $("#hardware-inventory").querySelectorAll("[data-memory-follow-up]").forEach(button=>button.addEventListener("click",()=>refreshMemoryTopology(button.dataset.memoryFollowUp,button,Number(button.dataset.gpuIndex) || 0)));
      $("#hardware-inventory").insertAdjacentHTML("beforeend", '<button class="btn-sm" id="detect-memory-topology">Refresh memory topology</button><span class="hint" id="memory-topology-status"> Built-in OS / firmware probe; no install needed.</span>');
      $("#detect-memory-topology").addEventListener("click",event=>refreshMemoryTopology(null,event.target));
    } else $("#hardware-inventory").insertAdjacentHTML("beforeend", '<p class="hint">Relaunch the desktop helper to enable built-in memory topology detection.</p>');
    $("#hardware-inventory").insertAdjacentHTML("beforeend", '<button class="btn-sm" id="measure-transfers">Measure transfer bandwidth</button><span class="hint" id="transfer-status"> Includes mounted NVMe drives; run while inference is idle.</span>');
    $("#measure-transfers").addEventListener("click", async (event) => {
      event.target.disabled = true;
      $("#transfer-status").textContent = " Measuring storage and GPU transfers…";
      try {
        const result = await api("/api/hardware/bandwidth", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({gpu_index: Number($("#gpu-index").value) || 0, storage_probe_directory: $("#storage-probe-directory")?.value.trim() || null, storage_probe_directories: storageProbeDirectories()})});
        state.env.hardware.transfer_bandwidth = result.transfer_bandwidth;
        state.env.hardware.transfer_coverage = result.transfer_coverage;
        renderHardware();
      } catch (error) {
        $("#transfer-status").textContent = " " + error.message;
        event.target.disabled = false;
      }
    });
  }
  if ($("#storage-probe-field")) $("#storage-probe-field").hidden = browserMode();
  const sensors = gpus.filter((g) => g.sensor_index != null);
  $("#gpu-index").innerHTML = sensors.length ? sensors.map((g) => `<option value="${esc(g.sensor_index)}">${esc(g.sensor_index)}: ${esc(g.name)}</option>`).join("") : '<option value="0">0 (default sensor)</option>';
  const setup=state.env.gpu_setup?.strata;
  if (!browserMode() && setup?.available && sensors.length>1) {
    $("#hardware-inventory").insertAdjacentHTML("beforeend", `<section class="hardware-section gpu-setup"><h4>GPU placement</h4><div class="row"><div class="field"><label for="strata-gpu-choice">Strata</label><select id="strata-gpu-choice"><option value="auto">Automatic · all supported cards</option>${sensors.map(card=>`<option value="${esc(card.sensor_index)}">GPU ${esc(card.sensor_index+1)} · ${esc(String(card.name || "GPU").replace(/^NVIDIA (?:GeForce\s+)?/,""))} only</option>`).join("")}</select></div></div><p class="hint" id="gpu-setup-status" role="status">${esc(setup.error || "Strata applies this at the next desktop model load. LM Studio uses its own GPU settings.")}</p></section>`);
    const select=$("#strata-gpu-choice");
    select.value=setup.choice?.mode === "single" ? String(setup.choice.gpu_index) : "auto";
    select.addEventListener("change", async()=>{
      select.disabled=true;
      try {
        const choice=select.value === "auto" ? {mode:"auto"} : {mode:"single",gpu_index:Number(select.value)};
        const saved=await api("/api/hardware/gpu-setup",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(choice)});
        setup.choice=saved.choice;
        $("#gpu-setup-status").textContent="Saved. Applies when you load a model in Strata Control.";
      } catch(error) {
        select.value=setup.choice?.mode === "single" ? String(setup.choice.gpu_index) : "auto";
        $("#gpu-setup-status").textContent=error.message;
      } finally { select.disabled=false; }
    });
  }
}
function memoryFollowUpMarkup(followUps=[]) {
  return followUps.length ? `<section class="hardware-section memory-follow-ups"><h4>Configuration check due</h4>${followUps.map(item=>`<div class="probe-empty">${conditionBadge(item.label)}<span>Matching GPU reconnected: update this benchmark's memory channel record.</span><button class="btn-sm" data-memory-follow-up="${esc(item.run_id)}" data-gpu-index="${esc(item.gpu_index ?? 0)}">Update memory topology</button></div>`).join("")}</section>` : "";
}
async function refreshMemoryTopology(runId,button,gpuIndex) {
  button.disabled=true;
  const status=$("#memory-topology-status");
  if(status) status.textContent=" Reading built-in OS / firmware topology…";
  try {
    const result=await api("/api/hardware/memory",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({run_id:runId || undefined,gpu_index:gpuIndex ?? (Number($("#gpu-index").value) || 0)})});
    state.env.hardware.memory=result.memory;
    if(runId) {
      state.env.hardware_follow_ups=(state.env.hardware_follow_ups || []).filter(item=>item.run_id !== runId);
      delete state.runCache[runId];state.detailSignature=null;
      await loadRuns();
    }
    renderHardware();
    $("#memory-topology-status").textContent=result.memory.channel_topology?.status === "unavailable"
      ? " Channel topology unavailable from this OS / firmware. No channel count was assumed."
      : " Topology captured; badges identify the source and verification level.";
  } catch(error) {
    if(status) status.textContent=" "+error.message;
    button.disabled=false;
  }
}
function renderConcurrency() {
  const levels = [...new Set([...CONCURRENCY_PRESETS, ...state.concurrencyLevels])].sort((a, b) => a - b);
  renderRange("#concurrency-chips", levels, state.concurrencyLevels, (value) => {
    toggleLevel(state.concurrencyLevels, value);
    renderConcurrency(); updatePlan();
  }, String, "simultaneous queries");
}

function storageProbeDirectories() {
  return ($("#storage-probe-directories")?.value || "").split(";").map(value => value.trim()).filter(Boolean);
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
  if ($("#skip-after-timeout")) {
    $("#skip-after-timeout").checked = defaults.skip_after_timeout === true;
    $("#skip-after-timeout").closest(".chip").dataset.on = defaults.skip_after_timeout === true ? "1" : "0";
  }
  renderModels(); renderSizes(); renderConcurrency(); updatePlan();
}

function applyLastConfig() {
  // fall back to the server's defaults, which are platform-aware (the system
  // power constants differ a lot between a desktop PC and a Mac)
  const last = state.env.last_config || state.env.defaults;
  if (!last) return;
  if (!browserMode() && last.provider && $("#helper-provider")) {
    $("#helper-provider").value = last.provider;
    updateProviderControls();
  }
  const connection = state.env.endpoint_connection;
  const storedUrl = localStorage.getItem(OPENAI_URL_STORAGE);
  if (!browserMode() && $("#helper-url") && !$("#helper-url").value.trim()) {
    $("#helper-url").value = connection?.base_url || storedUrl || "";
  }
  const storedProvider = localStorage.getItem(HELPER_PROVIDER_STORAGE);
  if (!browserMode() && storedProvider === "openai" && $("#helper-provider") && last.provider !== "openai") {
    $("#helper-provider").value = "openai";
    updateProviderControls();
  }
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
  if ($("#skip-after-timeout")) {
    $("#skip-after-timeout").checked = last.skip_after_timeout === true;
    $("#skip-after-timeout").closest(".chip").dataset.on = last.skip_after_timeout === true ? "1" : "0";
  }
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
/* What the MTP choice means for this connection: the modes asked for, the passes that can run,
 * and what to tell the user. An endpoint decides MTP itself, so only its standard pass runs. */
function mtpPlan() {
  const asked = browserMode() ? ["off"] : mtpModes();
  if (!genericHelper() || !asked.includes("on")) return {asked, passes: asked, notice: ""};
  const passes = asked.filter(mode => mode === "off");
  return {asked, passes, notice: passes.length
    ? "MTP pass not run: this endpoint decides MTP itself."
    : "This endpoint decides MTP itself, so an MTP-only benchmark cannot run on it. Choose Off or Compare both, or connect Provider: LM Studio (managed)."};
}

/* Guard the one thing the whole dataset rests on.
 *
 * Output length and the reasoning switch change what decode tk/s *means*, so a
 * run taken with different values cannot be compared with the ones already
 * cached — and nothing about the form makes that obvious, because the field
 * remembers whatever was used last. Say so before the benchmark runs, not
 * after, and offer to put it back in one click. */
function checkComparability() {
  if (providerManaged()) { if ($("#comparability")) $("#comparability").innerHTML = ""; return; }
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
  const hw = env.hardware || {};
  const cards = (hw.gpus || []).length ? hw.gpus : (env.gpu && env.gpu.name ? [{name: env.gpu.name, memory_bytes: env.gpu.vram_total_mb * 1048576, memory_architecture: env.gpu.memory_architecture}] : []);
  const label = cfg.provider === "openai" && !cfg.endpoint_inference_local ? (cfg.endpoint_label || "Endpoint benchmark") : (cfg.gpu_offload === "off" ? "CPU only" : cards.map(gpu => String(gpu.name || "Unknown GPU").replace(/^NVIDIA (?:GeForce\s+)?/, "")).join(" + ")) || "Local benchmark";
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
  if (cards.length > 1) notes.push("Power monitoring: all detected NVIDIA GPUs, per card and combined (monitoring only; model placement is engine controlled)");
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
  const mtp = mtpPlan();
  const base = state.models.size * state.sizes.size * mtp.passes.length * Number($("#repeats").value || 1);
  const tests = base * state.concurrencyLevels.size;
  const requests = base * [...state.concurrencyLevels].reduce((a, b) => a + b, 0);
  $("#plan-summary").textContent = tests
    ? `${state.models.size} model${state.models.size === 1 ? "" : "s"} / ${tests} tests / ${requests} queries` + (mtp.notice ? " · " + mtp.notice : "")
    : !mtp.passes.length ? mtp.notice : "Choose at least one model and workload.";
  const reconnect = !!state.providerNeedsConnection || (genericHelper() && state.endpointSignature !== helperConnectionSignature());
  $("#start-btn").disabled = !tests || reconnect;
  if ($("#run-condition-badges")) $("#run-condition-badges").innerHTML = engineBadgesMarkup({config:collectConfig()});
  if (reconnect && $("#helper-status")) { $("#helper-status").textContent = "Connect to apply changes."; $("#helper-status").dataset.state = "pending"; }
  checkComparability();
}

/* ---------------- run control ---------------- */
function collectConfig() {
  return {
    ...helperEndpointConfig(),
    storage_probe_directory: browserMode() ? null : $("#storage-probe-directory")?.value.trim() || null,
    storage_probe_directories: browserMode() ? [] : storageProbeDirectories(),
    ...publicRunMetadata(),
    canonical_model_id: $("#meta-canonical-model")?.value.trim() || "",
    reported_mtp: $("#meta-reported-mtp")?.value || "unknown",
    reported_reasoning: $("#meta-reported-reasoning")?.value.trim() || "unknown",
    endpoint_reasoning_effort: $("#helper-reasoning")?.value || null,
    token_limit_field: $("#token-limit-field")?.value || "max_tokens",
    temperature: $("#job-temperature")?.value === "" ? null : Number($("#job-temperature")?.value ?? "0"),
    models: [...state.models],
    sizes: [...state.sizes],
    concurrency_levels: [...state.concurrencyLevels].sort((a, b) => a - b),
    gpu_index: Number($("#gpu-index").value),
    runtime_gpu_placement: $("#runtime-gpu-placement").value.trim(),
    max_tokens: Number($("#max-tokens").value) || 256,
    repeats: Number($("#repeats").value) || 1,
    gpu_offload: providerManaged() ? "provider_managed" : $("#offload").value,
    context_policy: providerManaged() ? "provider_managed" : ($("#context-policy")?.value || "per_test"),
    mtp_modes: mtpPlan().asked,  // the helper resolves what an endpoint cannot run
    fallback_on_failure: providerManaged() ? false : $("#fallback").checked,
    stop_model_after_failure: $("#stop-after-fail").checked,
    skip_after_timeout: providerManaged() ? false : !!$("#skip-after-timeout")?.checked,
    warmup: browserMode() ? false : $("#warmup").checked,
    disable_thinking: providerManaged() ? null : ($("#helper-reasoning") ? $("#helper-reasoning").value === "none" : $("#no-think").checked),
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
    else if (status.run) renderLive(status);
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
    renderLive(data.status);
    try {
      await syncLiveBenchmark(data.status, changed);
    } finally {
      // Completion must remain visible even if the result was archived or
      // its final fetch failed after the worker finished.
      if (!data.status.running && state.poller) {
        clearInterval(state.poller); state.poller = null; leaveRunningUi();
      }
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
  const outcomes = {
    completed: {label:"Completed",heading:"Benchmark finished",style:"ok"},
    cancelled: {label:"Stopped",heading:"Benchmark stopped",style:"cancelled"},
    error: {label:"Failed",heading:"Benchmark failed",style:"failed"},
    interrupted: {label:"Incomplete",heading:"Benchmark incomplete",style:"interrupted"},
  };
  const outcome = !status.running && outcomes[status.run?.status];
  const finishing = status.running && !!outcomes[status.run?.status];
  const card = $("#live-card");
  card.dataset.status = outcome ? status.run.status : "running";
  if (outcome) card.classList.remove("hidden");
  $("#live-card h3").textContent = outcome?.heading || "Progress";
  $("#progress-bar").style.width = total ? (100 * done / total).toFixed(1) + "%" : "0%";
  const phase = status.cancelling ? "Stopping…" : finishing ? "Finishing…" : (live.phase || "idle");
  $("#live-phase").className = "st st-" + (outcome?.style || "running");
  $("#live-phase").textContent = outcome
    ? `${outcome.label}${total ? ` · ${done}/${total} tests` : ""}${status.run.n_failed ? ` · ${status.run.n_failed} failed` : ""}${status.run.n_skipped ? ` · ${status.run.n_skipped} skipped` : ""}`
    : `${phase}${live.model ? " · " + live.model : ""}${live.size ? " @ " + fmtTokens(live.size) : ""}${total ? `  (${done}/${total})` : ""}`;

  const deadline = $("#live-deadline");
  const hasDeadline = status.running && !finishing && ["prefill","decode","inference"].includes(live.phase) && Number.isFinite(live.timeout_budget_s) && live.timeout_budget_s > 0;
  deadline.hidden = !hasDeadline;
  deadline.textContent = hasDeadline ? `Current test deadline: ${fmtDuration(live.timeout_elapsed_s ?? 0)} elapsed / ${fmtDuration(live.timeout_budget_s)} budget${live.timeout_reason ? " (" + live.timeout_reason + ")" : ""}. Adaptive timing is capped by the configured timeout; it is not a memory limit.` : "";

  const t = live.telemetry || {};
  const sampleNote = $("#live-sample-note");
  sampleNote.hidden = !outcome;
  const sampleTime = t.timestamp ? new Date(t.timestamp) : null;
  sampleNote.textContent = outcome ? "Last recorded sample" + (sampleTime && Number.isFinite(sampleTime.getTime()) ? " \u00b7 " + sampleTime.toLocaleString() : "") : "";
  $("#pill-power").hidden = !status.running || !Number.isFinite(t.gpu_w);
  if (status.running && Number.isFinite(t.gpu_w)) $("#pill-power").innerHTML = `<span class="num">${Math.round(t.gpu_w)}</span> W GPU`;
  $("#live-stats").innerHTML = stat("TTFT", Number.isFinite(live.ttft_s) ? fmt(live.ttft_s, 2) : "\u2014", outcome ? "s last test" : "s this test")
    + stat("Stream arrival", Number.isFinite(live.decode_chunks_per_s_live) ? fmt(live.decode_chunks_per_s_live, 1) : "\u2014", outcome ? "chunks/s last test" : "chunks/s live");
  renderLiveIssues(status);
}

/* ---------------- notes: one slim line, unfolded on demand ---------------- */
/* Each note is {kind, tone, gist, many, html}: `gist` is its one-line summary,
   `many(n)` words several notes of one kind, `html` is shown once unfolded. */
function notesStrip(id, notes, action = "") {
  const items = notes.filter(Boolean);
  if (!items.length) return "";
  const tone = ["err", "warn", "info"].find(level => items.some(item => item.tone === level)) || "info";
  const kinds = new Map();
  for (const item of items) kinds.set(item.kind, [...(kinds.get(item.kind) || []), item]);
  const gists = [...kinds.values()].map(group => group.length > 1 && group[0].many ? group[0].many(group.length) : group[0].gist);
  return `<details class="notes" data-notes="${esc(id)}" data-tone="${tone}"${state.openNotes.has(id) ? " open" : ""}><summary><span class="notes-dot" aria-hidden="true"></span>${items.length > 1 ? `<span class="notes-count">${items.length} notes</span>` : ""}<span class="notes-gist">${gists.map(esc).join(" \u00b7 ")}</span>${action}</summary><div class="notes-body">${items.map(item => `<div class="note">${item.html}</div>`).join("")}</div></details>`;
}
function plainNote(kind, tone, gist, text) {
  return {kind, tone, gist, html: esc(text)};
}
function incompleteNote(run, named = false) {
  const html = benchmarkIncompleteNotice(run);
  if (!html) return null;
  const scope = run.experiment_scope || {};
  const remaining = Math.max(Number(run.remaining_tests) || 0, (Number(scope.planned_cases) || 0) - (Number(scope.recorded_cases) || 0));
  return {kind: "incomplete", tone: "warn", many: count => `${count} benchmarks incomplete`,
    gist: `Benchmark incomplete${remaining > 0 ? ` \u00b7 ${remaining} test${remaining === 1 ? "" : "s"} remaining` : ""}`,
    html: (named ? `<span class="note-source">${esc(run.label || run.id)}</span>` : "") + html};
}
function unservedNotes(rows) {
  return unservedModels(rows).map(item => ({kind: "unserved", tone: "warn", gist: `${item.model} was skipped: the endpoint could not serve it`,
    many: count => `${count} models skipped: the endpoint could not serve them`, html: unservedModelMarkup(item)}));
}

/* ---------------- what went wrong, said where it shows ---------------- */
/* A server's own explanation, without the JSON envelope it arrived in. */
function failureReason(row) {
  const text = String(row?.message || String(row?.fail_kind || "").replace(/_/g, " ")).trim();
  const reply = text.match(/^(HTTP \d{3}) from [^:]+: (\{[\s\S]*)$/);
  if (!reply) return text;
  try {
    const body = JSON.parse(reply[2]);
    const detail = [body?.error?.message, body?.error, body?.message, body?.detail].find(value => typeof value === "string" && value.trim());
    if (detail) return `${reply[1]}: ${detail.trim()}`;
  } catch (error) { /* A truncated reply is shown as received. */ }
  return text;
}

/* Models an endpoint could not serve, with the tests skipped because of it. */
function unservedModels(rows) {
  const found = new Map();
  for (const row of rows || []) {
    if (row.fail_kind === "model_unavailable") found.set(row.model, {model: row.model, session: row.session_id, reason: failureReason(row), skipped: 0});
  }
  for (const row of rows || []) {
    const item = found.get(row.model);
    if (item && row.status === "skipped" && row.session_id === item.session) item.skipped++;
  }
  return [...found.values()];
}
function unservedModelMarkup(item) {
  return `<b>${esc(item.model)} was skipped</b> — the endpoint could not serve it${item.skipped ? `, so ${item.skipped} of its tests ${item.skipped === 1 ? "was" : "were"} not run` : ""}. Other models were not affected.<small class="issue-reason">${esc(item.reason)}</small>`;
}

/* Under the progress bar: why the run stopped, which models were skipped, and how to go on. */
function renderLiveIssues(status) {
  const box = $("#live-issues");
  if (!box) return;
  const run = status.run || {}, ended = !status.running && !!run.id;
  const remaining = Number(run.remaining_tests) || 0;
  const unfinished = ended && remaining > 0 && ["error", "cancelled", "interrupted"].includes(run.status);
  const owed = remaining ? `${remaining} test${remaining === 1 ? " was" : "s were"} not run` : "";
  const action = unfinished && !browserMode() && state.env?.capabilities?.extend
    ? `<button type="button" class="btn-sm" data-continue-run="${esc(run.id)}" title="Review the remaining tests and run them in this benchmark">Continue this benchmark\u2026</button>` : "";
  // The first sentence of the reason stays readable while the strip is folded.
  const cause = (String(run.error || "").match(/^.*?\.(?=\s|$)/) || [String(run.error || "")])[0];
  const stopped = ended && run.status === "error" && run.error
    ? {kind: "stopped", tone: "err", gist: `The benchmark stopped: ${cause}${owed ? ` ${owed}.` : ""}`,
       html: `<b>The benchmark stopped</b> — ${esc(run.error)}${owed ? ` ${owed}.` : ""}`}
    : unfinished ? {kind: "unfinished", tone: "info", gist: `Not finished \u00b7 ${owed}`,
       html: `<b>Not finished</b> — ${owed}. Continue to review the remaining tests and run them in this benchmark.`} : null;
  const markup = notesStrip("live", [stopped, ...unservedNotes($("#live-card")?.dataset.runId === run.id ? state.liveResults : [])], action);
  // Redrawn only on change, so a focused button survives the status poll.
  if (box.dataset.markup !== markup) { box.dataset.markup = markup; box.innerHTML = markup; }
  box.hidden = !markup;
}

async function continueBenchmark(id) {
  showPanel("results");
  await selectRun(id);
  const run = state.runCache[id] || state.runs.find(item => item.id === id);
  if (run) openExtendDialog(run);
}

function liveGpuCards(telemetry) {
  if (!Array.isArray(telemetry.gpus) || !telemetry.gpus.length) return "";
  const value=(number,digits=0)=>Number.isFinite(number) ? fmt(number,digits) : "—";
  return `<div class="gpu-live-grid">${telemetry.gpus.map(gpu=>`<article class="gpu-live-card"><header><strong>GPU ${esc(gpu.index+1)} · ${esc(String(gpu.name || "GPU").replace(/^NVIDIA (?:GeForce\s+)?/,""))}</strong><span class="badge">${gpu.gpu_w == null ? "Power unavailable" : "Measured"}</span></header><dl>${[
    ["Power",value(gpu.gpu_w)+" W"],["GPU load",value(gpu.gpu_util)+"%"],
    ["VRAM used",value(Number.isFinite(gpu.vram_mb)?gpu.vram_mb/1024:null,1)+" GiB"],
    ["VRAM",value(gpu.vram_percent)+"%"],["Temperature",value(gpu.temp_c)+" °C"],["Clock",value(gpu.clock_mhz)+" MHz"]
  ].map(([label,reading])=>`<div><dt>${esc(label)}</dt><dd>${esc(reading)}</dd></div>`).join("")}</dl></article>`).join("")}</div>`;
}

const STATUS_MARK = { ok: "✓", failed: "✕", skipped: "–", cancelled: "⊘" };
function statusCell(r) {
  const mark = STATUS_MARK[r.status] || "?";
  const title = r.fail_kind ? ` title="${esc(r.message || r.fail_kind)}"` : "";
  return `<span class="st st-${esc(r.status)}"${title}>${mark} ${esc(r.status === "failed" ? (r.fail_kind || "failed").replace(/_/g, " ") : r.status)}</span>`;
}

function estimatedMark(r, aggregate = false) {
  if (!r) return "";
  if (aggregate) {
    return (r.aggregate_completion_tokens_estimated || (r.requests && r.requests.some(q => q && q.completion_tokens_estimated))) ? "*" : "";
  }
  return r.completion_tokens_estimated ? "*" : "";
}

function requestDetails(r) {
  if (!Array.isArray(r.requests) || r.requests.length <= 1) return "";
  return `<details><summary>${esc(r.completed_requests ?? 0)}/${r.requests.length} completed</summary>${r.requests.map((q, i) => `<div>Request ${i + 1}: ${esc(q.status || "unknown")} - ${fmt(q.decode_tps, 1)}${estimatedMark(q)} tk/s - TTFT ${fmt(q.ttft_s, 2)} s ${q.message ? esc(q.message) : ""}</div>`).join("")}</details>`;
}

function liveRowMetrics(r) {
  const n = r.concurrency || 1;
  const reqs = Array.isArray(r.requests) && r.requests.length ? r.requests : [r];
  const ok = reqs.filter((q) => q && (q.status === "ok" || (!q.status && r.status === "ok")));
  const num = (v) => typeof v === "number" && Number.isFinite(v) ? v : null;
  const median = (vals) => storageMedian(vals.map(num));
  const sum = (vals) => { const v = vals.map(num); return v.length && v.every((x) => x !== null) ? v.reduce((a, b) => a + b, 0) : null; };
  const outTokens = n > 1 ? (ok.length === n && reqs.length === n ? sum(ok.map(q => q.completion_tokens)) : null) : (num(r.completion_tokens) ?? sum(ok.map(q => q.completion_tokens)));
  let energy = null, watts = null, cards = [];
  if (r.group_power) {
    const g = r.group_power;
    energy = g.scope && g.available !== false && g.low_confidence === false && g.gpu_power_complete === true && (!Number.isFinite(g.gpu_sampled_coverage_fraction) || g.gpu_sampled_coverage_fraction >= 0.8) ? num(g.gpu_energy_j) : null; watts = num(g.gpu_w_avg);
    cards = (g.gpus || []).map((c) => ({ name: c.name || `GPU ${c.index}`, w: num(c.gpu_w_avg), vram: num(c.vram_mb_max) }));
  } else if (r.power) {
    const p = r.power;
    // Phase-only windows do not establish energy during response delivery tails.
    energy = null;
    const span = (num(r.ttft_s) || 0) + (num(r.decode_s) || 0);
    watts = null;
    cards = (p.gpus || []).map((c) => {
      const e = num(c.prefill?.gpu_energy_j) !== null && num(c.decode?.gpu_energy_j) !== null ? c.prefill.gpu_energy_j + c.decode.gpu_energy_j : null;
      return { name: c.name || `GPU ${c.index}`, w: e !== null && span > 0 ? e / span : null, vram: num(c.vram_peak_mb) };
    });
  }
  const estimated = !!(r.aggregate_completion_tokens_estimated || r.completion_tokens_estimated || reqs.some((q) => q && q.completion_tokens_estimated));
  const partial = { ...r, requests: reqs.length > 1 ? reqs : undefined };
  return {
    n, estimated, watts, cards, wall: num(r.wall_s),
    decodeTime: phaseMetrics()?.decodePhase(r)?.decode_s ?? null,
    prefillTime: phaseMetrics()?.prefillPhase(r)?.prefill_s ?? null,
    totalPrompt: (n === 1 || ok.length === n) ? sum(ok.map(q => q.prompt_tokens)) : null,
    totalOutput: (n === 1 || ok.length === n) ? sum(ok.map(q => q.completion_tokens)) : null,
    prompt: num(r.prompt_tokens) ?? median(ok.map((q) => q.prompt_tokens)),
    prefill: collectiveRate(partial, "prefill_tps", { partial: true }) ?? collectiveRate(r, "prefill_tps", { partial: true }),
    decode: decodeRate(r),decodeApprox:decodeApproximate(r),
    perRequestDecode: requestDecodeRate(r),
    outputTps: outTokens !== null && num(r.wall_s) > 0 ? outTokens / r.wall_s : null,
    ttft: num(r.ttft_s) ?? median(ok.map((q) => q.ttft_s)),
    jPerTok: energy !== null && outTokens && !estimated ? energy / outTokens : null,
    vram: num(r.group_power?.vram_mb_max) ?? num(r.vram_peak_mb),
  };
}

/* The headline columns of the live table; their means sit in the card title, in the column's colour. */
const LIVE_AVERAGES = [
  {key: "wall", tone: "wall", label: "Wall", unit: "s", digits: 2, column: "Batch wall s"},
  {key: "prefillTime", tone: "prefill", label: "Prefill", unit: "s", digits: 2, column: "Prefill time s"},
  {key: "prefill", tone: "prefill", label: "Prefill", unit: "tk/s", digits: 0, column: "Prefill tk/s"},
  {key: "decodeTime", tone: "decode", label: "Decode", unit: "s", digits: 2, column: "Decode time s", approximate: m => m.decodeApprox},
  {key: "decode", tone: "decode", label: "Decode", unit: "tk/s", digits: 1, column: "Total decode tk/s", approximate: m => m.estimated || m.decodeApprox},
];
/* Arithmetic mean of each headline column over the tests that completed; a test without that value is left out. */
function liveAverages(results, metrics) {
  const measured = metrics.filter((m, i) => results[i]?.status === "ok");
  return LIVE_AVERAGES.map(item => {
    const rows = measured.filter(m => typeof m[item.key] === "number" && Number.isFinite(m[item.key]));
    return {...item, count: rows.length, tests: measured.length,
      value: rows.length ? rows.reduce((total, m) => total + m[item.key], 0) / rows.length : null,
      approximate: !!item.approximate && rows.some(item.approximate)};
  });
}
function liveAveragesMarkup(averages) {
  const shown = averages.filter(item => item.value !== null);
  if (!shown.length) return "";
  const tests = shown[0].tests;
  const groups = [];
  for (const item of shown) {
    let group = groups.find(g => g.tone === item.tone);
    if (!group) groups.push(group = {tone: item.tone, label: item.label, items: []});
    group.items.push(item);
  }
  return `<span class="avg-lead" title="Arithmetic mean of each column over the tests that completed. Failed and skipped tests are left out.">Average · ${tests} test${tests === 1 ? "" : "s"}</span>${groups.map(group =>
    `<span class="avg" data-tone="${group.tone}"><span class="avg-k">${group.label}</span>${group.items.map(item =>
      `<span class="avg-v" title="Mean of ${esc(item.column)} over ${item.count} test${item.count === 1 ? "" : "s"}"><b>${fmt(item.value, item.digits)}${item.approximate ? "*" : ""}</b> ${item.unit}</span>`).join("")}</span>`).join("")}`;
}

function renderLiveTable() {
  const metrics = state.liveResults.map(liveRowMetrics);
  const note = `Decode excludes TTFT: scoped decode tokens divided by post-first-token decode time. Concurrent intervals are merged; overlap counts once and gaps without decoding are excluded. End-to-end output includes prefill and queueing.${metrics.some(m=>m.estimated) ? " * Output tokens estimated (server token usage unavailable)." : ""}${metrics.some(m=>m.decodeApprox && m.decode!==null) ? " * Decode boundary is approximate: streamed chunks can contain multiple tokens; legacy intervals may include unrecorded delivery tails." : ""}`;
  const noteBox = $("#live-table-note"), averagesBox = $("#live-averages");
  if (noteBox) noteBox.textContent = note;
  if (averagesBox) averagesBox.innerHTML = liveAveragesMarkup(liveAverages(state.liveResults, metrics));
  const rows = state.liveResults.map((r, i) => {
    const m = metrics[i], star = (v) => v != null && m.estimated ? "*" : "";
    const cardTip = (key, unit, f) => m.cards.length > 1 ? ` title="${esc(m.cards.map((c) => `${c.name}: ${f(c[key])} ${unit}`).join(" · "))}"` : "";
    // Without a status column the row itself carries the outcome: a failed test is drawn in red.
    const outcome = r.status && r.status !== "ok" ? r.status === "failed" ? (r.fail_kind || "failed").replace(/_/g, " ") : r.status : "";
    const reason = r.status !== "ok" ? failureReason(r) || (outcome ? outcome[0].toUpperCase() + outcome.slice(1) : "") : "";
    const lead = `<tr data-status="${esc(r.status || "")}"${outcome ? ` title="${esc(outcome)}"` : ""}>
      <td>${esc(r.model)}</td>
      <td class="num">${fmtTokens(r.target_tokens)}</td>
      <td>${providerManaged() ? '<span class="dim">provider controlled</span>' : r.mtp ? '<span class="badge accent">MTP</span>' : '<span class="dim">off</span>'}</td>
      <td class="num">${fmtInt(m.prompt)}</td>
      <td class="num">${m.n}</td>`;
    // A test without a measurement uses its empty cells to say why.
    if (reason) return `${lead}<td class="fail-reason" colspan="13">${esc(reason)}</td></tr>`;
    return `${lead}
      <td class="num">${fmtInt(m.totalPrompt)}</td><td class="num">${fmtInt(m.totalOutput)}${star(m.totalOutput)}</td><td class="num">${fmt(m.wall, 2)}</td>
      <td class="num">${fmt(m.prefillTime, 2)}</td><td class="num">${fmt(m.decodeTime, 2)}${m.decodeTime!==null && m.decodeApprox?'*':''}</td>
      <td class="num">${fmt(m.prefill, 0)}</td>
      <td class="num">${fmt(m.decode, 1)}${m.decode!==null && (m.estimated || m.decodeApprox)?'*':''}</td>
      <td class="num">${fmt(m.perRequestDecode, 1)}${m.perRequestDecode!==null && (m.estimated || m.decodeApprox)?'*':''}</td>
      <td class="num">${fmt(m.outputTps, 1)}${star(m.outputTps)}</td>
      <td class="num">${fmt(m.ttft, 2)}</td>
      <td class="num"${cardTip("w", "W", (v) => fmt(v, 0))}>${fmt(m.watts, 0)}</td>
      <td class="num">${fmt(m.jPerTok, 2)}</td>
      <td class="num"${cardTip("vram", "MB", fmtInt)}>${fmtInt(m.vram)}</td>
    </tr>`;
  }).join("");
  $("#live-table").innerHTML = `<thead><tr>
    <th>Model</th><th class="num">Target</th><th>MTP</th><th class="num" title="Median prompt tokens per request">Actual tk</th><th class="num">Queries</th>
    <th class="num">Total prompt tk</th><th class="num">Total output tk</th><th class="num" data-tone="wall">Batch wall s</th>
    <th class="num" data-tone="prefill" title="Union of client request-start to first-output intervals; includes queueing and network">Prefill time s (TTFT proxy)</th><th class="num" data-tone="decode" title="Union of post-first-token decode intervals; excludes TTFT and gaps outside those intervals">Decode time s</th>
    <th class="num" data-tone="prefill" title="Prompt tokens divided by union request-start to first-output intervals; client TTFT proxy includes queueing and network">Prefill tk/s (TTFT proxy)</th>
    <th class="num" data-tone="decode" title="Scoped decode tokens divided by union post-first-token duration; TTFT excluded">Total decode tk/s</th>
    <th class="num" title="Median individual request rate after the first token">Decode / request tk/s</th>
    <th class="num" title="Total completed output tokens divided by batch wall time, including prefill and queueing, at every query count">End-to-end output tk/s</th>
    <th class="num" title="Median time to first token per stream">TTFT / request s</th>
    <th class="num" title="Average GPU power over the whole case, all cards combined">GPU W</th>
    <th class="num" title="Whole-case GPU energy (all cards, prefill included) ÷ output tokens">J/out tk</th>
    <th class="num" title="Peak simultaneous monitored GPU memory usage">VRAM MiB</th>
    </tr></thead><tbody>${rows}</tbody>`;
}

/* ---------------- results tab ---------------- */
async function loadRuns() {
  const {runs} = await api("/api/runs");
  state.runs = runs;
  for (const summary of runs) {
    const cached = state.runCache[summary.id];
    if (cached && ((cached.results || []).length !== summary.n_results || cached.status !== summary.status || (cached.label || "") !== (summary.label || "") || (cached.notes || "") !== (summary.notes || "") || JSON.stringify(cached.hardware?.memory) !== JSON.stringify(summary.hardware?.memory))) delete state.runCache[summary.id];
  }
  state.compare = new Set([...state.compare].filter((id) => runs.some((run) => run.id === id)));
  if ((!state.selectionInitialized || (state.selected && !runs.some((run) => run.id === state.selected))) && runs.length) state.compare.add(runs[0].id);
  state.selectionInitialized = true;
  if (!state.compare.has(state.selected)) state.selected = [...state.compare][0] || null;
  renderRunsList();
  checkComparability();
  refreshMergeSuggestions();
  await renderResultsView();
  return runs;
}

function emptyState(title, body) {
  return `<div class="empty"><h3>${esc(title)}</h3><div>${body}</div></div>`;
}

function renderRunsList() {
  if (!state.runs.length) {
    $("#runs-list").innerHTML = '<p class="dim">Nothing cached yet.</p>';
    renderMergeBar();
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
          <span class="s">${esc((run.created_at || "").replace("T", " ").replace(/\+00:00$/, ""))}</span>
          <span class="s">${(run.models || []).length} model${(run.models || []).length === 1 ? "" : "s"} · ${run.n_results} test${run.n_results === 1 ? "" : "s"}</span>
          <span class="counts">
            ${run.community ? '<span class="badge accent badge-shared">Shared</span>' : ""}
            <span class="badge" title="Tests that completed, out of ${run.n_results} recorded">${run.n_ok} test${run.n_ok === 1 ? "" : "s"} passed</span>
            ${run.n_failed ? `<span class="badge">${run.n_failed} failed</span>` : ""}
            ${run.n_skipped ? `<span class="badge">${run.n_skipped} skipped</span>` : ""}
            ${run.n_cancelled ? `<span class="badge">${run.n_cancelled} cancelled</span>` : ""}
            ${(run.mtp_modes || []).includes("on") ? '<span class="badge accent">MTP</span>' : ""}
            ${run.status === "running" ? '<span class="badge badge-live">Live</span>' : run.status !== "completed" ? `<span class="badge">${esc(run.status)}</span>` : ""}
          </span>
        </span>
      </button><button class="run-delete" data-delete="${esc(run.id)}" title="${run.status === "running" ? "This benchmark is still running" : run.community ? "Hide this shared benchmark in this browser" : "Delete this benchmark from this computer"}" ${run.status === "running" ? "disabled" : ""} aria-label="Delete ${esc(run.label || run.id)}"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M3 5h14M8 3h4m-7 2 1 12h8l1-12M8 8v6m4-6v6"/></svg><span>Delete</span></button></div>`).join("")}`).join("");
  $$("#runs-list .run").forEach((btn) => btn.addEventListener("click", () => selectRun(btn.dataset.id)));
  $$("#runs-list [data-run-select]").forEach((input) => input.addEventListener("change", async () => {
    if (state.conditions !== null && input.checked) {
      await ensureRuns([input.dataset.runSelect]);
      availableMeasurementRuns([state.runCache[input.dataset.runSelect]]).filter(run=>!run.storage_alternative && !run.storage_baseline).forEach(run=>state.conditions.add(run.id));
    }
    if (input.checked) state.compare.add(input.dataset.runSelect); else state.compare.delete(input.dataset.runSelect);
    if (!state.compare.has(state.selected)) state.selected = [...state.compare][0] || null;
    renderRunsList(); await renderResultsView();
  }));
  $$("#runs-list [data-delete]").forEach((btn) => btn.addEventListener("click", () => deleteRun(state.runs.find((run) => run.id === btn.dataset.delete))));
  renderMergeBar();
}

async function deleteRun(run) {
  if (!run || !confirm(browserMode() ? (run.community ? `Hide shared benchmark "${run.label || run.id}" in this browser? The shared file will remain published.` : `Delete benchmark "${run.label || run.id}" from this browser?`) : `Delete benchmark "${run.label || run.id}" from this computer? A copy will remain in the local archive.`)) return;
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
  if (state.compare.size !== 1 || !state.compare.has(id)) { state.conditions = null; state.visibleModels = null; }
  state.selected = id;
  state.compare = new Set([id]);
  renderRunsList();
  await renderResultsView();
}

/* ---------------- merging benchmarks ----------------
 * Benchmarks measured on one machine can be stored as one benchmark, e.g. an
 * LM Studio run and a Strata run. The helper decides what is possible and says
 * why in plain words (/api/bench/merge-plan); this page only shows that plan,
 * collects the two things a person must decide (which benchmarks, and whether
 * an endpoint was served by this machine) and sends the merge. */
const MERGE_DISMISSED_STORAGE = "wattever-merge-dismissed";
function mergeAvailable() { return !browserMode() && !!state.env?.capabilities?.merge; }
function dismissedMergeSuggestions() {
  try { return new Set(JSON.parse(localStorage.getItem(MERGE_DISMISSED_STORAGE) || "[]")); } catch (error) { return new Set(); }
}
/* One suggestion at a time: the one about what is ticked, else the most recent. */
function currentMergeSuggestion() {
  const dismissed = dismissedMergeSuggestions(), known = new Set(state.runs.map(run => run.id));
  const open = (state.mergeSuggestions || []).filter(item => !dismissed.has(item.key) && item.run_ids.filter(id => known.has(id)).length > 1);
  return open.find(item => item.run_ids.some(id => state.compare.has(id))) || open[0] || null;
}
async function refreshMergeSuggestions() {
  if (!mergeAvailable()) return;
  try { state.mergeSuggestions = (await api("/api/bench/merge-suggestions")).suggestions || []; }
  catch (error) { state.mergeSuggestions = []; }
  renderMergeBar();
}
function renderMergeBar() {
  const bar = $("#merge-bar");
  if (!bar) return;
  const ticked = mergeAvailable() ? [...state.compare].filter(id => state.runs.some(run => run.id === id)) : [];
  const suggestion = mergeAvailable() && ticked.length < 2 ? currentMergeSuggestion() : null;
  bar.hidden = ticked.length < 2 && !suggestion;
  if (bar.hidden) { bar.innerHTML = ""; return; }
  bar.innerHTML = ticked.length > 1
    ? `<div class="merge-selection"><span><b>${ticked.length}</b> ticked</span><button type="button" class="btn-sm merge-action" id="merge-ticked" title="Check whether the ticked benchmarks ran on one machine, then merge them into one">Merge into one…</button></div>`
    : `<div class="merge-suggestion" role="note"><span class="eyebrow">SAME MACHINE</span><p><b>${esc(suggestion.engines.join(" + "))}</b> ran on ${esc(suggestion.machine)}.</p><div class="btn-row"><button type="button" class="btn-sm merge-action" id="merge-suggested" title="Review these benchmarks before anything changes">Merge ${suggestion.run_ids.length} benchmarks…</button><button type="button" class="btn-sm" id="merge-dismiss" title="Hide this suggestion">Not now</button></div></div>`;
  $("#merge-ticked")?.addEventListener("click", () => openMergeDialog(ticked));
  $("#merge-suggested")?.addEventListener("click", () => openMergeDialog(suggestion.run_ids));
  $("#merge-dismiss")?.addEventListener("click", () => {
    try { localStorage.setItem(MERGE_DISMISSED_STORAGE, JSON.stringify([...dismissedMergeSuggestions(), suggestion.key].slice(-50))); } catch (error) { /* a suggestion is only a convenience */ }
    state.mergeSuggestions = state.mergeSuggestions.filter(item => item.key !== suggestion.key);
    renderMergeBar();
  });
}

async function openMergeDialog(ids) {
  if (!mergeAvailable()) return;
  state.merge = {ids: new Set(ids), assumed: new Set(), left: new Map(), label: "", labelEdited: false, plan: null, error: "", busy: false, revision: 0};
  $("#merge-content").innerHTML = '<p class="hint">Checking these benchmarks…</p>';
  $("#merge-start").disabled = true; $("#merge-start").textContent = "Merge";
  $("#merge-dialog").showModal();
  await refreshMergePlan();
}
async function refreshMergePlan() {
  const merge = state.merge;
  if (!merge) return;
  const revision = ++merge.revision;
  try {
    const plan = merge.ids.size ? await api("/api/bench/merge-plan", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({run_ids: [...merge.ids], assume_local: [...merge.assumed].filter(id => merge.ids.has(id))})}) : null;
    if (state.merge !== merge || merge.revision !== revision) return;
    merge.plan = plan; merge.error = "";
    if (plan && !merge.labelEdited) merge.label = plan.label;
  } catch (error) {
    if (state.merge !== merge || merge.revision !== revision) return;
    merge.plan = null; merge.error = error.message;
  }
  renderMergeDialog();
}
function mergeRowMarkup(row, picked) {
  const blocks = picked ? (row.issues || []).filter(issue => issue.level === "block") : [];
  // An endpoint benchmark is attributed to this machine only on the operator's word.
  const confirm = picked && !blocks.length && row.attribution === "client";
  const tests = `${row.tests} test${row.tests === 1 ? "" : "s"} · ${row.models} model${row.models === 1 ? "" : "s"}`;
  return `<div class="merge-row" data-state="${blocks.length ? "block" : confirm && !row.confirmed ? "confirm" : "ok"}" data-picked="${picked}" style="--benchmark-color:${runColor(row.id)}">
    <label class="merge-pick"><input type="checkbox" data-merge-run="${esc(row.id)}" ${picked ? "checked" : ""}><span>${esc(row.label)}</span></label>
    <div class="merge-meta">${row.engines.map(engine => `<span class="badge">${esc(engine)}</span>`).join("")}<span>${tests}</span><span>${esc((row.created_at || "").slice(0, 10))}</span>${row.status && row.status !== "completed" ? `<span>${esc(row.status)}</span>` : ""}</div>
    ${blocks.map(issue => `<p class="merge-issue">${esc(issue.message)}</p>`).join("")}
    ${confirm ? `<label class="merge-confirm"><input type="checkbox" data-merge-confirm="${esc(row.id)}" ${row.confirmed ? "checked" : ""}><span><b>Inference ran on this machine</b><small>${row.loopback ? "The endpoint was reached at a local address, but the run was saved without saying where inference ran." : "The endpoint was reached over the network. Tick only if that address is this same computer."} Power was not recorded for it.</small></span></label>` : ""}
  </div>`;
}
function renderMergeDialog() {
  const merge = state.merge, content = $("#merge-content"), start = $("#merge-start");
  if (!merge || !content) return;
  const plan = merge.plan, rows = plan?.runs || [];
  const blocked = rows.filter(row => row.issues.some(issue => issue.level === "block"));
  const unconfirmed = rows.filter(row => !blocked.includes(row) && row.attribution === "client" && !row.confirmed);
  const warnings = (plan?.issues || []).filter(issue => issue.level === "warn");
  // Benchmarks that are not ticked stay folded until asked for, unless nothing else can be ticked.
  const folded = (id, title, items) => !items.length ? "" : `<details class="merge-more" id="${id}" ${($("#" + id) ? $("#" + id).open : rows.length < 2) ? "open" : ""}><summary>${title} · ${items.length} benchmark${items.length === 1 ? "" : "s"}</summary><div class="merge-list">${items.map(row => mergeRowMarkup(row, false)).join("")}</div></details>`;
  const next = merge.error ? "" : rows.length < 2 ? "Tick at least two benchmarks."
    : blocked.length ? `${blocked.length === 1 ? "One benchmark" : blocked.length + " benchmarks"} cannot join. <button type="button" class="btn-sm" id="merge-leave-out">Leave ${blocked.length === 1 ? "it" : "them"} out</button>`
    : unconfirmed.length ? `Confirm where inference ran for ${unconfirmed.length === 1 ? "the benchmark" : "the " + unconfirmed.length + " benchmarks"} marked above.${unconfirmed.length > 1 ? ' <button type="button" class="btn-sm" id="merge-confirm-all">All ran on this machine</button>' : ""}` : "";
  content.innerHTML = (merge.error ? banner("err", "Could not merge", esc(merge.error)) : "")
    + (plan?.machine ? `<p class="merge-machine"><span class="eyebrow">MACHINE</span>${esc(plan.machine)}</p>` : "")
    + (rows.length ? `<div class="merge-list">${rows.map(row => mergeRowMarkup(row, true)).join("")}</div>` : "")
    + folded("merge-more", "Also on this machine", (plan?.also_here || []).filter(row => !merge.left.has(row.id)))
    + folded("merge-left", "Left out", [...merge.left.values()])
    + `<div class="field"><label for="merge-label">Name of the merged benchmark</label><input type="text" id="merge-label" maxlength="160" value="${esc(merge.label)}"></div>`
    + (plan?.ok
      ? `<div class="merge-outcome" data-ready="true"><b>One benchmark</b> · ${esc(plan.engines.map(engine => engine.label).join(" + "))} · ${plan.models} model${plan.models === 1 ? "" : "s"} · ${plan.tests} test${plan.tests === 1 ? "" : "s"}`
        + (plan.superseded ? `<p>${plan.superseded} test${plan.superseded === 1 ? " was" : "s were"} measured more than once. The completed, most recent measurement is shown; the other${plan.superseded === 1 ? " stays" : "s stay"} in the benchmark's history.</p>` : "")
        + warnings.map(issue => `<p>${esc(issue.message)}</p>`).join("")
        + `<p>The originals move to the local archive. You can undo the merge afterwards.</p></div>`
      : next ? `<div class="merge-outcome" data-ready="false">${next}</div>` : "");
  start.disabled = !plan?.ok || merge.busy;
  start.textContent = merge.busy ? "Merging…" : plan?.ok ? `Merge ${rows.length} benchmarks` : "Merge";
}
/* Ticking, confirming and leaving out all end the same way: ask the helper again. */
function mergeDialogChanged(event) {
  const merge = state.merge, target = event.target;
  if (!merge || merge.busy) return;
  if (target.id === "merge-label") { if (event.type === "input") { merge.label = target.value; merge.labelEdited = true; } return; }
  if (event.type !== "change" && !["merge-leave-out", "merge-confirm-all"].includes(target.id)) return;
  const known = [...(merge.plan?.runs || []), ...(merge.plan?.also_here || [])];
  const leave = id => { merge.ids.delete(id); merge.assumed.delete(id); const row = known.find(item => item.id === id); if (row) merge.left.set(id, {...row, issues: [], confirmed: false}); };
  if (target.id === "merge-leave-out") (merge.plan?.runs || []).filter(row => row.issues.some(issue => issue.level === "block")).forEach(row => leave(row.id));
  else if (target.id === "merge-confirm-all") (merge.plan?.runs || []).filter(row => row.attribution === "client").forEach(row => merge.assumed.add(row.id));
  else if (target.dataset.mergeRun) { if (target.checked) { merge.ids.add(target.dataset.mergeRun); merge.left.delete(target.dataset.mergeRun); } else leave(target.dataset.mergeRun); }
  else if (target.dataset.mergeConfirm) { if (target.checked) merge.assumed.add(target.dataset.mergeConfirm); else merge.assumed.delete(target.dataset.mergeConfirm); }
  else return;
  refreshMergePlan();
}
async function startMerge() {
  const merge = state.merge, plan = merge?.plan;
  if (!plan?.ok || merge.busy) return;
  merge.busy = true; renderMergeDialog();
  try {
    const merged = await api("/api/bench/consolidate", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({run_ids: plan.run_ids, label: merge.label.trim() || plan.label,
        assume_local: plan.runs.filter(row => row.confirmed).map(row => row.id), overlap: "newest"})});
    $("#merge-dialog").close(); state.merge = null;
    await showMergeResult(plan.run_ids, [merged.id]);
  } catch (error) {
    merge.busy = false; merge.error = error.message; renderMergeDialog();
  }
}
/* After a merge or its undo the library changed under the page: forget what was cached and open the result. */
async function showMergeResult(changed, open) {
  changed.forEach(id => delete state.runCache[id]);
  state.conditions = null; state.visibleModels = null; state.detailSignature = null;
  state.compare = new Set(open); state.selected = open[0] || null;
  await loadRuns();
  $$("#runs-list .run").find(node => node.dataset.id === state.selected)?.scrollIntoView?.({block: "nearest"});
}
function mergeOriginMarkup(run) {
  const merge = mergeAvailable() ? state.runs.find(item => item.id === run.id)?.merge : null;
  if (!merge) return "";
  return `<p class="hint merge-origin">Merged from ${merge.sources} benchmarks${merge.at ? " on " + esc(String(merge.at).slice(0, 10)) : ""}.${merge.undo ? ` <button type="button" class="btn-sm" id="undo-merge" title="Bring back the original benchmarks exactly as they were" ${state.benchmarkRunning ? "disabled" : ""}>Undo merge</button>` : ""}</p>`;
}
async function undoMerge(run) {
  if (!confirm(`Undo the merge of "${run.label || run.id}"? The original benchmarks come back exactly as they were.`)) return;
  try {
    const {restored} = await api("/api/bench/merge-undo", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({run_id: run.id})});
    await showMergeResult([run.id, ...restored], restored);
  } catch (error) { alert("Could not undo the merge: " + error.message); }
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
    const engine = (run.engine_groups || []).find(group => group.id === session.engine_group_id)?.label || cfg.reproducibility?.engine || cfg.provider || "LM Studio";
    const modes = cfg.model_modes ? [...new Set(Object.values(cfg.model_modes).flat())] : (cfg.mtp_modes || []);
    const clocks = session.hardware?.memory?.configured_clock_mhz_reported || [];
    const conditions = (clocks.length || session.storage_condition ? `<p class="hint">${clocks.length ? "RAM firmware clock reports: " + clocks.map(esc).join(", ") + " MHz. " : ""}${session.storage_condition ? "Storage condition: " + esc(session.storage_condition) + "." : ""}</p>` : "") + (session.transfer_bandwidth ? transferBandwidthMarkup(session.transfer_bandwidth) : "") + (session.runtime_observations?.length ? `<details class="adv"><summary>Observed runtime configuration</summary><pre>${esc(JSON.stringify(session.runtime_observations, null, 2))}</pre></details>` : "");

    return `<p class="hint"><b>Session ${index + 1} · ${esc(engine)}</b>${session.started_at ? ` / ${esc(session.started_at)}` : ""} / ${modelCount} models / ${esc(cfg.provider === "openai" ? "provider controlled technique; reported MTP " + (cfg.reported_mtp || "unknown") + (session.measurement_scope?.mtp_pass ? "; MTP pass requested but not run, the endpoint decides MTP itself" : "") : modes.join(" + ") || "unknown technique")} / planner ${esc(cfg.context_planner_version ?? "legacy")} / ${esc(cfg.context_policy || "per_test")} / reserve ${esc(cfg.context_headroom ?? 512)} tokens + ${esc(100 * (cfg.context_safety_fraction ?? 0))}%</p>${conditions}`;
  }).join("")}</div></details>`;
}

function sharedDisplayRun(run) {
  if (!run.community) return run;
  const sensitive = /^(?:error|message|recovery_error|error_message|log|logs|traceback|stdout|stderr|interrupted_note)$/i;
  const localPath = /(?:[a-z]:[\\/]|\\\\|file:\/\/|\/(?:Users|home|tmp|var|mnt|private|Volumes)\/|WinError\s*\d+)/i;
  function clean(value) {
    if (typeof value === "string") return localPath.test(value) ? "" : value;
    if (Array.isArray(value)) return value.map(clean);
    if (!value || typeof value !== "object") return value;
    return Object.fromEntries(Object.entries(value).filter(([key]) => !sensitive.test(key)).map(([key, item]) => [key, clean(item)]));
  }
  const copy = clean(run);
  copy.shared_partial = run.status === "error" || !!run.error || Number(run.remaining_tests) > 0 || run.source_status === "running";
  return copy;
}
function benchmarkIncompleteNotice(run) {
  const scope = run.experiment_scope || {};
  const remaining = Math.max(Number(run.remaining_tests) || 0, (Number(scope.planned_cases) || 0) - (Number(scope.recorded_cases) || 0));
  const scopePartial = scope.partial === true || String(scope.status || "").startsWith("partial") || (Number(scope.planned_cases) || 0) > (Number(scope.recorded_cases) || 0);
  const partial = scopePartial || remaining > 0;
  if (run.community) return run.shared_partial || partial || run.error || run.source_status === "running"
    ? `<div class="banner warn"><div><b>Partial shared benchmark</b>${remaining > 0 ? ` - ${esc(remaining)} unrecorded cases` : ""}</div></div>` : "";
  if (run.status === "running" || !(partial || run.error || run.source_status === "running")) return "";
  const error = String(run.error || "");
  const reason = error ? (/WinError 5|PermissionError|Permission denied|Access is denied/i.test(error)
    ? "A file access error stopped this benchmark." : "The benchmark stopped before finishing.") : "";
  const pendingModels = [...new Set((scope.pending_stages || []).map(stage=>stage.model).filter(Boolean))];
  return `<div class="banner warn"><div><b>${run.source_status === "running" ? "Shared checkpoint" : "Benchmark incomplete"}</b>${reason ? ` - ${reason}` : ""}${remaining > 0 ? scopePartial ? ` ${esc(remaining)} planned tests not recorded.` : ` ${esc(remaining)} tests remaining.` : ""}${pendingModels.length ? ` Pending: ${pendingModels.map(esc).join(", ")}.` : ""} ${browserMode() ? 'Download the JSON and use the desktop app to continue where supported.' : 'Use Add / rerun tests to continue where supported.'}${error ? `<details class="benchmark-error-details"><summary>Technical details</summary><pre>${esc(error)}</pre></details>` : ""}</div></div>`;
}

/* One readable line of what a benchmark covers. */
function coverageLine(run) {
  const rows = run.results || [], count = status => rows.filter(row => row.status === status).length;
  const scope = resultLevels([run]), models = allModelsOf([run]).length;
  const span = (values, format) => values.length > 1 ? `${format(values[0])} to ${format(values[values.length - 1])}` : values.length ? format(values[0]) : "";
  const outcome = [`${count("ok")} passed`, count("failed") ? `${count("failed")} failed` : "", count("skipped") ? `${count("skipped")} skipped` : "", count("cancelled") ? `${count("cancelled")} cancelled` : ""].filter(Boolean).join(", ");
  return [`${models} model${models === 1 ? "" : "s"}`,
    scope.prompts.length ? `prompts of ${span(scope.prompts, fmtTokens)} tokens` : "",
    scope.queries.length ? `${span(scope.queries, String)} concurrent quer${scope.queries.length === 1 && scope.queries[0] === 1 ? "y" : "ies"}` : "",
    `${rows.length} test${rows.length === 1 ? "" : "s"}: ${outcome}`].filter(Boolean).join(" · ");
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
  const measurementNotice = measurementModes.size > 1 ? notesStrip("selection", [{kind: "modes", tone: "warn", gist: "Different measurement modes", html: '<p class="measurement-notice">These benchmarks use different measurement modes. Browser timings and desktop helper measurements may not be directly comparable.</p>'}]) : selectedRuns.length ? `<p class="hint measurement-notice">Measurement mode: ${measurementModes.has("browser") ? "Browser timing" : "Desktop helper"}</p>` : "";
  const allocationNotice = allocationKeys.size > 1 ? '<p class="hint">Different context allocation settings: these benchmarks reserve different amounts of memory, which can affect speed. Check benchmark settings before comparing.</p>' : "";
  const liveNotice = active.length ? `<p class="live-result-notice" role="status"><span class="badge badge-live">Live</span> ${active.reduce((count, item) => count + (item.results || []).length, 0)} completed tests in the ongoing benchmark${active.length > 1 ? "s" : ""}. Results update as each test finishes.</p>` : "";
  if (state.compare.size > 1) {
    $("#run-detail").innerHTML = `<div class="card selection-heading"><header><h3>Comparing ${state.compare.size} benchmarks</h3></header>${liveNotice}${measurementNotice}${allocationNotice}<div class="ov-systems">${selectedRuns.map(item => `<div class="ov-system" style="--benchmark-color:${runColor(item.id)}"><div class="ov-system-name"><strong>${esc(item.label || item.id)}</strong><small>${esc(coverageLine(item))}</small></div>${hardwareBadgesMarkup(item)}${engineBadgesMarkup(item)}</div>`).join("")}</div></div>`;
    return;
  }
  const sessionAllocationNotice = new Set(allocationConfigs(run).map(allocationKey)).size > 1
    ? plainNote("allocation", "info", "Sessions used different context allocation settings", "Sessions in this benchmark used different context allocation settings. Memory use and speed may differ; see session settings below.") : null;
  const sourceNotice = run.community ? `<p class="hint">Data source: <a target="_blank" rel="noopener noreferrer" href="https://github.com/lpalbou/Wattever/blob/main/results/runs/${encodeURIComponent(run.id)}.json">GitHub</a></p>` : "";
  const gpu = run.gpu || {}, host = run.host || {}, cfg = run.config || {};
  const testAction = resultsTestAction(run);
  const ok = (run.results || []).filter((row) => row.status === "ok");
  const failureNotice = (run.results || []).length && !ok.length
    ? plainNote("failed", "warn", "No test in this benchmark completed", "No test in this benchmark completed. Open Tables & CSV, then All measurements, for the failure details.") : null;
  $("#run-detail").innerHTML = `<div class="card selection-heading"><header><h3>${esc(run.label || run.id)}</h3>${testAction ? `<button class="btn-sm" id="extend-run" ${state.benchmarkRunning || run.status === "running" || !testAction.ready ? "disabled" : ""} title="${esc(testAction.title)}">${esc(testAction.label)}</button>` : ""}<button class="btn-sm" id="rename-run" title="${run.community ? "Shared benchmarks are read-only; download and import a copy to edit" : "Edit this benchmark name and notes"}" ${run.status === "running" || run.community ? "disabled" : ""}>Rename</button><a class="btn-sm" href="/api/runs/${encodeURIComponent(run.id)}/download" title="Download this benchmark's JSON file">JSON</a><button class="btn-sm btn-danger" id="delete-run" title="${run.community ? "Hide this shared benchmark in this browser" : "Delete this benchmark from this computer"}" ${run.status === "running" ? "disabled" : ""}>${run.community ? "Hide" : "Delete"}</button></header>${hardwareBadgesMarkup(run)}${engineBadgesMarkup(run)}${bandwidthBadgesMarkup(displayBandwidthProfile(run))}${mergeOriginMarkup(run)}${liveNotice}${sourceNotice}${measurementNotice}${notesStrip("detail", [failureNotice, sessionAllocationNotice, incompleteNote(run), ...unservedNotes(run.results)])}<p class="ov-coverage">${esc(coverageLine(run))}</p>${storageExperimentSummary(run)}<details class="adv"><summary>Machine, settings &amp; notes</summary><div><p class="hint">${esc(gpu.name || "CPU")} / ${esc(host.cpu || "")} / ${esc(run.status)} / ${esc(run.created_at || "")}</p>${run.hardware ? hardwareMarkup(run.hardware) : ""}${scientificMetadata(run)}<p class="saved-notes">${esc(run.notes || "")}</p><p class="hint">Output ${esc(cfg.max_tokens)} tokens / reasoning ${cfg.disable_thinking == null ? "provider controlled" : cfg.disable_thinking === false ? "on" : "off"} / placement ${esc(cfg.gpu_offload === "provider_managed" ? "provider controlled" : cfg.gpu_offload || "auto")} / ${cfg.context_policy === "per_session" ? "context, placement and techniques recorded per engine/session" : cfg.context_policy === "provider_managed" ? "context and speculative decoding provider controlled" : `context allocation ${cfg.context_policy === "fixed" ? "one fixed load per model" : "matched to each test"} / context reserve ${esc(cfg.context_headroom ?? 512)} tokens + ${esc(100 * (cfg.context_safety_fraction ?? 0))}% / planner ${esc(cfg.context_planner_version ?? "legacy")}`}</p>${run.interrupted_note ? `<p class="hint">${esc(run.interrupted_note)}</p>` : ""}${sessionSettings(run)}</div></details></div>`;
  $("#extend-run")?.addEventListener("click", () => openExtendDialog(run));
  bindTelemetry($("#run-detail"),run);
  $("#delete-run").addEventListener("click", () => deleteRun(run));
  $("#undo-merge")?.addEventListener("click", () => undoMerge(run));
  if (run.comparison_group?.run_ids?.length > 1) {
    const ids = run.comparison_group.run_ids.filter(id => state.runs.some(item => item.id === id));
    if (ids.length > 1) {
      $("#run-detail .selection-heading header").insertAdjacentHTML("beforeend", `<button class="btn-sm" id="compare-machine">Open ${esc(run.comparison_group.label || "machine benchmark")} (${ids.length} runs)</button>`);
      $("#compare-machine").addEventListener("click", async () => {
        state.compare = new Set(ids);
        renderRunsList();
        await renderResultsView();
      });
    }
  }
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

function resultsConnectionKey() {
  return browserMode() ? JSON.stringify(["browser", state.detectedEndpoint?.id, !!state.env?.lms?.server]) : JSON.stringify([helperConnectionSignature(), resultsConnectionReady()]);
}
function resultsConnectionReady() {
  return !state.providerNeedsConnection && (!genericHelper() || state.endpointSignature === helperConnectionSignature()) && (!browserMode() || !!state.env?.lms?.server);
}
function resultsTestAction(run) {
  if (run.community && !browserMode()) return null;
  const newBenchmark = browserMode();
  if (!newBenchmark && !state.env?.capabilities?.extend) return null;
  const ready = resultsConnectionReady();
  return {newBenchmark, ready, label: newBenchmark ? "New benchmark on connected endpoint" : "Add / rerun tests",
    title: !ready ? "Connect the endpoint in Run first" : newBenchmark ? "Fetch models from the current connection and review a new benchmark; saved results stay unchanged" : "Add or rerun tests in this benchmark"};
}
function updateExtendAvailability() {
  updateModelRerunAvailability();
  const button = $("#extend-run");
  if (button) {
    const run = state.runCache[state.selected] || {}, action = resultsTestAction(run);
    button.disabled = state.benchmarkRunning || !action?.ready;
    button.title = state.benchmarkRunning ? "Wait for the active benchmark to finish" : action?.title || "Reconnect before selecting tests";
    if (action) button.textContent = action.label;
  }
  const start = $("#extend-start");
  if (start && state.benchmarkRunning) start.disabled = true;
}

async function resultsConnectedCatalog(connection) {
  let found;
  if (browserMode()) found = await window.WatteverBrowser.connect();
  else if (connection.provider === "openai") found = await api("/api/endpoint/check", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(connection)});
  else found = await api("/api/models");
  let models = (Array.isArray(found) ? found : found.models || found.data || []).filter(model => model && typeof model.id === "string" && model.id && model.type !== "embeddings");
  let warning = found.error || "";
  if (!models.length && !browserMode() && connection.provider === "openai") {
    const manual = $("#helper-model")?.value.trim();
    if (manual) { models = [{id:manual, type:"llm", state:"manual model ID; discovery unavailable"}]; warning = "Model discovery is unavailable. This is the manually configured model ID, not a verified served-model listing."; }
  }
  if (!models.length && warning) throw new Error(warning);
  const unique = new Map();
  for (const model of models) if (!unique.has(model.id)) unique.set(model.id, typeof window !== "undefined" && window.WatteverModelMetadata ? window.WatteverModelMetadata.normalize(model) : {...model});
  return {models:[...unique.values()], warning, label:found.endpoint_identity?.label || found.provider?.label || connection.endpoint_label || "Connected endpoint"};
}
function renderResultsCatalog() {
  const extension = state.extension;
  $("#extend-content").innerHTML = `<p class="hint">Models from <b>${esc(extension.catalogLabel)}</b>. Choose models, then review the workload in Run. This creates a new benchmark; the selected historical result stays unchanged.</p>${extension.warning ? banner("warn", "Model discovery", esc(extension.warning)) : ""}<div class="extend-models">${extension.models.map((model,index) => `<label class="model" title="Use ${esc(model.id)} in a new benchmark"><input type="checkbox" data-catalog-model="${index}" ${extension.selectedModels.has(model.id)?"checked":""}><span class="name">${esc(model.id)}</span><span class="meta"><span class="badge" title="${model.quantization_source === "model_identifier" ? "Quantization inferred from the model ID" : "Quantization reported by the endpoint, when available"}">${esc(quantizationLabel(model))}</span><span class="badge">${model.max_context_length ? "ctx " + fmtTokens(model.max_context_length) : "Context unknown"}</span>${model.loaded_context_length ? `<span class="badge">loaded ctx ${esc(fmtTokens(model.loaded_context_length))}</span>` : ""}</span></label>`).join("") || '<p class="hint">No served language models were discovered on the current connection.</p>'}</div><p class="hint">Model loading, context, placement and MTP remain controlled by the selected provider.</p>`;
  $("#extend-preview").textContent = "Review a new benchmark; no inference starts from this dialog.";
  $("#extend-start").textContent = "Review new benchmark";
  $("#extend-start").disabled = !extension.selectedModels.size;
  $$("#extend-content input[data-catalog-model]").forEach(input => input.addEventListener("change", () => {
    const id = extension.models[Number(input.dataset.catalogModel)].id;
    if (input.checked) extension.selectedModels.add(id); else extension.selectedModels.delete(id);
    $("#extend-start").disabled = !extension.selectedModels.size || state.benchmarkRunning;
  }));
}

function selectedModelRerun() {
  if (state.compare.size !== 1) return null;
  const run = state.runCache[[...state.compare][0]];
  if (!run || run.community) return null;
  if (measurementRuns([run]).some(view=>view.storage_alternative || view.storage_baseline)) return null;
  const action = resultsTestAction(run), models = shownModels(allModelsOf([run]));
  if (!action || action.newBenchmark || models.length !== 1) return null;
  const model = models[0], group = executionGroup(run, (run.results || []).find(row => row.model === model) || {});
  const provider = group?.config?.provider || run.config?.provider || "lmstudio";
  const connected = helperEndpointConfig();
  const matches = provider === connected.provider && (provider !== "openai" || !(group?.config || run.config)?.endpoint_id || (group?.config || run.config).endpoint_id === connected.endpoint_id);
  return {run, model, ready: matches && action.ready && !state.benchmarkRunning && run.status !== "running"};
}

function selectedModelRemoval() {
  if (browserMode() || !state.env?.capabilities?.extend || state.compare.size !== 1) return null;
  const run = state.runCache[[...state.compare][0]];
  if (!run || run.community) return null;
  const models = shownModels(allModelsOf([run]));
  if (models.length !== 1) return null;
  return {run, model: models[0], ready: !state.benchmarkRunning && run.status !== "running" && allModelsOf([run]).length > 1};
}

async function removeSelectedModel() {
  const target = selectedModelRemoval();
  if (!target?.ready) return;
  const count = (target.run.results || []).filter(row => row.model === target.model).length;
  if (!confirm(`Remove ${target.model} and its ${count} measurements from ${target.run.label || target.run.id}? A backup will be kept. Other models remain in this benchmark.`)) return;
  try {
    await api(`/api/runs/${encodeURIComponent(target.run.id)}/models/delete`, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({models:[target.model]})});
    delete state.runCache[target.run.id]; state.visibleModels = null;
    await loadRuns();
    await selectRun(target.run.id);
  } catch (error) { alert(error.message); }
}

function updateModelRerunAvailability() {
  const button = $("#rerun-model");
  if (!button) return;
  const target = selectedModelRerun();
  button.hidden = browserMode() || !state.env?.capabilities?.extend || !target;
  button.disabled = !target?.ready;
  button.title = state.benchmarkRunning || target?.run.status === "running"
    ? "Wait for the active benchmark to finish"
    : target ? `Review a rerun of ${target.model} using the saved workload settings`
    : "Select one benchmark and one model to rerun";
  const remove = $("#remove-model"), removal = selectedModelRemoval();
  if (remove) {
    remove.hidden = browserMode() || !state.env?.capabilities?.extend || !removal;
    remove.disabled = !removal?.ready;
    remove.title = removal ? allModelsOf([removal.run]).length === 1 ? "Use Delete benchmark to remove its last model" : `Remove ${removal.model} and its measurements; keep a backup` : "Select one local benchmark and one model to remove";
  }
}

async function openExtendDialog(run, modelId = null, selectedCase = null) {
  if (state.benchmarkRunning) return;
  const dialog = $("#extend-dialog");
  const action = resultsTestAction(run), connectionKey = resultsConnectionKey();
  const pending = {};
  state.extension = pending;
  $("#extend-content").innerHTML = '<p class="hint">Fetching models from the current connection...</p>';
  $("#extend-title").textContent = action?.newBenchmark ? "New benchmark on connected endpoint" : modelId ? "Rerun selected model" : "Add or rerun tests";
  $("#extend-preview").textContent = "";
  $("#extend-start").disabled = true;
  $("#extend-start").textContent = "Add tests";
  dialog.showModal();
  try {
    if (!action?.ready) throw new Error("Connect the endpoint in Run before selecting new tests.");
    if (action.newBenchmark) {
      const connection = helperEndpointConfig(), catalog = await resultsConnectedCatalog(connection);
      if (!dialog.open || state.extension !== pending) return;
      if (resultsConnectionKey() !== connectionKey || !resultsConnectionReady()) throw new Error("The endpoint connection changed. Close this dialog and select tests again.");
      const selectedModels = new Set(catalog.models.filter(model => state.models.has(model.id)).map(model => model.id));
      if (!selectedModels.size && catalog.models.length === 1) selectedModels.add(catalog.models[0].id);
      state.extension = {mode:"new", connectionKey, models:catalog.models, selectedModels, catalogLabel:catalog.label, warning:catalog.warning};
      renderResultsCatalog();
      return;
    }
    const [plan, source] = await Promise.all([
      genericHelper() ? api("/api/bench/extend-options", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({run_id:run.id, connection:helperEndpointConfig()})}) : api("/api/bench/extend-plan?run_id=" + encodeURIComponent(run.id)),
      api("/api/runs/" + encodeURIComponent(run.id)),
    ]);
    if (!dialog.open || state.extension !== pending) return;
    if (resultsConnectionKey() !== connectionKey) throw new Error("The endpoint connection changed. Close this dialog and select tests again.");
    const cfg = plan.config || managedRunConfig(source), installed = (plan.models || []).filter(m => m.type !== "embeddings");
    const existing = cfg.models || [];
    const models = [...existing.map(id => installed.find(model => model.id === id) || {id, unavailable: true}),
      ...installed.filter(model => !existing.includes(model.id))];
    if (modelId && (!existing.includes(modelId) || !models.some(model => model.id === modelId && !model.unavailable))) {
      throw new Error("The selected model is no longer installed or belongs to another engine. Connect its engine and make that model available before rerunning its tests.");
    }
    state.extension = {runId: run.id, source, sourceConfig: cfg, models, connectionKey,
      connection: helperEndpointConfig(), engineGroupId: plan.engine_group_id, external: genericHelper(),
      storageDirectory: $("#storage-probe-directory")?.value.trim() || null, storageDirectories: storageProbeDirectories(),
      initialSizes: plan.sizes || cfg.sizes || [], initialConcurrency: plan.concurrency_levels || cfg.concurrency_levels || [1],
      repeats: Number(cfg.repeats) || 1, selectedCases: selectedCase ? [selectedCase] : null,
      timeout: Number(cfg.per_test_timeout_s) || 1800,
      allowFallback: cfg.fallback_on_failure ?? true,
      skipTimeouts: cfg.skip_after_timeout ?? false,
      modelModes: modelId ? {[modelId]: benchmarkTechniques(cfg, modelId)} : originalModelSelection(cfg, models),
      sizes: new Set(selectedCase ? [selectedCase[3]] : plan.sizes || []), concurrency: new Set(selectedCase ? [selectedCase[2]] : plan.concurrency_levels || [1]), action: modelId ? "rerun" : "add", revision: 0};
    if (selectedCase) state.extension.modelModes = {[selectedCase[0]]: [selectedCase[1] ? "on" : "off"]};
    if (state.extension.external) { state.extension.allowFallback = false; state.extension.modelModes = Object.fromEntries(Object.keys(state.extension.modelModes).map(id => [id,["off"]])); }
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
  const repeats = Math.max(1, Number(extension.repeats ?? cfg.repeats) || 1), cases = new Map();
  const rank = {ok: 4, failed: 3, skipped: 2, cancelled: 1};
  const selectedKeys = extension.selectedCases ? new Set(extension.selectedCases.map(item => JSON.stringify(item))) : null;
  for (const row of extension.source.results || []) {
    if (row.model !== model.id || !extension.sizes.has(row.target_tokens) || !extension.concurrency.has(row.concurrency || 1)) continue;
    const mode = row.mtp ? "on" : "off";
    if (!modes.includes(mode)) continue;
    for (const repeat of row.repeat == null ? Array.from({length: Math.min(repeats, Number(row.legacy_repeat_count ?? cfg.repeats) || 1)}, (_, i) => i) : [row.repeat]) {
      if (!Number.isInteger(repeat) || repeat < 0 || repeat >= repeats) continue;
      if (selectedKeys && !selectedKeys.has(JSON.stringify([row.model, !!row.mtp, row.concurrency || 1, row.target_tokens, repeat]))) continue;
      const key = [mode, row.target_tokens, row.concurrency || 1, repeat].join(":");
      if ((rank[row.status] || 0) > (rank[cases.get(key)] || 0)) cases.set(key, row.status);
    }
  }
  const counts = {ok: 0, failed: 0, skipped: 0, unfinished: 0,
    total: selectedKeys ? extension.selectedCases.filter(item => item[0] === model.id).length : modes.length * extension.sizes.size * extension.concurrency.size * repeats};
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
    ...(extension.storageDirectory ? {storage_probe_directory:extension.storageDirectory} : {}),
    ...(extension.storageDirectories?.length ? {storage_probe_directories:extension.storageDirectories} : {}),
    ...(extension.external && extension.connection ? {connection:extension.connection} : {}),
    ...(extension.engineGroupId ? {engine_group_id:extension.engineGroupId} : {}),
    ...(extension.repeats == null ? {} : {repeats: extension.repeats}),
    ...(extension.timeout == null ? {} : {per_test_timeout_s: extension.timeout}),
    ...(extension.allowFallback == null ? {} : {fallback_on_failure: extension.allowFallback}),
    ...(extension.skipTimeouts == null ? {} : {skip_after_timeout: extension.skipTimeouts}),
    ...(extension.selectedCases ? {selected_cases: extension.selectedCases} : {}),
    sizes: [...extension.sizes].sort((a, b) => a - b),
    concurrency_levels: [...extension.concurrency].sort((a, b) => a - b), retry_failed: extension.action === "retry" || extension.action === "rerun", rerun_existing: extension.action === "rerun"};
}

function renderExtendForm() {
  const extension = state.extension;
  const sizes = [...new Set([...(state.env.size_presets || []), ...extension.sizes])].sort((a,b) => a-b);
  const levels = [...new Set([...CONCURRENCY_PRESETS, ...extension.concurrency])].sort((a,b) => a-b);
  $("#extend-content").innerHTML = `<div class="field"><label>Models and techniques</label><p class="hint">Choose the models and techniques to run. Add missing tests continues unfinished work. Rerun selected tests also repeats successful measurements.</p><p class="extend-continuity" role="status">Successful tests will not run again unless you choose Rerun selected tests.</p><div class="btn-row"><button type="button" class="btn-sm" id="extend-select-existing" title="Restore the benchmark models and techniques; saved measurements remain untouched">Restore benchmark selection</button><button type="button" class="btn-sm" id="extend-clear-selection" title="Clear which models will run; saved results are retained">Clear selection</button></div><p class="hint extend-progress-caption">Progress below covers saved techniques and selected additions at the prompt sizes and query counts shown below. Unchecking a model keeps its results.</p><div class="extend-models">${extension.models.map((m, i) => `<div class="extend-model" data-progress-model="${i}"><div class="extend-model-info"><strong>${esc(m.id)}</strong><small>${esc(quantizationLabel(m))}</small>${extensionProgressMarkup(extension, m)}</div><label title="Standard decoding without MTP"><input type="checkbox" data-model="${i}" data-mode="off" ${(extension.modelModes[m.id] || []).includes("off") ? "checked" : ""} ${m.unavailable ? "disabled" : ""}> ${extension.external ? "Provider controlled" : "Standard"}</label><label ${extension.external ? "hidden" : ""} title="Speculative decoding; model and runtime support is not guaranteed"><input type="checkbox" data-model="${i}" data-mode="on" ${(extension.modelModes[m.id] || []).includes("on") ? "checked" : ""} ${m.unavailable ? "disabled" : ""}> MTP</label></div>`).join("") || '<p class="hint">No installed language models found.</p>'}</div></div><div class="field"><label>Prompt sizes</label><p class="hint">Select each prompt size to test.</p><div class="chips" id="extend-sizes"></div></div><div class="field"><label>Concurrent queries</label><p class="hint">Select each query count to test.</p><div class="chips" id="extend-concurrency"></div></div><fieldset class="extend-actions"><legend>Action</legend><label><input type="radio" name="extend-action" value="add" ${extension.action === "add" ? "checked" : ""}> Add missing tests</label><label><input type="radio" name="extend-action" value="retry" ${extension.action === "retry" ? "checked" : ""}> Retry failed tests <span class="hint">(includes missing and skipped)</span></label><label><input type="radio" name="extend-action" value="rerun" ${extension.action === "rerun" ? "checked" : ""}> Rerun selected tests <span class="hint">(includes successful, failed, skipped and missing)</span></label>${extension.action === "rerun" ? '<p class="banner warn">Selected measurements will be replaced; previous attempts are kept.</p>' : ""}</fieldset>`;
  $("#extend-content").insertAdjacentHTML("afterbegin", `<div class="field"><label for="extend-repeats">Total repeats per test</label><input id="extend-repeats" type="number" min="${Number(extension.sourceConfig.repeats) || 1}" max="100" value="${extension.repeats ?? extension.sourceConfig.repeats ?? 1}" ${extension.selectedCases ? "disabled" : ""}><p class="hint">Increase this to append repeats while keeping existing measurements. Repairing or rerunning a test keeps its previous attempt in history.</p>${extension.selectedCases ? `<p class="banner">Repairing one test only: ${esc(extension.selectedCases[0][0])}, ${fmtTokens(extension.selectedCases[0][3])} prompt tokens, ${extension.selectedCases[0][2]} queries, repeat ${extension.selectedCases[0][4]+1}.</p>` : ""}</div>`);
  const installedIds = new Set(extension.models.filter(model => !model.unavailable).map(model => model.id));
  $("#extend-content").insertAdjacentHTML("afterbegin", `<div class="field"><label for="extend-timeout">Timeout per test (seconds)</label><input id="extend-timeout" type="number" min="1" max="7200" value="${extension.timeout ?? 1800}"><p class="hint">Applies to these new attempts. Saved measurements keep their settings. Longer contexts can take much longer when GPU memory spills into RAM.</p></div>`);
  $("#extend-timeout").addEventListener("change", () => {
    extension.timeout = Number($("#extend-timeout").value);
    previewExtension();
  });
  $("#extend-content").insertAdjacentHTML("afterbegin", `<details class="adv"><summary>Failure policy</summary><label><input id="extend-fallback" type="checkbox" ${extension.external ? "disabled" : ""} ${!extension.external && extension.allowFallback ? "checked" : ""}> Allow CPU placement fallback after a model load failure</label><label><input id="extend-skip-timeouts" type="checkbox" ${extension.skipTimeouts ? "checked" : ""}> Skip remaining repeats and larger contexts after a timeout</label><p class="hint">A timeout marks a practical time limit, not proof of a hardware allocation failure. Skipped tests are identified separately.</p></details>`);
  $("#extend-fallback").addEventListener("change", () => { extension.allowFallback = $("#extend-fallback").checked; previewExtension(); });
  $("#extend-skip-timeouts").addEventListener("change", () => { extension.skipTimeouts = $("#extend-skip-timeouts").checked; previewExtension(); });
  const repairCases = [...new Map((extension.source.results || []).filter(row => installedIds.has(row.model)).map(row => {
    const key = JSON.stringify([row.model, !!row.mtp, row.concurrency || 1, row.target_tokens, row.repeat || 0]);
    return [key, {key, row}];
  })).values()];
  const selectedKey = extension.selectedCases ? JSON.stringify(extension.selectedCases[0]) : "";
  $("#extend-content").insertAdjacentHTML("afterbegin", `<div class="field"><label for="extend-case">Tests to update</label><select id="extend-case"><option value="">Selected models, sizes and repeats</option>${repairCases.map(({key,row}) => `<option value="${esc(key)}" ${key === selectedKey ? "selected" : ""}>${esc(row.model)} · ${fmtTokens(row.target_tokens)} · ${row.concurrency || 1} queries · repeat ${(row.repeat || 0)+1} · ${row.mtp ? "MTP" : "Standard"} · ${esc(row.status)}</option>`).join("")}</select><p class="hint">Choose a saved test to repair just that repeat, or update a whole selection below.</p></div>`);
  $("#extend-case").addEventListener("change", () => {
    const value = $("#extend-case").value;
    extension.selectedCases = value ? [JSON.parse(value)] : null;
    const item = extension.selectedCases?.[0];
    extension.modelModes = item ? {[item[0]]:[item[1] ? "on" : "off"]} : originalModelSelection(extension.sourceConfig, extension.models);
    extension.sizes = new Set(item ? [item[3]] : extension.initialSizes);
    extension.concurrency = new Set(item ? [item[2]] : extension.initialConcurrency);
    extension.repeats = Number(extension.sourceConfig.repeats) || 1;
    extension.action = item ? "rerun" : "add";
    renderExtendForm(); previewExtension();
  });
  $("#extend-repeats").addEventListener("change", () => {
    const value = Number($("#extend-repeats").value);
    extension.repeats = value;
    renderExtendForm(); previewExtension();
  });
  if (extension.selectedCases) {
    $$("#extend-content input[data-model], #extend-content input[name=extend-action]").forEach(input => input.disabled = true);
    $("#extend-select-existing").disabled = true;
    $("#extend-clear-selection").disabled = true;
  }
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
  renderRange("#extend-sizes", sizes, extension.sizes, value => { toggleLevel(extension.sizes, value); renderExtendForm(); previewExtension(); }, fmtTokens, "prompt tokens");
  renderRange("#extend-concurrency", levels, extension.concurrency, value => { toggleLevel(extension.concurrency, value); renderExtendForm(); previewExtension(); }, String, "simultaneous queries");
  if (extension.selectedCases) $$("#extend-sizes input, #extend-concurrency input").forEach(input => input.disabled = true);
  $$("#extend-content input[name=extend-action]").forEach(input => input.addEventListener("change", () => { extension.action = input.value; renderExtendForm(); previewExtension(); }));
}

async function previewExtension() {
  const extension = state.extension;
  if (!extension || extension.mode === "new") return;
  if (browserMode() || extension.connectionKey !== resultsConnectionKey()) {
    $("#extend-start").disabled = true;
    $("#extend-preview").innerHTML = banner("err", "Endpoint changed", "Close this dialog and select tests from the current connection again.");
    return;
  }
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
  const extension = state.extension;
  $("#extend-start").disabled = true;
  if (!resultsConnectionReady() || extension.connectionKey !== resultsConnectionKey()) {
    $("#extend-preview").innerHTML = banner("err", "Endpoint changed", "Reconnect in Run and select tests again. No benchmark was started.");
    return;
  }
  if (extension.mode === "new") {
    const selected = extension.models.filter(model => extension.selectedModels.has(model.id));
    if (!selected.length) return;
    state.env.models = extension.models.map(model => ({...model}));
    state.models = new Set(selected.map(model => model.id));
    renderModels(); updatePlan();
    $("#extend-dialog").close(); state.extension = null;
    showPanel("run");
    return;
  }
  if (browserMode()) {
    $("#extend-preview").innerHTML = banner("err", "New benchmark required", "Select models from the currently connected endpoint to create a new benchmark.");
    return;
  }
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
  const repairable = !run.community && run.status !== "running" && !state.benchmarkRunning
    && !browserMode() && state.env?.capabilities?.extend;
  const fields = [
    ["Status", row => row.status], ["Repeat", row => (row.repeat || 0) + 1],
    ["Actual prompt tokens", row => row.prompt_tokens], ["Query context", row => row.per_request_context_length || row.context_length],
    ["Cached prompt tokens (unknown if absent)", row => row.cached_prompt_tokens],
    ["Token count source", row => row.metric_sources?.completion_tokens],
    ["Provider-reported timings", row => row.reported_timings && Object.keys(row.reported_timings).length ? JSON.stringify(row.reported_timings) : null],
    ["Client timing scope",row=>row.timing_scope ? JSON.stringify(row.timing_scope) : null],
    ["Metric provenance",row=>row.metric_sources ? JSON.stringify(row.metric_sources) : null],
    ["Placement", row => row.gpu_offload], ["Time to first token (s)", row => row.ttft_s],
    ["Prefill (tk/s)", row => collectiveRate(row, "prefill_tps")], ["Total decode (tk/s)", row => decodeRate(row)],
    ["End-to-end output (tk/s)",row=>phaseMetrics()?.outputRate(row) ?? null],
    ["Decode per request (median tk/s)",row=>requestDecodeRate(row)],
    ["Prefill time (client TTFT proxy, s)",row=>phaseMetrics()?.prefillPhase(row)?.prefill_s ?? null],
    ["Decode time (s)",row=>phaseMetrics()?.decodePhase(row)?.decode_s ?? null],
    ["Scoped decode tokens",row=>phaseMetrics()?.decodePhase(row)?.decode_tokens ?? null],
    ["Output tokens", row => row.completion_tokens], ["Batch wall time (s)", row => row.wall_s],
    ["GPU power (W)", row => (row.power || {}).prefill_gpu_w ?? (row.group_power || {}).gpu_w_avg],
    ["GPU decode power (W)", row => (row.power || {}).decode_gpu_w],
    ["Estimated system power (W)", row => (row.power || {}).prefill_system_w_est ?? (row.group_power || {}).system_w_est],
    ["GPU prefill energy (J)", row => (row.power || {}).prefill_energy_j],
    ["GPU decode energy (J)", row => (row.power || {}).decode_energy_j],
    ["Group GPU energy (J)", row => (row.group_power || {}).gpu_energy_j],
    ["Energy per 1k prompt tokens (J)", row => (row.power || {}).j_per_1k_prefill],
    ["GPU decode energy per scoped decode token (J)", row => (row.power || {}).j_per_token_decode],
    ["Load time (s)", row => row.load_s],
    ["Timeout budget (s)", row => row.timeout_budget_s],
    ["Timeout rule", row => row.timeout_reason],
    ["Timeout trigger", row => row.timeout_trigger],
    ["Quantization", row => quantizationLabel(row)],
    ["Quantization format source", row => row.quantization_source],
    ["Quantization method source", row => row.quantization_method_source],
    ["Engine", row => executionGroup(run, row)?.label || run.provider?.label || run.config?.provider || "LM Studio"],
    ["Speculative decoding", row => rowProviderManaged(run, row) ? `provider controlled; reported ${row.model_configuration?.mtp?.user_reported || "unknown"}` : row.mtp ? "on" : "off"],
    ["VRAM peak (MB)", row => row.vram_peak_mb], ["Failure", row => row.message || row.fail_kind],
  ];
  const devices=new Map((run.results || []).flatMap(row=>(row.power || row.group_power || {}).gpus || []).map(card=>[card.index,card.name]));
  for(const [index,name] of devices) {
    const card=row=>(row.power || row.group_power || {}).gpus?.find(gpu=>gpu.index===index) || {};
    for(const [label,get] of [
      ["prefill power (W)",g=>g.prefill?.gpu_w_avg],["decode power (W)",g=>g.decode?.gpu_w_avg],
      ["prefill energy (J)",g=>g.prefill?.gpu_energy_j],["decode energy (J)",g=>g.decode?.gpu_energy_j],
      ["whole-case power (W)",g=>g.gpu_w_avg],["whole-case energy (J)",g=>g.gpu_energy_j],
      ["peak VRAM (MiB)",g=>g.vram_peak_mb ?? g.vram_mb_max],
      ["decode utilization (%)",g=>g.decode?.gpu_util_avg], ["whole-case utilization (%)",g=>g.gpu_util_avg],
      ["peak temperature (°C)",g=>{const values=[g.prefill?.temp_c_max,g.decode?.temp_c_max,g.temp_c_max].filter(Number.isFinite);return values.length?Math.max(...values):null;}],
      ["decode clock (MHz)",g=>g.decode?.clock_mhz_avg], ["whole-case clock (MHz)",g=>g.clock_mhz_avg]
    ]) fields.push([`GPU ${index+1} · ${name} · ${label}`,row=>get(card(row))]);
  }
  const exactValue = (label,value) => typeof value === "number" ? (/tokens|repeat|context/i.test(label) ? String(value) : n(value)) : value;
  const rows = (run.results || []).flatMap(row => [...fields.map(([label, get]) => {
    const value = get(row);
    if (value == null || value === "") return "";
    const repair = label === "Status" && repairable
      ? `<button class="btn-sm" data-run-id="${esc(run.id)}" data-repair-case="${esc(JSON.stringify([row.model, !!row.mtp, row.concurrency || 1, row.target_tokens, row.repeat || 0]))}" title="Rerun only this test; its previous attempt is retained">Repair repeat ${(row.repeat || 0)+1}</button>` : "";
    return `<tr class="colored-result-row" style="--table-color:${tableRowColor(run, row.model, modelUniverse())}"><td>${esc(row.model)}</td><td>${fmtTokens(row.target_tokens)}</td><td>${row.concurrency || 1}</td><td>${esc(sessionLabel(run, row))}</td><td>${esc(label)}</td><td class="measure-cell">${esc(exactValue(label,value))}${label.includes("tk/s") ? estimatedMark(row, (row.concurrency || 1) > 1) : ""}${repair}</td></tr>`;
  }), ...(row.requests || []).flatMap((request, index) => [
    ["Status", request.status], ["Prompt tokens", request.prompt_tokens],
    ["Output tokens", request.completion_tokens], ["Start offset (s)", request.start_offset_s], ["Time to first token (s)", request.ttft_s],
    ["Decode (tk/s)", request.decode_tps], ["Decode time (s)", request.decode_s], ["Scoped decode tokens", request.decode_token_count], ["Wall time (s)", request.wall_s], ["Failure", request.message],
  ].filter(([, value]) => value != null).map(([label, value]) =>
    `<tr class="colored-result-row" style="--table-color:${tableRowColor(run, row.model, modelUniverse())}"><td>${esc(row.model)}</td><td>${fmtTokens(row.target_tokens)}</td><td>${row.concurrency || 1}</td><td>${esc(sessionLabel(run, row))}</td><td>Request ${index + 1}: ${esc(label)}</td><td class="measure-cell">${esc(exactValue(label,value))}${label.startsWith("Output") ? estimatedMark(request) : ""}</td></tr>`))]).join("");
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

/* ---------------- model groups: one model under several names ---------------- */
// Parts of a served ID that say how a model was packaged, not which model it is.
const MODEL_QUANT = /(^|[-_.])(?:i?q\d+(?:_[k01])?(?:_(?:xxs|xs|s|m|l|xl|nl))?|oq\d+[a-z]?|\d+bit|\d+(?:\.\d+)?bpw|fp\d+|bf16|f16|f32|mxfp4|nvfp4)(?=$|[-_.])/gi;
const MODEL_PACKAGING = new Set(["gguf", "mlx", "awq", "gptq", "exl2", "exl3", "qat", "mtp", "ud", "imatrix", "i1", "unsloth", "bartowski", "thebloke", "mradermacher", "lmstudio"]);
const MODEL_SIZE = /^[ae]?\d+(?:\.\d+)?[bm]$/i;
/* What a served ID stands for: `label` drops the publisher, quantization and
 * packaging; `family` also drops the parameter count. Unknown words are kept,
 * so two IDs only meet when nothing but packaging tells them apart. */
function modelBase(id) {
  const name = String(id).trim().replace(/^.*\//, "").replace(/^[^/]*?--/, "").replace(/\.gguf$/i, "")
    .replace(/[@:]/g, "-").replace(MODEL_QUANT, "$1");
  const words = name.split(/[-_\s]+/).map(word => word.replace(/^\.+|\.+$/g, "")).filter(Boolean)
    .filter((word, index) => index === 0 || !MODEL_PACKAGING.has(word.toLowerCase()));
  const family = words.filter((word, index) => index === 0 || !MODEL_SIZE.test(word));
  return {label: words.join("-") || String(id), family: family.join("-") || String(id)};
}
/* Boxes for the model list: first the same model in several variants, then, among
 * the rest, one family in several sizes. A model with no sibling stays loose. */
function modelGroups(models) {
  const bases = new Map(models.map(model => [model, modelBase(model)]));
  const gather = (field, pool) => {
    const found = new Map();
    for (const model of pool) {
      const key = bases.get(model)[field].toLowerCase();
      found.set(key, [...(found.get(key) || []), model]);
    }
    return [...found.values()].filter(members => members.length > 1);
  };
  // The spelling with the most capitals is usually the publisher's own.
  const named = (members, field) => members.map(model => bases.get(model)[field])
    .sort((a, b) => (b.match(/[A-Z]/g) || []).length - (a.match(/[A-Z]/g) || []).length)[0];
  const variants = gather("label", models), taken = new Set(variants.flat());
  const sizes = gather("family", models.filter(model => !taken.has(model)));
  sizes.flat().forEach(model => taken.add(model));
  const groups = [...variants.map(members => ({name: named(members, "label"), kind: "variants", models: members})),
    ...sizes.map(members => ({name: named(members, "family"), kind: "sizes", models: members}))]
    .sort((a, b) => models.indexOf(a.models[0]) - models.indexOf(b.models[0]));
  return {groups, loose: models.filter(model => !taken.has(model))};
}

function renderVisibleModels(models) {
  updateModelRerunAvailability();
  const shown = shownModels(models);
  $("#visible-model-count").textContent = models.length ? `${shown.length} of ${models.length} shown` : "";
  const container = $("#visible-models");
  const signature = JSON.stringify(models);
  const {groups, loose} = modelGroups(models);
  if (container.dataset.models !== signature) {
    container.dataset.models = signature;
    const chip = model => `<label class="chip" title="Show or hide ${esc(model)}" data-hl-tag="model" data-hl-value="${esc(model)}"><input type="checkbox" value="${esc(model)}"><span class="model-key" aria-hidden="true"></span>${esc(model)}</label>`;
    const box = (group, index) => `<div class="model-group" data-kind="${group.kind}"><div class="model-group-head"><button type="button" class="model-group-toggle" data-model-group="${index}" aria-pressed="true"><i class="model-group-mark" aria-hidden="true"></i><strong>${esc(group.name)}</strong><small></small></button><button type="button" class="model-group-only" data-model-group-only="${index}" title="Show only these ${group.models.length} models">only</button></div><div class="chips">${group.models.map(chip).join("")}</div></div>`;
    container.classList.toggle("grouped", groups.length > 0);
    container.innerHTML = !models.length ? '<span class="dim">Select a benchmark to see its models.</span>'
      : !groups.length ? models.map(chip).join("")
      : `<div class="model-groups">${groups.map(box).join("")}</div>${loose.length ? `<div class="chips model-loose">${loose.map(chip).join("")}</div>` : ""}`;
    $$("#visible-models input").forEach(input => input.addEventListener("change", async () => {
      if (state.visibleModels === null) state.visibleModels = new Set(models);
      if (input.checked) state.visibleModels.add(input.value); else state.visibleModels.delete(input.value);
      await redrawResultsWithoutScrolling();
    }));
    // A box header shows all of its models, or hides them when all are already shown.
    $$("#visible-models [data-model-group]").forEach(button => button.addEventListener("click", async () => {
      const members = groups[Number(button.dataset.modelGroup)].models, on = shownModels(models);
      const hide = members.every(model => on.includes(model));
      if (state.visibleModels === null) state.visibleModels = new Set(models);
      members.forEach(model => state.visibleModels[hide ? "delete" : "add"](model));
      await redrawResultsWithoutScrolling();
    }));
    $$("#visible-models [data-model-group-only]").forEach(button => button.addEventListener("click", async () => {
      state.visibleModels = new Set(groups[Number(button.dataset.modelGroupOnly)].models);
      await redrawResultsWithoutScrolling();
    }));
  }
  $$("#visible-models [data-model-group]").forEach(button => {
    const group = groups[Number(button.dataset.modelGroup)], on = group.models.filter(model => shown.includes(model)).length;
    button.setAttribute("aria-pressed", on === group.models.length ? "true" : on ? "mixed" : "false");
    button.title = `${on === group.models.length ? "Hide" : "Show"} all ${group.models.length}: ${group.models.join(", ")}`;
    button.querySelector("small").textContent = `${group.models.length} ${group.kind} · ${on === group.models.length ? "all" : on || "none"} shown`;
  });
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
      symbol.innerHTML = shapes[chartAppearance({id: ""}, input.value, modelUniverse(models), false).marker] || "&#9679;";
      symbol.style.background = "transparent";
    }
  });
}
function configurationColor(run) {
  return resolveColor(runColor(run.parent_run_id || run.id));
}
function configurationVariantIndex(run) {
  const root = run.parent_run_id || String(run.id || "").split("::")[0];
  const source = state.runCache[root];
  if (!source) return (run.storage_alternative ? 1 : 0) + (run._rowConditionIndex || 0);
  const ids = availableMeasurementRuns([source]).map(item => item.id).sort();
  return Math.max(0, ids.indexOf(run.id)) * 5 + (run._rowConditionIndex || 0);
}
/* One encoding everywhere: colour carries the dimension the user picked
 * (models or benchmarks), marker shape carries the other one, and the line
 * pattern carries the extra level (query count or prompt length) and MTP. */
const LEVEL_DASHES = [null, "7 4", "2 3", "10 3 2 3", "12 5", "1 4", "14 3 3 3 3 3", "5 2"];
/* An ordered ramp for an ordered quantity: cyan through violet to red as the level rises. */
function levelColor(index, count) {
  return `hsl(${Math.round(190 + (count > 1 ? index * 180 / (count - 1) : 0)) % 360} 72% 60%)`;
}
function chartAppearance(run, model, models, mtp, levelIndex = 0, levelCount = 1) {
  if (state.colorBy === "levels") {
    const shape = Math.max(0, modelUniverse(models).indexOf(model)) + (runColorIndex(run.id) || 0) + configurationVariantIndex(run);
    return {color: levelColor(levelIndex, levelCount), marker: Charts.MARKERS[shape % Charts.MARKERS.length], dash: mtp ? "2 2" : null};
  }
  const byModel = state.colorBy === "models";
  const variant = configurationVariantIndex(run);
  const modelIndex = Math.max(0, modelUniverse(models).indexOf(model));
  const shapeIndex = byModel ? (runColorIndex(run.id) || 0) + variant : modelIndex + variant;
  const dash = LEVEL_DASHES[(variant + levelIndex) % LEVEL_DASHES.length];
  return {
    color: byModel ? modelColor(model, models) : configurationColor(run),
    marker: Charts.MARKERS[shapeIndex % Charts.MARKERS.length],
    dash: [dash, mtp ? "2 2" : null].filter(Boolean).join(" ") || null,
  };
}
function keyGlyph(appearance, {line = true, shape = true} = {}) {
  const c = esc(appearance.color || "currentColor");
  const mark = !shape ? "" : appearance.marker === "square" ? `<rect x="11" y="3" width="8" height="8" fill="${c}"/>`
    : appearance.marker === "triangle" ? `<polygon points="15,2 20,11 10,11" fill="${c}"/>`
    : appearance.marker === "diamond" ? `<polygon points="15,1.5 20.5,7 15,12.5 9.5,7" fill="${c}"/>` : `<circle cx="15" cy="7" r="4" fill="${c}"/>`;
  return `<svg width="30" height="14" aria-hidden="true">${line ? `<line x1="1" y1="7" x2="29" y2="7" stroke="${c}" stroke-width="2"${appearance.dash ? ` stroke-dasharray="${esc(appearance.dash)}"` : ""}/>` : ""}${mark}</svg>`;
}
/* The key is shared by every chart on the page, so it is drawn once. Hovering
 * an entry dims every other line. A benchmark entry is also a switch: clicking
 * it hides or shows that benchmark's curves. `used.curves` lists every
 * benchmark that has something to draw here, as {run, on, cases, measured}. */
function chartKeyMarkup(used, axis) {
  const byModel = state.colorBy !== "benchmarks", byLevel = state.colorBy === "levels", groups = [];
  const item = (tag, value, glyph, label, title = "") => `<span class="key-item" tabindex="0" data-hl-tag="${tag}" data-hl-value="${esc(value)}"${title ? ` title="${esc(title)}"` : ""}>${glyph}${esc(label)}</span>`;
  const curve = ({run, on, cases, measured}) => {
    const look = chartAppearance(run, "", used.allModels, false);
    const glyph = byModel ? keyGlyph({marker: look.marker}, {line: false}) : keyGlyph({color: configurationColor(run), dash: look.dash}, {shape: false});
    const counts = `${cases} matching case${cases === 1 ? "" : "s"} \u00b7 ${measured} measured`;
    const title = `${configurationFullTitle(run) || run.label || run.id}\n${counts}\n${on ? "Click to hide these curves" : "Hidden. Click to show these curves"}`;
    // Only a benchmark that is drawn can be picked out by hovering.
    return `<button type="button" class="key-item key-toggle" aria-pressed="${on}" data-curve="${esc(runKey(run))}"${on ? ` data-hl-tag="run" data-hl-value="${esc(runKey(run))}"` : ""}${byModel ? "" : ` style="--key-color:${esc(configurationColor(run))}"`} title="${esc(title)}">${glyph}${esc(run.label || run.id)}</button>`;
  };
  const modelItems = used.models.map(model => item("model", model, byModel && !byLevel
    ? `<i class="key-dot" style="background:${esc(modelColor(model, used.allModels))}"></i>`
    : keyGlyph({marker: chartAppearance({id: ""}, model, used.allModels, false).marker}, {line: false}), model));
  const runItems = (used.curves || used.runs.map(run => ({run, on: true, cases: 0, measured: 0}))).map(curve);
  // A lone benchmark needs no entry, unless it is switched off and this is the way back.
  const anyOff = (used.curves || []).some(entry => !entry.on);
  const colored = byModel ? ["Models", modelItems] : ["Benchmarks", runItems], shaped = byModel ? ["Benchmarks", runItems] : ["Models", modelItems];
  const levelName = level => axis === "context" ? `${level} quer${level === 1 ? "y" : "ies"}` : `${fmtTokens(level)} prompt`;
  if (byLevel) {
    if (used.levels.length) groups.push(`<div class="key-group"><span class="key-title">${axis === "context" ? "Concurrent queries" : "Prompt length"} <small>colour</small></span>${used.levels.map((level, index) => item("level", level, `<i class="key-dot" style="background:${levelColor(index, used.levels.length)}"></i>`, levelName(level))).join("")}</div>`);
    if (modelItems.length) groups.push(`<div class="key-group"><span class="key-title">Models <small>marker</small></span>${modelItems.join("")}</div>`);
    if (runItems.length > 1 || anyOff) groups.push(`<div class="key-group"><span class="key-title">Benchmarks <small>marker</small></span>${runItems.join("")}</div>`);
  } else if (colored[1].length) groups.push(`<div class="key-group"><span class="key-title">${colored[0]} <small>colour</small></span>${colored[1].join("")}</div>`);
  if (!byLevel && (shaped[1].length > 1 || (byModel && anyOff))) groups.push(`<div class="key-group"><span class="key-title">${shaped[0]} <small>marker</small></span>${shaped[1].join("")}</div>`);
  const lines = [];
  if (!byLevel && used.levels.length > 1) used.levels.forEach((level, index) => lines.push(item("level", level,
    keyGlyph({dash: LEVEL_DASHES[index % LEVEL_DASHES.length]}, {shape: false}),
    axis === "context" ? `${level} quer${level === 1 ? "y" : "ies"}` : `${fmtTokens(level)} prompt`)));
  if (used.mtp.size > 1) lines.push(item("mtp", "true", keyGlyph({dash: "2 2"}, {shape: false}), "Speculative decoding on"));
  if (lines.length) groups.push(`<div class="key-group"><span class="key-title">Line style</span>${lines.join("")}</div>`);
  return groups.join("");
}
function highlightSeries(target) {
  const tag = target?.dataset.hlTag, value = target?.dataset.hlValue;
  $$("#panel-compare .chart-series").forEach(node => node.classList.toggle("is-dim", !!tag && node.getAttribute("data-tag-" + tag) !== value));
}

function comparisonReadout(runs,models,key) {
  if(models.length !== 1) return "";
  const model=models[0],metric=METRICS[key];
  const tracks=tracksFor(runs).filter(track=>conditionRows(track.run,[model]).some(row=>!!row.mtp === !!track.mtp));
  if(tracks.length<2) return "";
  const lists=tracks.map(track=>conditionRows(track.run,[model]).filter(row=>!!row.mtp === !!track.mtp));
  const common=[...new Set(lists[0].map(row=>row.target_tokens))].filter(size=>lists.every(rows=>rows.some(row=>row.target_tokens===size)));
  if(!common.length) return "";
  const size=Math.max(...common),rows=lists.map(list=>list.filter(row=>row.target_tokens===size));
  const baseline=new Map(rows[0].filter(row=>row.status==="ok" && Number.isFinite(metric.get(row))).map(row=>[row.repeat || 0,row]));
  const baselineMedian=storageMedian([...baseline.values()].map(row=>metric.get(row)));
  const format=value=>Number.isFinite(value) ? fmt(value,2)+" "+metric.unit : "Unavailable";
  const cells=tracks.map((track,index)=>{
    const measured=rows[index].filter(row=>row.status==="ok" && Number.isFinite(metric.get(row)));
    const matched=measured.filter(row=>baseline.has(row.repeat || 0));
    const value=storageMedian(measured.map(row=>metric.get(row)));
    let change="",direction="same",reference="";
    if(index && matched.length) {
      const base=storageMedian(matched.map(row=>metric.get(baseline.get(row.repeat || 0))));
      const pairedValue=storageMedian(matched.map(row=>metric.get(row)));
      if(base>0 && pairedValue>0) {
        const percent=(pairedValue/base-1)*100;
        change=`${percent>0 ? "+" : ""}${percent.toFixed(1)}%${metric.unit==="s" ? " time" : metric.unit==="tk/s" ? " tk/s" : ""}`;
        if(base !== baselineMedian || pairedValue !== value) reference=`Matched: ${format(base)} → ${format(pairedValue)}`;
        direction=Math.abs(percent)<.05 ? "same" : metric.better === "low" ? percent<0 ? "better" : "worse" : metric.better === "high" ? percent>0 ? "better" : "worse" : "same";
      }
    }
    const timeout=rows[index].find(row=>row.status==="failed" && row.fail_kind==="timeout");
    const text=value!=null ? format(value) : timeout ? `Timeout · ${fmt(timeout.timeout_budget_s || timeout.wall_s,0)} s limit` : "No successful measurement";
    const label=track.run.storage_condition || track.name;
    return `<div class="chart-readout-item"><span title="${esc(track.name)}">${esc(label)}</span><strong>${esc(text)}</strong><small>${measured.length} measured repeat${measured.length===1 ? "" : "s"}${index ? matched.length ? ` · ${matched.length} matched` : " · unpaired" : ""}</small>${change ? `<span class="metric-delta" data-direction="${direction}" title="Change against the first condition using matching successful repeats; descriptive, not a significance test">${esc(change)}${reference ? " · matched only" : ""}</span>` : ""}${reference ? `<small>${esc(reference)}</small>` : ""}</div>`;
  }).join("");
  return `<div class="chart-readout" aria-label="Recorded condition values"><div class="chart-readout-title">${esc(fmtTokens(size))} prompt tokens · ${esc(metric.label)} · ${state.comparisonConcurrency} quer${state.comparisonConcurrency===1 ? "y" : "ies"}</div><div class="chart-readout-values">${cells}</div></div>`;
}

/* ---------------- compare tab ---------------- */
function selectedMetricKeys() {
  return Object.keys(METRICS).filter(key => state.metrics.has(key));
}
function metricGroups(keys = selectedMetricKeys()) {
  return keys.map(key => ({identity:key, unit:METRICS[key].unit, keys:[key]}));
}
function measureAppearance(base, key, keys, mtp) {
  if (keys.length < 2) return base;
  const styles = [null, "7 4", "2 3", "10 3 2 3", "12 5"];
  const dash = styles[keys.indexOf(key) % styles.length];
  return {...base, dash: [base.dash,dash,mtp ? "2 2" : null].filter(Boolean).join(" ") || null};
}
/* Which metrics the ticked benchmarks can actually draw. Cached per selection. */
function metricsWithData(runs) {
  const signature = JSON.stringify(runs.map(run => [run.id, (run.results || []).length]));
  if (state.metricAvailability?.signature === signature) return state.metricAvailability.keys;
  const keys = new Set();
  const rows = runs.flatMap(run => (run.results || []).filter(row => row.status === "ok"));
  for (const [key, metric] of Object.entries(METRICS)) if (rows.some(row => Number.isFinite(metric.get(row)))) keys.add(key);
  state.metricAvailability = {signature, keys};
  return keys;
}
function renderMetricSeg(runs = null) {
  const node = $("#result-metrics");
  if (!node) return;
  if (state.metrics.delete("aggregate_completion_tps")) state.metrics.add("decode_tps");
  const available = runs?.length ? metricsWithData(runs) : null;
  node.innerHTML = METRIC_GROUPS.map(([group, label, unit]) => `<div class="metric-group"><span class="metric-group-label">${esc(label)}${unit ? `<small>${esc(unit)}</small>` : ""}</span><div class="chips">${Object.entries(METRICS).filter(([, metric]) => metric.group === group).map(([key, metric]) => {
    const on = state.metrics.has(key), recorded = !available || available.has(key);
    return `<button type="button" class="chip metric-chip" data-metric="${esc(key)}" aria-pressed="${on}" data-on="${on ? 1 : 0}"${recorded ? "" : ' data-empty="1"'} title="${esc(metric.blurb + (recorded ? "" : " Not recorded in the ticked benchmarks."))}">${esc(metric.short)}</button>`;
  }).join("")}</div></div>`).join("");
}

const stableJson = value => JSON.stringify(value, (_, item) => item && typeof item === "object" && !Array.isArray(item)
  ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item);
/* A failed or skipped row is filed under the condition it was attempted in. */
const mergedConditionKeys = new WeakMap();
/* Session settings that can change a measured speed. The plan a session ran
 * (sizes, repeats, selected cases, timeouts) is not a condition of a result. */
const SESSION_CONDITION_FIELDS = ["provider", "endpoint_id", "context_policy", "context_headroom", "context_safety_fraction",
  "context_planner_version", "gpu_offload", "disable_thinking", "endpoint_reasoning_effort", "kv_cache_dtype", "max_tokens", "temperature"];
/* The same setting reported by a different source is the same setting. */
function withoutProvenance(value) {
  if (Array.isArray(value)) return value.map(withoutProvenance);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.entries(value).filter(([key]) => !/^(?:source|configuration_id|model_source)$|_source$/.test(key))
    .map(([key, item]) => [key, withoutProvenance(item)]));
}
function rowConditionIdentity(run, row) {
  const session = (run.sessions || []).find(item => item.id === row.session_id || item.session_id === row.session_id);
  const sessionConfig = session?.config ?? session?.execution_config ?? null;
  const power = row.group_power || row.power || {};
  // Target size, repeat and measured values are deliberately excluded; settings
  // and measurement boundaries must agree before repeats can be aggregated.
  return {
    // A configuration ID hashes provenance too, so it identifies a row only when the settings themselves are absent.
    configuration_id: row.model_configuration ? null : row.configuration_id ?? null,
    model_configuration: row.model_configuration ? withoutProvenance(row.model_configuration) : null,
    execution_config: row.execution_config ?? null,
    session_config: sessionConfig ? Object.fromEntries(SESSION_CONDITION_FIELDS.filter(field => sessionConfig[field] != null && sessionConfig[field] !== "").map(field => [field, sessionConfig[field]])) : null,
    quantization: row.quantization ?? null, quantization_method: row.quantization_method ?? null,
    engine: row.engine ?? null, engine_version: row.engine_version ?? null,
    model_revision: row.model_revision ?? null, kv_cache_dtype: row.kv_cache_dtype ?? null,
    reasoning_effort: row.reasoning_effort ?? null, requested_mtp: row.requested_mtp ?? null,
    temperature: row.temperature ?? null, max_tokens: row.max_tokens ?? null,
    gpu_offload: row.gpu_offload ?? null,
    power_scope: power.scope ?? null, power_gpu_indices: power.gpu_indices ?? null,
    // The distinct boundaries in use, not one entry per request: the same
    // condition measured at 1 and at 8 concurrent queries is still one condition.
    phase_protocol: [...new Set((row.requests?.length ? row.requests : [row]).map(request => JSON.stringify([request.timing_scope?.phase_protocol ?? "legacy_boundary", request.timing_scope?.decode_end_boundary ?? "legacy_boundary"])))].sort(),
  };
}
function rowConditionFingerprint(run, row) {
  return mergedConditionKeys.get(row) || stableJson(rowConditionIdentity(run, row));
}
function splitRowConditions(runs) {
  return runs.flatMap(run => {
    const groups = new Map(), unmeasured = [];
    const add = (key, identity, row) => { if (!groups.has(key)) groups.set(key, {key, identity, rows: []}); groups.get(key).rows.push(row); };
    for (const row of run.results || []) {
      mergedConditionKeys.delete(row);
      const identity = rowConditionIdentity(run, row);
      if (row.status === "ok") add(stableJson(identity), identity, row); else unmeasured.push([row, identity]);
    }
    for (const [row, identity] of unmeasured) {
      const key = stableJson(identity);
      // A test that never completed often records fewer settings and no timing
      // boundary. A setting it did not record cannot distinguish it, so it joins
      // the one measured condition that agrees on everything it did record.
      const fits = groups.has(key) ? [] : [...groups.values()].filter(group => group.rows.some(item => item.status === "ok")
        && Object.keys(identity).every(field => field === "phase_protocol" || identity[field] == null || stableJson(identity[field]) === stableJson(group.identity[field])));
      const same = fits.filter(group => group.rows.some(item => item.model === row.model && !!item.mtp === !!row.mtp));
      const target = fits.length === 1 ? fits[0] : same.length === 1 ? same[0] : null;
      if (target) { target.rows.push(row); mergedConditionKeys.set(row, target.key); continue; }
      // No measured condition to join: gather this model's unmeasured attempts that agree wherever both recorded a setting.
      const kin = groups.has(key) ? null : [...groups.values()].find(group => !group.rows.some(item => item.status === "ok")
        && group.rows.some(item => item.model === row.model && !!item.mtp === !!row.mtp)
        && Object.keys(identity).every(field => field === "phase_protocol" || identity[field] == null || group.identity[field] == null || stableJson(identity[field]) === stableJson(group.identity[field])));
      if (kin) { kin.rows.push(row); mergedConditionKeys.set(row, kin.key); } else add(key, identity, row);
    }
    if (groups.size <= 1) return [run];
    const ordered = [...groups.values()].sort((a, b) => a.key.localeCompare(b.key));
    const describe = ({rows}) => {
      const row = rows[0], config = row.model_configuration || {};
      const reasoning = config.reasoning?.effective || config.reasoning?.requested || row.reasoning_effort;
      const id = row.configuration_id || config.configuration_id;
      return {id: id || null, parts: {
        model: new Set(rows.map(item => item.model)).size === 1 ? row.model : null,
        quantization: row.quantization || config.quantization?.value || null,
        engine: config.engine?.value || row.engine || null,
        reasoning: reasoning && reasoning !== "unknown" ? `reasoning ${reasoning}` : null,
        placement: row.gpu_offload != null ? `placement ${row.gpu_offload}` : null,
        id: id ? `config ${String(id).slice(0, 12)}` : null}};
    };
    const described = ordered.map(describe);
    // Speculative decoding is already its own line, so it does not make a variant.
    const modelsOf = ordered.map(group => new Set(group.rows.map(row => row.model + "|" + !!row.mtp)));
    return ordered.map(({key, rows}, index) => {
      // Two models with different settings are simply two models. A condition is
      // a variant, and needs a name, only when another one measured the same model.
      const rivals = ordered.map((_, other) => other).filter(other => [...modelsOf[other]].some(model => modelsOf[index].has(model)));
      const telling = rivals.length < 2 ? [] : Object.keys(described[index].parts)
        .filter(field => field !== "model" && new Set(rivals.map(other => described[other].parts[field])).size > 1);
      const variant = rivals.indexOf(index);
      const details = rivals.length < 2 ? "" : telling.map(field => described[index].parts[field]).filter(Boolean).join(" / ") || `variant ${variant + 1}`;
      return {...run, _measurement_view:true, _rowConditionKey:key, _rowConditionIndex:variant,
        _rowConditionLabel:details, _rowConditionId:described[index].id, label:details ? `${run.label || run.id} / ${details}` : run.label || run.id, results:rows};
    });
  });
}
/* What the user sees as one benchmark line-up: a ticked benchmark, or a named variant of it. */
function runKey(run) {
  return run.id + (run._rowConditionIndex ? "#" + run._rowConditionIndex : "");
}
function configurationIdentity(run) {
  const gpu = run.gpu?.name || run.hardware?.gpu?.name || (Array.isArray(run.hardware?.gpus) ? run.hardware.gpus.map(item => item.name).filter(Boolean).join(", ") : "") || "GPU not recorded";
  const engine = run.execution_group?.label || run.config?.endpoint_label || run.config?.provider || run.engine || "Engine not recorded";
  return [run.label || run.id, engine, gpu].join(" \u00b7 ");
}
function configurationFullTitle(run) {
  return configurationIdentity(run) + (run._rowConditionId ? ` / full configuration ID: ${run._rowConditionId}` : "");
}
function graphRuns(runs) { return measurementRuns(runs); }
function graphModels(models) { return shownModels(models); }
function pressSeg(selector, attribute, value) {
  $$(selector).forEach(button => button.setAttribute("aria-pressed", String(button.dataset[attribute] === value)));
}
function renderResultControls(runs, models) {
  const measured = measurementRuns(runs), scope = state.resultScope || syncResultLevels(measured);
  const graphs = state.resultView === "compare", scalable = scope.queries.length > 1;
  const axis = state.comparisonAxis;
  const set = (selector, hidden) => { const node = $(selector); if (node) node.hidden = hidden; };
  set("#rc-axis-row", !graphs);
  pressSeg("[data-comparison-axis]", "comparisonAxis", axis);
  const scaling = $('[data-comparison-axis="concurrency"]');
  if (scaling) {
    scaling.setAttribute("aria-disabled", String(!scalable));
    scaling.title = scalable ? "Put the number of simultaneous queries on the horizontal axis" : "The ticked benchmarks recorded a single query at a time, so there is nothing to plot against.";
  }
  if ($("#rc-axis-note")) $("#rc-axis-note").textContent = !runs.length ? "" : scalable
    ? (axis === "context" ? "How each metric changes as the prompt gets longer." : "How each metric changes as more queries arrive at once.")
    : "Only single-query tests were recorded here, so prompt length is the only axis.";
  // The dimension that is not on the axis becomes a row of toggles: one line per ticked value.
  const prompts = graphs && axis === "concurrency";
  const values = prompts ? scope.prompts : scope.queries, active = prompts ? activePromptLevels() : activeQueryLevels();
  if ($("#rc-levels-label")) $("#rc-levels-label").textContent = prompts ? "Prompt length" : "Concurrent queries";
  if ($("#rc-levels-hint")) $("#rc-levels-hint").textContent = graphs ? "One line per ticked value" : "Rows to list";
  const keys = selectedMetricKeys(), shown = shownModels(models);
  const recorded = new Set(measured.flatMap(run => (run.results || []).filter(row => row.status === "ok" && shown.includes(row.model)
    && (!keys.length || keys.some(key => Number.isFinite(METRICS[key].get(row))))).map(row => prompts ? row.target_tokens : row.concurrency || 1)));
  if ($("#result-levels")) $("#result-levels").innerHTML = values.map(value => {
    const empty = graphs && !recorded.has(value), what = prompts ? `Prompts of ${fmtTokens(value)} tokens` : `${value} simultaneous quer${value === 1 ? "y" : "ies"}`;
    return `<button type="button" class="chip" data-level="${value}" data-level-kind="${prompts ? "prompt" : "queries"}" aria-pressed="${active.includes(value)}" data-on="${active.includes(value) ? 1 : 0}"${empty ? ' data-empty="1"' : ""} title="${what}${empty ? ". Nothing recorded here for the ticked metrics and models." : ""}">${prompts ? fmtTokens(value) : value}</button>`;
  }).join("") + (values.length > 2 ? `<button type="button" class="btn-sm" data-level-all="${prompts ? "prompt" : "queries"}" title="Tick every recorded value">All</button>` : "");
  set("#rc-levels-row", values.length < 2);
  pressSeg("[data-color-by]", "colorBy", !graphs && state.colorBy === "levels" ? "models" : state.colorBy);
  const metricHint = $("#rc-metrics-row .rc-label small");
  if (metricHint) metricHint.textContent = graphs ? "Tick any. Each gets its own chart." : "Tick any. Each gets its own column.";
  const byLevels = $('[data-color-by="levels"]');
  if (byLevels) { byLevels.textContent = prompts ? "Prompt length" : "Query count"; byLevels.hidden = !graphs || active.length < 2; }
  pressSeg("[data-comparison-layout]", "comparisonLayout", state.comparisonLayout);
  set("#rc-layout-option", !graphs);
  // Speculative decoding only matters where it was recorded.
  const mtp = measured.some(run => !runProviderManaged(run) && (run.results || []).some(row => row.mtp));
  if (!mtp && state.mtpFilter !== "all") state.mtpFilter = "all";
  pressSeg("[data-mtp-filter]", "mtpFilter", state.mtpFilter);
  set("#rc-mtp-option", !mtp);
  renderMetricSeg(measured);
}

/* ---------------- overview tab ----------------
 * Answers, per model: how fast at best, what happens as prompts grow, how far
 * it got, and what happens when queries arrive together. */
function trendOf(points, better) {
  if (points.length < 2 || !(points[0].y > 0)) return null;
  const first = points[0], last = points[points.length - 1], percent = (last.y / first.y - 1) * 100;
  return {first, last, percent, direction: Math.abs(percent) < 3 ? "same" : (percent > 0) === (better === "high") ? "better" : "worse"};
}
function peakOf(points) {
  return points.reduce((best, point) => !best || point.y > best.y ? point : best, null);
}
function sparkline(points) {
  if (points.length < 2) return "";
  const width = 76, height = 24, pad = 3, top = Math.max(...points.map(point => point.y)) || 1;
  // Zero-based, like the charts: a flat line must look flat.
  const at = (point, index) => [pad + index * (width - 2 * pad) / (points.length - 1), height - pad - (point.y / top) * (height - 2 * pad)];
  const path = points.map((point, index) => at(point, index).map(value => value.toFixed(1)).join(",")).join(" ");
  const [x, y] = at(points[points.length - 1], points.length - 1);
  return `<svg class="spark" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" aria-hidden="true"><polyline points="${path}" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/><circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.4" fill="currentColor"/></svg>`;
}
function deltaMarkup(trend, suffix = "") {
  if (!trend) return "";
  const arrow = trend.direction === "same" ? "&#9644;" : trend.percent > 0 ? "&#9650;" : "&#9660;";
  return `<span class="metric-delta" data-direction="${trend.direction}">${arrow} ${trend.percent > 0 ? "+" : ""}${trend.percent.toFixed(0)}%${suffix}</span>`;
}
function overviewRows(runs) {
  const out = [];
  for (const run of splitRowConditions(measurementRuns(runs))) for (const mtp of [false, true]) {
    if (mtp && runProviderManaged(run)) continue;
    const mine = (run.results || []).filter(row => !!row.mtp === mtp && (!mtp || row.fail_kind !== "mtp_unsupported"));
    for (const model of [...new Set(mine.map(row => row.model))].sort()) {
      const rows = mine.filter(row => row.model === model);
      const okLevels = [...new Set(rows.filter(row => row.status === "ok").map(row => row.concurrency || 1))].sort((a, b) => a - b);
      const base = okLevels[0] ?? Math.min(...rows.map(row => row.concurrency || 1));
      const prefill = pointsFor(run, model, METRICS.prefill_tps, mtp, base), decode = pointsFor(run, model, METRICS.decode_tps, mtp, base);
      const passed = [...new Set(rows.filter(row => row.status === "ok" && (row.concurrency || 1) === base).map(row => row.target_tokens))].sort((a, b) => a - b);
      const largest = passed.length ? passed[passed.length - 1] : null;
      // Every attempt that did not complete, at any query count, smallest first.
      const misses = rows.filter(row => row.status !== "ok" && row.fail_kind !== "mtp_unsupported")
        .map(row => ({x: row.target_tokens, count: row.concurrency || 1, reason: (row.fail_kind || row.status || "").replace(/_/g, " ")}))
        .sort((a, b) => (a.count === base ? 0 : 1) - (b.count === base ? 0 : 1) || a.x - b.x || a.count - b.count);
      const blocked = misses[0] || null;
      let load = null;
      if (okLevels.length > 1) {
        const prompt = resultLevels([{results: rows}]).bestPrompt;
        const total = pointsAcrossQueries(run, model, METRICS.decode_tps, mtp, prompt);
        const each = pointsAcrossQueries(run, model, METRICS.per_request_decode_tps, mtp, prompt);
        const wait = pointsAcrossQueries(run, model, METRICS.ttft_s, mtp, prompt);
        if (total.length > 1 || each.length > 1 || wait.length > 1) load = {prompt, total, each, wait};
      }
      const quant = rows.find(row => row.quantization);
      out.push({run, model, mtp, base, prefill, decode, largest, blocked, misses: misses.length, load, quant: quant ? quantizationLabel(quant) : ""});
    }
  }
  return out.sort((a, b) => a.model.localeCompare(b.model) || a.mtp - b.mtp);
}
function graphLink(target, body, title) {
  return `<button type="button" class="ov-link" data-open-graph="${esc(JSON.stringify(target))}" title="${esc(title)}">${body}</button>`;
}
function openGraphFor({model, metrics, axis}) {
  if (model) state.visibleModels = new Set([model]);
  if (metrics) state.metrics = new Set(metrics);
  state.comparisonAxis = axis || "context";
  state.queryLevels = null; state.promptLevels = null; state.levelsTouched = false; state.promptsTouched = false;
  if (metrics) state.metricsTouched = true;
  showResultsView("compare");
}
function renderOverviewTab(runs) {
  const target = $("#summary-body");
  if (!target) return;
  if (!runs.length) { target.innerHTML = ""; return; }
  const rows = overviewRows(runs);
  const lineups = new Map();
  for (const row of rows) lineups.set(row.model + "|" + row.mtp, (lineups.get(row.model + "|" + row.mtp) || 0) + 1);
  const many = runs.length > 1 || [...lineups.values()].some(count => count > 1);
  if (!rows.length) { target.innerHTML = `<div class="card">${emptyState("No measurements yet", "This benchmark has no recorded tests to summarise.")}</div>`; return; }
  const name = row => row.model + (row.mtp ? " · MTP" : "") + (many ? ` · ${row.run.label || row.run.id}` : "");
  const best = pick => rows.map(row => ({row, point: pick(row)})).filter(item => item.point && Number.isFinite(item.point.y))
    .sort((a, b) => b.point.y - a.point.y)[0];
  const tile = (label, value, unit, note, tone) => `<article class="ov-kpi" data-tone="${tone}"><span>${esc(label)}</span><strong>${value}<small>${esc(unit)}</small></strong><p>${note}</p></article>`;
  const prefill = best(row => peakOf(row.prefill)), decode = best(row => peakOf(row.decode));
  const reach = rows.filter(row => row.largest !== null).sort((a, b) => b.largest - a.largest)[0];
  const reached = reach ? new Set(rows.filter(row => row.largest === reach.largest).map(row => row.model)).size : 0;
  const modelCount = new Set(rows.map(row => row.model)).size;
  const loaded = best(row => row.load ? peakOf(row.load.total.filter(point => point.x > 1)) : null);
  // Older records kept each request's speed but not the group total.
  const shared = best(row => row.load?.each.length > 1 ? row.load.each[row.load.each.length - 1] : null);
  const kpis = [
    tile("Fastest prefill", prefill ? n(prefill.point.y) : "—", "tk/s", prefill ? `${esc(name(prefill.row))} at a ${fmtTokens(prefill.point.x)} prompt` : "Not recorded", "blue"),
    tile("Fastest decode", decode ? n(decode.point.y) : "—", "tk/s", decode ? `${esc(name(decode.row))} at a ${fmtTokens(decode.point.x)} prompt` : "Not recorded", "green"),
    tile("Longest prompt completed", reach ? fmtTokens(reach.largest) : "—", "tokens", reach ? `${reached} of ${modelCount} model${modelCount === 1 ? "" : "s"} got this far` : "No test completed", "violet"),
    loaded ? tile("Best throughput under load", n(loaded.point.y), "tk/s", `${esc(name(loaded.row))} with ${loaded.point.x} concurrent queries`, "amber")
      : shared ? tile("Each request under load", n(shared.point.y), "tk/s", `${esc(name(shared.row))} with ${shared.point.x} concurrent queries, down from ${n(shared.row.load.each[0].y)} alone`, "amber")
      : tile("Best throughput under load", "—", "", "Only single-query tests were recorded. Add simultaneous queries to a benchmark to see scaling.", "amber"),
  ].join("");
  // With several benchmarks ticked, the first one listing a model is its reference.
  const baseline = new Map();
  if (many) for (const row of rows) { const key = row.model + "|" + row.mtp; if (!baseline.has(key)) baseline.set(key, row); }
  const versus = (row, field) => {
    const first = baseline.get(row.model + "|" + row.mtp);
    if (!many || !first || first === row) return "";
    const a = peakOf(first[field]), b = peakOf(row[field]);
    if (!a || !b || !(a.y > 0)) return "";
    return deltaMarkup(trendOf([a, b], "high"), ` vs ${esc(first.run.label || first.run.id)}`);
  };
  const peakCell = (row, field, metric) => {
    const peak = peakOf(row[field]);
    if (!peak) return '<td class="num"><span class="dim">Not recorded</span></td>';
    return `<td class="num">${graphLink({model: row.model, metrics: [metric], axis: "context"}, `<b>${n(peak.y)}</b> <span class="ov-unit">tk/s</span>`, "Open this graph")}<small>at a ${fmtTokens(peak.x)} prompt</small>${row.base > 1 ? `<small class="ov-warn">total for ${row.base} queries at once; no single-query test</small>` : ""}${versus(row, field)}</td>`;
  };
  const trendCell = (row, field, metric) => {
    const points = row[field], trend = trendOf(points, "high");
    if (!trend) return `<td><span class="dim">${points.length ? "One prompt length only" : "Not recorded"}</span></td>`;
    return `<td>${graphLink({model: row.model, metrics: [metric], axis: "context"}, `<span class="ov-trend" data-direction="${trend.direction}">${sparkline(points)}${deltaMarkup(trend)}</span>`, "Open this graph")}<small>${n(trend.first.y)} &rarr; ${n(trend.last.y)} tk/s</small><small>from ${fmtTokens(trend.first.x)} to ${fmtTokens(trend.last.x)} tokens</small></td>`;
  };
  const miss = row => `${fmtTokens(row.blocked.x)}${row.blocked.count !== row.base || row.base > 1 ? ` at ${row.blocked.count} quer${row.blocked.count === 1 ? "y" : "ies"}` : ""}: ${esc(row.blocked.reason)}${row.misses > 1 ? `, and ${row.misses - 1} more` : ""}`;
  const reachCell = row => row.largest === null
    ? `<td><span class="metric-delta" data-direction="worse">No test completed</span>${row.blocked ? `<small>${miss(row)}</small>` : ""}</td>`
    : `<td><b>${fmtTokens(row.largest)}</b> <span class="ov-unit">tokens</span><small>${row.blocked ? `&#10005; ${miss(row)}` : "Every test passed"}</small></td>`;
  const loads = rows.some(row => row.load);
  const loadCell = row => {
    if (!loads) return "";
    if (!row.load) return '<td><span class="dim">Single query only</span></td>';
    const {total, each, wait, prompt} = row.load, first = total[0], last = total[total.length - 1];
    const eachTrend = trendOf(each, "high"), waitTrend = trendOf(wait, "low");
    const gain = total.length > 1 && first.y > 0 ? last.y / first.y : null;
    const direction = gain === null ? "same" : gain >= 1.03 ? "better" : gain <= .97 ? "worse" : "same";
    // Lead with total throughput; older records only kept each request's own speed.
    const lead = eachTrend || waitTrend, span = lead ? `${lead.first.x} &rarr; ${lead.last.x} queries` : "";
    const head = gain !== null ? `<span class="ov-trend" data-direction="${direction}">${sparkline(total)}<span class="metric-delta" data-direction="${direction}">${gain.toFixed(1)}&times; total throughput</span></span>`
      : eachTrend ? `<span class="ov-trend" data-direction="${eachTrend.direction}">${sparkline(each)}${deltaMarkup(eachTrend, " per request")}</span>`
      : waitTrend ? `<span class="ov-trend" data-direction="${waitTrend.direction}">${sparkline(wait)}${deltaMarkup(waitTrend, " first-token wait")}</span>` : '<span class="dim">Not recorded</span>';
    const detail = [gain !== null ? `${n(first.y)} &rarr; ${n(last.y)} tk/s, ${first.x} &rarr; ${last.x} queries` : eachTrend ? `${n(eachTrend.first.y)} &rarr; ${n(eachTrend.last.y)} tk/s each, ${span}` : span,
      gain !== null && eachTrend ? `each request ${eachTrend.percent > 0 ? "+" : ""}${eachTrend.percent.toFixed(0)}%` : "",
      waitTrend && (gain !== null || eachTrend) ? `first token ${n(waitTrend.first.y)} &rarr; ${n(waitTrend.last.y)} s` : ""].filter(Boolean);
    return `<td>${graphLink({model: row.model, metrics: ["decode_tps", "per_request_decode_tps", "ttft_s"], axis: "concurrency"}, head, "Open the concurrency graphs")}${detail.map(line => `<small>${line}</small>`).join("")}<small>at a ${fmtTokens(prompt)} prompt</small></td>`;
  };
  const body = rows.map(row => `<tr><td class="ov-model">${graphLink({model: row.model, metrics: ["prefill_tps", "decode_tps"], axis: "context"}, esc(row.model), "Open this model's graphs")}<small>${[esc(row.quant), row.mtp ? "speculative decoding on" : "", many ? `<span class="ov-bench" style="--benchmark-color:${esc(runColor(row.run.id))}">${esc(row.run.label || row.run.id)}</span>` : ""].filter(Boolean).join(" &middot; ")}</small></td>${peakCell(row, "prefill", "prefill_tps")}${peakCell(row, "decode", "decode_tps")}${trendCell(row, "prefill", "prefill_tps")}${trendCell(row, "decode", "decode_tps")}${reachCell(row)}${loadCell(row)}</tr>`).join("");
  target.innerHTML = `<div class="ov-kpis">${kpis}</div><div class="card ov-card"><header><h3>Performance by model</h3><span class="spacer"></span><span class="hint">Click any figure to open its graph.</span></header><div class="table-wrap"><table class="ov-table"><thead><tr><th>Model</th><th class="num" title="Highest prefill speed at any prompt length, one query at a time">Peak prefill</th><th class="num" title="Highest decode speed at any prompt length, one query at a time">Peak decode</th><th title="Prefill speed from the shortest to the longest completed prompt">Prefill as prompts grow</th><th title="Decode speed from the shortest to the longest completed prompt">Decode as prompts grow</th><th title="Longest prompt that completed, and the first size that did not">Longest prompt</th>${loads ? '<th title="Total decode throughput from the fewest to the most simultaneous queries">With concurrent queries</th>' : ""}</tr></thead><tbody>${body}</tbody></table></div><p class="hint ov-foot">Speeds are for one query at a time unless stated. &#9650; and &#9660; compare the shortest prompt with the longest that completed; green is a gain, red a loss. ${many ? "Changes marked &ldquo;vs&rdquo; compare against the first benchmark listing the same model." : ""}</p></div>`;
}

async function ensureRuns(ids) {
  await Promise.all(ids.map(async (id) => {
    if (!state.runCache[id]) state.runCache[id] = sharedDisplayRun(await api("/api/runs/" + encodeURIComponent(id)));
  }));
}

function comparisonLinkIds(search, runs) {
  const raw = new URLSearchParams(search || "").get("compare");
  if (!raw || raw.length > 4096) return null;
  const ids = [...new Set(raw.split(",").map(id=>id.trim()).filter(Boolean))];
  return ids.length >= 2 && ids.length <= 8 && ids.every(id=>runs.some(run=>run.id === id)) ? ids : null;
}

async function openComparisonLink(search) {
  const ids = comparisonLinkIds(search, state.runs);
  if (!ids) return false;
  await ensureRuns(ids);
  const models = ids.map(id=>allModelsOf([state.runCache[id]]));
  state.compare = new Set(ids); state.selected = ids[0]; state.conditions = null;
  state.visibleModels = new Set(models[0].filter(model=>models.every(list=>list.includes(model))));
  state.mtpFilter = "all"; state.comparisonConcurrency = 1; state.queryLevels = null; state.promptLevels = null; state.levelsTouched = true; state.metricsTouched = true;
  state.comparisonLayout = "models"; state.comparisonAxis = "context"; state.colorBy = "benchmarks";
  state.metrics = new Set(["prefill_tps"]);
  renderMetricSeg(); renderRunsList(); showPanel("results", false); showResultsView("compare");
  await renderResultsView();
  return true;
}

function buildSeries(runs, models, allModels, key, axis, levels) {
  const metric = METRICS[key], series = [], ticks = new Set();
  const modes = state.mtpFilter === "all" ? [false, true] : [state.mtpFilter === "on"];
  for (const run of runs) for (const mtp of modes) {
    if (runProviderManaged(run) && (state.mtpFilter !== "all" || mtp)) continue;
    for (const model of models) levels.forEach((level, levelIndex) => {
      const points = axis === "context" ? pointsFor(run, model, metric, mtp, level) : pointsAcrossQueries(run, model, metric, mtp, level);
      const failures = axis === "context" ? failuresFor(run, model, mtp, level) : failuresAcrossQueries(run, model, mtp, level);
      if (!points.length && !failures.length) return;
      [...points, ...failures].forEach(item => ticks.add(item.x));
      series.push({
        id: [run.id + (run._rowConditionKey || ""), model, key, mtp, level].join("|"), run, model, mtp, level,
        levelName: levels.length > 1 ? (axis === "context" ? `${level} quer${level === 1 ? "y" : "ies"}` : `${fmtTokens(level)} prompt`) : null,
        tags: {model, run: runKey(run), level, mtp},
        ...chartAppearance(run, model, allModels, mtp, levelIndex, levels.length),
        points: points.map(point => ({...point, unit: metric.unit})), failures,
      });
    });
  }
  // A line names its benchmark only when the same model is drawn for more than one.
  for (const item of series) {
    const lineups = new Set(series.filter(other => other.model === item.model).map(other => runKey(other.run)));
    item.name = [item.model, lineups.size > 1 ? item.run.label || item.run.id : null, item.levelName, item.mtp ? "MTP" : null].filter(Boolean).join("  ·  ");
  }
  return {series, ticks: [...ticks].sort((a, b) => a - b), measured: series.some(item => item.points.length)};
}

const levelWords = (axis, values) => axis === "context"
  ? `${values.join(", ")} concurrent quer${values.length === 1 && values[0] === 1 ? "y" : "ies"}`
  : `${values.map(fmtTokens).join(", ")}-token prompts`;
/* Every passed test is accounted for: drawn, or hidden for a stated reason with a one-click remedy. */
function graphCoverage(runs, models, keys, axis, levels) {
  const out = {total: 0, drawn: 0, models: 0, levels: new Map(), unrecorded: [], unrecordedAt: new Set()};
  for (const run of runs) for (const row of run.results || []) {
    if (row.status !== "ok") continue;
    if (state.mtpFilter !== "all" && (runProviderManaged(run) || !!row.mtp !== (state.mtpFilter === "on"))) continue;
    if (runProviderManaged(run) && row.mtp) continue;
    out.total++;
    const value = axis === "context" ? row.concurrency || 1 : row.target_tokens;
    if (!models.includes(row.model)) out.models++;
    else if (!levels.includes(value)) out.levels.set(value, (out.levels.get(value) || 0) + 1);
    else if (keys.some(key => Number.isFinite(METRICS[key].get(row)))) out.drawn++;
    else { out.unrecorded.push(row); out.unrecordedAt.add(value); }
  }
  // Offer the metrics that those undrawn tests did record, nearest in meaning first.
  const order = ["per_request_decode_tps", "ttft_s", "group_output_tps", "wall_s", ...Object.keys(METRICS)];
  out.suggest = [...new Set(order)].filter(key => !keys.includes(key) && out.unrecorded.length
    && out.unrecorded.every(row => Number.isFinite(METRICS[key].get(row)))).slice(0, 3);
  // Group rates need each request's start time; older multi-query records lack it.
  out.needsOffsets = out.unrecorded.length > 0 && out.unrecorded.every(row => (row.concurrency || 1) > 1
    && (row.requests || []).some(request => !Number.isFinite(request.start_offset_s)));
  return out;
}
function coverageMarkup(coverage, keys, axis) {
  if (!coverage.total) return "";
  if (coverage.drawn === coverage.total) return `<p class="coverage-bar" data-complete="1"><strong>All ${coverage.total} passed test${coverage.total === 1 ? " appears" : "s appear"} in at least one chart below.</strong> A chart can hold fewer points: not every metric is recorded for every test, and repeats share one point.</p>`;
  const parts = [], sorted = values => [...values].sort((a, b) => a - b);
  if (coverage.levels.size) {
    const hidden = [...coverage.levels.values()].reduce((sum, count) => sum + count, 0);
    parts.push(`<li><b>${hidden}</b> ${hidden === 1 ? "is" : "are"} at ${esc(levelWords(axis, sorted(coverage.levels.keys())))}, not ticked above. <button type="button" class="btn-sm" data-coverage="levels">Show every ${axis === "context" ? "query count" : "prompt length"}</button></li>`);
  }
  if (coverage.unrecorded.length) {
    const names = keys.map(key => METRICS[key].short).join(" or ");
    parts.push(`<li><b>${coverage.unrecorded.length}</b> at ${esc(levelWords(axis, sorted(coverage.unrecordedAt)))} ${coverage.unrecorded.length === 1 ? "has" : "have"} no ${esc(names)} value.${coverage.needsOffsets ? " This benchmark did not record when each request started, so rates for the whole group cannot be worked out." : ""}${coverage.suggest.length ? ` Recorded for ${coverage.unrecorded.length === 1 ? "it" : "them"}: ${coverage.suggest.map(key => `<button type="button" class="btn-sm" data-coverage="metric" data-metric-add="${esc(key)}">+ ${esc(METRICS[key].short)}</button>`).join(" ")}` : ""}</li>`);
  }
  if (coverage.models) parts.push(`<li><b>${coverage.models}</b> belong${coverage.models === 1 ? "s" : ""} to models that are not ticked. <button type="button" class="btn-sm" data-coverage="models">Show all models</button></li>`);
  if (coverage.hidden) parts.push(`<li><b>${coverage.hidden}</b> belong${coverage.hidden === 1 ? "s" : ""} to benchmarks switched off in the key. <button type="button" class="btn-sm" data-coverage="curves">Show every benchmark</button></li>`);
  return `<div class="coverage-bar" role="status"><strong>The charts below draw ${coverage.drawn} of ${coverage.total} passed tests.</strong><ul>${parts.join("")}</ul></div>`;
}
function coverageNote(coverage, keys, axis) {
  const html = coverageMarkup(coverage, keys, axis);
  if (!html) return null;
  const complete = coverage.drawn === coverage.total;
  return {kind: "coverage", tone: complete ? "info" : "warn", html,
    gist: complete ? `All ${coverage.total} passed test${coverage.total === 1 ? "" : "s"} drawn` : `Charts draw ${coverage.drawn} of ${coverage.total} passed tests`};
}
function applyCoverageAction(button) {
  const scope = state.resultScope || {queries: [], prompts: []};
  if (button.dataset.coverage === "metric") state.metricsTouched = true;
  if (button.dataset.coverage === "levels") {
    if (state.comparisonAxis === "context") state.levelsTouched = true; else state.promptsTouched = true;
    if (state.comparisonAxis === "context") { state.queryLevels = new Set(scope.queries); state.comparisonConcurrency = scope.queries[0] ?? 1; }
    else state.promptLevels = new Set(scope.prompts);
  } else if (button.dataset.coverage === "models") state.visibleModels = null;
  else if (button.dataset.coverage === "curves") state.curveChoice.clear();
  else if (button.dataset.coverage === "metric") state.metrics.add(button.dataset.metricAdd);
  redrawResultsWithoutScrolling();
}

async function renderCompare() {
  const generation = state.resultsRender;
  const body = $("#compare-body"), keyNode = $("#chart-key"), evidence = $("#concurrency-comparison");
  $$(".plot", body).forEach(plot => Charts.release?.(plot));
  if (evidence) { $$(".plot", evidence).forEach(plot => Charts.release?.(plot)); evidence.hidden = true; }
  const message = (title, text) => { body.innerHTML = emptyState(title, text); if (keyNode) keyNode.innerHTML = ""; };
  const ids = [...state.compare];
  if (!ids.length) return message(state.runs.length ? "Select a benchmark" : "Nothing to plot yet",
    state.runs.length ? "Tick one or more benchmarks in the list on the left." : "Run a benchmark first, or import one. Its graphs appear here.");
  await ensureRuns(ids);
  if (generation !== state.resultsRender) return;
  const source = ids.map(id => state.runCache[id]).filter(Boolean);
  // Every recorded condition is offered in the key. The selection decides which
  // are on to begin with; a click in the key overrides that for one benchmark.
  const offered = splitRowConditions(availableMeasurementRuns(source));
  const selected = new Set(splitRowConditions(measurementRuns(source)).map(runKey));
  const known = new Set(offered.map(runKey));
  for (const key of [...state.curveChoice.keys()]) if (!known.has(key)) state.curveChoice.delete(key);
  const curveOn = run => state.curveChoice.has(runKey(run)) ? state.curveChoice.get(runKey(run)) : selected.has(runKey(run));
  const pool = offered.filter(run => selected.has(runKey(run)) || state.curveChoice.get(runKey(run)));
  const runs = pool.filter(curveOn);
  // Colours follow the whole selection, so hiding one benchmark does not recolour the rest.
  const allModels = allModelsOf(pool), models = shownModels(allModels), keys = selectedMetricKeys();
  if (!allModels.length) return message("No tests were recorded", `${source.length === 1 ? "This benchmark" : "These benchmarks"} stopped before completing or failing a single test${source.length === 1 && source[0].status ? ` (status: ${source[0].status})` : ""}. There is nothing to plot.`);
  if (!keys.length) return message("Choose a metric", "Tick one or more metric buttons above.");
  if (!models.length) return message("Choose a model", "Tick one or more model buttons above, or press All.");
  const axis = state.comparisonAxis, levels = axis === "context" ? activeQueryLevels() : activePromptLevels();
  const perModel = state.comparisonLayout === "models";
  const charts = keys.flatMap(key => (perModel ? models.map(model => [model]) : [models])
    .map(group => ({key, models: group, ...buildSeries(runs, group, allModels, key, axis, levels)})));
  const drawn = charts.filter(chart => chart.measured);
  const missing = keys.filter(key => !drawn.some(chart => chart.key === key));
  const sources = [...new Set(runs.map(run => run.power_source).filter(Boolean))];
  const mixedPower = keys.some(key => ["j_per_1k_prefill", "j_per_token_decode", "prefill_gpu_w"].includes(key)) && sources.length > 1;
  const coverage = graphCoverage(runs, models, keys, axis, levels);
  coverage.hidden = graphCoverage(pool.filter(run => !curveOn(run)), models, keys, axis, levels).total;
  coverage.total += coverage.hidden;
  // One key entry per benchmark with something to draw here: drawn, or switched off but drawable.
  const rate = axis === "concurrency";
  const curves = [...new Map(offered.map(run => [runKey(run), run])).keys()].map(key => {
    const members = offered.filter(run => runKey(run) === key), on = curveOn(members[0]);
    const rows = members.flatMap(run => conditionRows(run, models, rate));
    return {run: members[0], on, cases: rows.length, members,
      measured: rows.filter(row => row.status === "ok" && keys.some(metric => Number.isFinite(METRICS[metric].get(row)))).length};
  });
  const switchedOff = curves.filter(entry => !entry.on && chartConditions(entry.members, models, keys).length);
  // Nothing is drawn because the benchmarks that could draw something here are switched off.
  const allOff = !drawn.length && switchedOff.length > 0;
  const notes = notesStrip("compare", [coverageNote(coverage, keys, axis), ...source.map(run => incompleteNote(run, source.length > 1)),
    mixedPower ? plainNote("power", "warn", "Different power sensors", "Different power sensors: these readings have different measurement boundaries. Compare them with care.") : null,
    missing.length && !allOff ? {kind: "missing", tone: "info", gist: `Not recorded: ${missing.map(key => METRICS[key].short).join(", ")}`,
      html: `<p class="graph-missing">Not recorded for this selection: ${missing.map(key => esc(METRICS[key].short)).join(", ")}.</p>`} : null]);
  if (!drawn.length) {
    // The key entries of switched-off benchmarks must stay, or nothing could be switched back on.
    body.innerHTML = notes + (allOff ? emptyState("Every benchmark is switched off", "Click a benchmark in the key above to show its curves.")
      : emptyState("Nothing recorded for this selection", "Tick another metric, model or level above. Greyed-out metric buttons were not recorded in these benchmarks."));
    if (keyNode) keyNode.innerHTML = allOff ? chartKeyMarkup({allModels, models: [], runs: [], curves: switchedOff, levels: [], mtp: new Set()}, axis) : "";
    return;
  }
  const used = {allModels, models: models.filter(model => drawn.some(chart => chart.series.some(item => item.model === model))),
    runs: runs.filter(run => drawn.some(chart => chart.series.some(item => item.run === run))),
    curves: curves.filter(entry => entry.on ? drawn.some(chart => chart.series.some(item => runKey(item.run) === runKey(entry.run))) : switchedOff.includes(entry)),
    levels: levels.filter(level => drawn.some(chart => chart.series.some(item => item.level === level))),
    mtp: new Set(drawn.flatMap(chart => chart.series.map(item => item.mtp)))};
  if (keyNode) keyNode.innerHTML = chartKeyMarkup(used, axis);
  const fixed = levels.length === 1 ? (axis === "context" ? `${levels[0]} concurrent quer${levels[0] === 1 ? "y" : "ies"}` : `${fmtTokens(levels[0])} prompt`) : "";
  const direction = metric => metric.better === "high" ? "&#8593; higher is better" : metric.better === "low" ? "&#8595; lower is better" : "";
  const single = drawn.length === 1;
  const lacksOffsets = runs.some(run => (run.results || []).some(row => row.status === "ok" && (row.concurrency || 1) > 1
    && (row.requests || []).some(request => !Number.isFinite(request.start_offset_s))));
  const card = (chart, index) => {
    const metric = METRICS[chart.key], model = perModel ? chart.models[0] : null;
    const readout = model && axis === "context" && levels.length === 1 ? comparisonReadout(chartConditions(runs, [model], [chart.key]), [model], chart.key) : "";
    // A ticked value with no line is either a test that failed or a metric that was not recorded there.
    const blank = levels.filter(level => !chart.series.some(item => item.level === level && item.points.length));
    const failed = blank.filter(level => chart.series.some(item => item.level === level && item.failures.length)
      && !runs.some(run => (run.results || []).some(row => row.status === "ok" && chart.models.includes(row.model) && (axis === "context" ? row.concurrency || 1 : row.target_tokens) === level)));
    const absent = blank.filter(level => !failed.includes(level));
    const why = absent.length && axis === "context" && absent.every(level => level > 1) && ["prefill_tps", "decode_tps", "prefill_s", "decode_s"].includes(chart.key) && lacksOffsets
      ? " A rate for the whole group needs each request's start time, which this benchmark did not record." : "";
    let across = "";
    if (axis === "concurrency") {
      const counts = new Set(runs.flatMap(run => (run.results || []).filter(row => row.status === "ok" && chart.models.includes(row.model) && levels.includes(row.target_tokens)).map(row => row.concurrency || 1)));
      const plotted = new Set(chart.series.flatMap(item => item.points.map(point => point.x)));
      const lost = [...counts].filter(count => !plotted.has(count)).sort((a, b) => a - b);
      if (lost.length) across = `No ${esc(metric.short)} recorded at ${esc(levelWords("context", lost))}.${lacksOffsets && lost.every(count => count > 1) && ["prefill_tps", "decode_tps", "prefill_s", "decode_s"].includes(chart.key) ? " A rate for the whole group needs each request's start time, which this benchmark did not record." : ""} The tests passed; this metric was not captured for them.`;
    }
    const gap = [across, absent.length ? `No ${esc(metric.short)} recorded at ${esc(levelWords(axis, absent))}.${why}` : "",
      failed.length ? `Every test at ${esc(levelWords(axis, failed))} failed.` : ""].filter(Boolean).map(text => `<p class="hint graph-gap">${text}</p>`).join("");
    return `<section class="card graph-card" data-metric="${esc(chart.key)}"><header><h3>${esc(model || metric.short)}</h3><span class="graph-direction">${[direction(metric), esc(metric.unit), fixed].filter(Boolean).join(" &middot; ")}</span></header><p class="hint graph-blurb">${model ? esc(modelSubtitle(runs, model)) : esc(metric.blurb)}</p>${readout}<div class="plot" data-graph="${index}"></div>${gap}</section>`;
  };
  body.innerHTML = notes + (perModel
    ? keys.filter(key => !missing.includes(key)).map(key => `<h3 class="graph-section">${esc(METRICS[key].short)}<small>${esc(METRICS[key].blurb)}</small></h3><div class="graph-grid">${drawn.map((chart, index) => chart.key === key ? card(chart, index) : "").join("")}</div>`).join("")
    : `<div class="graph-grid${single ? " single" : ""}">${drawn.map(card).join("")}</div>`)
    + `<p class="hint graph-help">Move the pointer over a chart to compare every line at that point. &#10005; on the baseline marks a test that failed. Hover an entry in the key to pick out its lines; click a benchmark there to hide or show its curves.</p>`;
  drawn.forEach((chart, index) => {
    const metric = METRICS[chart.key];
    Charts.line($(`[data-graph="${index}"]`, body), {
      unit: metric.unit, better: metric.better, yLabel: metric.unit, yScale: "auto", series: chart.series, xTicks: chart.ticks,
      xLabel: axis === "context" ? "prompt length (tokens)" : "concurrent queries",
      xUnit: axis === "context" ? "prompt tokens" : "concurrent queries",
      xScale: axis === "context" ? "log" : "linear", breakMissing: axis !== "context",
      height: single ? 380 : 290,
      ariaLabel: `${metric.short}${perModel ? " for " + chart.models[0] : ""} by ${axis === "context" ? "prompt length" : "concurrent queries"}`,
    });
  });
  // Per-request timelines stay available where queries overlap.
  if (evidence && axis === "concurrency" && globalThis.WatteverConcurrency) {
    const mapped = {group_output_tps: "output", decode_tps: "decode", prefill_tps: "prefill", wall_s: "batchwall", ttft_s: "ttft"}[keys.find(key => !missing.includes(key))] || "decode";
    evidence.hidden = false;
    globalThis.WatteverConcurrency.render(evidence, {
      runs: measurementRuns(source), models, state: state.scaling, charts: Charts, compact: true, evidenceOnly: true, metric: mapped, prompt: levels[0],
      allow: (run, row) => state.mtpFilter === "all" || (!runProviderManaged(run) && !!row.mtp === (state.mtpFilter === "on")),
      style: group => chartAppearance(group.run, group.model, allModels, group.identity.mtp === "on" || group.identity.mtp?.requested === "on"),
    });
  }
}

function resolveColor(cssVar) {
  const match = /^var\((--[a-z0-9-]+)\)$/i.exec(cssVar);
  if (!match) return cssVar;
  return getComputedStyle(document.documentElement).getPropertyValue(match[1]).trim() || "#888";
}

function pick(run, model, size, mtp, metric, concurrency = state.comparisonConcurrency) {
  const matches = (run.results || []).filter((r) => r.model === model
    && r.target_tokens === size && !!r.mtp === !!mtp && (r.concurrency || 1) === concurrency);
  if (new Set(matches.map(row => rowConditionFingerprint(run, row))).size > 1) return null;
  const ok = matches.filter((r) => r.status === "ok");
  if (!ok.length) return matches[0] || null;
  // several repeats: report the median so one hiccup does not define the point
  const measured = metric ? ok.filter((r) => Number.isFinite(metric.get(r))) : ok;
  if (!measured.length) return ok[0];
  const sorted = measured.slice().sort((a, b) => metric ? metric.get(a) - metric.get(b) : (a.prefill_tps || 0) - (b.prefill_tps || 0));
  const representative = sorted[Math.floor(sorted.length / 2)];
  if (!metric || measured.length < 2) return representative;
  const key = Object.keys(METRICS).find(key=>METRICS[key] === metric);
  if (!key) return representative;
  const value = (metric.get(sorted[Math.floor((sorted.length-1)/2)]) + metric.get(representative))/2;
  const median = {...representative,summary_repeats:measured.length,_metricValues:{...representative._metricValues,[key]:value}};
  if (["group_output_tps", "decode_tps", "per_request_decode_tps", "prefill_tps", "decode_s", "prefill_s"].includes(key)) { median._metricValues = {...median._metricValues, [key]:value}; if (key !== "group_output_tps") median[key] = value; }
  else if (["j_per_1k_prefill","j_per_token_decode","prefill_gpu_w"].includes(key)) median.power = {...median.power,[key]:value};
  else median[key] = value;
  return median;
}

function pointNote(run, r, metric) {
  return [
    r.summary_repeats ? `Median of ${r.summary_repeats} successful repeats` : r.prompt_tokens ? `${fmtInt(r.prompt_tokens)} actual tokens` : "",
    run.storage_condition || "",
    r.gpu_offload && r.gpu_offload !== "auto" ? `offload ${r.gpu_offload}` : "",
    r.vram_peak_mb ? `${fmtInt(r.vram_peak_mb)} MB VRAM` : "",
    ([METRICS.group_output_tps, METRICS.decode_tps].includes(metric) && (r.completion_tokens_estimated || r.aggregate_completion_tokens_estimated || r.requests?.some(request => request.completion_tokens_estimated))) ? "Output tokens and throughput estimated" : "",
    ([METRICS.decode_tps,METRICS.per_request_decode_tps,METRICS.decode_s].includes(metric) && decodeApproximate(r)) ? "Approximate first-chunk token boundary; legacy intervals may include response-delivery tails. Inspect timing scope." : "",
  ].filter(Boolean).join(" · ");
}
function pointsFor(run, model, metric, mtp, concurrency = state.comparisonConcurrency) {
  const sizes = [...new Set((run.results || []).filter((r) => r.model === model
    && !!r.mtp === !!mtp && (r.concurrency || 1) === concurrency).map((r) => r.target_tokens))].sort((a, b) => a - b);
  const out = [];
  for (const size of sizes) {
    const r = pick(run, model, size, mtp, metric, concurrency);
    if (!r || r.status !== "ok") continue;
    const value = metric.get(r);
    if (value === null || value === undefined) continue;
    out.push({ x: size, y: value, note: pointNote(run, r, metric) });
  }
  return out;
}
/* The same measurement read along the other axis: one prompt length, every query count. */
function pointsAcrossQueries(run, model, metric, mtp, size) {
  const counts = [...new Set((run.results || []).filter(r => r.model === model && !!r.mtp === !!mtp && r.target_tokens === size).map(r => r.concurrency || 1))].sort((a, b) => a - b);
  const out = [];
  for (const count of counts) {
    const r = pick(run, model, size, mtp, metric, count);
    if (!r || r.status !== "ok") continue;
    const value = metric.get(r);
    if (value === null || value === undefined) continue;
    out.push({x: count, y: value, note: pointNote(run, r, metric)});
  }
  return out;
}
function failuresAcrossQueries(run, model, mtp, size) {
  return (run.results || []).filter(r => r.model === model && !!r.mtp === !!mtp && r.target_tokens === size
    && (r.status === "failed" || r.status === "skipped") && r.fail_kind !== "mtp_unsupported")
    .map(r => ({x: r.concurrency || 1, reason: (r.fail_kind || r.status || "").replace(/_/g, " ")}));
}

function failuresFor(run, model, mtp, concurrency = state.comparisonConcurrency) {
  return (run.results || [])
    .filter((r) => r.model === model && !!r.mtp === !!mtp && (r.concurrency || 1) === concurrency
      && (r.status === "failed" || r.status === "skipped")
      && r.fail_kind !== "mtp_unsupported")
    .map((r) => ({ x: r.target_tokens, reason: (r.fail_kind || r.status || "").replace(/_/g, " ") }));
}

/* A track is one line/bar colour group: a run in one MTP mode. Colour still
 * follows the run, so MTP is distinguished by a dashed stroke and by its name
 * rather than by stealing a second hue from the categorical order. */
function tracksFor(runs) {
  runs = splitRowConditions(measurementRuns(runs));
  const modes = state.mtpFilter === "all" ? [false, true] : [state.mtpFilter === "on"];
  const tracks = [];
  for (const run of runs) {
    for (const mtp of modes) {
      if (runProviderManaged(run) && (state.mtpFilter !== "all" || mtp)) continue;
      const rows = (run.results || []).filter((r) => !!r.mtp === !!mtp && levelOn(r.concurrency || 1));
      const meaningful = rows.some((r) => r.status === "ok" || r.fail_kind !== "mtp_unsupported");
      if (!rows.length || !meaningful) continue;
      const bothShown = modes.length > 1 && !runProviderManaged(run);
      tracks.push({
        run, mtp,
        key: run.id + (run._rowConditionKey || "") + (mtp ? "::mtp" : ""),
        name: (run.label || run.id) + (mtp ? "  · MTP" : (bothShown ? "  · no MTP" : "")),
        color: resolveColor(runColor(run.id)),
        dash: run.storage_alternative ? "7 4" : mtp ? "6 4" : null,
      });
    }
  }
  return tracks;
}

function quantizationLabel(model) {
  const identity = model.model_configuration?.quantization || {};
  const raw = {...model, id: model.id || model.model,
    quantization: model.quantization || identity.value,
    quantization_method: model.quantization_method || identity.method};
  const normalized = globalThis.WatteverModelMetadata?.normalize(raw) || raw;
  const method = normalized.quantization_method || String(raw.id || "").match(/(?:^|[-_./@])(QAT|QAD|UD)(?=$|[-_./@])/i)?.[1]?.toUpperCase();
  return [normalized.quantization && normalized.quantization !== "unknown" ? normalized.quantization : "Quant unknown", method].filter(Boolean).join(" · ");
}

function modelSubtitle(runs, model) {
  return [...new Set(runs.filter(run=>(run.results || []).some(row=>row.model === model)).map(run => {
    const hit = (run.results || []).find(row => row.model === model && row.quantization);
    return hit ? quantizationLabel(hit) : "Quant unknown";
  }))].join(" / ");
}

function metricEstimateMark(row, metric) {
  if([METRICS.decode_tps,METRICS.per_request_decode_tps,METRICS.decode_s,METRICS.j_per_token_decode].includes(metric) && decodeApproximate(row))return '*';
  if (metric === METRICS.group_output_tps || metric === METRICS.decode_tps || metric === METRICS.j_per_token_decode) {
    return estimatedMark(row) || estimatedMark(row, true);
  }
  return "";
}

function tableRowColor(run, model, models) {
  return state.colorBy !== "benchmarks" ? modelColor(model, models) : resolveColor(runColor(run.id));
}

function compareTable(tracks, models, keys = selectedMetricKeys()) {
  const rows = [], levels = activeQueryLevels(), several = levels.length > 1;
  for (const model of models) {
    const sizes = [...new Set(tracks.flatMap(track => (track.run.results || [])
      .filter(row => row.model === model && !!row.mtp === !!track.mtp && levels.includes(row.concurrency || 1))
      .map(row => row.target_tokens)))].sort((a, b) => a - b);
    for (const size of sizes) for (const level of levels) {
      for (const track of tracks) {
        const representative = pick(track.run, model, size, track.mtp, METRICS[keys[0]], level);
        if (!representative) continue;
        const color = tableRowColor(track.run, model, models);
        const cells = keys.map(key => {
          const metric = METRICS[key];
          const row = pick(track.run, model, size, track.mtp, metric, level);
          const value = row && row.status === "ok" ? metric.get(row) : null;
          const absent = row?.fail_kind === "timeout" ? `Timeout${row.timeout_budget_s || row.wall_s ? `<small>${fmt(row.timeout_budget_s || row.wall_s,0)} s limit</small>` : ""}` : "\u2014";
          return `<td class="num measure-cell" data-measure="${key}">${value == null ? absent : (["wall_s","ttft_s"].includes(key) ? fmt(value,2) : n(value)) + metricEstimateMark(row, metric)}</td>`;
        }).join("");
        rows.push(`<tr class="colored-result-row" style="--table-color:${color}" data-model="${esc(model)}" data-benchmark="${esc(track.run.id)}"><td class="model-cell">${esc(model)}<small>${esc(quantizationLabel(representative))}</small></td><td class="num">${fmtTokens(size)}</td>${several ? `<td class="num">${level}</td>` : ""}<td class="benchmark-cell"><span class="table-benchmark-name" title="${esc(configurationFullTitle(track.run))}">${esc(track.name)}</span></td>${cells}<td>${statusCell(representative)}</td></tr>`);
      }
    }
  }
  if (!rows.length) return '<p class="dim">No measurements for the selected models and query count.</p>';
  return `<table class="measurements-table"><thead><tr><th>Model</th><th>Prompt tokens</th>${several ? "<th>Queries</th>" : ""}<th>Benchmark</th>${keys.map(key => `<th class="num" data-measure="${key}">${esc(METRICS[key].label)} <small>(${esc(METRICS[key].unit)})</small></th>`).join("")}<th>Status</th></tr></thead><tbody>${rows.join("")}</tbody><tfoot><tr><td colspan="${4 + keys.length + (several ? 1 : 0)}">One row per model, prompt size${several ? ", query count" : ""} and benchmark${several ? "" : `, at ${levels[0]} concurrent quer${levels[0] === 1 ? "y" : "ies"}`}. Each measure reports its median successful repeat. * Estimated token counts. Cell colors follow the Color by selection.</td></tr></tfoot></table>`;
}

if (typeof window !== "undefined") window.WatteverUI = {
  api, connection:helperEndpointConfig, metadata:publicRunMetadata, config:collectConfig, browserMode, showPanel,
  telemetryMarkup,bindTelemetry,
  models:()=>state.env?.models || [],
  selectedModels:()=>[...state.models],
  ready:()=>!state.providerNeedsConnection && (!genericHelper() || state.endpointSignature === helperConnectionSignature()),
};
init();
