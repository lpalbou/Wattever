/* Concurrency analysis uses recorded request groups, never sums client rates. */
(function (root) {
  "use strict";
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
    total: {title:"Total output throughput",unit:"tok/s",direction:"high",description:"All output tokens ÷ elapsed time for the whole request group. Includes prompt processing and queueing."},
    decode: {title:"Speed for each request",unit:"tok/s / request",direction:"high",description:"Median recorded per-request decode speed after its first token. Includes pauses while other requests prefill or wait for the scheduler; not total throughput."},
    prefill: {title:"Prompt processing per request",unit:"tok/s / request",direction:"high",description:"Median recorded client prefill rate: prompt tokens ÷ time to first token. Includes queueing and transport; this is not isolated backend compute."},
    ttft: {title:"Wait for the first token",unit:"seconds",direction:"low",description:"Median client time to first token. Includes queueing, transport and prompt processing. Whiskers show the observed request range."},
    finish: {title:"Wait for a complete response",unit:"seconds",direction:"low",description:"Median per-request completion time; whiskers show the fastest and slowest observed requests."},
    gain: {title:"Throughput scaling",unit:"× the 1-request rate",direction:"high",description:"Median gain across matching successful repeats at the same prompt target and recorded settings. The dotted line is a linear reference, not a prediction."},
    energy: {title:"GPU energy per output token",unit:"J / token",direction:"low",description:"Whole-group GPU energy, including prompt processing, divided by total output tokens. Overlapping requests are counted once; low-confidence energy is excluded."},
  };
  const tabs = {throughput:["total","decode"],prefill:["prefill","ttft"],latency:["ttft","finish"],efficiency:["gain","energy"]};

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
    if (Array.isArray(row.requests) && row.requests.length) return row.requests.filter(item=>item.status==="ok").map(item=>item[key]).filter(nonnegative);
    return (row.concurrency || 1)===1 && nonnegative(row[key]) ? [row[key]] : [];
  }
  function outputCount(row) {
    if ((row.concurrency || 1)===1) return nonnegative(row.completion_tokens) ? row.completion_tokens : null;
    if (nonnegative(row.aggregate_completion_tokens)) return row.aggregate_completion_tokens;
    return row.requests?.length && row.requests.every(item=>nonnegative(item.completion_tokens))
      ? row.requests.reduce((sum,item)=>sum+item.completion_tokens,0) : null;
  }
  function gpuEnergy(row) {
    const p=row.group_power || row.power;
    if (!p || !p.scope || p.low_confidence!==false || p.available===false) return null;
    if (row.group_power) return p.gpu_power_complete!==true || (number(p.gpu_sampled_coverage_fraction) && p.gpu_sampled_coverage_fraction<.8) ? null : nonnegative(p.gpu_energy_j) ? p.gpu_energy_j : null;
    if (p.prefill_gpu_power_complete!==true || p.decode_gpu_power_complete!==true) return null;
    return nonnegative(p.prefill_energy_j) && nonnegative(p.decode_energy_j) ? p.prefill_energy_j+p.decode_energy_j : null;
  }
  function measurements(row) {
    const count=outputCount(row), energy=gpuEnergy(row);
    const total=nonnegative(row.aggregate_completion_tps) ? row.aggregate_completion_tps : count!=null && row.wall_s>0 ? count/row.wall_s : null;
    return {total,decode:median(requestValues(row,"decode_tps")),prefill:median(requestValues(row,"prefill_tps")),ttft:median(requestValues(row,"ttft_s")),
      finish:median(requestValues(row,"wall_s")),energy:count>0 && energy!=null && !estimated(row) ? energy/count : null};
  }
  function estimated(row) {
    return !!(row.completion_tokens_estimated || row.aggregate_completion_tokens_estimated || row.requests?.some(item=>item.completion_tokens_estimated));
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
      cell.metrics=Object.fromEntries(["total","decode","prefill","ttft","finish","energy"].map(key=>[key,median(ok.map(row=>measurements(row)[key]))]));
      cell.estimated=ok.some(estimated);
      cell.ranges={};
      for (const [metric,key] of [["ttft","ttft_s"],["finish","wall_s"]]) {
        const values=ok.flatMap(row=>requestValues(row,key));
        cell.ranges[metric]=values.length ? [Math.min(...values),Math.max(...values)] : null;
      }
      cell.metricCounts=Object.fromEntries(["total","decode","prefill","ttft","finish","energy"].map(key=>[key,ok.filter(row=>number(measurements(row)[key])).length]));
    }
    const result={groups:[...groups.values()],prompts:[...prompts].sort((a,b)=>a-b),recordedPrompts:[...recordedPrompts].sort((a,b)=>a-b),levels:[...levels].sort((a,b)=>a-b)};
    for (const group of result.groups) for (const cell of group.cells.values()) {
      const baseline=group.cells.get(`${cell.prompt}:1`);
      const pairs=matchedPairs(baseline,cell);
      cell.matchedBaselineRepeats=pairs.length;
      cell.metrics.gain=median(pairs.map(([a,b])=>measurements(b).total/measurements(a).total));
      cell.decodeRetention=median(pairs.filter(([a,b])=>measurements(a).decode>0 && number(measurements(b).decode)).map(([a,b])=>100*measurements(b).decode/measurements(a).decode));
      cell.gainUnavailableReason=!baseline || !(baseline.metrics.total>0) ? "No measured 1-request throughput baseline"
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
      if (!estimated(a) && !estimated(b) && measurements(a).total>0 && number(measurements(b).total)
        && compatibleAllocation({ok:[a]},{ok:[b]})) pairs.push([a,b]);
    }
    return pairs;
  }
  function getCell(group,prompt,n) {return group.cells.get(`${prompt}:${n}`);}
  function requestTimeline(row) {
    // Saved requests omit their monotonic start timestamps. Do not invent a
    // shared origin, uninterrupted GPU activity, or timing for failed streams.
    const requests=row.requests?.length ? row.requests : (row.concurrency || 1)===1 ? [row] : [];
    const entries=requests.map((request,index)=>({index:index+1,status:request.status || row.status,
      first:nonnegative(request.ttft_s) ? request.ttft_s : null,
      finish:nonnegative(request.wall_s) ? request.wall_s : null}));
    const firsts=entries.map(item=>item.first).filter(nonnegative);
    return {entries,first:firsts.length ? Math.min(...firsts) : null,median:median(firsts),last:firsts.length ? Math.max(...firsts) : null,
      duration:Math.max(1,...entries.map(item=>item.finish ?? item.first ?? 0))};
  }
  function best(data,prompt,model) {
    return data.groups.filter(group=>model==="__all__" || group.model===model).flatMap(group=>[...group.cells.values()].filter(cell=>cell.prompt===prompt && number(cell.metrics.total)).map(cell=>({group,cell})))
      .sort((a,b)=>b.cell.metrics.total-a.cell.metrics.total || a.cell.n-b.cell.n)[0] || null;
  }
  function csv(data,prompt) {
    const quote=value=>`"${String(value ?? "").replace(/^[=+\-@\t\r]/,"'$&").replace(/"/g,'""')}"`;
    const fields=["benchmark","model","quant","configuration_id","prompt_target","concurrency","status","successful_repeats","recorded_attempts","total_output_tps","median_request_decode_tps","median_request_prefill_tps","median_request_ttft_s","median_request_completion_s","gain_vs_1","gpu_j_per_output_token_including_prefill","estimated_counts","matched_baseline_repeats","benchmark_label","condition_json","gpu_power_scope"];
    const rows=data.groups.flatMap(group=>[...group.cells.values()].filter(cell=>cell.prompt===prompt).map(cell=>[
      group.run.parent_run_id || group.run.id,group.model,group.identity.quant,group.identity.configuration_id,cell.prompt,cell.n,cell.status,cell.successes,cell.attempts,
      ...["total","decode","prefill","ttft","finish","gain","energy"].map(key=>cell.metrics[key]),cell.estimated,cell.matchedBaselineRepeats,
      group.run.label,stable(group.identity),[...new Set(cell.rows.map(row=>(row.group_power || row.power)?.scope || 'unknown'))].join(';')]));
    return [fields,...rows].map(row=>row.map(quote).join(",")).join("\r\n");
  }

  function render(container, options) {
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
    const peakDecode=peak?.cell.metrics.decode, decodeRetention=peak?.cell.decodeRetention;
    const measuredLevels=focus.flatMap(group=>[...group.cells.values()].filter(cell=>cell.prompt===prompt && cell.status==="ok").map(cell=>cell.n));
    const kpi=(label,value,unit,note,tone)=>`<article class="sc-kpi" data-tone="${tone}"><span>${label}</span><strong>${value}<small>${unit}</small></strong><p>${note}</p></article>`;
    const gains=peak?.cell.metrics.gain;
    container.innerHTML=`<div class="sc-workspace">
      <div class="sc-heading"><div><span class="sc-eyebrow">CONCURRENCY EXPLORER</span><h2>${options.summary ? "See how the workload scales." : "Find the useful concurrency."}</h2><p>Total work and each request's experience, at the same prompt target.</p></div>${options.summary ? '<button type="button" class="btn-sm sc-open-compare">Open full comparison →</button>' : '<button type="button" class="btn-sm sc-export">Export this slice</button>'}</div>
      <div class="sc-controls"><label>Prompt per request<select class="sc-prompt">${data.prompts.map(size=>`<option value="${size}" ${size===prompt ? "selected" : ""}>${tokens(size)} tokens</option>`).join("")}</select></label><label class="sc-model-control">Model in charts<select class="sc-model">${models.length>1 ? `<option value="__all__" ${view.model==="__all__" ? "selected" : ""}>All selected models (${models.length})</option>` : ""}${models.map(model=>`<option value="${escape(model)}" ${model===view.model ? "selected" : ""}>${escape(model)}</option>`).join("")}</select></label><div class="sc-tab-set" role="group" aria-label="Concurrency measures">${Object.keys(tabs).map(key=>`<button type="button" data-sc-tab="${key}" aria-pressed="${view.tab===key}">${{throughput:"Throughput",prefill:"Prefill",latency:"Latency",efficiency:"Efficiency"}[key]}</button>`).join("")}</div></div>
      <div class="sc-kpis">${kpi("Best recorded total",peak ? `${peak.cell.estimated ? "~" : ""}${fmt(peak.cell.metrics.total)}` : "—","tok/s",peak ? `At ${peak.cell.n} requests · ${escape(peak.group.model)} · ${escape(peak.group.run.label || peak.group.run.id)}` : "No complete measured group","cyan")}${kpi("Gain over one request",number(gains) ? `${fmt(gains)}×` : "—","",number(gains) ? `${peak.cell.matchedBaselineRepeats} matched repeat${peak.cell.matchedBaselineRepeats===1 ? "" : "s"} · ${fmt(gains/peak.cell.n*100)}% of linear scaling` : peak?.cell.gainUnavailableReason || "Needs a comparable, measured 1-request baseline","violet")}${kpi("Each request at that point",fmt(peakDecode),"tok/s",number(decodeRetention) ? `${fmt(decodeRetention)}% of its matched 1-request decode speed` : "Median recorded per-request decode speed","amber")}${kpi("Highest successful load",measuredLevels.length ? String(Math.max(...measuredLevels)) : "—","requests","Highest observed success across shown conditions; not a capacity limit","green")}</div>
      <div class="sc-condition-key">${focus.map(group=>`<span><i style="background:${style(group).color}"></i>${escape(label(group))}${focus.filter(g=>g.run.id===group.run.id).length>1 ? `<small>condition ${escape(group.identity.configuration_id?.slice(0,8) || String(data.groups.indexOf(group)+1))}</small>` : ""}</span>`).join("")}</div>
      <div class="sc-charts">${tabs[view.tab].map(key=>`<section class="sc-chart card"><header><div><h3>${metricInfo[key].title}</h3><span class="sc-direction">${metricInfo[key].direction==="low" ? "↓ Lower is better" : "↑ Higher is better"}</span></div></header><div class="plot" data-sc-chart="${key}"></div><p class="hint">${metricInfo[key].description}</p><p class="sc-no-data" data-sc-empty="${key}" hidden>Not recorded for this selection. Missing metrics are not estimated from other rates.</p></section>`).join("")}</div>
      <section class="sc-matrix card"><header><div><span class="sc-eyebrow">EVERY MODEL · EVERY REQUEST COUNT</span><h3>Concurrency map</h3></div><label class="sc-map-metric-label">Show<select class="sc-map-metric">${Object.entries(metricInfo).map(([key,metric])=>`<option value="${key}" ${key===(view.mapMetric || tabs[view.tab][0]) ? "selected" : ""}>${metric.title}</option>`).join("")}</select></label></header><p class="hint"><strong>${metricInfo[view.mapMetric || tabs[view.tab][0]].unit}</strong> · ${metricInfo[view.mapMetric || tabs[view.tab][0]].direction==="low" ? "Lower is better" : "Higher is better"}. Color is relative within each row. Click a cell for its requests.</p><div class="sc-matrix-scroll">${matrix(data,prompt,view,style)}</div><div class="sc-map-key"><span><i class="sc-heat-swatch"></i>Stronger result in this row</span><span class="sc-failed-key">× Failed / timed out</span><span>— Not measured</span><span>Ⅱ Skipped / cancelled</span><span>* Estimated token count</span></div></section>
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
            note:`${cell.successes} successful / ${cell.attempts} recorded repeat${cell.attempts===1 ? "" : "s"}${cell.estimated ? " · estimated output counts" : ""}${cell.gainUnavailableReason ? ` · ${cell.gainUnavailableReason}` : ""}`,onSelect:()=>{
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
        return `<td><button type="button" data-sc-cell="${index}" data-sc-n="${n}" data-state="${state}" aria-pressed="${selected}" aria-label="${escape(title)}" title="${escape(title)}" style="--sc-strength:${.08+strength*.36}"><b>${number(value) ? `${cell.estimated && ['total','decode'].includes(key) ? '*' : ''}${fmt(value)}${key==="gain" ? '×' : ''}` : state==='failed' ? '×' : state==='skipped' ? 'Ⅱ' : '—'}</b><small>${cell?.status==='unverified' ? 'Unverified group' : ['failed','skipped'].includes(state) ? escape(cell.status) : state==='missing' ? 'Not measured' : state==='unavailable' ? 'Unavailable' : `${cell.successes}/${cell.attempts} repeats`}</small></button></td>`;
      }).join("")}</tr>`;
    }).join("")}</tbody></table>`;
  }
  function evidence(group,cell) {
    if (!cell) return '<p class="hint">No case was recorded for this prompt target and concurrency. This is not a failed test.</p>';
    const requestRows=cell.rows.flatMap(row=>(row.requests?.length ? row.requests : (row.concurrency || 1)===1 ? [row] : []).map((request,index)=>`<tr><td>${(row.repeat || 0)+1}</td><td>${index+1}</td><td>${escape(request.status || row.status)}</td><td>${fmt(request.prompt_tokens)}</td><td>${fmt(request.completion_tokens)}${estimated(request) ? '*' : ''}</td><td>${fmt(request.ttft_s)}</td><td>${fmt(request.decode_tps)}</td><td>${fmt(request.wall_s)}</td></tr>`)).join('');
    const notes=cell.rows.filter(row=>row.status!=="ok" || !successful(row)).map(row=>row.message || row.fail_kind || (unverifiedGroup(row) ? "Request-group completeness was not recorded; excluded from successful scaling." : "Incomplete request group"));
    const settings=cell.rows.map(row=>({repeat:(row.repeat || 0)+1,group_wall_s:row.wall_s,actual_output_tokens:outputCount(row),
      context_tokens:row.context_length,per_request_context_tokens:row.per_request_context_length,placement:row.gpu_offload,
      model_configuration:row.model_configuration || group.identity,power_scope:(row.group_power || row.power)?.scope || "unreported",
      gpus:(row.group_power || row.power)?.gpus,
      low_confidence_energy:(row.group_power || row.power)?.low_confidence ?? "unreported"}));
    const timelines=cell.rows.map(row=>{
      const timing=requestTimeline(row);
      if (!timing.entries.some(item=>item.first!=null || item.finish!=null)) return "";
      return `<section class="sc-timeline"><h4>Repeat ${(row.repeat || 0)+1} · request timing</h4><div class="sc-timing-summary"><span>Earliest first token <b>${fmt(timing.first)} s</b></span><span>Median <b>${fmt(timing.median)} s</b></span><span>Latest <b>${fmt(timing.last)} s</b></span></div>${timing.entries.map(item=>`<div class="sc-timeline-row"><span>Request ${item.index}</span><div class="sc-timeline-track" role="img" aria-label="${escape(`Request ${item.index}: ${item.first==null ? 'first token unavailable' : `first token at ${fmt(item.first)} seconds`}, ${item.finish==null ? 'completion unavailable' : `completed at ${fmt(item.finish)} seconds`}`)}">${item.first!=null ? `<i class="sc-time-wait" style="width:${100*item.first/timing.duration}%"></i>` : ''}${item.first!=null && item.finish!=null && item.finish>=item.first ? `<i class="sc-time-response" style="left:${100*item.first/timing.duration}%;width:${100*(item.finish-item.first)/timing.duration}%"></i>` : ''}</div><small>${item.finish!=null ? `${fmt(item.finish)} s` : escape(item.status)}</small></div>`).join('')}<p class="hint"><i class="sc-time-key sc-time-wait"></i> Wait to first token <i class="sc-time-key sc-time-response"></i> After first token, including pauses. Each bar starts at that request's own start; dispatch offsets were not recorded.</p></section>`;
    }).join('');
    const placements=[...new Set(cell.rows.map(row=>row.gpu_offload).filter(value=>value!=null))];
    const placementNote=placements.some(partialOffload) ? '<p class="sc-error-note">Partial GPU offload: CPU work may dominate. This placement is a separate condition from full GPU offload.</p>' : '';
    return `<div class="sc-evidence-stats"><span><b>${fmt(cell.metrics.total)}</b> total tok/s</span><span><b>${fmt(cell.metrics.decode)}</b> tok/s per request</span><span><b>${fmt(cell.metrics.ttft)}</b> s to first token</span><span><b>${cell.successes}/${cell.attempts}</b> successful repeats</span></div>${placementNote}${notes.length ? `<p class="sc-error-note">${notes.map(escape).join(' · ')}</p>` : ""}${timelines}<div class="sc-evidence-scroll"><table><thead><tr><th>Repeat</th><th>Request</th><th>Status</th><th>Prompt tokens</th><th>Output tokens</th><th>First token (s)</th><th>Decode (tok/s)</th><th>Complete (s)</th></tr></thead><tbody>${requestRows}</tbody></table></div><details class="sc-settings"><summary>Recorded settings and measurement scope</summary><pre>${escape(JSON.stringify(settings,null,2))}</pre></details>`;
  }
  const api={build,successful,partialOffload,measurements,outputCount,gpuEnergy,compatibleAllocation,getCell,best,csv,metricInfo,median,requestTimeline,render};
  if (typeof module!=="undefined" && module.exports) module.exports=api;
  root.WatteverConcurrency=api;
})(typeof globalThis!=="undefined" ? globalThis : this);
