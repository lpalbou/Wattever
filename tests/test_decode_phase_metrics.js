const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const phase=require('../web/phase-metrics.js');
const q=(ttft,seconds,tokens=206,offset=0)=>({status:'ok',start_offset_s:offset,ttft_s:ttft,decode_s:seconds,decode_token_count:tokens,completion_tokens:tokens+1,wall_s:ttft+seconds,prompt_tokens:39252,decode_tps:tokens/seconds});
const row={status:'ok',concurrency:2,wall_s:66.95414739999978,requests:[
 q(26.804153399993083,5.105244900012622,206,.0020459000079426914),
 q(58.3408938000066,8.608976899995469,206,.004237200002535246)]};
const measured=phase.decodePhase(row);
assert(Math.abs(measured.tps-30.041806733)<1e-6);
assert.equal(measured.decode_tokens,412);
assert(Math.abs(measured.decode_s-13.7142218)<1e-8);
assert(Math.abs(phase.outputRate(row)-6.183336149837991)<1e-12);
const delayed=structuredClone(row);delayed.wall_s+=1000;delayed.requests.forEach(q=>{q.ttft_s+=1000;q.wall_s+=1000;});
assert(Math.abs(phase.decodePhase(delayed).tps-measured.tps)<1e-8,'Adding prefill/queue time must not reduce decode throughput');
assert(phase.outputRate(delayed)<phase.outputRate(row));
const overlap={status:'ok',concurrency:2,wall_s:30,requests:[q(20,5),q(20,5)]};
assert.equal(phase.decodePhase(overlap).tps,412/5,'Concurrent decode overlaps count once');
const separated={status:'ok',concurrency:2,wall_s:55,requests:[q(20,5),q(50,5)]};
assert.equal(phase.decodePhase(separated).tps,412/10,'A prefill/queue gap between streams is not decode time');
for(const field of ['decode_s','decode_token_count','start_offset_s','ttft_s']) {
 const copy=structuredClone(row);delete copy.requests[1][field];
 assert.equal(phase.decodePhase(copy),null,'Missing '+field+' cannot be filled from total wall time');
}
const single={...q(100,5,200),concurrency:1};
assert.equal(phase.decodePhase(single).tps,40);
single.ttft_s=1000;single.wall_s=1005;
assert.equal(phase.decodePhase(single).tps,40,'Single-request decode excludes TTFT too');
const bad=structuredClone(row);bad.requests[0].wall_s=1;
assert.equal(phase.decodePhase(bad),null,'Contradictory phase boundaries are unavailable');
// Safe counts and representable positive time intervals are required in every phase.
const huge={status:'ok',concurrency:2,wall_s:1,requests:[{status:'ok',completion_tokens:Number.MAX_SAFE_INTEGER},{status:'ok',completion_tokens:1}]};
assert.equal(phase.outputRate(huge),null,'Completion totals must remain safe integers');
const infinity={status:'ok',concurrency:1,ttft_s:0,decode_s:Number.MIN_VALUE,decode_token_count:1};
assert.equal(phase.decodePhase(infinity),null,'An infinite rate is unavailable');
assert.equal(phase.prefillPhase({status:'ok',concurrency:1,ttft_s:Number.MIN_VALUE,prompt_tokens:1}),null);
const rounded={status:'ok',concurrency:2,requests:[{status:'ok',start_offset_s:2**53,ttft_s:1,decode_s:2,decode_token_count:1},{status:'ok',start_offset_s:0,ttft_s:1,decode_s:1,decode_token_count:1}]};
assert.equal(phase.decodePhase(rounded),null,'A TTFT lost to timestamp rounding is not a valid interval');
const invalidWall={...single,wall_s:Infinity};assert.equal(phase.decodePhase(invalidWall),null);
const marked={...single,timing_scope:{decode_end_boundary:'last_observed_nonempty_output'}};
assert.equal(phase.decodePhase(marked).legacy_boundary,true,'End marker alone does not establish the protocol');
marked.timing_scope.phase_protocol='observed-output-phases-v2';
assert.equal(phase.decodePhase(marked).legacy_boundary,false);
const table={innerHTML:''};
const context={WatteverPhaseMetrics:phase,Charts:{fmtNum:String},document:{querySelector:s=>s==='#live-table'?table:null}, row};
vm.createContext(context);vm.runInContext(fs.readFileSync('web/app.js','utf8').replace(/\ninit\(\);\s*$/,''),context);
const live=vm.runInContext('liveRowMetrics(row)',context);
assert(Math.abs(live.decode-30.041806733)<1e-6);
assert(Math.abs(live.outputTps-6.183336149837991)<1e-12);
// The two requests decode one after the other, so one request gets the total rate: never more.
assert(Math.abs(live.perRequestDecode-30.041806733)<1e-6);
assert(live.perRequestDecode<=live.decode+1e-9 && live.decode<=2*live.perRequestDecode+1e-9,'per request <= total <= n x per request');
assert(Math.abs(live.decodeStats.together-1)<1e-9 && live.decodeStats.slowest<live.perRequestDecode && live.decodeStats.fastest>live.perRequestDecode);
// Two requests that decode together for their whole stream: total is their sum, 2 of 2 at once.
context.pair={concurrency:2,status:'ok',wall_s:6,requests:[0,0.001].map(start=>({status:'ok',start_offset_s:start,ttft_s:1,decode_s:4,decode_token_count:100,completion_tokens:101,prompt_tokens:10,wall_s:5.1}))};
const pair=vm.runInContext('liveRowMetrics(pair)',context);
assert(Math.abs(pair.perRequestDecode-25)<1e-9 && Math.abs(pair.decode-200/4.001)<1e-9 && Math.abs(pair.decodeStats.together-8/4.001)<1e-9);
// One stream stalls while the other prompt is read, then both decode: the old median (32.5) was above the total.
context.stalled={concurrency:2,status:'ok',wall_s:31,requests:[{start_offset_s:0,ttft_s:13,decode_s:16,decode_token_count:208},{start_offset_s:0,ttft_s:25,decode_s:5,decode_token_count:260}].map(q=>({...q,status:'ok',completion_tokens:q.decode_token_count+1,prompt_tokens:10,wall_s:q.ttft_s+q.decode_s}))};
const stalled=vm.runInContext('liveRowMetrics(stalled)',context);
assert(Math.abs(stalled.decode-468/17)<1e-9 && Math.abs(stalled.perRequestDecode-468/21)<1e-9 && stalled.perRequestDecode<stalled.decode);
assert(Math.abs(stalled.decode-stalled.perRequestDecode*stalled.decodeStats.together)<1e-9,'total = per request x decoding at once');
assert.equal(stalled.decodeStats.slowest,13); assert.equal(stalled.decodeStats.fastest,52);
vm.runInContext("row.model='qwen3.8-flash-next-iq3_s';row.target_tokens=40000;state.liveResults=[row];renderLiveTable()",context);
assert.match(table.innerHTML,/Total decode tk\/s/);
assert.match(table.innerHTML,/>30\.0\*<\/td>/);
assert.match(table.innerHTML,/>6\.2<\/td>/);
assert.match(table.innerHTML,/>13\.71\*<\/td>/);
assert.match(table.innerHTML,/End-to-end output tk\/s/);
console.log('Decode phase definitions: exact user case, overlap, gaps, long TTFT, missing/contradictory evidence and live table passed');
const missing={status:'ok',concurrency:1,decode_tps:888};
context.missing=missing;
assert.equal(vm.runInContext('decodeRate(missing)',context),null);
assert.equal(vm.runInContext('requestDecodeRate(missing)',context),null);
assert.equal(vm.runInContext('storageMedian([0,2])',context),1);
context.incomplete={status:'ok',concurrency:2,requests:[q(1,2),{status:'ok',decode_tps:888}]};
assert.equal(vm.runInContext('requestDecodeRate(incomplete)',context),null);
assert.match(fs.readFileSync('web/app.js','utf8'),/GPU decode energy per scoped decode token/);
