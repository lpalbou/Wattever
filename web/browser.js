/* Static-page backend. Credentials remain in this closure, never in storage. */
(function (root) {
  "use strict";
  const KEY = "wattever.browser.runs.v1";
  const HIDDEN_KEY = "wattever.browser.hidden-community.v1";
  let community = [], communityError = null, communityPromise = null, hiddenIds = null, communityGeneration = 0;
  const sizes = [512, 5000, 10000, 20000, 40000, 80000, 100000, 120000, 150000, 200000, 260000];
  const levels = [1,2,3,4,5,6,7,8,9,10,15,20,30,40,50,75,100];
  const defaults = {models: [], sizes: [512,5000,10000,20000,40000], concurrency_levels: [1], max_tokens: 256, repeats: 1, mtp_modes: ["off"], warmup: false, disable_thinking: null, per_test_timeout_s: 1800, context_policy: "provider_managed"};
  let endpoint = "http://localhost:1234/v1", apiKey = "", hardware = {}, models = [], connected = false;
  let active = null, running = false, cancelling = false, events = [], live = {}, controllers = new Set();
  let cachedRuns = null, storageError = null;
  const random = () => (root.crypto && root.crypto.randomUUID ? root.crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2));
  const object = v => !!v && typeof v === 'object' && !Array.isArray(v);
  const optionalObject = v => v == null || object(v);
  const optionalString = v => v == null || typeof v === 'string';
  function valid(run) {
    return object(run) && optionalString(run.id) && optionalString(run.label) && optionalString(run.notes) && optionalString(run.status) && optionalString(run.created_at) &&
      ['config','gpu','host','hardware','application','classification','idle'].every(k => optionalObject(run[k])) &&
      Array.isArray(run.results) && run.results.every(row => object(row) && optionalString(row.model) && optionalString(row.status) &&
        ['target_tokens','concurrency','repeat'].every(k => row[k] == null || (Number.isFinite(row[k]) && row[k] >= 0)) &&
        (row.requests == null || (Array.isArray(row.requests) && row.requests.every(object))) &&
        ['power','group_power'].every(k => optionalObject(row[k])));
  }
  function read() {
    if (cachedRuns === null) {
      try { const value = JSON.parse(root.localStorage.getItem(KEY) || '[]'); cachedRuns = Array.isArray(value) ? value.filter(valid) : []; }
      catch (_) { cachedRuns = []; storageError = 'Browser storage is unavailable; export results before closing this page.'; }
      cachedRuns = cachedRuns.map(r => r.status === 'running' ? Object.assign({}, r, {status:'interrupted'}) : r);
    }
    return cachedRuns;
  }
  function save(runs) { cachedRuns = runs; try { root.localStorage.setItem(KEY, JSON.stringify(runs)); storageError = null; } catch (_) { storageError = 'Browser storage is unavailable or full; export results before closing this page.'; } }
  function put(run) { const runs = read().filter(r => r.id !== run.id); runs.push(run); save(runs); }
  function hidden() {
    if (hiddenIds === null) {
      try { const ids = JSON.parse(root.localStorage.getItem(HIDDEN_KEY) || '[]'); hiddenIds = new Set(Array.isArray(ids) ? ids.filter(x => typeof x === 'string') : []); }
      catch (_) { hiddenIds = new Set(); storageError = 'Browser storage is unavailable'; }
    }
    return hiddenIds;
  }
  function hideCommunity(id) {
    hidden().add(id);
    try { root.localStorage.setItem(HIDDEN_KEY, JSON.stringify([...hiddenIds])); }
    catch (_) { storageError = 'Browser storage is unavailable'; }
  }
  async function loadCommunity(force = false) {
    if (!root.WATTEVER_STATIC) return [];
    if (communityPromise && !force) return communityPromise;
    const generation = ++communityGeneration;
    const controller = new AbortController();
    let timer;
    const promise = (async () => {
      try {
        const work = (async () => {
        const base = new URL('benchmarks/', document.baseURI);
        const response = await fetch(new URL('index.json', base), {credentials:'omit', redirect:'error', signal:controller.signal, cache:'no-cache'});
        if (!response.ok) throw new Error('Catalog unavailable');
        const manifest = await response.json();
        if (!manifest || manifest.version !== 1 || !Array.isArray(manifest.benchmarks)) throw new Error('Invalid catalog');
        const seen = new Set();
        const entries = await Promise.all(manifest.benchmarks.map(async item => {
          if (!item || typeof item.id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$/.test(item.id) || item.file !== item.id + '.json' || !/^[a-f0-9]{64}$/.test(item.sha256) || seen.has(item.id)) throw new Error('Invalid catalog entry');
          seen.add(item.id);
          const fileUrl = new URL(item.file, base);
          fileUrl.searchParams.set('sha256', item.sha256);
          const file = await fetch(fileUrl, {credentials:'omit', redirect:'error', signal:controller.signal});
          if (!file.ok) throw new Error('Shared benchmark unavailable');
          const bytes = await file.arrayBuffer();
          if (!root.crypto?.subtle) throw new Error('Cannot verify shared benchmark');
          const hash = [...new Uint8Array(await root.crypto.subtle.digest('SHA-256', bytes))].map(b => b.toString(16).padStart(2,'0')).join('');
          if (hash !== item.sha256) throw new Error('Shared benchmark verification failed');
          const run = JSON.parse(new TextDecoder().decode(bytes));
          if (!valid(run) || run.id !== item.id) throw new Error('Invalid shared benchmark');
          return Object.assign({}, run, run.status === "running" ? {status:"interrupted", source_status:"running"} : {}, {community:true});
        }));
        return entries;
        })();
        const timeout = new Promise((_, reject) => { timer = setTimeout(() => { controller.abort(); reject(new Error('Catalog timed out')); }, 10000); });
        const entries = await Promise.race([work, timeout]);
        if (generation === communityGeneration) { community = entries; communityError = null; }
      } catch (_) { if (generation === communityGeneration) communityError = 'Shared benchmarks are unavailable or could not be verified. Your local results are still available.'; }
      finally { clearTimeout(timer); }
      return community;
    })();
    communityPromise = promise;
    return promise;
  }
  function mergedRuns() {
    const local = read(), localIds = new Set(local.map(r => r.id));
    return [...local, ...community.filter(r => !localIds.has(r.id) && !hidden().has(r.id))];
  }
  function exportRun(run) { const copy = {...run}; delete copy.community; return copy; }
  function summary(run) { const rows = run.results || []; return {community:!!run.community, id:run.id, label:run.label, notes:run.notes, created_at:run.created_at, status:run.status, gpu:run.gpu || {}, host:run.host || {}, hardware:run.hardware || {}, config:run.config || {}, models:[...new Set(rows.map(r=>r.model).filter(Boolean))], sizes:[...new Set(rows.map(r=>r.target_tokens).filter(Boolean))], concurrency_levels:[...new Set(rows.map(r=>r.concurrency || 1))], n_results:rows.length, n_ok:rows.filter(r=>r.status === "ok").length, n_failed:rows.filter(r=>r.status !== "ok").length}; }
  function status() { return {running, cancelling, run_id:active && active.id, run:active && summary(active), live, storage_error:storageError}; }
  function emit(kind, extra) { events.push(Object.assign({kind, seq:events.length}, extra)); }
  function configure(config) {
    if (running) throw new Error("Wait for the active benchmark to finish before changing connection.");
    const url = new URL(config.endpoint || "http://localhost:1234/v1");
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error("Endpoint must be an HTTP(S) URL without credentials, query or fragment.");
    url.pathname = url.pathname.replace(/\/+$/, "").replace(/\/v1$/, "") + "/v1";
    endpoint = url.toString().replace(/\/$/, ""); apiKey = String(config.apiKey || "");
    const h = config.hardware && typeof config.hardware === "object" ? config.hardware : {};
    const memory = v => Number.isFinite(Number(v)) && Number(v) > 0 ? Number(v) : null;
    hardware = {cpu:String(h.cpu || ""), gpu:String(h.gpu || ""), ram_gb:memory(h.ram_gb), vram_gb:memory(h.vram_gb)};
    models = []; connected = false;
  }
  async function provider(path, options) {
    const headers = Object.assign({"Content-Type":"application/json"}, options.headers || {});
    if (apiKey) headers.Authorization = "Bearer " + apiKey;
    let response;
    const hostname = new URL(endpoint).hostname.toLowerCase();
    const local = hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '[::1]';
    try { response = await fetch(endpoint + path, Object.assign({}, options, {headers, credentials:"omit"}, local ? {targetAddressSpace:'loopback'} : {})); }
    catch (e) { if (e.name === "AbortError") throw e; throw new Error("Cannot reach endpoint. Check URL, CORS and browser local network permission."); }
    if (!response.ok) { const error = new Error(response.status === 401 || response.status === 403 ? "Endpoint rejected authorization. Check the API key." : "Endpoint returned HTTP " + response.status + "."); if (response.status === 401 || response.status === 403) error.auth = true; throw error; }
    return response;
  }
  async function connect() {
    const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 20000);
    let data; try { const response = await provider('/models', {method:'GET', signal:controller.signal}); data = await response.json(); }
    catch (e) { if (e.name === 'AbortError') throw new Error('Connection timed out. Check the endpoint and browser permissions.'); if (e.auth) throw e; if (e instanceof SyntaxError) throw new Error('Endpoint returned an invalid model list.'); throw e; }
    finally { clearTimeout(timer); }
    if (!data || !Array.isArray(data.data)) throw new Error('Endpoint returned an invalid model list.');
    models = data.data.filter(m => m && typeof m.id === 'string' && m.id).map(m => ({id:m.id, type:'llm'}));
    connected = true; return {models};
  }
  function importRun(input) {
    let run; try { run = typeof input === 'string' ? JSON.parse(input) : JSON.parse(JSON.stringify(input)); } catch (_) { throw new Error('Invalid benchmark JSON.'); }
    if (!valid(run)) throw new Error('Invalid benchmark structure or result rows.');
    delete run.community;
    const existingId = run.id;
    const collides = id => read().some(r => r.id === id) || community.some(r => r.id === id);
    if (!run.id || collides(run.id)) { if (existingId) run.source_run_id = existingId; run.id = 'import-' + random(); }
    while (collides(run.id)) run.id = 'import-' + random();
    if (run.status === 'running') run.status = 'interrupted';
    put(run); return run;
  }
  function env() { return {gpus:[], gpu:{name:hardware.gpu || null, vram_total_mb:hardware.vram_gb ? hardware.vram_gb*1024 : null}, platform:'browser', host:{cpu:hardware.cpu || null, ram_gb:hardware.ram_gb || null}, hardware:{cpu:{name:hardware.cpu || null}, system:{memory_bytes:hardware.ram_gb ? hardware.ram_gb*1073741824 : null}, gpus:hardware.gpu ? [{name:hardware.gpu, memory_bytes:hardware.vram_gb ? hardware.vram_gb*1073741824 : null}] : [], source:'manual'}, corpus:{approx_tokens:null}, power:{source:'none', readable:false}, lms:{cli:false, server:connected, version:null}, models, default_models:models.map(m=>m.id), missing_defaults:[], defaults, capabilities:{resume:false,extend:false,context_planning:false}, size_presets:sizes, concurrency_presets:levels, last_config:null, running, storage_error:storageError}; }
  async function stream(model, prompt, cfg) {
    const controller = new AbortController(); controllers.add(controller);
    let timedOut = false; const timer = setTimeout(() => { timedOut = true; controller.abort(); }, cfg.per_test_timeout_s * 1000);
    const start = performance.now(); let first = null, end = null, output = '', usage = null, done = false, finish = null;
    try {
      const response = await provider('/chat/completions', {method:'POST', signal:controller.signal, body:JSON.stringify({model, messages:[{role:'user', content:prompt}], max_tokens:cfg.max_tokens, stream:true, stream_options:{include_usage:true}})});
      if (!response.body || !response.body.getReader) throw new Error('Endpoint did not provide a streaming response.');
      const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = '', dataLines = [];
      function dispatch() {
        if (!dataLines.length) return;
        const value = dataLines.join('\n'); dataLines = [];
        if (value === '[DONE]') { done = true; return; }
        let chunk; try { chunk = JSON.parse(value); } catch (_) { throw new Error('Invalid streaming response.'); }
        if (chunk.error) throw new Error('Endpoint reported a streaming error.');
        if (chunk.usage) usage = chunk.usage;
        for (const choice of chunk.choices || []) {
          const delta = choice.delta && (choice.delta.content || choice.delta.reasoning_content);
          if (typeof delta === 'string' && delta) { if (first === null) first = performance.now(); output += delta; end = performance.now(); }
          if (choice.finish_reason) finish = choice.finish_reason;
        }
      }
      function line(value) { if (value === '') dispatch(); else if (value.startsWith('data:')) dataLines.push(value.slice(5).replace(/^ /,'')); }
      while (true) { const part = await reader.read(); buffer += decoder.decode(part.value || new Uint8Array(), {stream:!part.done}); let i; while ((i = buffer.indexOf('\n')) >= 0) { line(buffer.slice(0,i).replace(/\r$/, '')); buffer = buffer.slice(i+1); } if (part.done) break; }
      if (buffer) line(buffer.replace(/\r$/, '')); dispatch();
      if (!done || !finish || first === null) throw new Error('Streaming response ended before completion.');
      const ttft = (first-start)/1000, decode = (end-first)/1000, elapsed = (performance.now()-start)/1000;
      const exactPrompt = usage && Number.isSafeInteger(usage.prompt_tokens) && usage.prompt_tokens > 0 ? usage.prompt_tokens : null;
      const exactCompletion = usage && Number.isSafeInteger(usage.completion_tokens) && usage.completion_tokens > 0 ? usage.completion_tokens : null;
      const completion = exactCompletion === null ? Math.max(1, Math.ceil(output.length / 4)) : exactCompletion;
      return {status:'ok', prompt_tokens:exactPrompt, completion_tokens:completion, completion_tokens_estimated:exactCompletion === null, ttft_s:ttft, wall_s:elapsed, prefill_tps:exactPrompt !== null && ttft > 0 ? exactPrompt/ttft : null, decode_tps:decode > 0 && completion > 1 ? (completion-1)/decode : null};
    } catch (e) { if (timedOut) return {status:'failed', failure:'timeout', message:'Request timed out; later cases stopped because the provider may still be running.'}; if (e.name === 'AbortError') return {status:'cancelled', message:'Cancelled'}; return {status:'failed', failure:e.auth ? 'authentication' : 'error', message:e.message || 'Request failed.'}; }
    finally { clearTimeout(timer); controllers.delete(controller); }
  }
  async function execute(run, cfg) {
    try {
      const response = await fetch(new URL('corpus.md', document.baseURI), {credentials:'omit'});
      if (!response.ok) throw new Error('Local corpus is unavailable.');
      const corpus = await response.text(); if (!corpus.trim()) throw new Error('Local corpus is empty.');
      run.corpus = {source:'bundled corpus.md', sha256:null};
      if (root.crypto && root.crypto.subtle && typeof TextEncoder !== 'undefined') {
        try { const digest = await root.crypto.subtle.digest('SHA-256', new TextEncoder().encode(corpus)); run.corpus.sha256 = Array.from(new Uint8Array(digest), x=>x.toString(16).padStart(2,'0')).join(''); } catch (_) { /* hash unavailable */ }
      }
      outer: for (const model of cfg.models) for (const concurrency of cfg.concurrency_levels) for (const size of cfg.sizes) for (let repeat=0; repeat<cfg.repeats; repeat++) {
        if (cancelling) break outer;
        live = Object.assign({}, live, {phase:'inference', model, size});
        const target = Math.max(0, Math.ceil(size*4.6)-64);
        const prompt = corpus.repeat(Math.ceil(target/corpus.length)).slice(0,target);
        const began = performance.now();
        const requests = await Promise.all(Array.from({length:concurrency}, () => stream(model, 'Benchmark nonce: ' + random() + '\n' + prompt, cfg)));
        const duration = (performance.now()-began)/1000, ok = requests.filter(x=>x.status === 'ok');
        const row = {model, target_tokens:size, concurrency, repeat, mtp:null, status:ok.length === concurrency ? 'ok' : (cancelling ? 'cancelled' : 'failed'), requests, completed_requests:ok.length};
        if (concurrency === 1) Object.assign(row, requests[0]);
        if (ok.length === concurrency && duration > 0) { row.aggregate_completion_tps = ok.reduce((sum,x)=>sum+x.completion_tokens,0)/duration; row.aggregate_completion_tokens_estimated = ok.some(x=>x.completion_tokens_estimated); }
        run.results.push(row); put(run); live.done++; emit('result',{result:row,done:live.done});
        if (requests.some(x=>x.failure === 'timeout')) { run.error = 'Stopped after a timeout; the provider may still be running.'; break outer; }
        if (requests.some(x=>x.failure === 'authentication')) { run.error = 'Endpoint rejected authorization; benchmark stopped.'; break outer; }
      }
      run.status = cancelling ? 'cancelled' : run.error ? 'error' : 'completed';
    } catch (e) { run.status = 'error'; run.error = e.message || 'Benchmark failed.'; }
    finally { run.finished_at = new Date().toISOString(); running = false; live.phase = 'idle'; put(run); emit('done',{run_id:run.id,status:run.status}); }
  }
  function start(input) {
    if (running) throw new Error('A benchmark is already running.');
    if (!connected) throw new Error('Connect to the endpoint first.');
    const cfg = Object.assign({}, defaults);
    for (const key of ['models','sizes','concurrency_levels','max_tokens','repeats','mtp_modes','model_modes','per_test_timeout_s','disable_thinking','label','notes']) if (Object.prototype.hasOwnProperty.call(input,key)) cfg[key] = input[key];
    if (!Array.isArray(cfg.models) || !cfg.models.length || cfg.models.some(id=>!models.some(m=>m.id===id))) throw new Error('Select installed models.');
    if ((cfg.mtp_modes || []).some(x=>x !== 'off') || Object.values(cfg.model_modes || {}).some(a=>a.some(x=>x !== 'off'))) throw new Error('MTP is not supported in browser mode.');
    for (const key of ['sizes','concurrency_levels']) if (!Array.isArray(cfg[key]) || !cfg[key].length || cfg[key].some(n=>!Number.isInteger(n) || n < 1 || n > (key === 'sizes' ? 1000000 : 100))) throw new Error('Invalid workload selection.');
    for (const key of ['max_tokens','repeats','per_test_timeout_s']) if (!Number.isInteger(cfg[key]) || cfg[key] < 1 || cfg[key] > (key === 'per_test_timeout_s' ? 7200 : key === 'repeats' ? 100 : 1000000)) throw new Error('Invalid ' + key + '.');
    cfg.sizes = [...new Set(cfg.sizes)].sort((a,b)=>a-b); cfg.concurrency_levels = [...new Set(cfg.concurrency_levels)].sort((a,b)=>a-b);
    cfg.context_policy = 'provider_managed'; cfg.warmup = false; cfg.mtp_modes = []; cfg.mtp_control = 'provider_managed'; cfg.disable_thinking = null;
    const run = {id:'browser-'+random(), created_at:new Date().toISOString(), label:String(cfg.label || 'Browser benchmark'), notes:String(cfg.notes || ''), status:'running', measurement_mode:'browser', browser_schema_version:1, application:{name:'Wattever',version:root.WATTEVER_VERSION || '2.4.1',protocol_version:'browser-1'}, hardware:env().hardware, host:env().host, gpu:env().gpu, config:cfg, results:[]};
    active=run; running=true; cancelling=false; events=[]; live={phase:'starting',done:0,total:cfg.models.length*cfg.sizes.length*cfg.concurrency_levels.length*cfg.repeats}; put(run); execute(run,cfg); return {run_id:run.id};
  }
  async function request(path, options = {}) {
    const url = new URL(path, 'http://local.invalid'), route = url.pathname, method = (options.method || 'GET').toUpperCase();
    let body = {}; if (options.body) { try { body = JSON.parse(options.body); } catch (_) { throw new Error('Invalid JSON request.'); } }
    if (route === '/api/version') return {application:'Wattever',version:root.WATTEVER_VERSION || '2.4.1'};
    if (route === '/api/env') return env();
    if (route === '/api/models') return {models};
    if (route === '/api/status') return status();
    if (route === '/api/events') { const cursor = Math.max(0, Number(url.searchParams.get('cursor')) || 0); return {events:events.slice(cursor),cursor:events.length,status:status()}; }
    if (route === '/api/runs' && method === 'GET') { await loadCommunity(); return {runs:mergedRuns().map(summary),storage_error:storageError,community_error:communityError}; }
    if (route === '/api/import' && method === 'POST') { await loadCommunity(); const items = Array.isArray(body.runs) ? body.runs : [body]; if (!items.every(valid)) throw new Error('Invalid benchmark structure or result rows.'); return {runs:items.map(importRun),storage_error:storageError}; }
    if (route === '/api/export.json' && method === 'GET') { await loadCommunity(); const ids=(url.searchParams.get('ids') || '').split(',').filter(Boolean); return {runs:mergedRuns().filter(r=>!ids.length || ids.includes(r.id)).map(exportRun)}; }
    if (route === '/api/export.csv' && method === 'GET') { await loadCommunity(); const ids=(url.searchParams.get('ids') || '').split(',').filter(Boolean); const quote = v => '"' + String(v == null ? '' : v).replace(/"/g,'""') + '"'; return [['run_id','model','target_tokens','concurrency','status','prefill_tps','decode_tps','aggregate_completion_tps','measurement_mode','protocol_version','completion_tokens_estimated','aggregate_completion_tokens_estimated'].join(','), ...mergedRuns().filter(r=>!ids.length || ids.includes(r.id)).flatMap(r=>(r.results || []).map(x=>[r.id,x.model,x.target_tokens,x.concurrency,x.status,x.prefill_tps,x.decode_tps,x.aggregate_completion_tps,r.measurement_mode || 'helper',(r.application || {}).protocol_version,x.completion_tokens_estimated,x.aggregate_completion_tokens_estimated].map(quote).join(',')))].join('\n'); }
    if (route === '/api/bench/start' && method === 'POST') return start(body);
    if (route === '/api/bench/cancel' && method === 'POST') { cancelling=true; controllers.forEach(c=>c.abort()); return {ok:true}; }
    const match = route.match(/^\/api\/runs\/([^/]+)(?:\/(meta|download))?$/);
    if (match) { const id=decodeURIComponent(match[1]); let run=(active && active.id===id ? active : read().find(r=>r.id===id)); if (!run) { await loadCommunity(); run=mergedRuns().find(r=>r.id===id); } if (!run) throw new Error('No such benchmark.');
      if (method === 'DELETE') { if (run.community) { hideCommunity(id); return {deleted:true,storage_error:storageError}; } if (running && active.id===id) throw new Error('Stop the active benchmark before deleting it.'); save(read().filter(r=>r.id!==id)); if (active && active.id===id) active=null; return {deleted:true,storage_error:storageError}; }
      if (method === 'POST' && match[2] === 'meta') { if (run.community) throw new Error('Shared benchmarks are read-only. Download and import a copy to edit.'); if (running && active.id===id) throw new Error('Wait for the active benchmark to finish.'); if (body.label != null) run.label=String(body.label); if (body.notes != null) run.notes=String(body.notes); put(run); return summary(run); }
      if (method === 'GET') return match[2] === 'download' ? exportRun(run) : run;
    }
    throw new Error('This operation is unavailable in browser mode.');
  }
  root.WatteverBrowser = {enabled:root.WATTEVER_STATIC === true, request, configure, connect, importRun, loadCommunity};
})(window);
