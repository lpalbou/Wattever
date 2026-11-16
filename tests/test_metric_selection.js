const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let html = '';
const panel = {dataset: {}, set innerHTML(value) { html = value; }, get innerHTML() { return html; }};
const context = {Charts:{fmtNum:String}, WatteverConcurrency:require('../web/concurrency.js'),
  document:{querySelector(selector) { return selector==='#result-metrics' ? panel : null; }, querySelectorAll(){ return []; }}};
vm.createContext(context);
vm.runInContext(fs.readFileSync('web/app.js','utf8').replace(/\ninit\(\);\s*$/, ''),context);
const pressed = () => [...html.matchAll(/data-metric="([^"]+)" aria-pressed="true"/g)].map(match => match[1]);

assert.deepEqual(Array.from(vm.runInContext('[...state.metrics]',context)),['prefill_tps','decode_tps'],'Prefill and decode are shown first');
vm.runInContext('renderMetricSeg()',context);
assert.doesNotMatch(html,/<select|<option/,'Metrics are toggle buttons, never a menu');
assert.equal((html.match(/<button type="button" class="chip metric-chip"/g) || []).length,vm.runInContext('Object.keys(METRICS).length',context),'Every metric has its own button');
assert.deepEqual(pressed().sort(),['decode_tps','prefill_tps'],'Several metrics can be ticked at once');
assert.match(html,/>Speed</); assert.match(html,/>Time</); assert.match(html,/>Energy &amp; memory</);

vm.runInContext('state.metrics=new Set(["aggregate_completion_tps"]);renderMetricSeg()',context);
assert.deepEqual(pressed(),['decode_tps'],'A retired metric key migrates to decode speed');

// The axis never changes which metrics are ticked.
vm.runInContext('state.metrics=new Set(["per_request_decode_tps","ttft_s"]);state.resultView="compare";state.comparisonAxis="concurrency";renderMetricSeg()',context);
assert.deepEqual(pressed().sort(),['per_request_decode_tps','ttft_s']);
vm.runInContext('state.comparisonAxis="context";renderMetricSeg()',context);
assert.deepEqual(pressed().sort(),['per_request_decode_tps','ttft_s'],'Returning to prompt length keeps the same metrics');

// Buttons for metrics the ticked benchmarks never recorded are marked, not hidden.
context.sample={id:'r',results:[{model:'m',target_tokens:512,status:'ok',concurrency:1,wall_s:3,ttft_s:1}]};
vm.runInContext('renderMetricSeg([sample])',context);
assert.match(html,/data-metric="wall_s" aria-pressed="false" data-on="0" title/,'A recorded metric is offered normally');
assert.match(html,/data-metric="vram_peak_mb" aria-pressed="false" data-on="0" data-empty="1"/,'An unrecorded metric is marked as such');
assert.match(html,/Not recorded in the ticked benchmarks/);
console.log('Metric toggle tests passed');
