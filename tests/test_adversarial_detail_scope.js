const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {WatteverPhaseMetrics:require('../web/phase-metrics.js'),Charts:{fmtNum:String},document:{querySelector:()=>null,querySelectorAll:()=>[]}};
vm.createContext(context);
vm.runInContext(fs.readFileSync('web/app.js','utf8').replace(/\ninit\(\);\s*$/, ''),context);
vm.runInContext(`
  browserMode=()=>true; tableRowColor=()=>''; modelUniverse=()=>[]; sessionLabel=()=>'';
  const run={id:'r',results:[{model:'m',status:'ok',concurrency:2,target_tokens:512,
    group_power:{gpus:[{index:0,name:'GPU',gpu_util_avg:80,clock_mhz_avg:1000}]}}]};
  globalThis.rendered=detailedResultsTable(run);
`,context);
assert.doesNotMatch(context.rendered,/decode utilization \(%\)/i,'Whole-case utilization is not decode utilization');
assert.doesNotMatch(context.rendered,/decode clock \(MHz\)/i,'Whole-case clock is not decode clock');
vm.runInContext(`
  const estimatedRun={id:'estimated',results:[{model:'m',status:'ok',concurrency:2,target_tokens:512,wall_s:10,
    aggregate_completion_tokens_estimated:true,requests:[
      {status:'ok',completion_tokens:100,completion_tokens_estimated:true},
      {status:'ok',completion_tokens:100,completion_tokens_estimated:false}]}]};
  globalThis.estimatedHtml=detailedResultsTable(estimatedRun);
`,context);
assert.match(context.estimatedHtml, /<td>End-to-end output \(tk\/s\)<\/td><td class="measure-cell">20\*<\/td>/,
  'A group output rate derived from estimated request counts must be marked');
console.log('Details distinguish whole-case and decode-only telemetry');
