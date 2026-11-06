const assert=require('node:assert/strict');
const C=require('../web/concurrency.js');
const single={model:'model',target_tokens:20000,concurrency:1,status:'ok',repeat:0,completion_tokens:100,wall_s:10,
  decode_tps:50,prefill_tps:10000,ttft_s:2,per_request_context_length:24000,gpu_offload:'max',
  power:{scope:'all_cards',gpu_indices:[0,1],low_confidence:false,prefill_gpu_power_complete:true,decode_gpu_power_complete:true,prefill_energy_j:30,decode_energy_j:70}};
const group={model:'model',target_tokens:20000,concurrency:2,status:'ok',repeat:0,completed_requests:2,
  aggregate_completion_tokens:200,aggregate_completion_tps:200/12,wall_s:12,per_request_context_length:24000,gpu_offload:'max',
  requests:[{status:'ok',completion_tokens:100,prompt_tokens:20010,decode_tps:30,prefill_tps:5000,ttft_s:3,wall_s:10},
    {status:'ok',completion_tokens:100,prompt_tokens:20011,decode_tps:40,prefill_tps:4000,ttft_s:5,wall_s:12}],
  group_power:{scope:'all_cards',gpu_indices:[0,1],low_confidence:false,gpu_power_complete:true,gpu_energy_j:180,gpu_sampled_coverage_fraction:.98}};
const failed={...group,concurrency:4,status:'failed',fail_kind:'timeout',completed_requests:1,aggregate_completion_tps:900};
const run={id:'r',label:'GPU setup',config:{models:['model'],sizes:[512,20000,80000],concurrency_levels:[1,2,4,8],temperature:0},results:[single,group,failed]};
const clone=x=>JSON.parse(JSON.stringify(x));
const raw=JSON.stringify(run),data=C.build([run]),g=data.groups[0],one=C.getCell(g,20000,1),two=C.getCell(g,20000,2);
assert.deepEqual(data.levels,[1,2,4,8]);assert.deepEqual(data.prompts,[512,20000,80000]);assert.deepEqual(data.recordedPrompts,[20000]);
assert.equal(one.metrics.total,10,'Single-request group rate includes prefill, never substitutes decode50');
assert.equal(two.metrics.total,200/12,'Recorded group rate, never sum client rates70');
assert.equal(two.metrics.decode,35);assert.equal(two.metrics.prefill,4500);assert.equal(two.metrics.ttft,4);
assert.equal(two.metrics.finish,11);assert.deepEqual(two.ranges.ttft,[3,5]);assert.deepEqual(two.ranges.finish,[10,12]);
assert.equal(two.metrics.gain,(200/12)/10);assert.equal(one.metrics.energy,1);assert.equal(two.metrics.energy,.9,'One shared energy interval, not once per request');
assert.equal(C.getCell(g,20000,4).status,'timeout');assert.equal(C.getCell(g,20000,4).metrics.total,null,'Partial and failed groups never compete for best throughput');
assert.equal(C.getCell(g,20000,8),undefined,'Missing groups stay missing');assert.equal(C.best(data,20000,'model').cell.n,2);
assert.equal(JSON.stringify(run),raw,'Analysis must not mutate historical records');

const partial={...group,status:'ok',completed_requests:1};assert.equal(C.successful(partial),false);
assert.equal(C.successful({...group,requests:[group.requests[0]]}),false);
assert.equal(C.successful({...group,requests:[group.requests[0],{status:'failed'}]}),false);
const unverified={...group,completed_requests:undefined,requests:[],aggregate_completion_tps:900};
assert.equal(C.successful(unverified),false,'Concurrent completeness requires a count or complete request records');
assert.equal(C.getCell(C.build([{...run,results:[unverified]}]).groups[0],20000,2).status,'unverified');
assert.equal(C.best(C.build([{...run,results:[unverified]}]),20000,'model'),null);
assert.equal(C.successful({...group,requests:[]}),true,'Explicit complete count remains usable without request details');
assert.equal(C.partialOffload('0.5'),true,'Real saved CPU fallback uses a numeric string');
assert.equal(C.partialOffload(.5),true);assert.equal(C.partialOffload('max'),false);
assert.equal(C.partialOffload('auto'),false);assert.equal(C.partialOffload(''),false);
assert.equal(C.measurements({...single,completion_tokens:null,aggregate_completion_tps:null}).total,null,'Unknown count is not zero and not decode');
assert.equal(C.measurements({...group,requests:[],decode_tps:77}).decode,null,'Group row field is not invented per-request evidence');

