const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/app.js', 'utf8').replace(/\ninit\(\);\s*$/, '');
const elements = {
  '#max-tokens': {value: '256'}, '#repeats': {value: '2'}, '#offload': {value: 'max'},
  '#gpu-index': {value: '2'}, '#runtime-gpu-placement': {value: 'GPU 1'},
  '#fallback': {checked: true}, '#stop-after-fail': {checked: true}, '#warmup': {checked: true},
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
  if ([...through(CONCURRENCY_PRESETS, 5)].join(',') !== '1,2,3,4,5') throw Error('select five');
  if ([...through(CONCURRENCY_PRESETS, 2)].join(',') !== '1,2') throw Error('lower selection');
  if ([...through([512,5000,10000,20000,40000], 20000)].join(',') !== '512,5000,10000,20000') throw Error('context selection');
  state.env = {defaults: {other_idle_w: 10, cpu_tdp_w: 25, power_interval_ms: 200}};

  state.metrics = new Set(['prefill_tps', 'decode_tps', 'ttft_s']);
  const groups = metricGroups();
  if (groups.length !== 2 || groups.find(group => group.unit === 'tk/s').keys.length !== 2) throw Error('multi-measure unit grouping');
  if (measureAppearance({}, 'prefill_tps', ['prefill_tps','decode_tps'], false).dash === measureAppearance({}, 'decode_tps', ['prefill_tps','decode_tps'], false).dash) throw Error('measure styles');
  if (metricGroups(['j_per_1k_prefill', 'j_per_token_decode']).length !== 2) throw Error('energy normalization grouping');
  state.metrics = new Set(['prefill_tps']);
  state.comparisonConcurrency = 10;
  const cfg = collectConfig();
  if (cfg.other_idle_w !== 10 || cfg.cpu_tdp_w !== 25 || cfg.power_interval_ms !== 200) throw Error('automatic power defaults');
  const extension = extendBody({runId:'r', modelModes:{m:['off'], q:['on']}, sizes:new Set([5000,512]), concurrency:new Set([10,1]), action:'rerun'});
  if (extension.run_id !== 'r' || extension.model_modes.q[0] !== 'on' || extension.sizes.join(',') !== '512,5000' || extension.concurrency_levels.join(',') !== '1,10' || !extension.retry_failed || !extension.rerun_existing) throw Error('extension config');
  if (cfg.concurrency_levels.join(',') !== '1,10' || cfg.gpu_index !== 2 || cfg.runtime_gpu_placement !== 'GPU 1') throw Error('config');
  const machine = {
    hardware: {cpu: {name: 'Test CPU'}, system: {memory_bytes: 64 * 1073741824}, gpus: [{name: 'NVIDIA GeForce RTX 5060 Ti', memory_bytes: 16 * 1073741824, sensor_index: 2}]},
    models: [{id: 'model', type: 'llm'}],
  };
  const generated = generatedMetadata(machine, cfg);
  if (generated.label !== 'RTX 5060 Ti 16 GB VRAM / 64 GB RAM / Test CPU') throw Error('hardware name');
  if (!generated.notes.includes('all 1 installed models') || !generated.notes.includes('Simultaneous queries: 1, 10') || !generated.notes.includes('Reasoning: off')) throw Error('settings notes');
  const changed = generatedMetadata(machine, {...cfg, models: [], concurrency_levels: [1,2], max_tokens: 512, gpu_offload: 'off'});
  if (!changed.label.startsWith('CPU only /') || !changed.notes.includes('Answer: 512 tokens') || !changed.notes.includes('Models: none')) throw Error('changed config metadata');
  const dual = generatedMetadata({...machine, hardware: {...machine.hardware, gpus: [...machine.hardware.gpus, {name: 'GTX 1080 Ti', memory_bytes: 11 * 1073741824, sensor_index: 1}]}}, cfg);
  if (!dual.label.includes('GTX 1080 Ti 11 GB VRAM') || !dual.notes.includes('monitoring only')) throw Error('multi GPU metadata');
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
