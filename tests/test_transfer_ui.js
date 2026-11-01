const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const context = {window: {}, Charts: {fmtNum: String}, document: {querySelector() {}, querySelectorAll() { return []; }}, console};
vm.createContext(context);
vm.runInContext(fs.readFileSync('web/app.js', 'utf8').replace(/\ninit\(\);\s*$/, ''), context);
assert.match(vm.runInContext('transferBandwidthMarkup(null)', context), /No transfer capture attached/);
context.profile = {captured_at: '2026-10-31', provenance: 'measured_later_on_matching_hardware',
  storage_to_ram: {status: 'ok', bytes_per_second: 537000000, samples: [{}, {}, {}], volume: 'C:'},
  ram_to_vram: {status: 'ok', bytes_per_second: 13540000000, samples: [{}, {}, {}], host_memory: 'pinned'}};
let html = vm.runInContext('transferBandwidthMarkup(profile)', context);
assert.match(html, /0.54 GB\/s/);assert.match(html,/537 MB\/s/);
assert.match(html, /13.54 GB\/s/);
assert.match(html, /Measured after this benchmark/);
assert.match(html, /3 storage samples and 3 GPU samples/);
context.profile.ram_to_vram = {status: 'unavailable', reason: '<driver unavailable>'};
html = vm.runInContext('transferBandwidthMarkup(profile)', context);
assert.match(html, /&lt;driver unavailable&gt;/);
assert.doesNotMatch(html, /13.54/);
context.profile.ram_copy_parallel={status:'ok',bytes_per_second:12000000000,workers:6,note:'Copied payload bytes'};
context.profile.storage_volumes=[{status:'ok',bytes_per_second:1800000000,volume:'F:',device_model:'NVMe SSD'}];
html=vm.runInContext('transferBandwidthMarkup(profile)',context);
assert.match(html,/12.00 GB\/s/);assert.match(html,/6 workers/);
assert.match(html,/1.80 GB\/s/);assert.match(html,/\(F:\)/);assert.match(html,/NVMe SSD/);
console.log('Transfer bandwidth labels, units, unavailable state and historical provenance passed');
context.taggedRun = {tags: ['RTX 5070 Ti', 'Strata', '<script>'], config: {}};
const taggedHtml = vm.runInContext('scientificMetadata(taggedRun)', context);
assert.match(taggedHtml, /Tags:<\/b> RTX 5070 Ti · Strata · &lt;script&gt;/);
assert.doesNotMatch(taggedHtml, /<script>/);
context.comparisonRun={id:'sample',results:[{model:'m',target_tokens:512,concurrency:1,repeat:0,wall_s:10,status:'ok'},
  {model:'other',target_tokens:512,wall_s:99,status:'ok'}],storage_comparison:{protocol:'wattever-storage-comparison-v1',
  baseline_label:'Samsung SATA',alternative_label:'NVMe',cache_policy:'OS cache uncontrolled',rows:[
    {model:'m<script>',quantization:'IQ3_S',target_tokens:512,concurrency:1,baseline_successes:2,alternative_successes:2,paired_successes:2,baseline_wall_s:10,alternative_wall_s:5,wall_speedup:2}],
  alternate_results:[{model:'m',target_tokens:512,concurrency:1,repeat:0,wall_s:5,status:'ok'}]}};
context.comparisonRun.storage_comparison.baseline_results=[{...context.comparisonRun.results[0]}];
const comparisonHtml=vm.runInContext('storageExperimentSummary(comparisonRun)',context);
assert.match(comparisonHtml,/data-compare-storage="sample"/);
assert.match(comparisonHtml,/Samsung SATA/);assert.match(comparisonHtml,/NVMe/);
assert.match(comparisonHtml,/2.00/);
const csv=vm.runInContext('storageComparisonCsv(comparisonRun)',context);
assert.equal(csv.split('\r\n').length,3);assert.match(csv,/Samsung SATA/);assert.match(csv,/NVMe/);
assert.doesNotMatch(csv,/other/);
vm.runInContext('comparisonRun.storage_comparison.baseline_results=[{...comparisonRun.results[0]}];comparisonRun.results[0].wall_s=123',context);
const frozenCsv=vm.runInContext('storageComparisonCsv(comparisonRun)',context);
assert.doesNotMatch(frozenCsv,/123/);assert.match(frozenCsv,/"10"/);
context.comparisonRun.storage_comparison.alternate_results[0].requests = [
  {status:'ok',prompt_tokens:513,completion_tokens:128,ttft_s:1,decode_tps:64},
  {status:'ok',prompt_tokens:514,completion_tokens:130,ttft_s:2,decode_tps:65}];