const repeats=clone(run);repeats.results.push({...group,repeat:1,aggregate_completion_tps:30});
assert.equal(C.getCell(C.build([repeats]).groups[0],20000,2).metrics.total,((200/12)+30)/2);
assert.equal(C.getCell(C.build([repeats]).groups[0],20000,2).successes,2);
const changed=clone(run);changed.results[1].gpu_offload='partial';
const changedGroups=C.build([changed]).groups;
const changedCell=changedGroups.map(g=>C.getCell(g,20000,2)).find(Boolean);
assert.equal(changedGroups.length,2,'Different actual placements remain separate conditions');
assert.equal(changedCell.metrics.gain,null);assert.match(changedCell.gainUnavailableReason,/baseline/);
const estimated=clone(run);estimated.results[1].aggregate_completion_tokens_estimated=true;
assert.equal(C.getCell(C.build([estimated]).groups[0],20000,2).metrics.gain,null);
assert.equal(C.getCell(C.build([estimated]).groups[0],20000,2).metrics.energy,null);
assert.equal(C.measurements({...single,power:{...single.power,low_confidence:true}}).energy,null);
assert.equal(C.measurements({...single,power:{...single.power,scope:null}}).energy,null,'Unknown power boundary is not a comparable energy observation');
assert.equal(C.measurements({...single,power:{...single.power,decode_gpu_power_complete:null}}).energy,null,'Unknown completeness is not assumed complete');
assert.equal(C.measurements({...group,group_power:{...group.group_power,gpu_sampled_coverage_fraction:.3}}).energy,null);

for (const [key,value] of [['quantization','Q8'],['kv_cache_dtype','fp16'],['configuration_id','different']]) {
  const split=clone(run);split.results[1][key]=value;
  assert.equal(C.build([split]).groups.length,2,`Separate recorded condition: ${key}`);
}
const second=clone(run);second.id='other';assert.equal(C.build([run,second]).groups.length,2,'Separate machines never pool');
const contexts=clone(run);contexts.results.push({...group,target_tokens:80000,aggregate_completion_tps:2});
assert.equal(C.getCell(C.build([contexts]).groups[0],20000,2).metrics.total,200/12,'Context sizes never pool');
assert.equal(C.getCell(C.build([repeats]).groups[0],20000,2).metrics.gain,(200/12)/10,'Only matching repeat0 contributes to gain, never unmatched repeat1');
const unmatched=clone(run);unmatched.results[1].repeat=5;
assert.equal(C.getCell(C.build([unmatched]).groups[0],20000,2).metrics.gain,null,'Unmatched repeats have no fabricated scaling ratio');
const skipped=clone(run);skipped.results[2].status='skipped';
assert.equal(C.getCell(C.build([skipped]).groups[0],20000,4).status,'skipped','Skipped after timeout is not a measured timeout');
assert.equal(C.build([run],{models:['missing']}).groups.length,0);
assert.equal(C.build([run],{allow:(run,row)=>row.concurrency===1}).groups[0].cells.size,1);
const exported=C.csv(data,20000);assert.match(exported,/median_request_ttft_s/);assert.match(exported,/timeout/);assert.doesNotMatch(exported,/,900,/);
const timing=C.requestTimeline(group);
assert.equal(timing.first,3);assert.equal(timing.median,4);assert.equal(timing.last,5);assert.equal(timing.duration,12);
assert.equal(timing.entries[0].finish,10);
assert.equal(C.requestTimeline({...group,requests:[{status:'failed'}]}).first,null,'Failed streams have no invented timing');
assert.equal(C.requestTimeline({...group,requests:[]}).entries.length,0,'No fake concurrent request bars');
console.log('Concurrency analysis: matched conditions, fixed contexts, medians, true group throughput, partial/failure handling, shared energy, unknowns and preserved sources passed.');
