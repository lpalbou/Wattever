const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const context = {window: {}, Charts: {fmtNum: String}, document: {querySelector() {}, querySelectorAll() { return []; }}, console};
vm.createContext(context);
vm.runInContext(fs.readFileSync('web/app.js', 'utf8').replace(/\ninit\(\);\s*$/, ''), context);
assert.match(vm.runInContext('transferBandwidthMarkup(null)', context), /not measured/);
context.profile = {captured_at: '2026-10-31', provenance: 'measured_later_on_matching_hardware',
  storage_to_ram: {status: 'ok', bytes_per_second: 537000000, samples: [{}, {}, {}], volume: 'C:'},
  ram_to_vram: {status: 'ok', bytes_per_second: 13540000000, samples: [{}, {}, {}], host_memory: 'pinned'}};
let html = vm.runInContext('transferBandwidthMarkup(profile)', context);
assert.match(html, /0.54 GB\/s \(537 MB\/s\)/);
assert.match(html, /13.54 GB\/s/);
assert.match(html, /Measured after this benchmark/);
assert.match(html, /3 storage samples and 3 GPU samples/);
context.profile.ram_to_vram = {status: 'unavailable', reason: '<driver unavailable>'};
html = vm.runInContext('transferBandwidthMarkup(profile)', context);
assert.match(html, /Unavailable: &lt;driver unavailable&gt;/);
assert.doesNotMatch(html, /13.54/);
context.profile.ram_copy_parallel={status:'ok',bytes_per_second:12000000000,workers:6,note:'Copied payload bytes'};
context.profile.storage_volumes=[{status:'ok',bytes_per_second:1800000000,volume:'F:',device_model:'NVMe SSD'}];
html=vm.runInContext('transferBandwidthMarkup(profile)',context);
assert.match(html,/12.00 GB\/s/);assert.match(html,/6 workers/);
assert.match(html,/1.80 GB\/s/);assert.match(html,/volume F:/);assert.match(html,/NVMe SSD/);
console.log('Transfer bandwidth labels, units, unavailable state and historical provenance passed');
context.taggedRun = {tags: ['RTX 5070 Ti', 'Strata', '<script>'], config: {}};
const taggedHtml = vm.runInContext('scientificMetadata(taggedRun)', context);
assert.match(taggedHtml, /Tags:<\/b> RTX 5070 Ti · Strata · &lt;script&gt;/);
assert.doesNotMatch(taggedHtml, /<script>/);
context.comparisonRun={id:'sample',results:[{model:'m',target_tokens:512,concurrency:1,repeat:0,wall_s:10,status:'ok'},
  {model:'other',target_tokens:512,wall_s:99,status:'ok'}],storage_comparison:{protocol:'wattever-storage-comparison-v1',
  baseline_label:'Samsung SATA',alternative_label:'NVMe',cache_policy:'OS cache uncontrolled',rows:[
    {model:'m<script>',quantization:'IQ3_S',target_tokens:512,concurrency:1,paired_successes:2,baseline_wall_s:10,alternative_wall_s:5,wall_speedup:2}],
  alternate_results:[{model:'m',target_tokens:512,concurrency:1,repeat:0,wall_s:5,status:'ok'}]}};
const comparisonHtml=vm.runInContext('storageComparisonMarkup(comparisonRun)',context);
assert.match(comparisonHtml,/2.00×/);assert.match(comparisonHtml,/OS cache uncontrolled/);
assert.match(comparisonHtml,/m&lt;script&gt;/);assert.doesNotMatch(comparisonHtml,/<script>/);
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
