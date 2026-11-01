const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/app.js', 'utf8').replace(/\ninit\(\);\s*$/, '');
const elements = {
  '#max-tokens': {value: '256'}, '#repeats': {value: '2'}, '#offload': {value: 'max'},
  '#gpu-index': {value: '2'}, '#runtime-gpu-placement': {value: 'GPU 1'},
  '#fallback': {checked: true}, '#stop-after-fail': {checked: true}, '#warmup': {checked: true},
  '#skip-after-timeout': {checked: true},
  '#no-think': {checked: true}, '#per-test-timeout': {value: '1800'}, '#load-timeout': {value: '900'},
  '#headroom': {value: '512'}, '#other-idle': {value: '60'}, '#cpu-tdp': {value: '140'},
  '#power-interval': {value: '150'}, '#run-label': {value: 'run'}, '#run-notes': {value: 'notes'},
  '#mtp-chips input:checked': {value: 'off'},
};
const context = {Charts: {fmtNum: String}, document: {querySelector: (s) => elements[s], querySelectorAll: () => []}};
vm.createContext(context);
vm.runInContext(source, context);
vm.runInContext(`
  if (benchmarkFilename('a.1') !== 'a.1.json' || benchmarkFilename('../bad') !== 'benchmark.json') throw Error('filename');
  state.models = new Set(['model']); state.sizes = new Set([512]); state.concurrencyLevels = new Set([1, 10]);
  const issue = benchmarkIncompleteNotice({status:'error',remaining_tests:24,error:'WinError 5: Access is denied: local.json.tmp'});
  if(!issue.includes('A file access error') || !issue.includes('24 tests remaining') || !issue.includes('<summary>Technical details</summary>')) throw Error('Storage errors need readable guidance and optional diagnostics');
  const shared = sharedDisplayRun({community:true,status:'error',remaining_tests:24,error:'WinError 5 C:\\private',notes:'ordinary setup notes',hardware:{cpu:{name:'Test CPU'},gpus:[{name:'Test GPU'}]},config:{gpu_offload:'max'},sessions:[{id:'one',config:{context_policy:'fixed'},error:'C:\\private'}],results:[{message:'C:\\private',requests:[{message:'C:\\private'}]}]});
  if (JSON.stringify(shared).includes('private') || benchmarkIncompleteNotice(shared).includes('WinError') || !benchmarkIncompleteNotice(shared).includes('24 unrecorded cases')) throw Error('shared diagnostic leakage');
  if (shared.notes !== 'ordinary setup notes' || shared.hardware.gpus[0].name !== 'Test GPU' || shared.hardware.cpu.name !== 'Test CPU' || shared.config.gpu_offload !== 'max' || shared.sessions[0].config.context_policy !== 'fixed') throw Error('shared hardware/settings provenance lost');
  if(benchmarkIncompleteNotice({status:'completed',remaining_tests:0}) || benchmarkIncompleteNotice({status:'running',remaining_tests:24})) throw Error('Completed and active benchmarks must not show stale incomplete warnings');
  if ([...through(CONCURRENCY_PRESETS, 5)].join(',') !== '1,2,3,4,5') throw Error('select five');
  if ([...through(CONCURRENCY_PRESETS, 2)].join(',') !== '1,2') throw Error('lower selection');
  if ([...through([512,5000,10000,20000,40000], 20000)].join(',') !== '512,5000,10000,20000') throw Error('context selection');
  state.env = {defaults: {other_idle_w: 10, cpu_tdp_w: 25, power_interval_ms: 200}};

  state.metrics = new Set(['prefill_tps', 'decode_tps', 'ttft_s']);
  const groups = metricGroups();
  if (groups.length !== 3 || groups.some(group => group.keys.length !== 1)) throw Error('one chart per selected metric');
  if (measureAppearance({}, 'prefill_tps', ['prefill_tps','decode_tps'], false).dash === measureAppearance({}, 'decode_tps', ['prefill_tps','decode_tps'], false).dash) throw Error('measure styles');
  if (metricGroups(['j_per_1k_prefill', 'j_per_token_decode']).length !== 2) throw Error('energy normalization grouping');
  const telemetry=telemetryMarkup({telemetry:{summary:{observations:100,metrics:{gpu_util:{mean:50,max:100,last:60,early_60s_mean:40,late_60s_mean:60,late_minus_early:20,coverage_fraction:.9}}},history:{points:[{elapsed_s:0,gpu_util:50}],retention:{retained_points:1,total_observations:100}}}});
  if(!telemetry.includes('Late − early')||!telemetry.includes('90.0%')||!telemetry.includes('recent ring')||!telemetry.includes('not proof of throttling')) throw Error('telemetry summary provenance');
  const telemetryPlots=[]; const oldLine=Charts.line; Charts.line=(target,options)=>telemetryPlots.push(options); const oldResolve=resolveColor; resolveColor=value=>value;
  const report={dataset:{},querySelector:()=>({}),addEventListener:()=>{}};
  bindTelemetry({dataset:{},querySelectorAll:()=>[report]},{telemetry:{history:{points:[{elapsed_s:0,gpu_util:50,temp_c:60},{elapsed_s:1,gpu_util:60,temp_c:65}]}}});
  if(telemetryPlots.length!==2 || telemetryPlots.some(plot=>plot.xScale!=='linear')) throw Error('telemetry uses linear elapsed-time plots');
  Charts.line=oldLine; resolveColor=oldResolve;
  state.metrics = new Set(['prefill_tps']);
  state.comparisonConcurrency = 10;
  const cfg = collectConfig();
  if (!cfg.skip_after_timeout) throw Error('Practical timeout skipping is configurable before a benchmark starts');
  if (cfg.other_idle_w !== 10 || cfg.cpu_tdp_w !== 25 || cfg.power_interval_ms !== 200) throw Error('automatic power defaults');
  const extension = extendBody({runId:'r', modelModes:{m:['off'], q:['on']}, sizes:new Set([5000,512]), concurrency:new Set([10,1]), action:'rerun'});
  if (extension.run_id !== 'r' || extension.model_modes.q[0] !== 'on' || extension.sizes.join(',') !== '512,5000' || extension.concurrency_levels.join(',') !== '1,10' || !extension.retry_failed || !extension.rerun_existing) throw Error('extension config');
  if (cfg.concurrency_levels.join(',') !== '1,10' || cfg.gpu_index !== 2 || cfg.runtime_gpu_placement !== 'GPU 1') throw Error('config');
  const machine = {
    hardware: {cpu: {name: 'Test CPU'}, system: {memory_bytes: 64 * 1073741824}, gpus: [{name: 'NVIDIA GeForce RTX 5060 Ti', memory_bytes: 16 * 1073741824, sensor_index: 2}]},
    models: [{id: 'model', type: 'llm'}],
  };
  const generated = generatedMetadata(machine, cfg);
  if (generated.label !== 'RTX 5060 Ti') throw Error('hardware name');
  if (!generated.notes.includes('all 1 installed models') || !generated.notes.includes('Simultaneous queries: 1, 10') || !generated.notes.includes('Reasoning: off')) throw Error('settings notes');
  const changed = generatedMetadata(machine, {...cfg, models: [], concurrency_levels: [1,2], max_tokens: 512, gpu_offload: 'off'});
  if (changed.label !== 'CPU only' || !changed.notes.includes('Answer: 512 tokens') || !changed.notes.includes('Models: none')) throw Error('changed config metadata');
  const dual = generatedMetadata({...machine, hardware: {...machine.hardware, gpus: [...machine.hardware.gpus, {name: 'GTX 1080 Ti', memory_bytes: 11 * 1073741824, sensor_index: 1}]}}, cfg);
  if (!dual.label.includes('GTX 1080 Ti') || !dual.notes.includes('monitoring only')) throw Error('multi GPU metadata');
  const minimal = generatedMetadata({}, cfg);
  if (minimal.label !== 'Local benchmark' || minimal.label.includes('undefined')) throw Error('missing hardware metadata');
  const rows = [
    {model:'m', target_tokens:512, concurrency:1, status:'ok', aggregate_completion_tps:999, prefill_tps:999},
    {model:'m', target_tokens:512, concurrency:10, status:'ok', aggregate_completion_tps:10, prefill_tps:100},
    {model:'m', target_tokens:512, concurrency:10, status:'ok', aggregate_completion_tps:30, prefill_tps:10},
    {model:'m', target_tokens:512, concurrency:10, status:'ok', aggregate_completion_tps:20, prefill_tps:30},
    {model:'m', target_tokens:1000, concurrency:1, status:'failed', fail_kind:'error'}
  ];
  const run = {id:'r', results:rows};
  if (pick(run, 'm', 512, false, METRICS.aggregate_completion_tps).aggregate_completion_tps !== 20) throw Error('metric median or concurrency');
  if (pointsFor(run, 'm', METRICS.aggregate_completion_tps, false)[0].y !== 20) throw Error('points');
  if (failuresFor(run, 'm', false).length !== 0) throw Error('failure filter');
  state.comparisonConcurrency = 2;
  if (tracksFor([run]).length !== 0) throw Error('track filter');
`, context);
console.log('UI logic tests passed');

(async()=>{
  let contacted = false;
  const staticContext = {window:{WATTEVER_STATIC:true}, fetch:()=>{contacted=true; throw Error('must not fetch');}, Charts:{fmtNum:String}, document:{querySelector:()=>null}};
  vm.createContext(staticContext); vm.runInContext(source, staticContext);
  await assert.rejects(vm.runInContext('api("/api/env")', staticContext), /Browser application failed to load/);
  assert.equal(contacted,false);
})().catch(error=>{console.error(error);process.exitCode=1});
