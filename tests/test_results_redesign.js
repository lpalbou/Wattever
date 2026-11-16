const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const concurrency = require('../web/concurrency.js');
const context = {WatteverConcurrency:concurrency,Charts:{fmtNum:String},document:{querySelector:()=>null,querySelectorAll:()=>[]}};
vm.createContext(context);
vm.runInContext(fs.readFileSync('web/app.js','utf8').replace(/\ninit\(\);\s*$/, ''),context);
const one={model:'m',target_tokens:512,status:'ok',concurrency:1,completion_tokens:100,wall_s:2.5,decode_tps:50};
const two={model:'m',target_tokens:512,status:'ok',concurrency:2,wall_s:4,requests:[
  {status:'ok',completion_tokens:100,decode_tps:50}, {status:'ok',completion_tokens:100,decode_tps:45}]};
for(const [row,expected] of [[one,40],[two,50]]) {
  context.row=row;
  assert.equal(vm.runInContext('METRICS.group_output_tps.get(row)',context),expected,'Prompt-length graph uses completed output divided by group wall time');
  assert.equal(concurrency.measurements(row).output,expected,'Concurrency graph uses the same throughput');
}
context.run={id:'r',results:[one,two]};
const csv=vm.runInContext('selectedResultsCsv([run])',context);
assert.match(csv,/"decode_tps"/,'CSV keeps its recorded decode field');
assert.match(csv,/""decode_tps"":50/,'CSV retains the raw recorded rate as request evidence');
assert.match(csv,/"group_output_tps","per_request_decode_tps"/);
assert.match(csv,/"40",$/,'Missing phase evidence leaves derived decode unavailable');
vm.runInContext('state.comparisonConcurrency=2',context);
const concurrentCsv=vm.runInContext('selectedResultsCsv([run])',context);
assert.match(concurrentCsv,/""decode_tps"":45/,'CSV retains individual concurrent request rates');
assert.match(concurrentCsv,/"50",$/,'Concurrent derived decode needs phase evidence for every request');
context.missing={...one,completion_tokens:null};
assert.equal(vm.runInContext('METRICS.group_output_tps.get(missing)',context),null,'Missing output counts do not fabricate group throughput');
context.run.storage_comparison={baseline_results:[one,two],alternate_results:[one,two]};
const storageCsv=vm.runInContext('storageComparisonCsv(run)',context);
assert.match(storageCsv,/"group_output_tps","per_request_decode_tps"/);
assert.match(storageCsv,/"50",/);
vm.runInContext('state.comparisonConcurrency=1',context);
assert.match(vm.runInContext('storageComparisonCsv(run)',context),/"40",/);
context.row=one;
assert.equal(vm.runInContext('METRICS.per_request_decode_tps.get(row)',context),null,'Per-request decode needs phase evidence');
assert.equal(one.decode_tps,50);
assert.deepEqual(two.requests.map(request=>request.decode_tps),[50,45],'Per-request decode evidence stays distinct');
context.mixed={id:'mixed',label:'Shared benchmark',results:[
  {model:'m',target_tokens:512,concurrency:1,status:'ok',configuration_id:'config-alpha',prompt_tokens:10,ttft_s:1,prefill_tps:10},
  {model:'m',target_tokens:512,concurrency:1,status:'ok',configuration_id:'config-beta',prompt_tokens:20,ttft_s:1,prefill_tps:20},
]};
vm.runInContext('state.conditions=null;state.comparisonConcurrency=1;state.visibleModels=null',context);
assert.equal(vm.runInContext('pick(mixed,"m",512,false,METRICS.prefill_tps)',context),null,'A mixed configuration cannot be silently averaged');
const separated=vm.runInContext('splitRowConditions([mixed])',context);
assert.equal(separated.length,2,'Recorded configurations produce separate graph and table sources');
context.separated=separated;
assert.deepEqual(Array.from(vm.runInContext('separated.map(run=>METRICS.prefill_tps.get(pick(run,"m",512,false,METRICS.prefill_tps))).sort((a,b)=>a-b)',context)),[10,20]);
console.log('Results throughput axis consistency tests passed');


