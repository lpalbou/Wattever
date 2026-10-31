/* Versioned capability tests. No code from model responses is executed. */
(function(root) {
  'use strict';
  const ui = root.WatteverUI;
  if (!ui) return;
  const $ = selector => document.querySelector(selector);
  const escape = value => String(value ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  const state = {runs:[],cache:new Map(),selected:new Set(),suites:[],timer:null,busy:false,active:false,focus:null,libraryQuery:'',navigation:0,liveRun:null,liveCursor:0,runnerCompatible:false};
  const COMPLETION_POLICY='finished-stream-visible-answer-v2';
  const UPDATE_MESSAGE='This benchmark helper is running an older capability runner. Relaunch Wattever to load the updated backend. Saved results remain available to browse.';
  const percent = score => Number.isFinite(score?.percent) ? score.percent : Number.isFinite(score?.accuracy) ? score.accuracy*100 : null;
  const number = value => Number.isFinite(value) ? value.toFixed(1) : '—';
  const request = (path,body) => ui.api('/api/capabilities'+path,body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  function message(text,error=false) { $('#cap-status').textContent = text; $('#cap-status').className = error ? 'cap-error' : ''; }
  function config() {
    const base = ui.config();
    return {...base,model:$('#cap-model').value,models:[$('#cap-model').value],suite_id:$('#cap-suite').value,endpoint_inference_local:$('#cap-local').checked,
      repeats:Number($('#cap-repeats').value),sample_size:Number($('#cap-samples').value),max_tokens:Number($('#cap-max-tokens').value),
      timeout_s:Number($('#cap-timeout').value),seed:Number($('#cap-seed').value),send_seed:$('#cap-send-seed').checked,
      top_p:$('#cap-top-p').value === '' ? null : Number($('#cap-top-p').value),
      endpoint_reasoning_effort:$('#cap-reasoning').value==='default'?null:$('#cap-reasoning').value || 'none',
      temperature:$('#cap-temperature').value===''?null:Number($('#cap-temperature').value),
      token_limit_field:$('#cap-token-limit').value || 'max_tokens'};
  }
  function identity() {
    const cfg = config(), meta = cfg.reproducibility || {};
    $('#cap-identity').textContent = [meta.quantization ? 'Quantization: '+meta.quantization : 'Quantization unknown',
      'Requested reasoning: '+(cfg.endpoint_reasoning_effort || 'provider default'),
      'Reported reasoning: '+(cfg.reported_reasoning || 'unknown'),'Reported MTP: '+(cfg.reported_mtp || 'unknown')].join(' · ');
    $('#cap-connection').textContent = ui.connection().endpoint_label || (ui.connection().provider === 'lmstudio' ? 'LM Studio' : 'Endpoint');
  }
  function suiteInfo() {
    const suite = state.suites.find(item=>(item.id || item.suite_id) === $('#cap-suite').value);
    if (!suite) { $('#cap-suite-info').textContent = ''; return; }
    const counts = suite.categories || suite.category_counts;
    const count = suite.item_count ?? suite.task_count ?? (Array.isArray(suite.tasks) ? suite.tasks.length : null);
    const selection = $('#cap-samples'), previous=selection.value, choices=[24,50,100,300].filter(value=>count == null || value<=count);
    selection.innerHTML=choices.map(value=>`<option value="${value}">${value}</option>`).join('')+`<option value="0">Full suite${count != null ? ' ('+count+')' : ''}</option>`;
    const next = state.previousSuite === $('#cap-suite').value && [...choices,0].includes(Number(previous)) ? Number(previous) : (String(suite.id || suite.suite_id).includes('core') && choices.includes(24) ? 24 : choices.includes(50) ? 50 : 0);
    selection.value=String(next); state.previousSuite=$('#cap-suite').value;
    const origin=root.WatteverCapabilityDashboard?.suiteOrigin(suite);
    $('#cap-suite-info').textContent = [origin?.label,origin?.description,count != null ? count+' questions' : '',suite.version ? 'version '+suite.version : '',
      counts && !Array.isArray(counts) ? Object.keys(counts).join(', ') : '',suite.sha256 || suite.digest ? 'Version fingerprint recorded' : ''].filter(Boolean).join(' · ');
  }
  const dashboard=root.WatteverCapabilityDashboard?.create({container:$('#cap-detail'),headerContainer:$('#cap-detail-header'),comparisonContainer:$('#cap-comparison'),descriptor,interval,unknownConfiguration,
    telemetryMarkup:ui.telemetryMarkup,bindTelemetry:ui.bindTelemetry,
    download:id=>download('/runs/'+encodeURIComponent(id)+'/download','capability-'+id+'.json'),
    onFocus:id=>detail(id,true).then(compare).catch(error=>message(error.message,true)),
    onClearComparison:()=>{state.selected=new Set(state.focus?[state.focus]:[]);compare().catch(error=>message(error.message,true));}});
  async function refresh() {
    const data=await request('/runs');state.runs=Array.isArray(data)?data:data.runs || [];
    state.selected=new Set([...state.selected].filter(id=>state.runs.some(run=>run.id===id)));
    if(!state.runs.some(run=>run.id===state.focus))state.focus=(state.runs.find(run=>(run.summary?.scored || 0)>0) || state.runs[0])?.id || null;
    if(state.focus&&!state.selected.size)state.selected.add(state.focus);
    renderLibrary();
    if(state.focus&&dashboard)await detail(state.focus);
    if($('#cap-empty'))$('#cap-empty').hidden=!!state.focus;
    if(!state.focus){$('#cap-detail').hidden=true;$('#cap-detail-header').hidden=true;}
    await compare();
  }
  function renderLibrary() {
    const query=state.libraryQuery.toLowerCase(),runs=state.runs.filter(run=>{const model=descriptor(run);return [model.model,model.quant,model.endpoint,run.label,run.suite_id].join(' ').toLowerCase().includes(query);});
    $('#cap-runs').innerHTML=runs.length?runs.map(run=>{
      const model=descriptor(run),summary=run.summary || run,rate=percent(summary),suite=run.suite_id || run.suite?.id || 'Capability suite';
      return `<div class="cap-run ${state.focus===run.id?'cap-run-active':''}"><input type="checkbox" value="${escape(run.id)}" ${state.selected.has(run.id)?'checked':''} aria-label="Compare ${escape(model.model)}" title="Include in comparison"><button type="button" data-cap-run="${escape(run.id)}" title="${escape(run.label || run.id)}"><span class="cap-run-title"><strong>${escape(model.model)}</strong><b>${number(rate)}${rate==null?'':'%'}</b></span><small>${escape(model.quant)} &middot; ${escape(model.endpoint)}</small><small>${escape(root.WatteverCapabilityDashboard?.suiteTitle?.({id:suite,name:run.suite?.name}) || suite)} &middot; ${escape(run.status || '')}${run.community?' &middot; Shared':''}</small><span class="cap-run-mini-track"><span style="width:${rate==null?0:Math.max(0,Math.min(100,rate))}%"></span></span></button></div>`;
    }).join(''):'<p class="hint">'+(state.runs.length?'No runs match this search.':'No capability results yet.')+'</p>';
    $('#cap-runs').querySelectorAll('input').forEach(input=>input.addEventListener('change',async()=>{if(input.checked)state.selected.add(input.value);else state.selected.delete(input.value);try{await compare();}catch(error){message(error.message,true);}}));
    $('#cap-runs').querySelectorAll('[data-cap-run]').forEach(button=>button.addEventListener('click',()=>detail(button.dataset.capRun,true).then(compare).catch(error=>message(error.message,true))));
    $('#cap-export').href='/api/capabilities/export.json'+(state.selected.size?'?ids='+[...state.selected].map(encodeURIComponent).join(','):'');
    if($('#cap-run-count'))$('#cap-run-count').textContent=String(state.runs.length);
  }
  async function load(id) {
    if (!state.cache.has(id)) state.cache.set(id,await request('/runs/'+encodeURIComponent(id)));
    return state.cache.get(id);
  }
  function descriptor(run) {
    const model = run.model_configuration || {}, cfg = run.config || {}, reported = cfg.reproducibility || {};
    const value = field => field && typeof field === 'object' ? field.value : field;
    return {model:value(model.model) || model.canonical_model_id || model.served_model_id || cfg.canonical_model_id || cfg.model || cfg.models?.[0] || run.model || 'Unknown model',
      quant:value(model.quantization) || reported.quantization || 'unknown', endpoint:model.endpoint?.label || model.endpoint_label || run.provider?.label || cfg.endpoint_label || run.endpoint_label || 'unknown',
      reasoning:model.reasoning?.requested ?? model.requested_reasoning_effort ?? model.requested_reasoning ?? cfg.endpoint_reasoning_effort ?? 'provider default',
      reportedReason:model.reasoning?.user_reported || model.reported_reasoning || cfg.reported_reasoning || 'unknown',mtp:model.mtp?.user_reported || model.reported_mtp || cfg.reported_mtp || 'unknown'};
  }
  function interval(summary) {
    const ci = summary.wilson_95;
    return Number.isFinite(ci?.low) && Number.isFinite(ci?.high) ? `${number(ci.low*100)}–${number(ci.high*100)}%` : '—';
  }
  function unknownConfiguration(run) {
    const config=run.model_configuration || {}, unknown=value=>!value || value==='unknown';
    return unknown(config.model?.value) || unknown(config.quantization?.value) || unknown(config.endpoint?.id) ||
      (unknown(config.reasoning?.effective) && unknown(config.reasoning?.user_reported)) ||
      (unknown(config.mtp?.effective) && unknown(config.mtp?.user_reported));
  }
  async function compare() {
    renderLibrary();const ids=[...state.selected];
    if(ids.length<2){$('#cap-comparison').hidden=true;return;}
    const runs=await Promise.all(ids.map(load));if(ids.join('|')!==[...state.selected].join('|'))return;
    dashboard?.renderComparison(runs,state.focus);
  }
  async function detail(id,scroll=false) {
    const navigation=++state.navigation,run=await load(id);if(navigation!==state.navigation)return;state.focus=id;
    if(state.selected.size<=1)state.selected=new Set([id]);
    if($('#cap-empty'))$('#cap-empty').hidden=true;
    dashboard?.render(run);renderLibrary();
    if(scroll)$('#cap-detail-header').scrollIntoView({behavior:'auto',block:'start'});
  }
  async function download(path,name) {
    const result=await request(path), blob=new Blob([JSON.stringify(result,null,2)],{type:'application/json'}), url=URL.createObjectURL(blob), anchor=document.createElement('a');
    anchor.href=url; anchor.download=name; anchor.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  async function poll() {
    if (state.busy) return; state.busy=true;
    try {
      const status=await request('/status'), live=status.live || {};
      $('#cap-start').disabled=!!status.running || ui.browserMode() || !state.runnerCompatible; $('#cap-cancel').hidden=!status.running;
      $('#cap-run-status').hidden=!status.running;
      $('#cap-progress').hidden=!status.running;
      $('#cap-progress div').style.width=live.total ? Math.min(100,100*(live.done || 0)/live.total)+'%' : '0%';
      $('#cap-live').textContent=status.running ? [live.phase,live.item_id,live.category,`${live.done || 0}/${live.total || 0}`].filter(Boolean).join(' · ') : '';
      if(status.running && status.run_id && dashboard){
        const first=state.liveRun!==status.run_id;
        if(first){state.liveRun=status.run_id;state.liveCursor=0;}if(!state.cache.has(status.run_id))await load(status.run_id);
        const updates=await request('/events?cursor='+state.liveCursor);state.liveCursor=updates.cursor ?? state.liveCursor;
        const run=await load(status.run_id);let changed=first;
        for(const event of updates.events || [])if(event.kind==='result' && event.result){const row=event.result,index=run.results.findIndex(saved=>saved.item_id===row.item_id && saved.repeat===row.repeat);if(index<0)run.results.push(row);else run.results[index]=row;if(event.summary)run.summary=event.summary;changed=true;}
        if(updates.status?.run?.summary)run.summary=updates.status.run.summary;else if(!(updates.events || []).some(event=>event.summary) && status.run?.summary)run.summary=status.run.summary;
        if(changed){const entry=state.runs.find(item=>item.id===run.id);if(entry){entry.summary=run.summary;entry.status=run.status;}renderLibrary();if(state.focus===status.run_id)dashboard.render(run);if(state.selected.has(status.run_id))await compare();}
        if(!state.timer)state.timer=setInterval(poll,1000);
      }
      const telemetry=live.telemetry || {};
      $('#cap-telemetry').innerHTML=[['CPU',telemetry.cpu_util_percent,'%'],['GPU',telemetry.gpu_util,'%'],['RAM',telemetry.system_memory_percent,'%'],['VRAM',telemetry.vram_percent,'%'],['CPU temp',telemetry.cpu_temp_c,'°C'],['GPU temp',telemetry.temp_c,'°C']].filter(item=>Number.isFinite(item[1])).map(([label,value,unit])=>`<div class="stat"><div class="k">${label}</div><div class="v">${number(value)} <span class="u">${unit}</span></div></div>`).join('');
      if (!status.running && state.timer) {
        clearInterval(state.timer); state.timer=null; message('Test finished.');
        const following=!state.focus || state.focus===status.run_id;state.cache.clear();await refresh();
        if(status.run_id && following){state.selected=new Set([status.run_id]);await detail(status.run_id);await compare();}
      }
    } catch(error) { message('Status unavailable: '+error.message,true); }
    finally {state.busy=false;}
  }
  async function start() {
    try {
      if(!state.runnerCompatible)throw new Error(UPDATE_MESSAGE);
      if (!ui.ready()) throw new Error('Connect your endpoint in Run first.');
      const cfg=config();
      if (!cfg.model) throw new Error('Select a model.');
      $('#cap-start').disabled=true; message('Starting…');
      const result=await request('/start',cfg);state.cache.delete(result.run_id);state.focus=result.run_id;state.selected=new Set([result.run_id]);await refresh();
      state.timer=setInterval(poll,1000); await poll();
    } catch(error) {message(error.message,true);$('#cap-start').disabled=ui.browserMode() || !state.runnerCompatible;}
  }
  async function activate() {
    state.runnerCompatible=false;
    const selected=$('#cap-model').value, models=ui.models().filter(model=>model.type!=='embeddings');
    $('#cap-model').innerHTML=models.map(model=>`<option value="${escape(model.id)}">${escape(model.id)}</option>`).join('');
    if (models.some(model=>model.id===selected)) $('#cap-model').value=selected;
    else if (ui.selectedModels?.().length) $('#cap-model').value=ui.selectedModels()[0];
    $('#cap-unavailable').hidden=!ui.browserMode(); $('#cap-start').disabled=true;
    if($('#cap-import-label')) $('#cap-import-label').hidden=true; identity();
    try {
      const data=await request('/suites'); state.suites=Array.isArray(data)?data:data.suites || [];
      state.runnerCompatible=!ui.browserMode() && data.runner?.completion_policy===COMPLETION_POLICY;
      if($('#cap-import-label'))$('#cap-import-label').hidden=ui.browserMode() || !state.runnerCompatible;
      $('#cap-helper-compatibility').hidden=ui.browserMode() || state.runnerCompatible;
      $('#cap-helper-compatibility').textContent=state.runnerCompatible?'':UPDATE_MESSAGE;
      const selectedSuite=$('#cap-suite').value;
      $('#cap-suite').innerHTML=state.suites.map(suite=>`<option value="${escape(suite.id || suite.suite_id)}">${escape(root.WatteverCapabilityDashboard?.suiteTitle?.(suite) || suite.name || suite.label || suite.id || suite.suite_id)}</option>`).join('');
      if(state.suites.some(suite=>(suite.id || suite.suite_id)===selectedSuite)) $('#cap-suite').value=selectedSuite; suiteInfo();
      await refresh(); if (!ui.browserMode()) await poll();
    } catch(error) {message(error.message,true);}
  }
  $('#cap-new-run')?.addEventListener('click',()=>{const panel=$('#cap-launch-panel');if(panel){panel.open=!panel.open;if(panel.open)$('#cap-model').focus({preventScroll:true});}});
  $('#cap-run-search')?.addEventListener('input',event=>{state.libraryQuery=event.target.value;renderLibrary();});
  $('#cap-start').addEventListener('click',start);
  $('#cap-cancel').addEventListener('click',async()=>{try{await request('/cancel',{});message('Stopping…');}catch(error){message(error.message,true);}});
  $('#cap-refresh').addEventListener('click',()=>{state.cache.clear();if(ui.browserMode())root.WatteverCapabilityCatalog?.refresh();activate();});
  $('#cap-suite').addEventListener('change',suiteInfo);
  $('#cap-reasoning').addEventListener('change',identity);
  $('#cap-import').addEventListener('change',async event=>{try{const file=event.target.files[0];if(file){await request('/import',JSON.parse(await file.text()));state.cache.clear();await refresh();message('Imported.');}}catch(error){message(error.message,true);}finally{event.target.value='';}});
  $('#cap-export').addEventListener('click',event=>{event.preventDefault();download('/export.json'+(state.selected.size?'?ids='+[...state.selected].map(encodeURIComponent).join(','):''),'wattever-capabilities.json').catch(error=>message(error.message,true));});
  root.WatteverCapabilities={activate,descriptor,percent,interval,config,unknownConfiguration};
})(window);
