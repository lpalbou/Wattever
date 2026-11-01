/* Static-page backend. Credentials remain in this closure, never in storage. */
(function (root) {
  "use strict";
  const KEY = "wattever.browser.runs.v1";
  const HIDDEN_KEY = "wattever.browser.hidden-community.v1";
  let community = [], communityError = null, communityPromise = null, hiddenIds = null, communityGeneration = 0;
  const sizes = [512, 5000, 10000, 20000, 40000, 80000, 100000, 120000, 150000, 200000, 260000];
  const levels = [1,2,3,4,5,6,7,8,9,10,15,20,30,40,50,75,100];
  const defaults = {models: [], sizes: [512,5000,10000,20000,40000], concurrency_levels: [1], max_tokens: 256, repeats: 1, mtp_modes: ["off"], warmup: false, disable_thinking: null, per_test_timeout_s: 1800, context_policy: "provider_managed", endpoint_reasoning_effort:"none",token_limit_field:"max_tokens",temperature:0};
  let endpoint = "http://localhost:1234/v1", apiKey = "", hardware = {}, models = [], connected = false;
  let detectedEndpoint = null, endpointNameOverride = '';
  const ROUTES_KEY = 'wattever.browser.endpoint-routes.v1', memoryRoutes = new Map();
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
  // Match wattever/privacy.py: imported extras receive the same protection as
  // newly captured runs. Arbitrary prose still needs human review before sharing.
  const localDetail = /(?:\b[A-Za-z]:[\\/]|\\\\[^\\\s]+\\|\/(?:Users|home|root|tmp|private|mnt|media|var|opt)\/|(?:^|\s)\/(?:[^\s/]+\/)+[^\s]+|file:\/\/|\bWinError\s*\d+)/i;
  const networkDetail = /(?:https?:\/\/[^\s<>"']+|\b(?:\d{1,3}\.){3}\d{1,3}\b|\[[0-9a-f:]*:[0-9a-f:]*\]|(?:^|[^0-9a-z])(?:[0-9a-f]{0,4}:){3,}[0-9a-f]{0,4}(?:%[\w.-]+)?|\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+|\bsk-[A-Za-z0-9_-]{12,})/i;
  const diagnosticKeys = new Set(['error','message','log','traceback','exception','stderr','stdout']);
  const privateKeys = new Set(['authorization','proxyauthorization','apikey','accesstoken','refreshtoken','password','passwd','secret','clientsecret','credentials','credential','token','authtoken','hftoken','cookie','setcookie','headers','requestheaders','responseheaders','environment','environ','env','endpoint','endpointurl','baseurl','serverurl','hostname','hostaddress','ip','ipaddress','ipv4','ipv6','macaddress','username','userprofile','computername','machinename','serial','serialnumber','uuid','deviceuuid','gpuuuid','machineid','deviceid','connection','connectionconfig']);
  function privateKey(key) {
    const normalized = key.toLowerCase().replace(/[^a-z0-9]/g, '');
    return ['user','auth','authentication','bearer','key','accesskey','privatekey','serveraddress'].includes(normalized) || privateKeys.has(normalized) || /(?:^|_)(?:TOKEN|PASSWORD|SECRET|API_KEY)$/i.test(key) || ['apikey','password','passwd','secret','accesstoken','refreshtoken','authorization','serialnumber','deviceuuid','gpuuuid','endpointurl','baseurl','apitoken','authtoken','sessiontoken','idtoken','apikeyenv'].some(suffix=>normalized.endsWith(suffix));
  }
  function publicSource(value) {
    if (typeof value !== 'string' || /\s|\b(?:Bearer|Basic)\b|\bsk-[A-Za-z0-9_-]{12,}/i.test(value)) return false;
    try {
      const url = new URL(value), host = url.hostname.toLowerCase();
      return ['http:','https:'].includes(url.protocol) && /^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$/.test(host) &&
        !/\.(?:local|localhost|internal|test|invalid|example|lan|home|corp)$/.test(host) &&
        !(url.username || url.password || url.search || url.hash);
    } catch (_) { return false; }
  }
  function publicProjection(value, path = []) {
    if (Array.isArray(value)) return value.map(item=>publicProjection(item,path));
    if (object(value)) {
      if (safeConfiguration(value)) return JSON.parse(JSON.stringify(value));
      const result = {}; let redacted = false, storageAccess = false;
      for (const [key,item] of Object.entries(value)) {
        if (localDetail.test(key) || networkDetail.test(key) || privateKey(key) || (key.toLowerCase() === 'host' && typeof item === 'string')) { redacted = true; continue; }
        if (diagnosticKeys.has(key.toLowerCase())) { redacted = true; if (/\bWinError\s*5\b/i.test(String(item))) storageAccess = true; continue; }
        if (key === 'source' && path.includes('performance_claims') && publicSource(item)) { result[key] = item; continue; }
        if (typeof item === 'string' && localDetail.test(item)) {
          result[key] = '[local detail redacted]'; redacted = true;
          if (/\bWinError\s*5\b/i.test(item)) storageAccess = true;
        } else if (typeof item === 'string' && networkDetail.test(item)) {
          result[key] = '[network or credential detail redacted]'; redacted = true;
        } else Object.defineProperty(result, key, {value:publicProjection(item,[...path,key]),enumerable:true,writable:true,configurable:true});
      }
      if (redacted) result.diagnostic_redacted = true;
      if (storageAccess) result.diagnostic_category = 'storage_access';
      return result;
    }
    if (typeof value === 'string' && localDetail.test(value)) return '[local detail redacted]';
    if (typeof value === 'string' && networkDetail.test(value)) return '[network or credential detail redacted]';
    return value;
  }
  function safeConfiguration(value) {
    if (!object(value) || value.fingerprint_version !== 'wattever-model-configuration-v1' || !/^[a-f0-9]{64}$/.test(value.configuration_id || '')) return false;
    const pair=['value','source'], shapes={model:pair,request_model:pair,endpoint:['id','label','provider','source','scope'],quantization:['value','source','user_reported'],engine:pair,engine_version:pair,model_revision:pair,model_source:pair,kv_cache_dtype:pair,context_tokens:pair,mtp_draft_tokens:pair,reasoning:['requested','effective','effective_source','user_reported'],mtp:['requested','effective','effective_source','user_reported']};
    if (object(value.quantization) && 'method' in value.quantization) shapes.quantization.push('method','method_source');
    const keys=[...Object.keys(shapes),'fingerprint_version','configuration_id'];
    if (Object.keys(value).length!==keys.length || Object.keys(value).some(k=>!keys.includes(k))) return false;
    return Object.entries(shapes).every(([key,allowed])=>object(value[key]) && Object.keys(value[key]).length===allowed.length && Object.entries(value[key]).every(([k,v])=>allowed.includes(k) && ((typeof v==='string' && v.length<=250 && (!localDetail.test(v) && !networkDetail.test(v) || key==='model_source' && k==='value' && publicSource(v))) || (k==='value' && ['context_tokens','mtp_draft_tokens'].includes(key) && Number.isSafeInteger(v) && v>=0 && v<=10000000))));
  }
  async function modelConfiguration(cfg, model) {
    const meta=cfg.reproducibility || {}, text=v=>v==null || v==='' ? 'unknown' : String(v).trim(), reported=v=>({value:text(v),source:v==null || v==='' || v==='unknown' ? 'unknown':'user_reported'});
    const privateModel=/^[\\/]/.test(model)||localDetail.test(model)||networkDetail.test(model),alias=privateModel?'private model identifier':model;
    const result={fingerprint_version:'wattever-model-configuration-v1',model:{value:text(cfg.canonical_model_id || (privateModel?'unknown':model)),source:cfg.canonical_model_id?'user_reported':privateModel?'unknown':'requested_model_identifier'},request_model:{value:alias,source:privateModel?'redacted_transport_identifier':'requested_model_identifier'},endpoint:{id:text(cfg.endpoint_id),label:text(cfg.endpoint_label),provider:'openai',source:cfg.endpoint_identity?.source || (cfg.endpoint_id?'user_reported':'unknown'),scope:'public route identity; actual inference backend is not verified'},quantization:{...reported(meta.quantization),user_reported:text(meta.quantization)},engine:reported(meta.engine),engine_version:reported(meta.engine_version),model_revision:reported(meta.model_revision),model_source:reported(cfg.model_source),kv_cache_dtype:reported(meta.kv_cache_dtype),reasoning:{requested:cfg.endpoint_reasoning_effort || 'omitted',effective:'unknown',effective_source:'unknown',user_reported:text(cfg.reported_reasoning)},mtp:{requested:'unknown',effective:'unknown',effective_source:'unknown',user_reported:cfg.reported_mtp || 'unknown'},context_tokens:reported(meta.context_tokens),mtp_draft_tokens:reported(meta.mtp_draft_tokens)};
    const catalogModel = models.find(item => item.id === model) || {id:model};
    const discovered = root.WatteverModelMetadata?.normalize(catalogModel) || catalogModel;
    if (result.quantization.value === 'unknown' && discovered.quantization) {
      result.quantization.value = discovered.quantization;
      result.quantization.source = discovered.quantization_source || 'endpoint_reported';
    }
    if (discovered.quantization_method) {
      result.quantization.method = discovered.quantization_method;
      result.quantization.method_source = discovered.quantization_method_source || 'endpoint_reported';
    }
    function ordered(value) { if(Array.isArray(value)) return value.map(ordered); if(object(value)) return Object.fromEntries(Object.keys(value).sort().map(k=>[k,ordered(value[k])])); return value; }
    const bytes=new TextEncoder().encode(JSON.stringify(ordered(result))), hash=await root.crypto.subtle.digest('SHA-256',bytes);
    result.configuration_id=Array.from(new Uint8Array(hash),x=>x.toString(16).padStart(2,'0')).join('');
    if (!safeConfiguration(result)) throw new Error('Model identity must contain public labels and IDs only.');
    return result;
  }
  function exportRun(run) { const copy = publicProjection(run); delete copy.community; return copy; }
  function summary(run) { const rows = run.results || []; return {community:!!run.community, id:run.id, label:run.label, notes:run.notes, created_at:run.created_at, status:run.status, gpu:run.gpu || {}, host:run.host || {}, hardware:run.hardware || {}, config:run.config || {}, models:[...new Set(rows.map(r=>r.model).filter(Boolean))], sizes:[...new Set(rows.map(r=>r.target_tokens).filter(Boolean))], concurrency_levels:[...new Set(rows.map(r=>r.concurrency || 1))], n_results:rows.length, n_ok:rows.filter(r=>r.status === "ok").length, n_failed:rows.filter(r=>r.status === "failed").length, n_skipped:rows.filter(r=>r.status === "skipped").length, n_cancelled:rows.filter(r=>r.status === "cancelled").length}; }
  function status() { return {running, cancelling, run_id:active && active.id, run:active && summary(active), live, storage_error:storageError}; }
  function emit(kind, extra) { events.push(Object.assign({kind, seq:events.length}, extra)); }
  function configure(config) {
    if (running) throw new Error("Wait for the active benchmark to finish before changing connection.");
    connected=false; detectedEndpoint=null;
    if(!String(config.endpoint || '').trim()) throw new Error('Enter the API base URL.');
    const url = new URL(config.endpoint);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error("Endpoint must be an HTTP(S) URL without credentials, query or fragment.");
    url.pathname = url.pathname.replace(/\/+$/, "") || "/v1";
    endpoint = url.toString().replace(/\/$/, ""); apiKey = String(config.apiKey || "");
    endpointNameOverride=String(config.endpoint_name_override || '').trim();
    if(endpointNameOverride.length>120 || localDetail.test(endpointNameOverride) || networkDetail.test(endpointNameOverride) || /[\x00-\x1f]/.test(endpointNameOverride)) throw new Error('Display name must be short public text, not a URL or credential.');
    const h = config.hardware && typeof config.hardware === "object" ? config.hardware : {};
    const memory = v => Number.isFinite(Number(v)) && Number(v) > 0 ? Number(v) : null;
    hardware = {cpu:String(h.cpu || ""), gpu:String(h.gpu || ""), ram_gb:memory(h.ram_gb), vram_gb:memory(h.vram_gb)};
    models = []; connected = false;
  }
  function locationKind(host) {
    if(['localhost','127.0.0.1','[::1]'].includes(host) || /^127\./.test(host) || host.endsWith('.localhost')) return 'local';
    if(/^(?:10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)/.test(host) || /\.(?:local|lan|internal|home|corp)$/.test(host) || /^\[(?:fc|fd|fe80)/i.test(host) || !host.includes('.') && !host.includes(':')) return 'private';
    return 'remote';
  }
  function identifiedService(payload, serverHeader='') {
    const names={'strata':'Strata','strataserver':'Strata','strataengine':'Strata','strataqwen':'Strata','lmstudio':'LM Studio','llamacpp':'llama.cpp','llamaserver':'llama.cpp','vllm':'vLLM','ollama':'Ollama','sglang':'SGLang','textgenerationinference':'TGI','tgi':'TGI','mlxlm':'MLX LM','koboldcpp':'KoboldCpp','aphrodite':'Aphrodite','llamacpppython':'llama-cpp-python'};
    const rawVersion=payload?.version || payload?.service?.version;
    const version=typeof rawVersion==='string' && /^(?:v?\d{1,4}(?:\.\d{1,4}){0,3}(?:(?:rc|alpha|beta)\d{0,3})?(?:\+\d{1,6})?|b\d{2,7})$/.test(rawVersion)?rawVersion:'unknown';
    for(const candidate of [payload?.service,payload?.backend,payload?.software,payload?.server,serverHeader]) {
      const raw=typeof candidate==='string'?candidate: candidate?.name;
      if(!raw)continue;
      const normalized=raw.toLowerCase().trim().replace(/[ ._-]/g,'').split('/')[0];
      if(names[normalized])return{service:names[normalized],source:'detected',version};
    }
    if(payload && typeof payload==='object' && payload.default_generation_settings && (payload.model_path || payload.build_info))return{service:'llama.cpp',source:'inferred'};
    return null;
  }
  function privateRouteId() {
    if(memoryRoutes.has(endpoint)) return memoryRoutes.get(endpoint);
    let routes={};try{const stored=JSON.parse(root.localStorage.getItem(ROUTES_KEY)||'{}');if(object(stored))routes=stored;}catch(_){}
    let id=routes[endpoint];
    if(typeof id!=='string'||!/^endpoint-[A-Za-z0-9-]{8,80}$/.test(id))id='endpoint-'+random().replace(/[^a-z0-9-]/gi,'').slice(0,40);
    memoryRoutes.set(endpoint,id);Object.defineProperty(routes,endpoint,{value:id,enumerable:true,writable:true,configurable:true});
    try{root.localStorage.setItem(ROUTES_KEY,JSON.stringify(routes));}catch(_){}
    return id;
  }
  async function resolveEndpointIdentity(payload,serverHeader) {
    const url=new URL(endpoint),location=locationKind(url.hostname.toLowerCase());
    const clouds={'api.openai.com':['openai-api','OpenAI API','/v1'],'api.anthropic.com':['anthropic-api','Anthropic API','/v1'],'openrouter.ai':['openrouter-api','OpenRouter','/api/v1'],'api.groq.com':['groq-api','Groq API','/openai/v1'],'api.mistral.ai':['mistral-api','Mistral API','/v1'],'api.deepseek.com':['deepseek-api','DeepSeek API','/v1'],'api.together.xyz':['together-api','Together API','/v1'],'api.fireworks.ai':['fireworks-api','Fireworks API','/inference/v1'],'api.deepinfra.com':['deepinfra-api','DeepInfra API','/v1/openai'],'generativelanguage.googleapis.com':['gemini-api','Google Gemini API','/v1beta/openai']};
    const cloud=clouds[url.hostname.toLowerCase()];
    let detected=identifiedService(payload,serverHeader);
    if(!cloud && !detected && location!=='remote') {
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),1500);
      try {
        for(const path of ['/health','/props','/version']) {
          try {
            const headers={Accept:'application/json'};if(apiKey)headers.Authorization='Bearer '+apiKey;
            const response=await fetch(new URL(path,url.origin),{method:'GET',headers,credentials:'omit',redirect:'error',signal:controller.signal,...(location==='local'?{targetAddressSpace:'loopback'}:{})});
            if(!response.ok)continue;
            let data={};try{data=await response.json();}catch(_){}
            detected=identifiedService(data,response.headers?.get?.('Server'));
            if(detected)break;
          }catch(_){if(controller.signal.aborted)break;}
        }
      }finally{clearTimeout(timer);}
    }
    const service=cloud?cloud[1].replace(/ API$/,''):detected?.service || 'unknown';
    const fallback=location==='local'?'Local endpoint':location==='private'?'Private endpoint':'Remote endpoint';
    const label=cloud?cloud[1]:service!=='unknown'?service+' '+fallback.toLowerCase():fallback;
    const canonical=!!cloud && url.protocol==='https:' && (!url.port || url.port==='443') && url.pathname.replace(/\/$/,'')===cloud[2];
    return{id:canonical?cloud[0]:privateRouteId(),label,source:cloud?'inferred':detected?.source || 'unknown',service,version:detected?.version || 'unknown',location,route_registry:canonical?'known_cloud':'browser_local_mapping'};
  }
  function displayIdentity(override=endpointNameOverride) { return detectedEndpoint ? {...detectedEndpoint,...(override?{label:override,source:'operator_override'}:{})}:null; }
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
    let data,serverHeader; try { const response = await provider('/models', {method:'GET', signal:controller.signal}); serverHeader=response.headers?.get?.('Server');data = await response.json(); }
    catch (e) { if (e.name === 'AbortError') throw new Error('Connection timed out. Check the endpoint and browser permissions.'); if (e.auth) throw e; if (e instanceof SyntaxError) throw new Error('Endpoint returned an invalid model list.'); throw e; }
    finally { clearTimeout(timer); }
    if (!data || !Array.isArray(data.data)) throw new Error('Endpoint returned an invalid model list.');
    models = data.data.filter(m => m && typeof m.id === 'string' && m.id).map(m => window.WatteverModelMetadata?.normalize(m) || {id:m.id, type:'llm'});
    detectedEndpoint=await resolveEndpointIdentity(data,serverHeader);
    connected = true; return {models,endpoint_identity:displayIdentity()};
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
  function env() { return {endpoint_identity:displayIdentity(),gpus:[], gpu:{name:hardware.gpu || null, vram_total_mb:hardware.vram_gb ? hardware.vram_gb*1024 : null}, platform:'browser', host:{cpu:hardware.cpu || null, ram_gb:hardware.ram_gb || null}, hardware:{cpu:{name:hardware.cpu || null}, system:{memory_bytes:hardware.ram_gb ? hardware.ram_gb*1073741824 : null}, gpus:hardware.gpu ? [{name:hardware.gpu, memory_bytes:hardware.vram_gb ? hardware.vram_gb*1073741824 : null}] : [], source:'manual'}, corpus:{approx_tokens:null}, power:{source:'none', readable:false}, lms:{cli:false, server:connected, version:null}, models, default_models:models.map(m=>m.id), missing_defaults:[], defaults, capabilities:{resume:false,extend:false,context_planning:false}, size_presets:sizes, concurrency_presets:levels, last_config:null, running, storage_error:storageError}; }
  async function stream(model, prompt, cfg) {
    const controller = new AbortController(); controllers.add(controller);
    let timedOut = false; const timer = setTimeout(() => { timedOut = true; controller.abort(); }, cfg.per_test_timeout_s * 1000);
    const start = performance.now(); let first = null, end = null, output = '', usage = null, done = false, finish = null, outputEvents=0,emptyBefore=0,reasoningSeen=false;
    try {
      const body={model,messages:[{role:'user',content:prompt}],[cfg.token_limit_field]:cfg.max_tokens,stream:true,stream_options:{include_usage:true}};
      if(cfg.endpoint_reasoning_effort!=null) body.reasoning_effort=cfg.endpoint_reasoning_effort;
      if(cfg.temperature!=null) body.temperature=cfg.temperature;
      const response = await provider('/chat/completions', {method:'POST', signal:controller.signal, body:JSON.stringify(body)});
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
          if(choice.delta?.reasoning_content) reasoningSeen=true;
          if (typeof delta === 'string' && delta) { outputEvents++; if (first === null) first = performance.now(); output += delta; end = performance.now(); }
          else if(first===null && object(choice.delta) && Object.keys(choice.delta).length===0 && !choice.finish_reason) emptyBefore++;
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
      const reportedReasoning=usage?.completion_tokens_details?.reasoning_tokens;
      const reasoning=Number.isSafeInteger(reportedReasoning) && reportedReasoning>=0 ? reportedReasoning:null;
      if(reasoning!==null && exactCompletion!==null && reasoning>exactCompletion) throw new Error('Invalid reasoning token usage.');
      let timedTokens=exactCompletion===null?null:exactCompletion-(reasoning!==null&&!reasoningSeen?reasoning:0);
      if(outputEvents<2 || (emptyBefore && reasoning===null)) timedTokens=null;
      const decodeTokens=timedTokens===null?null:Math.max(0,timedTokens-1);
      return {status:'ok',prompt_tokens:exactPrompt,completion_tokens:exactCompletion,completion_tokens_estimated:false,reasoning_tokens:reasoning,cached_prompt_tokens:Number.isSafeInteger(usage?.prompt_tokens_details?.cached_tokens)?usage.prompt_tokens_details.cached_tokens:null,decode_token_count:decodeTokens,timing_scope:{boundary:'first observed nonempty content or reasoning text',empty_deltas_before_output:emptyBefore,reasoning_streamed:reasoningSeen,requested_reasoning_effort:cfg.endpoint_reasoning_effort,decode_token_scope:timedTokens===null?'unavailable; missing usage, hidden reasoning or only one observed output event':'server_usage_after_excluding_reported_unstreamed_reasoning'},metric_sources:{completion_tokens:exactCompletion===null?'unavailable':'server_usage',prefill_tps:'prompt_tokens_over_client_ttft; includes network/queue and possible hidden reasoning',decode_tps:'scoped_server_usage_over_client_output_interval'},ttft_s:ttft,wall_s:elapsed,prefill_tps:exactPrompt!==null&&ttft>0?exactPrompt/ttft:null,decode_tps:decode>0&&decodeTokens?decodeTokens/decode:null};
    } catch (e) { if (timedOut) return {status:'failed', failure:'timeout', message:'Request timed out; later cases stopped because the provider may still be running.'}; if (e.name === 'AbortError') return {status:'cancelled', message:'Cancelled'}; return {status:'failed', failure:e.auth ? 'authentication' : 'error', message:e.message || 'Request failed.'}; }
    finally { clearTimeout(timer); controllers.delete(controller); }
  }
  async function execute(run, cfg) {
    try {
      const response = await fetch(new URL('corpus.md', document.baseURI), {credentials:'omit'});
      if (!response.ok) throw new Error('Local corpus is unavailable.');
      const corpus = await response.text(); if (!corpus.trim()) throw new Error('Local corpus is empty.');
      run.model_configurations=await Promise.all(cfg.models.map(model=>modelConfiguration(cfg,model)));
      const configurationByModel=new Map(cfg.models.map((model,index)=>[model,run.model_configurations[index]]));
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
        row.model_configuration=configurationByModel.get(model);row.configuration_id=row.model_configuration.configuration_id;
        const quant = row.model_configuration.quantization;
        Object.assign(row,{quantization:quant.value === 'unknown' ? null : quant.value,quantization_source:quant.source,
          quantization_method:quant.method || null,quantization_method_source:quant.method_source || 'unknown'});
        if (concurrency === 1) Object.assign(row, requests[0]);
        if (ok.length === concurrency && duration > 0 && ok.every(x=>x.completion_tokens!==null)) { row.aggregate_completion_tps = ok.reduce((sum,x)=>sum+x.completion_tokens,0)/duration; row.aggregate_completion_tokens_estimated = false; }
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
    for (const key of ['models','sizes','concurrency_levels','max_tokens','repeats','mtp_modes','model_modes','per_test_timeout_s','disable_thinking','label','notes','endpoint_reasoning_effort','token_limit_field','temperature','endpoint_id','endpoint_label','canonical_model_id','reported_mtp','reported_reasoning','model_source','endpoint_name_override','endpoint_identity']) if (Object.prototype.hasOwnProperty.call(input,key)) cfg[key] = input[key];
    const automatic=displayIdentity(Object.prototype.hasOwnProperty.call(input,'endpoint_name_override') ? input.endpoint_name_override : endpointNameOverride);
    if(automatic && !(input.endpoint_id && !input.endpoint_identity)) {cfg.endpoint_id=automatic.id;cfg.endpoint_label=automatic.label;cfg.endpoint_identity=automatic;}
    if(!['max_tokens','max_completion_tokens'].includes(cfg.token_limit_field))throw new Error('Invalid token limit field.');
    if(cfg.endpoint_reasoning_effort!=null&&!['none','minimal','low','medium','high','xhigh'].includes(cfg.endpoint_reasoning_effort))throw new Error('Invalid requested reasoning.');
    if(cfg.temperature!=null&&(!Number.isFinite(cfg.temperature)||cfg.temperature<0||cfg.temperature>2))throw new Error('Invalid temperature.');
    if(cfg.canonical_model_id&&cfg.models.length!==1)throw new Error('A canonical model ID applies to one selected model.');
    if (!Array.isArray(cfg.models) || !cfg.models.length || cfg.models.some(id=>!models.some(m=>m.id===id))) throw new Error('Select installed models.');
    if ((cfg.mtp_modes || []).some(x=>x !== 'off') || Object.values(cfg.model_modes || {}).some(a=>a.some(x=>x !== 'off'))) throw new Error('MTP is not supported in browser mode.');
    for (const key of ['sizes','concurrency_levels']) if (!Array.isArray(cfg[key]) || !cfg[key].length || cfg[key].some(n=>!Number.isInteger(n) || n < 1 || n > (key === 'sizes' ? 1000000 : 100))) throw new Error('Invalid workload selection.');
    for (const key of ['max_tokens','repeats','per_test_timeout_s']) if (!Number.isInteger(cfg[key]) || cfg[key] < 1 || cfg[key] > (key === 'per_test_timeout_s' ? 7200 : key === 'repeats' ? 100 : 1000000)) throw new Error('Invalid ' + key + '.');
    cfg.sizes = [...new Set(cfg.sizes)].sort((a,b)=>a-b); cfg.concurrency_levels = [...new Set(cfg.concurrency_levels)].sort((a,b)=>a-b);
    cfg.context_policy = 'provider_managed'; cfg.warmup = false; cfg.mtp_modes = []; cfg.mtp_control = 'provider_managed'; cfg.disable_thinking = null;
    const reported = {};
    if (input.reproducibility != null && !object(input.reproducibility)) throw new Error('Invalid reproducibility settings.');
    for (const key of ['engine','engine_version','quantization','model_revision','context_tokens','kv_cache_dtype','mtp_draft_tokens']) {
      const value = (input.reproducibility || {})[key];
      if (value == null || value === '') continue;
      if (['context_tokens','mtp_draft_tokens'].includes(key)) {
        if (!Number.isInteger(value) || value < 0 || value > 10000000) throw new Error('Invalid reported ' + key + '.');
        reported[key] = value;
      } else {
        if (typeof value !== 'string' || value.length > 250) throw new Error('Invalid reported ' + key + '.');
        reported[key] = value;
      }
    }
    if (input.performance_claims != null && (!Array.isArray(input.performance_claims) || input.performance_claims.length > 20)) throw new Error('Invalid performance claims.');
    const claims = (input.performance_claims || []).map(claim=>{
      if (!object(claim) || !['FP4','INT4','FP8','INT8','FP16','BF16','FP32'].includes(claim.precision) ||
          !Number.isFinite(claim.value) || claim.value < 0 || !['advertised_peak','measured','estimate'].includes(claim.kind) ||
          !['unknown','dense','sparse'].includes(claim.sparsity)) throw new Error('Invalid performance claim.');
      if (typeof claim.execution_path !== 'string' || !claim.execution_path.trim() || claim.execution_path.length > 120 ||
          typeof claim.source !== 'string' || !claim.source.trim() || claim.source.length > 300) throw new Error('Performance claims require source and execution path.');
      return {precision:claim.precision,value:claim.value,unit:claim.precision.startsWith('INT') ? 'TOPS' : 'TFLOP/s',kind:claim.kind,
        sparsity:claim.sparsity,execution_path:claim.execution_path,source:claim.source,provenance:'user_reported'};
    });
    cfg.reproducibility = reported; cfg.performance_claims = claims;
    const run = {id:'browser-'+random(), created_at:new Date().toISOString(), label:String(cfg.label || 'Browser benchmark'), notes:String(cfg.notes || ''), status:'running', measurement_mode:'browser', browser_schema_version:2, application:{name:'Wattever',version:root.WATTEVER_VERSION || '2.4.1',protocol_version:'browser-2'}, hardware:env().hardware, host:env().host, gpu:env().gpu, config:cfg, results:[]};
    run.reproducibility = {source:'user_reported',settings:reported}; run.performance_claims = claims;
    active=run; running=true; cancelling=false; events=[]; live={phase:'starting',done:0,total:cfg.models.length*cfg.sizes.length*cfg.concurrency_levels.length*cfg.repeats}; put(run); execute(run,cfg); return {run_id:run.id};
  }
  async function request(path, options = {}) {
    const url = new URL(path, 'http://local.invalid'), route = url.pathname, method = (options.method || 'GET').toUpperCase();
    let body = {}; if (options.body) { try { body = JSON.parse(options.body); } catch (_) { throw new Error('Invalid JSON request.'); } }
    if(route.startsWith('/api/capabilities/') && root.WatteverCapabilityCatalog) return root.WatteverCapabilityCatalog.request(path,options);
    if (route === '/api/version') return {application:'Wattever',version:root.WATTEVER_VERSION || '2.4.1'};
    if (route === '/api/env') return env();
    if (route === '/api/models') return {models};
    if (route === '/api/status') return status();
    if (route === '/api/events') { const cursor = Math.max(0, Number(url.searchParams.get('cursor')) || 0); return {events:events.slice(cursor),cursor:events.length,status:status()}; }
    if (route === '/api/runs' && method === 'GET') { await loadCommunity(); return {runs:mergedRuns().map(summary),storage_error:storageError,community_error:communityError}; }
    if (route === '/api/import' && method === 'POST') { await loadCommunity(); const items = Array.isArray(body.runs) ? body.runs : [body]; if (!items.every(valid)) throw new Error('Invalid benchmark structure or result rows.'); return {runs:items.map(importRun),storage_error:storageError}; }
    if (route === '/api/export.json' && method === 'GET') { await loadCommunity(); const ids=(url.searchParams.get('ids') || '').split(',').filter(Boolean); return {runs:mergedRuns().filter(r=>!ids.length || ids.includes(r.id)).map(exportRun)}; }
      if (route === '/api/export.csv' && method === 'GET') { await loadCommunity(); const ids=(url.searchParams.get('ids') || '').split(',').filter(Boolean); const quote = v => '"' + String(v == null ? '' : v).replace(/"/g,'""') + '"'; return [['run_id','model','quantization','quantization_method','quantization_source','quantization_method_source','target_tokens','concurrency','status','prefill_tps','decode_tps','aggregate_completion_tps','measurement_mode','protocol_version','completion_tokens_estimated','aggregate_completion_tokens_estimated'].join(','), ...mergedRuns().filter(r=>!ids.length || ids.includes(r.id)).map(exportRun).flatMap(r=>(r.results || []).map(x=>[r.id,x.model,x.quantization,x.quantization_method,x.quantization_source,x.quantization_method_source,x.target_tokens,x.concurrency,x.status,x.prefill_tps,x.decode_tps,x.aggregate_completion_tps,r.measurement_mode || 'helper',(r.application || {}).protocol_version,x.completion_tokens_estimated,x.aggregate_completion_tokens_estimated].map(quote).join(',')))].join('\n'); }
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
