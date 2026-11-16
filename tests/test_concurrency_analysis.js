const assert=require('node:assert/strict');
const C=require('../web/concurrency.js');
const single={model:'model',target_tokens:20000,concurrency:1,status:'ok',repeat:0,completion_tokens:100,wall_s:10,
  decode_tps:50,decode_token_count:40,decode_s:4,prefill_tps:10000,ttft_s:2,per_request_context_length:24000,gpu_offload:'max',
  power:{scope:'all_cards',gpu_indices:[0,1],low_confidence:false,prefill_gpu_power_complete:true,decode_gpu_power_complete:true,prefill_energy_j:30,decode_energy_j:70}};
const group={model:'model',target_tokens:20000,concurrency:2,status:'ok',repeat:0,completed_requests:2,
  aggregate_completion_tokens:200,wall_s:12,per_request_context_length:24000,gpu_offload:'max',
  requests:[{status:'ok',completion_tokens:100,prompt_tokens:20010,decode_tps:30,decode_token_count:90,decode_s:3,prefill_tps:5000,start_offset_s:0,ttft_s:3,wall_s:10},
    {status:'ok',completion_tokens:100,prompt_tokens:20011,decode_tps:40,decode_token_count:80,decode_s:4,prefill_tps:4000,start_offset_s:2,ttft_s:5,wall_s:12}],
  group_power:{scope:'all_cards',gpu_indices:[0,1],low_confidence:false,gpu_power_complete:true,gpu_energy_j:180,gpu_sampled_coverage_fraction:.98}};
const failed={...group,concurrency:4,status:'failed',fail_kind:'timeout',completed_requests:1};
const run={id:'r',label:'GPU setup',config:{models:['model'],sizes:[512,20000,80000],concurrency_levels:[1,2,4,8],temperature:0},results:[single,group,failed]};
const clone=x=>JSON.parse(JSON.stringify(x));
const raw=JSON.stringify(run),data=C.build([run]),g=data.groups[0],one=C.getCell(g,20000,1),two=C.getCell(g,20000,2);
assert.deepEqual(data.levels,[1,2,4,8]);assert.deepEqual(data.prompts,[512,20000,80000]);assert.deepEqual(data.recordedPrompts,[20000]);
assert.equal(one.metrics.output,10,'Single-request end-to-end output');
assert.equal(two.metrics.output,200/12);
assert.equal(two.metrics.decode,170/7);assert.equal(two.metrics.prefill,40021/7);assert.equal(two.metrics.ttft,4);
assert.equal(two.metrics.finish,11);assert.equal(two.metrics.batchwall,12,'Group wall differs from the median request duration');assert.deepEqual(two.ranges.ttft,[3,5]);assert.deepEqual(two.ranges.finish,[10,12]);
assert.equal(two.metrics.gain,(200/12)/10);assert.equal(one.metrics.energy,null,'Phase-only energy excludes trailing case time');assert.equal(two.metrics.energy,.9,'One shared energy interval, not once per request');
assert.equal(C.getCell(g,20000,4).status,'timeout');assert.equal(C.getCell(g,20000,4).metrics.output,null,'Partial and failed groups never compete for best throughput');
assert.equal(C.getCell(g,20000,8),undefined,'Missing groups stay missing');assert.equal(C.best(data,20000,'model').cell.n,2,'Wall-clock throughput uses the same boundary for each load');
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
assert.equal(C.measurements({...single,completion_tokens:null,decode_tps:null}).output,null,'Unknown decode is not invented');
assert.equal(C.measurements({...group,requests:[],decode_tps:77}).decode,null,'Group row field is not invented per-request evidence');

