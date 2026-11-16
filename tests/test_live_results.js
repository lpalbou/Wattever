const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/app.js', 'utf8').replace(/\ninit\(\);\s*$/, '');
const element=()=>({dataset:{},style:{},hidden:false,textContent:'',innerHTML:'',className:'',classList:{add(){},remove(){}}});
const elements=Object.fromEntries(['#live-card','#live-card h3','#progress-bar','#live-phase','#live-deadline','#live-sample-note','#pill-power','#live-stats','#live-table'].map(key=>[key,element()]));
const context = {WatteverPhaseMetrics:require('../web/phase-metrics.js'),Charts:{fmtNum:String},document:{querySelector:s=>elements[s] || null},clearInterval:()=>{}};
vm.createContext(context);
vm.runInContext(source,context);
(async()=>{
  const origRender = context.renderLiveTable;
  const result = await vm.runInContext(`(async()=>{
    let reads=0, redraws=0;
    const run={id:'live',status:'running',results:[{model:'m',status:'ok'}]};
    api=async()=>{reads++;return run};
    renderLiveTable=()=>{}; loadRuns=async()=>{redraws++};
    const status={running:true,run_id:'live',run:{id:'live',n_results:1,status:'running'}};
    await syncLiveBenchmark(status);
    await syncLiveBenchmark(status);
    if(reads!==1||redraws!==1) throw Error('unchanged status was reloaded');
    run.results.push({model:'m',status:'failed'});status.run.n_results=2;
    await syncLiveBenchmark(status);
    if(reads!==2||redraws!==2||state.liveResults.length!==2) throw Error('new test did not refresh');
    run.status='completed';status.running=false;status.run.status='completed';
    await syncLiveBenchmark(status);
    if(reads!==3||state.runCache.live.status!=='completed') throw Error('completion was not refreshed');
    return true;
  })()`,context);
  assert.equal(result,true);
  context.finished={running:false,run:{status:'completed',n_results:24,n_failed:0},live:{phase:'idle',done:24,total:24,model:'last-model',size:80000,timeout_budget_s:900,decode_chunks_per_s_live:12.3,telemetry:{gpu_w:144,timestamp:'2026-11-01T23:54:00Z'}}};
  vm.runInContext('renderLive(finished)',context);
  assert.equal(elements['#live-phase'].textContent,'Completed · 24/24 tests');
  assert.equal(elements['#live-card h3'].textContent,'Benchmark finished');
  assert.equal(elements['#live-card'].dataset.status,'completed');
  assert.equal(elements['#progress-bar'].style.width,'100.0%');
  assert.equal(elements['#live-deadline'].hidden,true);
  assert.equal(elements['#live-deadline'].textContent,'');
  assert.equal(elements['#pill-power'].hidden,true);
  assert.match(elements['#live-sample-note'].textContent,/Last recorded sample/);
  assert.match(elements['#live-stats'].innerHTML,/chunks\/s last test/);
  assert.doesNotMatch(elements['#live-stats'].innerHTML,/tk\/s live/);
  for(const [status,label] of [['cancelled','Stopped'],['error','Failed'],['interrupted','Incomplete']]){
    context.finished.run.status=status;vm.runInContext('renderLive(finished)',context);
    assert.match(elements['#live-phase'].textContent,new RegExp('^'+label));
    assert.equal(elements['#live-card'].dataset.status,status);
  }
  context.finished.running=true;context.finished.run.status='completed';
  vm.runInContext('renderLive(finished)',context);
  assert.match(elements['#live-phase'].textContent,/Finishing/,'Cleanup is still running');
  assert.equal(elements['#live-deadline'].hidden,true);
  context.finished.run.status='running';context.finished.live.phase='prefill';
  vm.runInContext('renderLive(finished)',context);
  assert.equal(elements['#live-card h3'].textContent,'Progress');
  assert.equal(elements['#live-deadline'].hidden,false);
  assert.equal(elements['#live-sample-note'].hidden,true);
  assert.equal(elements['#pill-power'].hidden,false);
  context.multiLive={running:true,live:{phase:'decode',telemetry:{gpu_w:190,gpu_util:45,vram_mb:18000,vram_percent:56,
    gpus:[{index:0,name:'NVIDIA GeForce RTX 5070 Ti',gpu_w:120,gpu_util:50,vram_mb:10000,vram_percent:62.5,temp_c:65,clock_mhz:2700},
          {index:1,name:'NVIDIA GeForce RTX 5060 Ti',gpu_w:70,gpu_util:40,vram_mb:8000,vram_percent:50,temp_c:59,clock_mhz:2400}]}}};
  vm.runInContext('renderLive(multiLive)',context);
  context.multiLive.live.ttft_s=0;context.multiLive.live.decode_chunks_per_s_live=12.3;
  vm.runInContext('renderLive(multiLive)',context);
  assert.match(elements['#live-stats'].innerHTML,/chunks\/s live/);
  assert.doesNotMatch(elements['#live-stats'].innerHTML,/tk\/s live/,'Chunk delivery is not token throughput');
  assert.match(elements['#live-stats'].innerHTML,/>0\.00 /,'Zero observed TTFT is not missing');
  assert.doesNotMatch(elements['#live-stats'].innerHTML,/RTX 5070 Ti|All GPUs power/, 'Hardware readings belong to System');
  assert.match(vm.runInContext('liveGpuCards(multiLive.live.telemetry)',context), /RTX 5070 Ti/);
  // An archived final result can return404; terminal status must still render
  // and unlock the UI rather than being lost inside the polling catch block.
  await vm.runInContext(`(async()=>{
    let stopped=false;state.pollBusy=false;state.poller=42;
    api=async()=>({events:[{kind:'done'}],cursor:1,status:{running:false,run:{status:'completed'},live:{phase:'idle',done:24,total:24}}});
    syncLiveBenchmark=async()=>{throw Error('not found')};
    leaveRunningUi=()=>{stopped=true};
    await poll();
    if(!stopped || state.poller!==null || state.pollBusy)throw Error('Final refresh failure left polling active');
  })()`,context);
  assert.match(elements['#live-phase'].textContent,/^Completed/);

  // Live table rendering: concurrent multi-GPU run must populate all fields and not show unneeded estimated caption
  context.renderLiveTable = origRender;
  vm.runInContext(`
    state.liveResults = [{
      model: 'qwen/qwen3.8-27b', target_tokens: 5000, concurrency: 2, status: 'ok', completed_requests: 2, wall_s: 88, aggregate_completion_tokens: 512, aggregate_completion_tps: 512/88,
      requests: [
        { status: 'ok', prompt_tokens: 4670, completion_tokens: 256, decode_tps: 6.1, decode_s:255/6.1, decode_token_count:255, prefill_tps: 598, start_offset_s:0, ttft_s: 40.46 },
        { status: 'ok', prompt_tokens: 4670, completion_tokens: 256, decode_tps: 6.5, decode_s:255/6.5, decode_token_count:255, prefill_tps: 602, start_offset_s:0, ttft_s: 42.80 }
      ],
      group_power: {
        scope:"all_cards", low_confidence:false, gpu_power_complete:true, gpu_sampled_coverage_fraction:0.95, gpu_w_avg: 120, gpu_energy_j: 1500, vram_mb_max: 17000,
        gpus: [
          { index: 0, name: 'RTX 5070 Ti', gpu_w_avg: 70, vram_mb_max: 10000 },
          { index: 1, name: 'RTX 5060 Ti', gpu_w_avg: 50, vram_mb_max: 8000 }
        ]
      }
    }];
    renderLiveTable();
  `, context);
  const tableHtml = elements['#live-table'].innerHTML;
  assert.equal(tableHtml.includes('<caption>* Output tokens'), false, 'Estimated caption must not be shown when tokens are not estimated');
  assert.match(tableHtml, />4,670</, 'Actual tokens = median prompt tokens');
  assert.doesNotMatch(tableHtml, /Total tk\/s/, 'Group wall-throughput is not a live-table column');
  assert.match(tableHtml, /12\.2/, 'Total decode excludes TTFT and merges overlapping intervals');
  assert.match(tableHtml, /5\.8/, 'End-to-end output = 512 tokens / 88 s wall time');
  assert.match(tableHtml, /218/, 'Group prefill = 9340 tokens / latest ttft');
  assert.match(tableHtml, />41\.63</, 'TTFT = median per request');
  assert.match(tableHtml, />120</, 'GPU W = whole-case all-card average');
  assert.match(tableHtml, />2\.93</, 'J/out tk = 1500 J / 512 tokens');
  assert.match(tableHtml, />17,000</, 'VRAM uses simultaneous group peak');
  assert.match(tableHtml, /RTX 5070 Ti: 70 W/, 'Per-card breakdown in tooltip');
  // Single request on two cards: same definitions, energy from prefill+decode phases
  vm.runInContext(`state.liveResults=[{model:'m',target_tokens:512,concurrency:1,status:'ok',prompt_tokens:522,completion_tokens:256,
    wall_s:10,ttft_s:0.8,decode_s:9.2,decode_token_count:255,prefill_tps:652,decode_tps:255/9.2,vram_peak_mb:9000,
    group_power:{scope:'all_cards',low_confidence:false,gpu_power_complete:true,gpu_sampled_coverage_fraction:1,gpu_energy_j:1000,gpu_w_avg:100,vram_mb_max:9000},
    power:{scope:"all_cards",low_confidence:false,prefill_gpu_power_complete:true,decode_gpu_power_complete:true,prefill_energy_j:80,decode_energy_j:920,gpus:[{index:0,name:'A',vram_peak_mb:5000,prefill:{gpu_energy_j:50},decode:{gpu_energy_j:600}},{index:1,name:'B',vram_peak_mb:4000,prefill:{gpu_energy_j:30},decode:{gpu_energy_j:320}}]}}];renderLiveTable();`,context);
  const one = elements['#live-table'].innerHTML;
  assert.match(one, />653</, 'Single collective prefill equals stream prefill');
  assert.match(one, />27\.7\*</, 'Single collective decode equals stream decode');
  assert.match(one, />100</, 'Single GPU W = 1000 J / 10 s');
  assert.match(one, />3\.91</, 'Single J/out tk = 1000/256');
  assert.doesNotMatch(tableHtml, />—<\/td>/, 'All fields should have values, no blank dashes');

  vm.runInContext(`
    const base = {concurrency:2,status:'ok',aggregate_completion_tokens:999,requests:[{status:'ok',completion_tokens:50},{status:'ok',completion_tokens:50}],group_power:{scope:'all_cards',low_confidence:false,gpu_power_complete:true,gpu_energy_j:100,gpu_sampled_coverage_fraction:.9,gpus:[{vram_mb_max:10},{vram_mb_max:20}]}};
    if (liveRowMetrics(base).jPerTok !== 1 || liveRowMetrics(base).vram !== null) throw Error('valid group energy or independent peaks');
    for (const changes of [{low_confidence:true},{gpu_power_complete:false},{gpu_sampled_coverage_fraction:.2}]) if (liveRowMetrics({...base,group_power:{...base.group_power,...changes}}).jPerTok !== null) throw Error('unreliable group energy');
    if (liveRowMetrics({status:'ok',completion_tokens:10,power:{j_per_token_decode:3}}).jPerTok !== null) throw Error('decode-only energy is not whole case');
  `,context);

  console.log('Live refresh, terminal outcomes, final-sample labels, live-table concurrent/multi-card metrics and archived-result completion passed');
})().catch(error=>{console.error(error);process.exitCode=1});
