const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const context={window:{},Charts:{fmtNum:String},document:{querySelector(){},querySelectorAll(){return[];}},console};
vm.createContext(context);vm.runInContext(fs.readFileSync('web/app.js','utf8').replace(/\ninit\(\);\s*$/,''),context);
const rows=(model,values)=>values.map((wall_s,repeat)=>({model,target_tokens:80000,concurrency:1,repeat,status:'ok',wall_s,decode_tps:100/wall_s}));
context.runs=[{id:'lm',label:'LM Studio',config:{provider:'lmstudio'},results:rows('other',[10,11])},
 {id:'ssd',label:'Samsung SSD',storage_condition:'SSD',config:{provider:'openai'},results:rows('flash',[115,116])},
 {id:'nvme',label:'NVMe',storage_condition:'NVMe',config:{provider:'openai'},results:rows('flash',[113,114])}];
vm.runInContext('resolveColor=x=>x;state.visibleModels=new Set(["flash"]);state.comparisonConcurrency=1;state.metrics=new Set(["wall_s"]);',context);
assert.equal(vm.runInContext('conditionRows(runs[0]).length',context),0);
assert.equal(vm.runInContext('conditionRows(runs[1]).length',context),2);
assert.equal(vm.runInContext('chartConditions(runs,["flash"],["wall_s"]).length',context),2);
let html=vm.runInContext('comparisonReadout(chartConditions(runs,["flash"],["wall_s"]),["flash"],"wall_s")',context);
assert.match(html,/115.50 s/);assert.match(html,/113.50 s/);assert.match(html,/-1.7% time/);assert.match(html,/2 matched/);assert.doesNotMatch(html,/LM Studio/);
context.unequal=JSON.parse(JSON.stringify(context.runs));
context.unequal[1].results[0].wall_s=100;context.unequal[1].results[1].wall_s=1000;
context.unequal[2].results=[{...context.unequal[2].results[0],wall_s:50}];
const unequal=vm.runInContext('comparisonReadout(chartConditions(unequal,["flash"],["wall_s"]),["flash"],"wall_s")',context);
assert.match(unequal,/550.00 s/);assert.match(unequal,/50.00 s/);assert.match(unequal,/-50.0% time/);
assert.match(unequal,/matched only/);assert.match(unequal,/Matched: 100.00 s/);
context.runs[1].results.forEach(row=>{row.status='failed';row.fail_kind='timeout';row.timeout_budget_s=600;});
html=vm.runInContext('comparisonReadout(chartConditions(runs,["flash"],["wall_s"]),["flash"],"wall_s")',context);
assert.match(html,/Timeout/);assert.match(html,/600 s limit/);assert.match(html,/unpaired/);assert.doesNotMatch(html,/metric-delta/);
assert.equal(vm.runInContext('chartConditions(runs,["flash"],["wall_s"]).length',context),2,'Failure-only recorded conditions remain represented');
vm.runInContext('state.mtpFilter="off"',context);
assert.equal(vm.runInContext('conditionRows(runs[1]).length',context),0,'External reported MTP must not be relabeled as verified off');
console.log('Model-scoped conditions, overlapping-curve readouts, repeat matching and failure evidence passed');