assert.equal(C.measurements({...group,requests:[group.requests[0],{...group.requests[1],ttft_s:null}]}).prefill,null,'Every included stream needs TTFT');
assert.equal(C.measurements({...group,requests:[group.requests[0],{...group.requests[1],ttft_s:null}]}).ttft,null,'Missing TTFT cannot become a complete-group median');
assert.equal(C.measurements({...group,requests:[group.requests[0],{...group.requests[1],start_offset_s:undefined}]}).prefill,null,'Legacy timing has no shared start');
assert.equal(C.measurements({...group,aggregate_completion_tokens:999}).decode,170/7,'Use counts of the included streams');
const csvLines=C.csv(data,20000).split('\n');
assert.equal(csvLines[0].split(',').length,(csvLines[1].match(/"(?:[^"]|"")*"/g) || []).length,'CSV fields match the header');

const slowerRepeat={...group,repeat:1,wall_s:24,aggregate_completion_tokens:200,requests:group.requests};
const repeats=clone(run);repeats.results.push(slowerRepeat);
assert.equal(C.getCell(C.build([repeats]).groups[0],20000,2).metrics.output,((200/12)+(200/24))/2);
assert.equal(C.getCell(C.build([repeats]).groups[0],20000,2).successes,2);
const changed=clone(run);changed.results[1].gpu_offload='partial';
const changedGroups=C.build([changed]).groups;
const changedCell=changedGroups.map(g=>C.getCell(g,20000,2)).find(Boolean);
assert.equal(changedGroups.length,2,'Different actual placements remain separate conditions');
assert.equal(changedCell.metrics.gain,null);assert.match(changedCell.gainUnavailableReason,/baseline/);
const estimated=clone(run);estimated.results[1].aggregate_completion_tokens_estimated=true;
assert.equal(C.getCell(C.build([estimated]).groups[0],20000,2).metrics.gain,null);
assert.equal(C.getCell(C.build([estimated]).groups[0],20000,2).metrics.energy,null);
assert.equal(C.measurements({...group,group_power:{...group.group_power,low_confidence:true}}).energy,null);
assert.equal(C.measurements({...group,group_power:{...group.group_power,scope:null}}).energy,null,'Unknown power boundary is not a comparable energy observation');
assert.equal(C.measurements({...group,group_power:{...group.group_power,gpu_power_complete:null}}).energy,null,'Unknown completeness is not assumed complete');
assert.equal(C.measurements({...group,group_power:{...group.group_power,gpu_sampled_coverage_fraction:.3}}).energy,null);
const singlePower={...single,completion_tokens:999,requests:[{status:'ok',completion_tokens:100}],group_power:{scope:'all_cards',gpu_power_complete:true,low_confidence:false,gpu_energy_j:200}};
assert.equal(C.measurements(singlePower).energy,2,'Energy uses the same successful request count as end-to-end output');
assert.equal(C.measurements({...singlePower,status:'failed'}).energy,null,'Failed groups do not acquire a whole-case energy rate');

for (const [key,value] of [['quantization','Q8'],['kv_cache_dtype','fp16'],['configuration_id','different']]) {
  const split=clone(run);split.results[1][key]=value;
  assert.equal(C.build([split]).groups.length,2,`Separate recorded condition: ${key}`);
}
// Protocols have different decode end boundaries and must not share repeat medians or baselines.
const currentProtocol='observed-output-phases-v2';
const modernGroup={...group,repeat:1,requests:group.requests.map(request=>({...request,timing_scope:{phase_protocol:currentProtocol,decode_end_boundary:'last_observed_nonempty_output'}}))};
const protocolData=C.build([{...run,results:[single,group,modernGroup]}]);
assert.equal(protocolData.groups.length,2,'Separate legacy and current phase protocols');
const modern=protocolData.groups.find(item=>item.identity.phase_protocol.some(([protocol])=>protocol===currentProtocol));
const legacy=protocolData.groups.find(item=>item.identity.phase_protocol.some(([protocol])=>protocol==='legacy_boundary'));
assert.equal(C.getCell(modern,20000,2).successes,1);
assert.equal(C.getCell(legacy,20000,2).successes,1);
assert.equal(C.getCell(modern,20000,2).metrics.gain,null,'Legacy one-request baseline is not paired with the current protocol');
assert.match(C.csv(protocolData,20000),/observed-output-phases-v2/,'CSV carries the condition protocol');
const rowScope={...modernGroup,timing_scope:{phase_protocol:'row-protocol'}};
assert.equal(C.build([{...run,results:[rowScope]}]).groups[0].identity.phase_protocol[0][0],currentProtocol,'Every request scope determines concurrent conditions');
const mixedProtocol={...modernGroup,repeat:2,requests:[modernGroup.requests[0],group.requests[1]]};
assert.equal(C.build([{...run,results:[modernGroup,mixedProtocol]}]).groups.length,2,'A legacy second request creates a separate condition');
const reversed={...mixedProtocol,requests:[...mixedProtocol.requests].reverse()};
assert.equal(C.build([{...run,results:[mixedProtocol,reversed]}]).groups.length,1,'Protocol grouping does not depend on dispatch order');
const second=clone(run);second.id='other';assert.equal(C.build([run,second]).groups.length,2,'Separate machines never pool');
const contexts=clone(run);contexts.results.push({...group,target_tokens:80000,decode_tps:2,prefill_tps:2,
  requests:[{status:'ok',decode_tps:1,prefill_tps:1,ttft_s:1,wall_s:1,completion_tokens:1,prompt_tokens:1},{status:'ok',decode_tps:1,prefill_tps:1,ttft_s:1,wall_s:1,completion_tokens:1,prompt_tokens:1}]});
assert.equal(C.getCell(C.build([contexts]).groups[0],20000,2).metrics.output,200/12,'Context sizes never pool');
assert.equal(C.getCell(C.build([repeats]).groups[0],20000,2).metrics.gain,(200/12)/10,'Only matching repeat0 contributes to gain, never unmatched repeat1');
const unmatched=clone(run);unmatched.results[1].repeat=5;
assert.equal(C.getCell(C.build([unmatched]).groups[0],20000,2).metrics.gain,null,'Unmatched repeats have no fabricated scaling ratio');
const skipped=clone(run);skipped.results[2].status='skipped';
assert.equal(C.getCell(C.build([skipped]).groups[0],20000,4).status,'skipped','Skipped after timeout is not a measured timeout');
assert.equal(C.build([run],{models:['missing']}).groups.length,0);
assert.equal(C.build([run],{allow:(run,row)=>row.concurrency===1}).groups[0].cells.size,1);
const exported=C.csv(data,20000);assert.match(exported,/median_request_ttft_s/);assert.match(exported,/collective_decode_tps/);assert.match(exported,/median_batch_wall_s/);assert.match(exported,/timeout/);assert.match(exported,/end_to_end_output_tps/);
const timing=C.requestTimeline(group);
assert.equal(timing.first,3);assert.equal(timing.median,5);assert.equal(timing.last,7);assert.equal(timing.duration,14);
assert.equal(timing.entries[0].finish,10);
assert.equal(C.requestTimeline({...group,requests:[{status:'failed'}]}).first,null,'Failed streams have no invented timing');
assert.equal(C.requestTimeline({...group,requests:[]}).entries.length,0,'No fake concurrent request bars');
// Sequentially scheduled streams expose the difference between phase union and group wall.
const sequential={status:'ok',concurrency:2,completed_requests:2,wall_s:66.9541474,requests:[
 {status:'ok',start_offset_s:.0020459,ttft_s:26.804153399993083,decode_s:5.105244900012622,decode_token_count:206,completion_tokens:207},
 {status:'ok',start_offset_s:.0042372,ttft_s:58.3408938000066,decode_s:8.608976899995469,decode_token_count:206,completion_tokens:207}]};
assert(Math.abs(C.measurements(sequential).decode-30.0418)<.0001);
assert(Math.abs(C.measurements(sequential).output-6.1833)<.0001);
const staggered={...sequential,requests:[{...sequential.requests[0],start_offset_s:0,ttft_s:2,prompt_tokens:100},{...sequential.requests[1],start_offset_s:5,ttft_s:2,prompt_tokens:100}]};
assert.equal(C.measurements(staggered).prefill,50,'Disjoint TTFT intervals exclude gaps from the client prefill proxy');
assert.equal(C.measurements({...sequential,requests:[sequential.requests[0],{...sequential.requests[1],decode_s:null}]}).decode,null);
assert.equal(C.measurements({...sequential,requests:[sequential.requests[0],{...sequential.requests[1],start_offset_s:null}]}).decode,null);
assert.equal(C.measurements({...sequential,requests:[sequential.requests[0],{...sequential.requests[1],decode_token_count:null}]}).decode,null);
assert.equal(C.phaseMetrics,require('../web/phase-metrics.js'));
const rawPath='results/runs/20261113-191954_rtx-5070-ti_f950fa6773.json';
if (require('node:fs').existsSync(rawPath)) {
 const rawRun=JSON.parse(require('node:fs').readFileSync(rawPath,'utf8'));
 const rawRow=rawRun.results.find(row=>row.target_tokens===40000 && row.concurrency===2);
 assert(Math.abs(C.measurements(rawRow).decode-30.0418)<.0001);
 assert(Math.abs(C.measurements(rawRow).output-6.1833)<.0001);
}

const vm=require('node:vm'), fs=require('node:fs'), context={WatteverPhaseMetrics:require('../web/phase-metrics.js')};
vm.runInNewContext(fs.readFileSync(require.resolve('../web/concurrency.js'),'utf8').replace('  const api={phaseMetrics,build,', '  globalThis.testEvidence=evidence; globalThis.testMatrix=matrix; globalThis.testMetricEstimateNote=metricEstimateNote;\n  const api={phaseMetrics,build,'),context);
const boundaryGroup={...group,requests:group.requests.map(request=>({...request,timing_scope:{decode_count_estimated_boundary:true,decode_end_boundary:'last_observed_nonempty_output',phase_protocol:'observed-output-phases-v2'}}))};
const boundaryData=C.build([{...run,results:[boundaryGroup]}]), boundaryCell=C.getCell(boundaryData.groups[0],20000,2);
assert.equal(boundaryCell.estimated,false,'The first-output convention does not estimate completion counts');
assert.equal(boundaryCell.decodeBoundaryEstimated,true);
assert.equal(boundaryCell.legacyDecodeBoundary,false);
assert.equal(boundaryCell.metrics.energy,.9,'Whole-case energy keeps measured completion counts');
const decodeMap=context.testMatrix(boundaryData,20000,{mapMetric:'decode',tab:'throughput',model:'__all__'},()=>({color:'red'}));
const outputMap=context.testMatrix(boundaryData,20000,{mapMetric:'output',tab:'throughput',model:'__all__'},()=>({color:'red'}));
assert.match(decodeMap,/<b>\*[^<]+<\/b>/,'Decode boundary approximation is marked');
assert.doesNotMatch(outputMap,/<b>\*[^<]+<\/b>/,'Output is not marked for a decode-only approximation');
assert.match(context.testMetricEstimateNote(boundaryCell,'decode'),/first output event may contain multiple tokens/);
assert.equal(context.testMetricEstimateNote(boundaryCell,'output'),'');
const boundaryEvidence=context.testEvidence(boundaryData.groups[0],boundaryCell);
assert.match(boundaryEvidence,/<td>100<\/td><td>3<\/td><td>30\*<\/td>/,'Request output count remains exact while decode rate is marked');
const boundaryCsv=C.csv(boundaryData,20000);
assert.match(boundaryCsv,/decode_count_estimated_boundary/);assert.match(boundaryCsv,/legacy_decode_end_boundary_unknown/);
const legacyBoundaryCell=C.getCell(C.build([{...run,results:[group]}]).groups[0],20000,2);
assert.equal(legacyBoundaryCell.legacyDecodeBoundary,true,'Unknown older decode ends remain identified');
assert.match(context.testMetricEstimateNote(legacyBoundaryCell,'decode'),/may include trailing usage/);
const legacyGroup={...group,requests:group.requests.map(({start_offset_s,...request})=>request)};
const legacyData=C.build([{...run,results:[legacyGroup]}]);
const legacyEvidence=context.testEvidence(legacyData.groups[0],C.getCell(legacyData.groups[0],20000,2));
assert.match(legacyEvidence,/shared timing is unavailable.*without recorded start offsets/);
assert.doesNotMatch(legacyEvidence,/class="sc-timeline-row"/,'Missing offsets never produce aligned bars');
assert.match(legacyEvidence,/<td>10<\/td>/,'Recorded request durations remain in the table');
const mixedGroup={...group,requests:[group.requests[0],legacyGroup.requests[1]]};
const mixedData=C.build([{...run,results:[mixedGroup]}]);
const mixedEvidence=context.testEvidence(mixedData.groups[0],C.getCell(mixedData.groups[0],20000,2));
assert.match(mixedEvidence,/shared timing is unavailable/);
assert.match(mixedEvidence,/class="sc-timeline-row"/,'Requests with offsets remain visible');
assert.doesNotMatch(context.testEvidence(g,two),/shared timing is unavailable/,'Recorded offsets require no warning');
console.log('Concurrency analysis: matched conditions, fixed contexts, medians, collective decode throughput, partial/failure handling, shared energy, unknowns and preserved sources passed.');
