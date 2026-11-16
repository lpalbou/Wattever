/* Concurrency analysis uses recorded group wall-clock observations. */
(function (root) {
  "use strict";
  const phaseMetrics = root.WatteverPhaseMetrics || (typeof require === "function" ? require("./phase-metrics.js") : null);
  const number = value => typeof value === "number" && Number.isFinite(value);
  const nonnegative = value => number(value) && value >= 0;
  const median = values => {
    const sorted = values.filter(number).sort((a,b)=>a-b), n=sorted.length;
    return n ? (sorted[Math.floor((n-1)/2)] + sorted[Math.floor(n/2)])/2 : null;
  };
  const escape = value => String(value ?? "").replace(/[&<>"']/g, char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  const fmt = value => !number(value) ? "—" : value===0 ? "0" : Math.abs(value)>=1000 ? value.toLocaleString(undefined,{maximumFractionDigits:0}) : value.toLocaleString(undefined,{maximumFractionDigits:2});
  const tokens = value => value>=1000 ? `${value/1000}k` : String(value);
  const stable = value => JSON.stringify(value, (_,item)=>item && !Array.isArray(item) && typeof item==="object" ? Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])) : item);
  const valueOf = value => value && typeof value==="object" ? value.value : value;
  const metricInfo = {
    output: {title:"End-to-end output throughput",unit:"tok/s",direction:"high",description:"Total output tokens divided by group wall time, including queueing and prefill."},
    decode: {title:"Collective decode throughput",unit:"tok/s",direction:"high",description:"Scoped post-first-token output divided by the union of observed decode intervals."},
    prefill: {title:"Group prefill throughput",unit:"tok/s",direction:"high",description:"Total prompt tokens divided by the union of request-start to first-output intervals. This client TTFT proxy includes queueing, transport and possible overlap with other requests decoding."},
    ttft: {title:"Wait for the first token",unit:"seconds",direction:"low",description:"Median client time to first token. Includes queueing, transport and prompt processing. Whiskers show the observed request range."},
    finish: {title:"Completion time per request",unit:"seconds",direction:"low",description:"Median per-request completion time; whiskers show the fastest and slowest observed requests."},
    batchwall: {title:"Wall time for all requests",unit:"seconds",direction:"low",description:"Median recorded group wall time across successful repeats, from group dispatch through completion. Includes queueing and staggered starts."},
    gain: {title:"End-to-end output scaling",unit:"\u00d7 the 1-request output rate",direction:"high",description:"Median end-to-end output gain across matching successful repeats at the same prompt target and recorded settings. The dotted line is a linear reference, not a prediction."},
    energy: {title:"Whole-case GPU energy per output token",unit:"J / token",direction:"low",description:"Whole-group GPU energy, including prompt processing, divided by total output tokens. Overlapping requests are counted once; low-confidence energy is excluded."},
  };
  const tabs = {throughput:["decode","output","prefill"],prefill:["prefill","ttft"],latency:["ttft","finish","batchwall"],efficiency:["gain","energy"]};

  function successful(row) {
    const n=row.concurrency || 1, requests=row.requests;
    return row.status==="ok" && (row.completed_requests==null || row.completed_requests===n)
      && (!Array.isArray(requests) || !requests.length || (requests.length===n && requests.every(item=>item.status==="ok")))
      && (n===1 || row.completed_requests===n || (requests?.length===n && requests.every(item=>item.status==="ok")));
  }
  function unverifiedGroup(row) {
    return row.status==="ok" && (row.concurrency || 1)>1 && row.completed_requests==null && !row.requests?.length;
  }
  function partialOffload(value) {
    const fraction=typeof value==='number' ? value : typeof value==='string' && /^\d*\.?\d+$/.test(value.trim()) ? Number(value) : null;
    return number(fraction) && fraction>=0 && fraction<1;
  }
  function requestValues(row, key) {
    if (key === "decode_tps") {
      const requests=Array.isArray(row.requests) && row.requests.length ? row.requests : (row.concurrency || 1)===1 ? [row] : [];
      return requests.filter(item=>item.status==="ok").map(item=>phaseMetrics?.decodePhase({...item,concurrency:1})?.tps).filter(nonnegative);
    }
    if (Array.isArray(row.requests) && row.requests.length) return row.requests.filter(item=>item.status==="ok").map(item=>item[key]).filter(nonnegative);
    return (row.concurrency || 1)===1 && nonnegative(row[key]) ? [row[key]] : [];
  }
  function outputCount(row) {
    if (!successful(row)) return null;
    if ((row.concurrency || 1)===1) {
      const request=Array.isArray(row.requests) && row.requests.length ? row.requests[0] : row;
      return Number.isSafeInteger(request.completion_tokens) && request.completion_tokens>=0 ? request.completion_tokens : null;
    }
    const requests=row.requests;
    if (!Array.isArray(requests) || requests.length !== row.concurrency || !requests.every(item=>item.status === "ok" && Number.isSafeInteger(item.completion_tokens) && item.completion_tokens>=0)) return null;
    const total=requests.reduce((sum,item)=>sum+item.completion_tokens,0);
    return Number.isSafeInteger(total) ? total : null;
  }
  function gpuEnergy(row) {
    // Separate phase windows end at the last observed output; they do not
    // establish the energy of the entire request including trailing traffic.
    const p=row.group_power;
    if (!p || !p.scope || p.low_confidence!==false || p.available===false || p.gpu_power_complete!==true) return null;
    return (number(p.gpu_sampled_coverage_fraction) && p.gpu_sampled_coverage_fraction<.8) ? null : nonnegative(p.gpu_energy_j) ? p.gpu_energy_j : null;
  }
  function measurements(row) {
    const count=outputCount(row), energy=gpuEnergy(row);
    const decode=phaseMetrics?.decodePhase(row)?.tps ?? null, output=phaseMetrics?.outputRate(row) ?? null;
    const reqs=Array.isArray(row.requests) && row.requests.length ? row.requests : (row.concurrency || 1)===1 ? [row] : [];
    const ttfts=reqs.map(request=>request.ttft_s);
    const ttft=successful(row) && reqs.length===(row.concurrency || 1) && ttfts.every(nonnegative) ? median(ttfts) : null;
    return {decode,output,prefill:phaseMetrics?.prefillPhase(row)?.tps ?? null,ttft,
      finish:median(requestValues(row,"wall_s")),batchwall:nonnegative(row.wall_s) ? row.wall_s : null,energy:count>0 && energy!=null && !estimated(row) ? energy/count : null};
  }
  function estimated(row) {
    return !!(row.completion_tokens_estimated || row.aggregate_completion_tokens_estimated || row.requests?.some(item=>item.completion_tokens_estimated));
  }
  function decodeBoundaryEstimated(row) {
    return row.timing_scope?.decode_count_estimated_boundary===true || !!row.requests?.some(request=>request.status==="ok" && request.timing_scope?.decode_count_estimated_boundary===true);
  }
  function legacyDecodeBoundary(row) {
    const requests=Array.isArray(row.requests) && row.requests.length ? row.requests : (row.concurrency || 1)===1 ? [row] : [];
    return requests.some(request=>request.status==="ok" && number(phaseMetrics?.decodePhase({...request,concurrency:1})?.tps) && !phaseMetrics?.currentScope(request));
  }
  function metricEstimated(cell,key) {
    return key==="decode" ? cell.estimated || cell.decodeBoundaryEstimated || cell.legacyDecodeBoundary : key==="output" && cell.estimated;
  }
  function metricEstimateNote(cell,key) {
    const notes=[];
    if (key==="decode" && cell.decodeBoundaryEstimated) notes.push("approximate decode boundary; first output event may contain multiple tokens");
    if (key==="decode" && cell.legacyDecodeBoundary) notes.push("legacy decode interval may include trailing usage or transport");
    if (["decode","output"].includes(key) && cell.estimated) notes.push("estimated output counts");
    return notes.length ? `; * ${notes.join("; ")}` : "";
  }
  function condition(run,row) {
    const config=row.model_configuration || {}, session=row.session_id==null ? null : (run.sessions || []).find(item=>item.id===row.session_id || item.session_id===row.session_id);
    const cfg={...(run.config || {}),...(session?.config || session?.execution_config || {}),...(row.execution_config || {})};
    return {configuration_id:row.configuration_id || config.configuration_id || null,
      quant:valueOf(config.quantization) || row.quantization || "unknown quant",
      method:config.quantization?.method || row.quantization_method || null,
      engine:valueOf(config.engine) || row.engine || run.execution_group?.label || cfg.provider || "unknown engine",
      version:valueOf(config.engine_version) || row.engine_version || null,
      revision:valueOf(config.model_revision) || row.model_revision || null,
      reasoning:config.reasoning || row.reasoning_effort || cfg.endpoint_reasoning_effort || (cfg.disable_thinking===true ? "off" : "unknown"),
      mtp:config.mtp || row.requested_mtp || (row.mtp===true ? "on" : row.mtp===false ? "off" : "unknown"),
      kv:valueOf(config.kv_cache_dtype) || row.kv_cache_dtype || cfg.kv_cache_dtype || null,
      endpoint:config.endpoint?.id || run.provider?.identity?.id || cfg.endpoint_id || null,
      temperature:row.temperature ?? cfg.temperature ?? null, max_tokens:row.max_tokens ?? cfg.max_tokens ?? null,
      placement:row.gpu_offload ?? cfg.gpu_offload ?? null,
      phase_protocol:[...new Set((row.requests?.length ? row.requests : [row]).map(request=>JSON.stringify([request.timing_scope?.phase_protocol ?? "legacy_boundary",request.timing_scope?.decode_end_boundary ?? "legacy_boundary"])))].sort().map(JSON.parse),
      measurement_mode:run.measurement_mode || "helper"};
  }
  function build(runs, options={}) {
    const groups=new Map(), prompts=new Set(), recordedPrompts=new Set(), levels=new Set();
    for (const run of runs) {
      for (const row of run.results || []) {
        if (options.models && !options.models.includes(row.model)) continue;
        if (options.allow && !options.allow(run,row)) continue;
        const n=row.concurrency || 1;
        if (!Number.isInteger(n) || n<1 || !number(row.target_tokens) || row.target_tokens<=0) continue;
        const identity=condition(run,row), id=stable([run.id,row.model,identity]);
        if (!groups.has(id)) groups.set(id,{id,run,model:row.model,identity,rows:[],cells:new Map()});
        const group=groups.get(id); group.rows.push(row);
        const key=`${row.target_tokens}:${n}`;
        if (!group.cells.has(key)) group.cells.set(key,{n,prompt:row.target_tokens,rows:[]});
        group.cells.get(key).rows.push(row); prompts.add(row.target_tokens); recordedPrompts.add(row.target_tokens); levels.add(n);
      }
      for (const n of run.config?.concurrency_levels || []) if (Number.isInteger(n) && n>0) levels.add(n);
      for (const size of run.config?.sizes || []) if (number(size) && size>0) prompts.add(size);
    }
    for (const group of groups.values()) for (const cell of group.cells.values()) {
      const ok=cell.rows.filter(successful);
      cell.ok=ok; cell.successes=ok.length; cell.attempts=cell.rows.length;
      cell.status=ok.length ? "ok" : cell.rows.some(row=>row.status==="failed" && row.fail_kind==="timeout") ? "timeout"
        : cell.rows.some(row=>row.status==="failed" || (row.status==="ok" && !successful(row) && !unverifiedGroup(row))) ? "failed"
        : cell.rows.some(unverifiedGroup) ? "unverified" : cell.rows[0]?.status || "missing";
      cell.metrics=Object.fromEntries(["output","decode","prefill","ttft","finish","batchwall","energy"].map(key=>[key,median(ok.map(row=>measurements(row)[key]))]));
      cell.estimated=ok.some(estimated);
      cell.decodeBoundaryEstimated=ok.some(row=>number(measurements(row).decode) && decodeBoundaryEstimated(row));
      cell.legacyDecodeBoundary=ok.some(row=>number(measurements(row).decode) && legacyDecodeBoundary(row));
      cell.ranges={};
      for (const [metric,key] of [["ttft","ttft_s"],["finish","wall_s"]]) {
        const values=ok.flatMap(row=>requestValues(row,key));
        cell.ranges[metric]=values.length ? [Math.min(...values),Math.max(...values)] : null;
      }
      cell.metricCounts=Object.fromEntries(["output","decode","prefill","ttft","finish","batchwall","energy"].map(key=>[key,ok.filter(row=>number(measurements(row)[key])).length]));
    }
    const result={groups:[...groups.values()],prompts:[...prompts].sort((a,b)=>a-b),recordedPrompts:[...recordedPrompts].sort((a,b)=>a-b),levels:[...levels].sort((a,b)=>a-b)};
    for (const group of result.groups) for (const cell of group.cells.values()) {
      const baseline=group.cells.get(`${cell.prompt}:1`);
      const pairs=matchedPairs(baseline,cell);
      cell.matchedBaselineRepeats=pairs.length;
      cell.metrics.gain=median(pairs.map(([a,b])=>measurements(b).output/measurements(a).output));
      cell.decodeRetention=median(pairs.map(([a,b])=>{const x=median(requestValues(a,"decode_tps")), y=median(requestValues(b,"decode_tps")); return x>0 && number(y) ? 100*y/x : null;}));
      cell.gainUnavailableReason=!baseline || !(baseline.metrics.output>0) ? "No measured 1-request end-to-end output baseline"
        : baseline.estimated || cell.estimated ? "Estimated output counts; measured scaling unavailable"
        : !pairs.length ? "No matching successful 1-request repeats with the same recorded settings" : "";
    }
    return result;
  }
  function compatibleAllocation(a,b) {
    const signature=row=>stable([row.per_request_context_length ?? row.context_length ?? null,row.gpu_offload ?? null,
      row.power?.scope ?? row.group_power?.scope ?? null,row.power?.gpu_indices ?? row.group_power?.gpu_indices ?? null]);
    const left=new Set(a.ok.map(signature)),right=new Set(b.ok.map(signature));
    return left.size===1 && right.size===1 && [...left][0]===[...right][0];
  }
  function matchedPairs(baseline,cell) {
    if (!baseline) return [];
    const byRepeat=rows=>{
      const result=new Map();
      for(const row of rows) {const key=row.repeat || 0;if(!result.has(key))result.set(key,[]);result.get(key).push(row);}
      return result;
    };
    const left=byRepeat(baseline.ok),right=byRepeat(cell.ok),pairs=[];
    for (const [repeat,rows] of right) {
      const base=left.get(repeat);
      if (base?.length!==1 || rows.length!==1) continue;
      const a=base[0],b=rows[0];
      if (!estimated(a) && !estimated(b) && measurements(a).output>0 && number(measurements(b).output)
        && compatibleAllocation({ok:[a]},{ok:[b]})) pairs.push([a,b]);
    }
    return pairs;
  }
  function getCell(group,prompt,n) {return group.cells.get(`${prompt}:${n}`);}
  function requestTimeline(row) {
    // Only align concurrent streams when a shared-origin offset was recorded.
    const requests=row.requests?.length ? row.requests : (row.concurrency || 1)===1 ? [row] : [];
    const entries=requests.map((request,index)=>({index:index+1,status:request.status || row.status,
      start:(row.concurrency || 1)===1 ? 0 : (nonnegative(request.start_offset_s) ? request.start_offset_s : null),
      first:nonnegative(request.ttft_s) && ((row.concurrency || 1)===1 || nonnegative(request.start_offset_s)) ? (request.start_offset_s || 0)+request.ttft_s : null,
      finish:nonnegative(request.wall_s) && ((row.concurrency || 1)===1 || nonnegative(request.start_offset_s)) ? (request.start_offset_s || 0)+request.wall_s : null}));
    const firsts=entries.map(item=>item.first).filter(nonnegative);
    return {entries,first:firsts.length ? Math.min(...firsts) : null,median:median(firsts),last:firsts.length ? Math.max(...firsts) : null,
      duration:Math.max(1,...entries.map(item=>item.finish ?? item.first ?? 0))};
  }
  function best(data,prompt,model) {
    return data.groups.filter(group=>model==="__all__" || group.model===model).flatMap(group=>[...group.cells.values()].filter(cell=>cell.prompt===prompt && number(cell.metrics.decode)).map(cell=>({group,cell})))
      .sort((a,b)=>b.cell.metrics.decode-a.cell.metrics.decode || a.cell.n-b.cell.n)[0] || null;
  }
  function csv(data,prompt) {
    const quote=value=>`"${String(value ?? "").replace(/^[=+\-@\t\r]/,"'$&").replace(/"/g,'""')}"`;
    const fields=["benchmark","model","quant","configuration_id","prompt_target","concurrency","status","successful_repeats","recorded_attempts","collective_decode_tps","end_to_end_output_tps","collective_prefill_tps","median_request_ttft_s","median_request_completion_s","median_batch_wall_s","end_to_end_output_gain_vs_1","gpu_j_per_output_token_including_prefill","estimated_counts","decode_count_estimated_boundary","legacy_decode_end_boundary_unknown","matched_baseline_repeats","benchmark_label","condition_json","gpu_power_scope"];
    const rows=data.groups.flatMap(group=>[...group.cells.values()].filter(cell=>cell.prompt===prompt).map(cell=>[
      group.run.parent_run_id || group.run.id,group.model,group.identity.quant,group.identity.configuration_id,cell.prompt,cell.n,cell.status,cell.successes,cell.attempts,
      ...["decode","output","prefill","ttft","finish","batchwall","gain","energy"].map(key=>cell.metrics[key]),cell.estimated,cell.decodeBoundaryEstimated,cell.legacyDecodeBoundary,cell.matchedBaselineRepeats,
      group.run.label,stable(group.identity),[...new Set(cell.rows.map(row=>(row.group_power || row.power)?.scope || 'unknown'))].join(';')]));
    return [fields,...rows].map(row=>row.map(quote).join(",")).join("\r\n");
  }

  function renderCompact(container, options) {
    const data=build(options.runs,options), charts=options.charts;
    if (!data.groups.length) {
      container.innerHTML='<div class="empty"><h3>No matching measurements</h3><p>Choose models and configurations to see concurrency measurements.</p></div>';
      return;
    }
    const key=options.metric, metric=metricInfo[key];
    if (!metric) {
      container.innerHTML='<div class="empty"><h3>Choose a concurrency measure</h3><p>This measure is not available for the concurrency axis.</p></div>';
      return;
    }
    const prompt=options.prompt ?? data.recordedPrompts[0];
    const state=options.state || (options.state={});
    const view=state.compactEvidence || (state.compactEvidence={});
    view.model="__all__"; view.mapMetric=key;
    let selected=data.groups.find(group=>group.id===view.selectedGroup);
    if (!selected || !getCell(selected,prompt,view.n)) {
      selected=data.groups.find(group=>[...group.cells.values()].some(cell=>cell.prompt===prompt)) || data.groups[0];
      view.selectedGroup=selected.id;
      view.n=data.levels.find(n=>getCell(selected,prompt,n)) || data.levels[0];
    }
    const selectedCell=getCell(selected,prompt,view.n);
    const modelCount=new Set(data.groups.map(group=>group.model)).size;
    const style=group=>options.style(group,{compareModels:modelCount>1});
    const label=group=>{
      const run=group.run;
      const gpu=run.gpu?.name || run.hardware?.gpu?.name || (Array.isArray(run.hardware?.gpus) ? run.hardware.gpus.map(item=>item.name).filter(Boolean).join(', ') : '') || 'not recorded';
      return `${group.model} · ${run.label || run.id} · Engine: ${group.identity.engine} · GPU: ${gpu} · ${group.identity.quant}${group.identity.configuration_id ? ` · ${group.identity.configuration_id}` : ""}${group.identity.placement!=null ? ` · GPU offload ${group.identity.placement}` : ""}${group.identity.mtp==="on" || group.identity.mtp?.requested==="on" ? " · MTP on" : ""}`;
    };
    const legend=data.groups.map(group=>{
      const s=style(group);
      const marker=s.marker==="square" ? '<rect x="11" y="3" width="6" height="6"/>' : s.marker==="triangle" ? '<polygon points="14,2 18,10 10,10"/>' : s.marker==="diamond" ? '<polygon points="14,2 18,6 14,10 10,6"/>' : '<circle cx="14" cy="6" r="3"/>';
      return `<span><svg width="28" height="12" aria-hidden="true"><line x1="1" y1="6" x2="27" y2="6" stroke="${escape(s.color)}" stroke-width="2"${s.dash ? ` stroke-dasharray="${escape(s.dash)}"` : ""}/><g fill="${escape(s.color)}">${marker}</g></svg>${escape(label(group))}</span>`;
    }).join("");
    const chartless=options.evidenceOnly===true;
    container.innerHTML=`<div class="sc-workspace sc-compact">
      <section class="sc-chart card"${chartless ? " hidden" : ""}><header><div><h3>${escape(metric.title)}</h3><span class="sc-direction">${metric.direction==="low" ? "↓ Lower is better" : "↑ Higher is better"}</span></div></header>
        <p class="hint">Prompt fixed at ${escape(tokens(prompt))} tokens per request · concurrent query count changes along the x-axis.</p>
        <div class="sc-condition-key" aria-label="Chart series">${legend}${key==="gain" ? '<span><svg width="28" height="12" aria-hidden="true"><line x1="1" y1="6" x2="27" y2="6" stroke="currentColor" stroke-width="2" stroke-dasharray="3 5"/></svg>Linear reference</span>' : ""}</div>
        <div class="plot" data-sc-chart="${escape(key)}"></div><p class="hint">${escape(metric.description)}</p>
        <p class="sc-no-data" data-sc-empty="${escape(key)}" hidden>Not recorded for this selection. Missing metrics are not estimated from other rates.</p>
      </section>
      <details class="sc-compact-details"${view.open ? " open" : ""}><summary>${chartless ? "Every query count at a glance, and what each request did" : "Concurrency map and request evidence"}</summary>
        <section class="sc-matrix card"><header><h3>Concurrency map</h3></header><p class="hint">${escape(metric.unit)} · Color is relative within each row. Select a cell for request evidence.</p>
          <div class="sc-matrix-scroll">${matrix(data,prompt,view,style)}</div>
          <div class="sc-map-key"><span><i class="sc-heat-swatch"></i>Stronger result in this row</span><span>× Failed / timed out</span><span>— Not measured</span><span>Ⅱ Skipped / cancelled</span><span>* Estimated output count or approximate decode boundary; legacy intervals may include trailing delivery</span></div>
        </section>
        <section class="sc-evidence card"><header><div><h3>${escape(selected.model)} · ${escape(view.n)} concurrent queries</h3></div><span class="badge">${escape(selectedCell?.status || "not measured")}</span></header>
          <p class="sc-evidence-condition">${escape(label(selected))}</p>${evidence(selected,selectedCell)}</section>
        <p class="sc-method-note">Prompt targets are per request; actual counts are retained above. Concurrency means client requests, not verified parallel engine slots. Medians use successful complete groups; failed, partial and missing cases stay visible.</p>
      </details></div>`;
    const details=container.querySelector('.sc-compact-details');
    details.addEventListener('toggle',()=>{view.open=details.open;});
    const select=(group,n)=>{
      view.selectedGroup=group.id; view.n=n; view.open=true;
      container.querySelectorAll('.plot').forEach(plot=>charts.release?.(plot));
      renderCompact(container,options);
      container.querySelector('.sc-evidence')?.scrollIntoView({behavior:'smooth',block:'nearest'});
    };
    container.querySelectorAll('[data-sc-cell]').forEach(button=>button.addEventListener('click',()=>select(data.groups[Number(button.dataset.scCell)],Number(button.dataset.scN))));
    if (chartless) return;
    const series=data.groups.map(group=>{
      const points=[],failures=[];
      for (const n of data.levels) {
        const cell=getCell(group,prompt,n);
        if (cell && number(cell.metrics[key])) points.push({x:n,y:cell.metrics[key],low:cell.ranges[key]?.[0],high:cell.ranges[key]?.[1],
          note:`${cell.successes} successful / ${cell.attempts} recorded repeats${metricEstimateNote(cell,key)}${key==="gain" && cell.gainUnavailableReason ? ` · ${cell.gainUnavailableReason}` : ""}`,
          onSelect:()=>select(group,n)});
        else if (cell && ['failed','timeout'].includes(cell.status)) failures.push({x:n,reason:cell.status});
      }
      return {id:group.id,name:label(group),...style(group),points,failures};
    });
    if (key==="gain" && series.some(item=>item.points.length)) series.push({id:'linear-reference',name:'Linear reference',color:'var(--text-muted)',dash:'3 5',marker:'diamond',reference:true,points:data.levels.map(n=>({x:n,y:n,note:'Reference only; not a measurement'}))});
    container.querySelector(`[data-sc-empty="${key}"]`).hidden=series.some(item=>!item.reference && item.points.length);
    charts.line(container.querySelector(`[data-sc-chart="${key}"]`),{series,xTicks:data.levels,xScale:'linear',yScale:'linear',height:300,
      xLabel:'Concurrent queries',xUnit:'concurrent queries',yLabel:metric.unit,unit:metric.unit,breakMissing:true,
      ariaLabel:`${metric.title} by concurrent queries; prompt fixed at ${tokens(prompt)} tokens per request`});
  }
  function render(container, options) {
    if (options.compact) return renderCompact(container,options);
    const data=build(options.runs,options), view=options.state, charts=options.charts;
    const models=[...new Set(data.groups.map(group=>group.model))].sort();
    if (!models.length) {container.innerHTML='<div class="empty"><h3>Choose models to explore</h3><p>Select a benchmark and models above to see their concurrency measurements.</p></div>';return;}
    if (!data.prompts.includes(view.prompt)) view.prompt=data.recordedPrompts.includes(20000) ? 20000 : data.recordedPrompts[0] || data.prompts[0];
    if (view.model!=="__all__" && !models.includes(view.model)) view.model=models.length>1 && models.length<=6 ? "__all__" : models[0];
    if (!tabs[view.tab]) view.tab="throughput";
    const prompt=view.prompt, focus=data.groups.filter(group=>view.model==="__all__" || group.model===view.model), peak=best(data,prompt,view.model);
    const style=group=>{
      const base=options.style(group,{compareModels:view.model==="__all__"});
      const siblings=data.groups.filter(item=>item.model===group.model && (item.run.parent_run_id || item.run.id)===(group.run.parent_run_id || group.run.id));
      if (siblings.length<2) return base;
      const index=siblings.indexOf(group);
      return {...base,dash:[null,'6 4','2 3','9 3 2 3'][index%4],marker:['circle','square','triangle','diamond'][index%4]};
    };
    const label=group=>`${view.model==="__all__" ? group.model+" · " : ""}${group.run.label || group.run.id} · ${group.identity.quant}${group.identity.placement!=null ? ` · GPU offload ${group.identity.placement}` : ""}${group.identity.mtp==="on" || group.identity.mtp?.requested==="on" ? " · MTP on" : ""}`;
    const cellAt=group=>getCell(group,prompt,view.n || peak?.cell.n || data.levels[0]);
    let selected=data.groups.find(group=>group.id===view.selectedGroup);
    if (!selected || !cellAt(selected)) {selected=peak?.group || focus[0];view.selectedGroup=selected.id;view.n=peak?.cell.n || data.levels[0];}
    const selectedCell=getCell(selected,prompt,view.n);
    const decodeRetention=peak?.cell.decodeRetention;
    const measuredLevels=focus.flatMap(group=>[...group.cells.values()].filter(cell=>cell.prompt===prompt && cell.status==="ok").map(cell=>cell.n));
    const kpi=(label,value,unit,note,tone)=>`<article class="sc-kpi" data-tone="${tone}"><span>${label}</span><strong>${value}<small>${unit}</small></strong><p>${note}</p></article>`;
    const gains=peak?.cell.metrics.gain;
    container.innerHTML=`<div class="sc-workspace">
      <div class="sc-heading"><div><span class="sc-eyebrow">CONCURRENCY EXPLORER</span><h2>${options.summary ? "See how the workload scales." : "Find the useful concurrency."}</h2><p>Total work and each request's experience, at the same prompt target.</p></div>${options.summary ? '<button type="button" class="btn-sm sc-open-compare">Open full comparison →</button>' : '<button type="button" class="btn-sm sc-export">Export this slice</button>'}</div>
      <div class="sc-controls"><label>Prompt per request<select class="sc-prompt">${data.prompts.map(size=>`<option value="${size}" ${size===prompt ? "selected" : ""}>${tokens(size)} tokens</option>`).join("")}</select></label><label class="sc-model-control">Model in charts<select class="sc-model">${models.length>1 ? `<option value="__all__" ${view.model==="__all__" ? "selected" : ""}>All selected models (${models.length})</option>` : ""}${models.map(model=>`<option value="${escape(model)}" ${model===view.model ? "selected" : ""}>${escape(model)}</option>`).join("")}</select></label><div class="sc-tab-set" role="group" aria-label="Concurrency measures">${Object.keys(tabs).map(key=>`<button type="button" data-sc-tab="${key}" aria-pressed="${view.tab===key}">${{throughput:"Throughput",prefill:"Prefill",latency:"Latency",efficiency:"Efficiency"}[key]}</button>`).join("")}</div></div>
      <div class="sc-kpis">${kpi("Best collective decode",peak ? `${fmt(peak.cell.metrics.decode)}${metricEstimated(peak.cell,"decode") ? "*" : ""}` : "—","tok/s",peak ? `At ${peak.cell.n} requests · ${escape(peak.group.model)} · ${escape(peak.group.run.label || peak.group.run.id)}` : "No complete measured group","cyan")}${kpi("End-to-end output gain over 1 request",number(gains) ? `${fmt(gains)}×` : "—","",number(gains) ? `${peak.cell.matchedBaselineRepeats} matched repeat${peak.cell.matchedBaselineRepeats===1 ? "" : "s"} · ${fmt(gains/peak.cell.n*100)}% of linear scaling` : peak?.cell.gainUnavailableReason || "Needs a comparable, measured 1-request baseline","violet")}${kpi("Per-stream decode (median)",peak ? `${fmt(median(peak.cell.rows.filter(successful).flatMap(row=>requestValues(row,"decode_tps"))))}${metricEstimated(peak.cell,"decode") ? "*" : ""}` : fmt(null),"tok/s",number(decodeRetention) ? `${fmt(decodeRetention)}% of its matched 1-request median stream rate` : "Median single-stream decode among measured requests","amber")}${kpi("Highest successful load",measuredLevels.length ? String(Math.max(...measuredLevels)) : "—","requests","Highest observed success across shown conditions; not a capacity limit","green")}</div>
      <div class="sc-condition-key">${focus.map(group=>`<span><i style="background:${style(group).color}"></i>${escape(label(group))}${focus.filter(g=>g.run.id===group.run.id).length>1 ? `<small>condition ${escape(group.identity.configuration_id?.slice(0,8) || String(data.groups.indexOf(group)+1))}</small>` : ""}</span>`).join("")}</div>
      <div class="sc-charts">${tabs[view.tab].map(key=>`<section class="sc-chart card"><header><div><h3>${metricInfo[key].title}</h3><span class="sc-direction">${metricInfo[key].direction==="low" ? "↓ Lower is better" : "↑ Higher is better"}</span></div></header><div class="plot" data-sc-chart="${key}"></div><p class="hint">${metricInfo[key].description}</p><p class="sc-no-data" data-sc-empty="${key}" hidden>Not recorded for this selection. Missing metrics are not estimated from other rates.</p></section>`).join("")}</div>
      <section class="sc-matrix card"><header><div><span class="sc-eyebrow">EVERY MODEL · EVERY REQUEST COUNT</span><h3>Concurrency map</h3></div><label class="sc-map-metric-label">Show<select class="sc-map-metric">${Object.entries(metricInfo).map(([key,metric])=>`<option value="${key}" ${key===(view.mapMetric || tabs[view.tab][0]) ? "selected" : ""}>${metric.title}</option>`).join("")}</select></label></header><p class="hint"><strong>${metricInfo[view.mapMetric || tabs[view.tab][0]].unit}</strong> · ${metricInfo[view.mapMetric || tabs[view.tab][0]].direction==="low" ? "Lower is better" : "Higher is better"}. Color is relative within each row. Click a cell for its requests.</p><div class="sc-matrix-scroll">${matrix(data,prompt,view,style)}</div><div class="sc-map-key"><span><i class="sc-heat-swatch"></i>Stronger result in this row</span><span class="sc-failed-key">× Failed / timed out</span><span>— Not measured</span><span>Ⅱ Skipped / cancelled</span><span>* Estimated output count or approximate decode boundary; legacy intervals may include trailing delivery</span></div></section>
      <section class="sc-evidence card"><header><div><span class="sc-eyebrow">REQUEST EVIDENCE</span><h3>${escape(selected.model)} · ${view.n} concurrent request${view.n===1 ? "" : "s"}</h3></div><span class="badge">${escape(selectedCell?.status || "not measured")}</span></header><p class="sc-evidence-condition">${escape(label(selected))}</p>${evidence(selected,selectedCell)}</section>
      <p class="sc-method-note">Prompt targets are per request; actual counts are retained below. Concurrency means client requests, not a verified number of parallel engine slots. Medians use successful complete groups; failed, partial and missing cases stay visible. Different quants, reasoning, MTP and recorded configurations remain separate.</p>
    </div>`;
    const redraw=()=>{container.querySelectorAll('.plot').forEach(plot=>charts.release?.(plot));render(container,options);};
    container.querySelector('.sc-prompt').addEventListener('change',event=>{view.prompt=Number(event.target.value);redraw();});
    container.querySelector('.sc-model').addEventListener('change',event=>{view.model=event.target.value;view.selectedGroup=null;redraw();});
    container.querySelectorAll('[data-sc-tab]').forEach(button=>button.addEventListener('click',()=>{view.tab=button.dataset.scTab;view.mapMetric=tabs[view.tab][0];redraw();}));
    container.querySelector('.sc-map-metric').addEventListener('change',event=>{view.mapMetric=event.target.value;redraw();});
    container.querySelectorAll('[data-sc-cell]').forEach(button=>button.addEventListener('click',()=>{
      const group=data.groups[Number(button.dataset.scCell)];view.selectedGroup=group.id;view.n=Number(button.dataset.scN);view.model=group.model;redraw();
      container.querySelector('.sc-evidence').scrollIntoView({behavior:'smooth',block:'nearest'});
    }));
    container.querySelector('.sc-open-compare')?.addEventListener('click',()=>options.openCompare?.());
    container.querySelector('.sc-export')?.addEventListener('click',()=>{
      const blob=new Blob([csv(data,prompt)],{type:'text/csv;charset=utf-8'}),url=URL.createObjectURL(blob),link=document.createElement('a');
      link.href=url;link.download=`wattever-concurrency-${prompt}.csv`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    });
    for (const key of tabs[view.tab]) {
      const metric=metricInfo[key], series=focus.map(group=>{
        const points=[],failures=[];
        for (const n of data.levels) {
          const cell=getCell(group,prompt,n);
          if (cell && number(cell.metrics[key])) points.push({x:n,y:cell.metrics[key],low:cell.ranges[key]?.[0],high:cell.ranges[key]?.[1],
            note:`${cell.successes} successful / ${cell.attempts} recorded repeat${cell.attempts===1 ? "" : "s"}${metricEstimateNote(cell,key)}${cell.gainUnavailableReason ? ` · ${cell.gainUnavailableReason}` : ""}`,onSelect:()=>{
              view.selectedGroup=group.id;view.n=n;redraw();container.querySelector('.sc-evidence').scrollIntoView({behavior:'smooth',block:'nearest'});
            }});
          else if (cell && ['failed','timeout'].includes(cell.status)) failures.push({x:n,reason:cell.status});
        }
        return {id:group.id,name:label(group),...style(group),points,failures};
      });
      if (key==="gain" && series.some(item=>item.points.length)) series.push({id:'linear-reference',name:'Linear reference',color:'var(--text-muted)',dash:'3 5',marker:'diamond',reference:true,points:data.levels.map(n=>({x:n,y:n,note:'Reference only; not a measurement'}))});
      const populated=series.some(item=>!item.reference && item.points.length);
      container.querySelector(`[data-sc-empty="${key}"]`).hidden=populated;
      charts.line(container.querySelector(`[data-sc-chart="${key}"]`),{series,xTicks:data.levels,xScale:'linear',yScale:'linear',height:300,
        xLabel:'Concurrent requests',xUnit:'concurrent requests',yLabel:metric.unit,unit:metric.unit,breakMissing:true,
        ariaLabel:`${metric.title} by concurrent requests for ${view.model}`});
    }
  }
  function matrix(data,prompt,view,style) {
    const key=view.mapMetric || tabs[view.tab][0],metric=metricInfo[key];
    return `<table class="sc-matrix-table"><thead><tr><th>Model / recorded condition</th>${data.levels.map(n=>`<th scope="col">${n}<small>request${n===1 ? "" : "s"}</small></th>`).join("")}</tr></thead><tbody>${data.groups.map((group,index)=>{
      const values=data.levels.map(n=>getCell(group,prompt,n)?.metrics[key]).filter(number),min=Math.min(...values),max=Math.max(...values);
      return `<tr class="${group.model===view.model ? "sc-focused-row" : ""}"><th scope="row"><i style="background:${style(group).color}"></i><span>${escape(group.model)}<small>${escape(group.run.label || group.run.id)} · ${escape(group.identity.quant)}${group.identity.placement!=null ? ` · GPU offload ${escape(group.identity.placement)}` : ""}${group.identity.mtp==="on" || group.identity.mtp?.requested==="on" ? " · MTP on" : ""}${data.groups.filter(item=>item.model===group.model && item.run.id===group.run.id).length>1 ? ` · ${escape(group.identity.configuration_id?.slice(0,8) || `condition ${index+1}`)}` : ""}</small></span></th>${data.levels.map(n=>{
        const cell=getCell(group,prompt,n),value=cell?.metrics[key],selected=view.selectedGroup===group.id && view.n===n;
        const strength=!number(value) ? 0 : max===min ? .55 : metric.direction==="low" ? (max-value)/(max-min) : (value-min)/(max-min);
        const state=!cell ? 'missing' : ['failed','timeout'].includes(cell.status) ? 'failed' : cell.status==="unverified" ? 'unavailable' : cell.status!=="ok" ? 'skipped' : !number(value) ? 'unavailable' : 'measured';
        const title=`${group.model}, ${n} concurrent requests, ${tokens(prompt)} prompt: ${number(value) ? `${fmt(value)} ${metric.unit}` : cell?.status || "not measured"}`;
        return `<td><button type="button" data-sc-cell="${index}" data-sc-n="${n}" data-state="${state}" aria-pressed="${selected}" aria-label="${escape(title)}" title="${escape(title)}" style="--sc-strength:${.08+strength*.36}"><b>${number(value) ? `${metricEstimated(cell,key) ? '*' : ''}${fmt(value)}${key==="gain" ? '×' : ''}` : state==='failed' ? '×' : state==='skipped' ? 'Ⅱ' : '—'}</b><small>${cell?.status==='unverified' ? 'Unverified group' : ['failed','skipped'].includes(state) ? escape(cell.status) : state==='missing' ? 'Not measured' : state==='unavailable' ? 'Unavailable' : `${cell.successes}/${cell.attempts} repeats`}</small></button></td>`;
      }).join("")}</tr>`;
    }).join("")}</tbody></table>`;
  }
  function evidence(group,cell) {
    if (!cell) return '<p class="hint">No case was recorded for this prompt target and concurrency. This is not a failed test.</p>';
    const requestRows=cell.rows.flatMap(row=>(row.requests?.length ? row.requests : (row.concurrency || 1)===1 ? [row] : []).map((request,index)=>`<tr><td>${(row.repeat || 0)+1}</td><td>${index+1}</td><td>${escape(request.status || row.status)}</td><td>${fmt(request.prompt_tokens)}</td><td>${fmt(request.completion_tokens)}${estimated(request) ? '*' : ''}</td><td>${fmt(request.ttft_s)}</td><td>${fmt(phaseMetrics?.decodePhase({...request,concurrency:1})?.tps)}${number(phaseMetrics?.decodePhase({...request,concurrency:1})?.tps) && (estimated(request) || decodeBoundaryEstimated(request) || legacyDecodeBoundary({...request,concurrency:1})) ? "*" : ""}</td><td>${fmt(request.wall_s)}</td></tr>`)).join('');
    const notes=cell.rows.filter(row=>row.status!=="ok" || !successful(row)).map(row=>row.message || row.fail_kind || (unverifiedGroup(row) ? "Request-group completeness was not recorded; excluded from successful scaling." : "Incomplete request group"));
    const settings=cell.rows.map(row=>({repeat:(row.repeat || 0)+1,group_wall_s:row.wall_s,actual_output_tokens:outputCount(row),
      context_tokens:row.context_length,per_request_context_tokens:row.per_request_context_length,placement:row.gpu_offload,
      model_configuration:row.model_configuration || group.identity,power_scope:(row.group_power || row.power)?.scope || "unreported",
      gpus:(row.group_power || row.power)?.gpus,
      low_confidence_energy:(row.group_power || row.power)?.low_confidence ?? "unreported",
      recorded_metric_sources:row.metric_sources || null,request_timing_scopes:(row.requests || [row]).map(request=>request.timing_scope || null)}));
    const timelines=cell.rows.map(row=>{
      const timing=requestTimeline(row);
      const missingOffsets=(row.concurrency || 1)>1 && Array.isArray(row.requests) && row.requests.some(request=>!nonnegative(request.start_offset_s) && (nonnegative(request.ttft_s) || nonnegative(request.wall_s)));
      const unavailable=missingOffsets ? `<p class="hint">Repeat ${(row.repeat || 0)+1}: shared timing is unavailable for requests without recorded start offsets. Their individual durations remain in the table.</p>` : "";
      if (!timing.entries.some(item=>item.first!=null || item.finish!=null)) return unavailable;
      return `${unavailable}<section class="sc-timeline"><h4>Repeat ${(row.repeat || 0)+1} · request timing</h4><div class="sc-timing-summary"><span>Earliest first token <b>${fmt(timing.first)} s</b></span><span>Median <b>${fmt(timing.median)} s</b></span><span>Latest <b>${fmt(timing.last)} s</b></span></div>${timing.entries.map(item=>`<div class="sc-timeline-row"><span>Request ${item.index}</span><div class="sc-timeline-track" role="img" aria-label="${escape(`Request ${item.index}: ${item.first==null ? 'first token unavailable' : `first token at ${fmt(item.first)} seconds`}, ${item.finish==null ? 'completion unavailable' : `completed at ${fmt(item.finish)} seconds`}`)}">${item.first!=null ? `<i class="sc-time-wait" style="left:${100*item.start/timing.duration}%;width:${100*(item.first-item.start)/timing.duration}%"></i>` : ''}${item.first!=null && item.finish!=null && item.finish>=item.first ? `<i class="sc-time-response" style="left:${100*item.first/timing.duration}%;width:${100*(item.finish-item.first)/timing.duration}%"></i>` : ''}</div><small>${item.finish!=null ? `${fmt(item.finish)} s` : escape(item.status)}</small></div>`).join('')}<p class="hint"><i class="sc-time-key sc-time-wait"></i> Wait to first token <i class="sc-time-key sc-time-response"></i> After first token, including pauses. Times use the group start; older records without dispatch offsets cannot be aligned.</p></section>`;
    }).join('');
    const placements=[...new Set(cell.rows.map(row=>row.gpu_offload).filter(value=>value!=null))];
    const placementNote=placements.some(partialOffload) ? '<p class="sc-error-note">Partial GPU offload: CPU work may dominate. This placement is a separate condition from full GPU offload.</p>' : '';
    return `<div class="sc-evidence-stats"><span><b>${fmt(cell.metrics.decode)}${number(cell.metrics.decode) && metricEstimated(cell,"decode") ? "*" : ""}</b> collective decode tok/s</span><span><b>${fmt(cell.metrics.prefill)}</b> collective prefill tok/s</span><span><b>${fmt(cell.metrics.ttft)}</b> s to first token</span><span><b>${cell.successes}/${cell.attempts}</b> successful repeats</span></div>${placementNote}${notes.length ? `<p class="sc-error-note">${notes.map(escape).join(' · ')}</p>` : ""}${timelines}<div class="sc-evidence-scroll"><table><thead><tr><th>Repeat</th><th>Request</th><th>Status</th><th>Prompt tokens</th><th>Output tokens</th><th>First token (s)</th><th>Decode (tok/s)</th><th>Complete (s)</th></tr></thead><tbody>${requestRows}</tbody></table></div><details class="sc-settings"><summary>Recorded settings and measurement scope</summary><pre>${escape(JSON.stringify(settings,null,2))}</pre></details>`;
  }
  const api={phaseMetrics,build,successful,partialOffload,measurements,outputCount,gpuEnergy,compatibleAllocation,getCell,best,csv,metricInfo,median,requestTimeline,render};
  if (typeof module!=="undefined" && module.exports) module.exports=api;
  root.WatteverConcurrency=api;
})(typeof globalThis!=="undefined" ? globalThis : this);
