const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {Charts:{fmtNum:String}, WatteverConcurrency:require('../web/concurrency.js'), document:{querySelector:()=>null,querySelectorAll:()=>[]}};
vm.createContext(context);
vm.runInContext(fs.readFileSync('web/app.js','utf8').replace(/\ninit\(\);\s*$/, ''),context);
const actual = vm.runInContext(`(() => {
  state.comparisonConcurrency=2;
  const repeat = tokens => ({model:'m',target_tokens:100,concurrency:2,status:'ok',wall_s:10,
    requests:[{status:'ok',completion_tokens:tokens},{status:'ok',completion_tokens:tokens}]});
  const run={results:[repeat(10),repeat(20)]};
  const point=pick(run,'m',100,false,METRICS.group_output_tps);
  return {value:METRICS.group_output_tps.get(point), repeats:point.summary_repeats};
})()`,context);
assert.equal(actual.repeats,2);
assert.equal(actual.value,3,'The chart and table must show the median of 2 and 4, not one representative repeat');
const prefill = vm.runInContext(`(() => {
  const repeat = tokens => ({model:'m',target_tokens:100,concurrency:2,status:'ok',wall_s:10,
    requests:[{status:'ok',prompt_tokens:tokens,ttft_s:2,start_offset_s:0},
              {status:'ok',prompt_tokens:tokens,ttft_s:2,start_offset_s:0}]});
  const run={results:[repeat(10),repeat(20)]};
  return METRICS.prefill_tps.get(pick(run,'m',100,false,METRICS.prefill_tps));
})()`,context);
assert.equal(prefill,15,'The prefill chart must show the median of 10 and 20');
console.log('Even-repeat collective median passed');