const concurrentCsv=vm.runInContext('storageComparisonCsv(comparisonRun)',context);
assert.match(concurrentCsv,/request_metrics_json/);
assert.match(concurrentCsv,/""prompt_tokens"":513/);
assert.match(concurrentCsv,/""prompt_tokens"":514/);
assert.match(concurrentCsv,/""decode_tps"":65/);

// Paired metric summaries match repeat IDs, exclude failures, and use actual query metrics.
context.metricRun={id:'metrics',storage_comparison:{protocol:'wattever-storage-comparison-v1',
 baseline_results:[{model:'m',concurrency:2,target_tokens:80000,repeat:0,status:'ok',wall_s:100,requests:[{ttft_s:10,decode_tps:20},{ttft_s:30,decode_tps:40}]},
  {model:'m',concurrency:2,target_tokens:80000,repeat:1,status:'failed',wall_s:600}],
 alternate_results:[{model:'m',concurrency:2,target_tokens:80000,repeat:0,status:'ok',wall_s:50,requests:[{ttft_s:5,decode_tps:40},{ttft_s:15,decode_tps:80}]},
  {model:'m',concurrency:2,target_tokens:80000,repeat:1,status:'ok',wall_s:75,requests:[{ttft_s:100,decode_tps:1}]}]}};
const before=JSON.stringify(context.metricRun);
let group=vm.runInContext('storageComparisonGroups(metricRun,"wall_s")[0]',context);
assert.deepEqual([group.primary,group.alternative,group.speedup,group.pairedCount,group.baselinePassed,group.alternativePassed],[100,50,2,1,1,2]);
group=vm.runInContext('storageComparisonGroups(metricRun,"ttft_s")[0]',context);
assert.deepEqual([group.primary,group.alternative,group.speedup],[20,10,2]);
group=vm.runInContext('storageComparisonGroups(metricRun,"decode_tps")[0]',context);
assert.deepEqual([group.primary,group.alternative,group.speedup],[30,60,2]);
assert.equal(JSON.stringify(context.metricRun),before,'Rendering must preserve raw observations');
const direct=vm.runInContext('storageExperimentSummary(metricRun)',context);
assert.match(direct,/^<section/);assert.match(direct,/Compare recorded storage/);
assert.match(direct,/alternative observations/);
context.timeoutRun=JSON.parse(JSON.stringify(context.metricRun));
context.timeoutRun.storage_comparison.baseline_results.forEach(row=>{row.status='failed';row.fail_kind='timeout';row.timeout_budget_s=600;});
const limited=vm.runInContext('storageExperimentSummary(timeoutRun)',context);
assert.match(limited,/No matched successes/);
assert.doesNotMatch(limited,/2.00/);
context.badgeRun={hardware:{gpus:[{name:'NVIDIA GeForce RTX 5060 Ti',memory_bytes:16*1073741824}],system:{memory_bytes:64*1073741824},
 storage:[{model:'Hard disk',rotational:true},{model:'SSD model',interface:'SATA'},{model:'Unknown disk',interface:'SATA'}],
 transfer_bandwidth:{storage_volumes:[{device_model:'Example',bus_type:'NVMe'}]}},
 engine_groups:[{id:'strata',config:{provider:'openai',reported_mtp:'on',reproducibility:{engine:'Strata',context_tokens:98304},repeats:2}}],results:[]};
const machine=vm.runInContext('hardwareBadgesMarkup(badgeRun)+engineBadgesMarkup(badgeRun)',context);
for(const label of ['RTX 5060 Ti','16 GiB VRAM','64 GiB RAM','HDD','SSD','NVMe','Strata','MTP on · reported','2 repeats'])assert(machine.includes(label),label);
assert.doesNotMatch(machine,/MTP on · verified/);
context.badgeRun.results=[{engine_group_id:'strata',model_configuration:{mtp:{effective:'on'}}},{engine_group_id:'strata',model_configuration:{mtp:{effective:'unknown'}}}];
assert.doesNotMatch(vm.runInContext('engineBadgesMarkup(badgeRun)',context),/verified<\/span>/,'Mixed known and unknown states must not claim verified MTP');
context.badgeRun.config={provider:'openai',endpoint_inference_local:false};
assert.doesNotMatch(vm.runInContext('hardwareBadgesMarkup(badgeRun)',context),/RTX 5060 Ti/,'Remote inference must not inherit client hardware');
console.log('Storage filters, paired metrics, raw repeat evidence and hardware/MTP badges passed');
