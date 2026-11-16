// Both result views must use the same evidence and shared timing origin.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const explorer = require('../web/concurrency.js');
const source = fs.readFileSync('web/app.js', 'utf8').replace(/\ninit\(\);\s*$/, '');
const context = {WatteverConcurrency:explorer,Charts:{fmtNum:String}, document:{querySelector:()=>null,querySelectorAll:()=>[]}};
vm.createContext(context);
vm.runInContext(source, context);

const row = {status:'ok',concurrency:2,wall_s:10,requests:[
  {status:'ok',prompt_tokens:100,completion_tokens:10,start_offset_s:0,ttft_s:3},
  {status:'ok',prompt_tokens:100,completion_tokens:10,start_offset_s:5,ttft_s:2},
]};
context.row = row;
assert.ok(Math.abs(explorer.measurements(row).prefill - 200/5) < 1e-9);
assert.ok(Math.abs(vm.runInContext('collectiveRate(row,"prefill_tps")',context) - 200/5) < 1e-9,
  'Main Results must account for staggered request starts');
for (const key of ['ttft_s','start_offset_s']) {
  const missing = structuredClone(row);
  missing.requests[1][key] = null;
  context.row = missing;
  assert.equal(explorer.measurements(missing).prefill, null);
  assert.equal(vm.runInContext('collectiveRate(row,"prefill_tps")',context), null,
    `Main Results must leave prefill unavailable when ${key} is missing`);
}
const legacy = {status:'ok',concurrency:2,wall_s:10,prefill_tps:999,requests:[]};
context.row = legacy;
assert.equal(vm.runInContext('collectiveRate(row,"prefill_tps")',context), null,
  'A historical stored rate without matching provenance is not a group rate');
context.row = {...row,model:'m',target_tokens:100,prefill_tps:999,decode_tps:999};
context.run = {id:'r',results:[context.row],storage_comparison:{baseline_results:[context.row],alternate_results:[context.row]}};
vm.runInContext('state.comparisonConcurrency=2; state.visibleModels=null',context);
const csv = vm.runInContext('selectedResultsCsv([run])',context);
const storageCsv = vm.runInContext('storageComparisonCsv(run)',context);
for (const result of [csv,storageCsv]) {
  assert.match(result,/"40"/);
  assert.match(result,/start_offset_s/);
  assert.doesNotMatch(result,/"999"/);
}
context.row = {...context.row,requests:context.row.requests.slice(0,1)};
assert.equal(vm.runInContext('collectiveRate(row,"decode_tps",{partial:true})',context),null,'Successful batches require all requests');
console.log('Shared-origin and missing-evidence regression cases passed');
