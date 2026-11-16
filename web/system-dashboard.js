/* Live machine history. Values retain their units; different units never share an unlabeled axis. */
(function(root) {
  'use strict';
  const finite = Number.isFinite;
  const esc = v => String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const colors = ['#38bdf8','#a78bfa','#34d399','#fb7185','#fbbf24','#2dd4bf','#f97316','#e879f9'];
  const format = v => finite(v) ? v.toLocaleString(undefined,{maximumFractionDigits:1}) : '\u2014';
  function catalog(t) {
    const metrics = [
      ['cpu_util_percent','CPU load','%',colors[0],100], ['gpu_util',t.gpus?.length ? 'NVIDIA GPU load' : 'GPU/SoC load','%',colors[1],100],
      ['system_memory_percent','RAM used','%',colors[2],100], ['vram_percent',t.gpus?.length ? 'GPU memory used' : 'GPU/SoC memory used','%',colors[3],100],
      ['gpu_w',t.gpus?.length ? 'NVIDIA board power' : 'GPU/SoC power','W',colors[4]], ['system_w_est','System power (estimated)','W',colors[5]],
      ['temp_c','GPU temperature','\u00b0C',colors[6]], ['cpu_temp_c','CPU temperature','\u00b0C',colors[7]],
      ['system_memory_used_bytes','RAM used','GiB',colors[2],null,1073741824], ['vram_mb','GPU/SoC memory used','GiB',colors[3],null,1024],
    ].map(([key,label,unit,color,limit,divisor=1])=>({key,label,unit,color,limit,divisor}));
    for (const gpu of t.gpus || []) for (const [key,label,unit,color,limit,divisor=1] of [
      ['gpu_util','load','%',colors[gpu.index%colors.length],100],['vram_percent','memory','%',colors[(gpu.index+2)%colors.length],100],
      ['gpu_w','power','W',colors[(gpu.index+4)%colors.length]],['temp_c','temperature','\u00b0C',colors[(gpu.index+6)%colors.length]],
    ]) metrics.push({key:`gpu:${gpu.index}:${key}`,label:`GPU ${gpu.index+1} ${label}`,unit,color,limit,divisor,dash:`${7+gpu.index*2} 4`});
    return metrics;
  }
  function value(t,m) {
    const parts=m.key.split(':');
    const v=parts[0]==='gpu' ? t.gpus?.find(g=>g.index===Number(parts[1]))?.[parts[2]] : t[m.key];
    return finite(v) && v>=0 ? v/m.divisor : null;
  }
  function append(points,sample) {
    const t=sample.telemetry;
    if (!t || !finite(sample.timestamp) || (finite(t.sample_age_s) && t.sample_age_s>4)) return false;
    const date=Date.parse(t.timestamp);
    const time=finite(date) ? date/1000 : sample.timestamp-(t.sample_age_s || 0);
    if (points.length && time<=points[points.length-1].time) return false;
    points.push({time,telemetry:JSON.parse(JSON.stringify(t))});
    while(points.length>900 || points.length && time-points[0].time>900) points.shift();
    return true;
  }
  function segments(points,metric,x,y) {
    let prev=null, path='', paths=[];
    for (const p of points) {
      const v=value(p.telemetry,metric);
      if (v===null || prev!==null && p.time-prev>4) { if(path) paths.push(path);path=''; }
      if(v!==null) path+=(path?' L':'M')+x(p.time).toFixed(2)+','+y(v).toFixed(2);
      prev=p.time;
    }
    if(path) paths.push(path);
    return paths;
  }
  function nearest(points,time,maxDistance=2) {
    const p=points.reduce((best,p)=>!best || Math.abs(p.time-time)<Math.abs(best.time-time)?p:best,null);
    return p && Math.abs(p.time-time)<=maxDistance ? p : null;
  }
  class Dashboard {
    constructor(node) {
      this.node=node;this.points=[];this.metrics=[];this.selected=new Set(['cpu_util_percent','gpu_util','system_memory_percent','vram_percent']);
      this.window=120;this.paused=false;this.pausedAt=null;this.now=null;this.inspect=null;this.latest=null;this.fresh=false;
      node.classList.add('system-dashboard');
      node.innerHTML='<div class="observatory-gauges"></div><section class="observatory-history"><header><div><span class="eyebrow">LIVE OBSERVATORY</span><h3>Follow the machine over time</h3></div><div class="observatory-tools"><label>History<select data-history-window><option value="60">1 minute</option><option value="120" selected>2 minutes</option><option value="300">5 minutes</option><option value="900">15 minutes</option></select></label><label>Overlay<select data-history-scale><option value="units">Actual units (one axis per unit)</option><option value="relative">Relative to visible peak (%)</option></select></label><button class="btn-sm" data-history-pause>Pause history</button><button class="btn-sm" data-history-clear>Clear</button></div></header><div class="observatory-presets" role="group" aria-label="Metric presets"><button class="btn-sm" data-preset="load">Load &amp; memory</button><button class="btn-sm" data-preset="power">Power</button><button class="btn-sm" data-preset="thermal">Temperatures</button><button class="btn-sm" data-preset="none">Clear selection</button></div><div class="observatory-metrics" role="group" aria-label="Metrics to follow"></div><p class="hint" data-history-note></p><div class="observatory-plots"></div><div class="observatory-inspect"><label>Inspect history<input type="range" min="0" max="0" value="0" data-history-scrub disabled></label><button class="btn-sm" data-history-live>Follow live</button><span data-history-time></span></div><div class="observatory-readout" aria-live="off"></div><p class="hint">History is collected while System is visible, kept in this page for up to 15 minutes. Missing readings and sampling gaps stay empty. System power is an estimate; GPU board power covers monitored sensors.</p></section><div data-device-cards></div>';
      this.q=s=>node.querySelector(s);
      this.q('[data-history-window]').onchange=e=>{this.window=Number(e.target.value);this.inspect=null;this.draw();};
      this.q('[data-history-scale]').onchange=()=>this.draw();
      this.q('[data-history-pause]').onclick=e=>{this.paused=!this.paused;this.pausedAt=this.paused?this.now:null;e.target.textContent=this.paused?'Resume history':'Pause history';e.target.setAttribute('aria-pressed',String(this.paused));this.draw();};
      this.q('[data-history-clear]').onclick=()=>{this.points=[];this.inspect=null;if(this.latest)this.gauges();this.draw();};
      this.q('[data-history-live]').onclick=()=>{this.inspect=null;this.draw();};
      this.q('[data-history-scrub]').oninput=e=>{this.inspect=this.visible()[Number(e.target.value)]?.time ?? null;this.draw();};
      node.querySelectorAll('[data-preset]').forEach(button=>button.onclick=()=>{
        const keys={load:['cpu_util_percent','gpu_util','system_memory_percent','vram_percent'],power:['gpu_w','system_w_est'],thermal:['temp_c','cpu_temp_c'],none:[]};
        this.selected=new Set(keys[button.dataset.preset]);this.controls();if(this.latest)this.gauges();this.draw();
      });
    }
    update(sample,deviceMarkup='') {
      this.now=finite(sample.timestamp)?sample.timestamp:Date.now()/1000;
      this.points=this.points.filter(p=>p.time>=this.now-900);
      if (!sample.telemetry) {this.fresh=false;if(this.latest)this.gauges();this.draw();return;}
      this.latest=sample.telemetry;
      const date=Date.parse(this.latest.timestamp);
      this.fresh=(!finite(this.latest.sample_age_s) || this.latest.sample_age_s<=4) && (!finite(date) || Math.abs(this.now-date/1000)<=4);
      const next=catalog(this.latest);
      if(JSON.stringify(next)!==JSON.stringify(this.metrics)){this.metrics=next;this.controls();}
      if(!this.paused && this.fresh) append(this.points,sample);
      this.gauges();this.q('[data-device-cards]').innerHTML=deviceMarkup;this.draw();
    }
    tick(time=Date.now()/1000) {this.now=time;this.fresh=false;this.points=this.points.filter(p=>p.time>=time-900);if(this.latest)this.gauges();this.draw();}
    controls() {
      this.q('.observatory-metrics').innerHTML=this.metrics.map(m=>`<label style="--metric-color:${m.color}"><input type="checkbox" value="${esc(m.key)}" ${this.selected.has(m.key)?'checked':''}><i class="${m.dash?'metric-dashed':''}"></i>${esc(m.label)} <small>${esc(m.unit)}</small></label>`).join('');
      this.q('.observatory-metrics').querySelectorAll('input').forEach(input=>input.onchange=()=>{if(input.checked)this.selected.add(input.value);else this.selected.delete(input.value);this.gauges();this.draw();});
    }
    gauges() {
      const focused=this.node.ownerDocument?.activeElement?.dataset.gauge;
      const keys=['cpu_util_percent','gpu_util','system_memory_percent','vram_percent','gpu_w','system_w_est'];
      this.q('.observatory-gauges').innerHTML=keys.map(key=>{
        const m=this.metrics.find(m=>m.key===key),v=value(this.latest,m),available=v!==null;
        const ratio=available && m.limit ? Math.max(0,Math.min(100,100*v/m.limit)) : 0;
        const history=this.points.slice(-40), vals=history.map(p=>value(p.telemetry,m)).filter(finite),max=Math.max(1,...vals);
        const spark=segments(history,m,t=>history.length>1?100*(t-history[0].time)/Math.max(1,history.at(-1).time-history[0].time):0,v=>28-26*v/(m.limit || max));
        return `<button class="observatory-gauge" data-gauge="${key}" aria-pressed="${this.selected.has(key)}" style="--metric-color:${m.color};--gauge-value:${ratio}%"><span class="gauge-label">${esc(m.label)}</span><div class="gauge-main">${m.limit?`<span class="gauge-ring"><b>${format(v)}</b><small>${m.unit}</small></span>`:`<strong>${format(v)} <small>${m.unit}</small></strong>`}<svg viewBox="0 0 100 32" aria-hidden="true">${spark.map(d=>`<path d="${d}"/>`).join('')}</svg></div><span class="gauge-caption">${!available?'Sensor unavailable':!this.fresh?'Stale sensor reading':m.limit?(key.includes('util')?'Current utilization':'of observed capacity'):key==='system_w_est'?'Estimated whole-system draw':'Measured sensor power'}${this.selected.has(key)?' \u00b7 Following':''}</span></button>`;
      }).join('');
      this.q('.observatory-gauges').querySelectorAll('button').forEach(button=>button.onclick=()=>{const key=button.dataset.gauge;if(this.selected.has(key))this.selected.delete(key);else this.selected.add(key);this.controls();this.gauges();this.draw();});
      if(focused)this.q(`[data-gauge="${focused}"]`)?.focus({preventScroll:true});
    }
    end() {return (this.paused?this.pausedAt:this.now) ?? this.points.at(-1)?.time ?? 0;}
    visible() {const end=this.end();return this.points.filter(p=>p.time>=end-this.window && p.time<=end);}
    draw() {
      const points=this.visible(),metrics=this.metrics.filter(m=>this.selected.has(m.key)),relative=this.q('[data-history-scale]').value==='relative';
      this.q('[data-history-note]').textContent=(this.paused?'History paused; current gauges remain live. ':!this.fresh?'No fresh sample; the empty interval shows the sampling gap. ':'')+(relative?'Each line is scaled to its own visible maximum; heights compare trends, not physical magnitudes.':'Metrics with the same unit share an axis. Different units have separate synchronized plots.');
      const groups=new Map();for(const m of metrics){const unit=relative?'% of visible peak':m.unit;if(!groups.has(unit))groups.set(unit,[]);groups.get(unit).push(m);}
      const end=this.end(),start=end-this.window;
      let inspected=this.inspect===null?(this.fresh || this.paused?points.at(-1):null):nearest(points,this.inspect);
      const svgGroups=[...groups].map(([unit,items])=>{
        const maxima=new Map(items.map(m=>[m.key,Math.max(0,...points.map(p=>value(p.telemetry,m)).filter(finite))]));
        const peak=relative?100:unit==='%'?Math.max(100,...maxima.values()):Math.max(1,...maxima.values())*1.12;
        const x=t=>52+((t-start)/this.window)*820,y=v=>180-150*v/peak;
        const lines=items.map(m=>{
          const actual=value(inspected?.telemetry || {},m),max=maxima.get(m.key);
          const paths=segments(points,m,x,v=>y(relative?(max>0?v/max*100:0):v));
          return paths.map(d=>`<path d="${d}" fill="none" stroke="${m.color}" stroke-width="2.5" stroke-linecap="round"${m.dash?` stroke-dasharray="${m.dash}"`:''}/>`).join('')+(actual!==null && inspected?`<circle cx="${x(inspected.time)}" cy="${y(relative?(max>0?actual/max*100:0):actual)}" r="4" fill="${m.color}"/>`:'');
        }).join('');
        return `<section class="observatory-plot"><h4>${esc(unit)}</h4><svg viewBox="0 0 900 215" role="img" aria-label="${esc(items.map(m=>m.label).join(', '))} history in ${esc(unit)}">${[0,.25,.5,.75,1].map(f=>`<line x1="52" x2="872" y1="${y(peak*f)}" y2="${y(peak*f)}" class="history-grid"/><text x="43" y="${y(peak*f)+4}" text-anchor="end">${format(peak*f)}</text>`).join('')}${[0,.25,.5,.75,1].map(f=>`<text x="${52+820*f}" y="205" text-anchor="middle">${f===1?(this.paused?'Paused':'Now'):Math.round(this.window*(1-f))+'s ago'}</text>`).join('')}${lines}<line class="history-cursor" x1="${x(inspected?.time ?? end)}" x2="${x(inspected?.time ?? end)}" y1="25" y2="180"/></svg></section>`;
      });
      this.q('.observatory-plots').innerHTML=!metrics.length?'<p class="history-empty">Select metrics above or click a gauge to follow it.</p>':!points.length?'<p class="history-empty">Waiting for fresh samples. Gauges will build their history here.</p>':svgGroups.join('');
      this.q('.observatory-plots').querySelectorAll('svg').forEach(svg=>{
        svg.onpointermove=e=>{if(!points.length)return;const rect=svg.getBoundingClientRect(),fraction=Math.max(0,Math.min(1,((e.clientX-rect.left)/rect.width*900-52)/820));this.inspect=start+fraction*this.window;this.readout(points,metrics);};
        svg.onpointerleave=()=>{this.inspect=null;this.readout(points,metrics);};
      });
      const scrub=this.q('[data-history-scrub]');scrub.max=Math.max(0,points.length-1);scrub.disabled=!points.length;
      scrub.value=inspected?points.indexOf(inspected):0;
      this.readout(points,metrics);
    }
    readout(points,metrics) {
      const p=this.inspect===null?(this.fresh || this.paused?points.at(-1):null):nearest(points,this.inspect);
      this.q('[data-history-time]').textContent=p?(this.inspect===null?'Latest sample \u00b7 ':'Inspecting \u00b7 ')+new Date(p.time*1000).toLocaleTimeString():this.inspect!==null?'No sample at this time':'Waiting for a fresh sample';
      this.q('.observatory-readout').innerHTML=metrics.map(m=>`<span style="--metric-color:${m.color}"><i class="${m.dash?'metric-dashed':''}"></i>${esc(m.label)} <b>${format(value(p?.telemetry || {},m))} ${esc(m.unit)}</b></span>`).join('');
      if(p) {
        const end=this.end(),x=52+820*(p.time-(end-this.window))/this.window;
        this.q('.observatory-plots').querySelectorAll('.history-cursor').forEach(line=>{line.setAttribute('x1',x);line.setAttribute('x2',x);});
        this.q('.observatory-plots').querySelectorAll('circle').forEach(dot=>dot.style.visibility=this.inspect===null?'visible':'hidden');
        this.q('[data-history-scrub]').value=points.indexOf(p);
      }
      else if(this.inspect!==null) {
        const x=52+820*(this.inspect-(this.end()-this.window))/this.window;
        this.q('.observatory-plots').querySelectorAll('.history-cursor').forEach(line=>{line.setAttribute('x1',x);line.setAttribute('x2',x);});
        this.q('.observatory-plots').querySelectorAll('circle').forEach(dot=>dot.style.visibility='hidden');
      }
    }
  }
  const api={Dashboard,catalog,value,append,segments,nearest};
  if(typeof module!=='undefined' && module.exports)module.exports=api;
  root.WatteverSystemDashboard=api;
})(typeof globalThis!=='undefined'?globalThis:this);
