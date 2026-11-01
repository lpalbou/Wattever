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
