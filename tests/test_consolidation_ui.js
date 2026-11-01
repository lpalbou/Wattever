const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const context={window:{},Charts:{fmtNum:String},document:{querySelector(){},querySelectorAll(){return[];}},console};
vm.createContext(context);
vm.runInContext(fs.readFileSync('web/app.js','utf8').replace(/\ninit\(\);\s*$/,''),context);
context.run={id:'gpu',label:'RTX 5070 Ti',config:{provider:'mixed'},
 engine_groups:[{id:'lm',label:'LM Studio',config:{provider:'lmstudio',models:['gemma']}},
                {id:'strata',label:'Strata',config:{provider:'openai',models:['qwen']}}],
 sessions:[{id:'s1',engine_group_id:'lm'},{id:'s2',engine_group_id:'strata'}],
 results:[{model:'gemma',session_id:'s1',mtp:false,status:'ok',concurrency:1},
          {model:'qwen',session_id:'s2',mtp:null,status:'ok',concurrency:1}]};
const views=vm.runInContext('measurementRuns([run])',context);
assert.equal(views.length,2);assert.equal(views[0].results.length,1);assert.equal(views[1].results.length,1);
assert.equal(views[1].config.provider,'openai');
assert.equal(vm.runInContext('rowProviderManaged(run,run.results[1])',context),true);
assert.equal(vm.runInContext('rowProviderManaged(run,run.results[0])',context),false);
assert.equal(vm.runInContext('managedRunConfig(run).models[0]',context),'gemma');
vm.runInContext('state.comparisonConcurrency=1;state.mtpFilter="all";resolveColor=x=>x;',context);
assert.equal(vm.runInContext('tracksFor([run]).length',context),2);
vm.runInContext('state.mtpFilter="off";',context);
assert.equal(vm.runInContext('tracksFor([run]).length',context),1);
assert.match(vm.runInContext('scientificMetadata(run)',context),/Engines in this benchmark/);
console.log('Consolidated engine attribution, charts, MTP filtering and repair scope passed');
