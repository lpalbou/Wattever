const assert = require('node:assert/strict');
const explorer = require('../web/concurrency.js');
const row = {
  status:'ok', concurrency:2, wall_s:10, aggregate_completion_tokens:999,
  requests:[{status:'ok',completion_tokens:100},{status:'ok',completion_tokens:100}],
  group_power:{scope:'all_cards',low_confidence:false,gpu_power_complete:true,
    gpu_energy_j:200,gpu_sampled_coverage_fraction:1},
};
const measured = explorer.measurements(row);
assert.equal(measured.output, 20);
assert.equal(measured.decode, null, 'Missing decode timing and scoped counts cannot be inferred from whole-case output');
assert.ok(measured.energy === 1 || measured.energy === null,
  'Energy must use the same 200 included output tokens as throughput or be unavailable');
console.log('Energy normalization uses consistent request counts');
