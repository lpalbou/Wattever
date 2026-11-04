const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const nodes=new Map();
function node(selector){
 if(!nodes.has(selector)){
  const classes=new Set();
  nodes.set(selector,{value:'',checked:false,hidden:false,disabled:false,style:{},dataset:{},handlers:{},innerHTML:'',textContent:'',
   classList:{add:name=>classes.add(name),remove:name=>classes.delete(name),contains:name=>classes.has(name),toggle(name,on){const enabled=on===undefined?!classes.has(name):on;if(enabled)classes.add(name);else classes.delete(name);return enabled;}},
   querySelectorAll:()=>[],querySelector:child=>node(selector+' '+child),contains:()=>false,setAttribute(){},scrollIntoView(){},addEventListener(type,fn){this.handlers[type]=fn;}});
 }
 return nodes.get(selector);
}
const configuration={provider:'openai',endpoint_id:'forge-strata',endpoint_label:'Forge Strata',endpoint_base_url:'http://localhost:8086/v1',endpoint_reasoning_effort:'none',canonical_model_id:'org/model',reproducibility:{quantization:'IQ3_S'},reported_mtp:'on',reported_reasoning:'off',temperature:null,token_limit_field:'max_completion_tokens'};
const run={id:'cap-fixture',status:'completed',model_configuration:{model:{value:'org/<unsafe>'},endpoint:{id:'forge-strata',label:'Forge Strata'},quantization:{value:'IQ3_S',source:'user_reported'},reasoning:{requested:'none',user_reported:'off'},mtp:{user_reported:'on'}},summary:{percent:75,scored:4},results:[]};
const calls=[];
let advertiseRunner=true;
const window={WatteverUI:{config:()=>configuration,connection:()=>configuration,models:()=>[{id:'served-alias',type:'llm'}],ready:()=>true,browserMode:()=>false,api:async(path)=>{calls.push(path);if(path.endsWith('/suites'))return{runner:advertiseRunner?{completion_policy:'finished-stream-visible-answer-v2'}:undefined,suites:[{id:'wattever-core-v1',name:'Core',task_count:24}]};if(path.endsWith('/runs'))return{runs:[run]};if(path.endsWith('/runs/cap-fixture'))return run;if(path.endsWith('/status'))return{running:false};throw Error('Unexpected request '+path);}}};
const context={window,document:{querySelector:node,querySelectorAll:()=>[]},URL,Blob,setTimeout,clearTimeout,setInterval,clearInterval,console,structuredClone};
vm.createContext(context);
for(const filename of ['web/capability-metrics.js','web/capability-dashboard.js','web/capabilities.js'])vm.runInContext(fs.readFileSync(filename,'utf8'),context,{filename});
const ui=window.WatteverCapabilities;
(async()=>{
 const origin=window.WatteverCapabilityDashboard.suiteOrigin;
 assert.equal(origin({id:'wattever-core-v1'}).kind,'custom');
 assert(origin({source:{kind:'bundled_original'}}).description.includes('not an established benchmark'));
 assert.equal(origin({id:'gsm8k-test-chat-v1'}).kind,'published');
 assert.equal(origin({id:'arc-challenge-test-chat-v1'}).kind,'published');
 assert.equal(origin({id:'mmlu-pro-test-chat-v1'}).kind,'published');
 assert(origin({id:'mmlu-pro-test-chat-v1'}).description.includes('12,032'));
 assert(origin({id:'mmlu-pro-balanced64-chat-v1'}).description.includes('subject weighting differs'));
 assert.equal(window.WatteverCapabilityDashboard.suiteTitle({id:'mmlu-pro-test-chat-v1'}),'MMLU-Pro · test');
 assert.equal(window.WatteverCapabilityDashboard.suiteTitle({id:'mmlu-pro-balanced64-chat-v1'}),'MMLU-Pro · balanced 64');
 assert(origin({id:'gsm8k-test-chat-v1'}).description.includes('chat adaptation'));
 assert.equal(origin({id:'unrecognized-suite'}).kind,'unknown');
 assert.equal(ui.descriptor(run).model,'org/<unsafe>');
 assert.equal(ui.descriptor(run).quant,'IQ3_S');assert.equal(ui.descriptor(run).reasoning,'none');assert.equal(ui.descriptor(run).reportedReason,'off');assert.equal(ui.descriptor(run).mtp,'on');
 assert.equal(ui.unknownConfiguration(run),false);assert.equal(ui.unknownConfiguration({...run,model_configuration:{...run.model_configuration,quantization:{value:'unknown'}}}),true);
 assert.equal(ui.interval({wilson_95:{low:.25,high:.75}}),'25.0–75.0%');assert.equal(ui.percent({accuracy:.5}),50);assert.equal(ui.percent({accuracy:null}),null);
 node('#cap-model').value='served-alias';node('#cap-suite').value='wattever-core-v1';node('#cap-repeats').value='2';node('#cap-max-tokens').value='32768';node('#cap-timeout').value='0';node('#cap-seed').value='0';node('#cap-reasoning').value='none';node('#cap-temperature').value='';node('#cap-token-limit').value='max_completion_tokens';
 let cfg=ui.config();assert.equal(cfg.model,'served-alias');assert.equal(cfg.canonical_model_id,'org/model');assert.equal(cfg.temperature,null);assert.equal(cfg.token_limit_field,'max_completion_tokens');assert.equal(cfg.endpoint_inference_local,false);assert.equal(cfg.top_p,null);assert.equal(cfg.send_seed,false);
 assert.equal(cfg.max_tokens,32768);assert.equal(cfg.timeout_s,0);
 configuration.endpoint_reasoning_effort='high';configuration.temperature=1.5;configuration.token_limit_field='max_tokens';node('#cap-temperature').value='0.2';
 cfg=ui.config();assert.equal(cfg.endpoint_reasoning_effort,'none');assert.equal(cfg.temperature,.2);assert.equal(cfg.token_limit_field,'max_completion_tokens');
 node('#cap-reasoning').value='default';assert.equal(ui.config().endpoint_reasoning_effort,null);node('#cap-reasoning').value='none';node('#cap-temperature').value='';
 node('#cap-local').checked=true;assert.equal(ui.config().endpoint_inference_local,true);
 await ui.activate();
 assert(calls.includes('/api/capabilities/suites'));assert(calls.includes('/api/capabilities/runs'));
 assert(node('#cap-runs').innerHTML.includes('org/&lt;unsafe&gt;'));assert(!node('#cap-runs').innerHTML.includes('org/<unsafe>'));assert(!node('#cap-runs').innerHTML.includes('[object Object]'));
 assert(node('#cap-identity').textContent.includes('Reported MTP: on'));
 assert.equal(node('#cap-start').disabled,false);
 advertiseRunner=false;await ui.activate();
 assert.equal(node('#cap-start').disabled,true);assert.equal(node('#cap-helper-compatibility').hidden,false);assert(node('#cap-helper-compatibility').textContent.includes('Relaunch Wattever'));
 await node('#cap-start').handlers.click();assert(!calls.includes('/api/capabilities/start'));assert(node('#cap-runs').innerHTML.includes('org/&lt;unsafe&gt;'));
 advertiseRunner=true;await ui.activate();assert.equal(node('#cap-start').disabled,false);assert.equal(node('#cap-helper-compatibility').hidden,true);
 const audited={...run,id:'audited-fixture',suite:{tasks:[{id:'math-1',category:'arithmetic',prompt:'Calculate 256 + 256. Reply with only the integer.',scorer:{kind:'exact_text',expected:'512'}}]},protocol:{selected_item_ids:['math-1'],repeats:2},summary:{},results:[{item_id:'math-1',repeat:0,status:'ok',response:'<b>512</b>',scoring:{scored:true,correct:false,reason:'answer_mismatch',rule:'exact_text',expected:'512'}}]};
 const dashboard=window.WatteverCapabilityDashboard.create({container:node('#unit-dashboard'),comparisonContainer:node('#unit-comparison'),descriptor:ui.descriptor,interval:ui.interval,unknownConfiguration:ui.unknownConfiguration,onFocus(){},onClearComparison(){},download(){}});
 dashboard.render(audited);
 const overview=node('#unit-dashboard').innerHTML;
 for(const id of ['cap-metric-accuracy','cap-metric-coverage','cap-metric-issues','cap-category-chart','cap-outcome-chart','cap-repeat-chart','cap-filter-category','cap-filter-outcome','cap-filter-repeat','cap-filter-query'])assert(overview.includes(id),id+' is rendered');
 assert(overview.includes('org/&lt;unsafe&gt;'));assert(!overview.includes('org/<unsafe>'));
 dashboard.selectCell('math-1::0');
 const inspector=node('#unit-dashboard #cap-inspector').innerHTML;
 assert(inspector.includes('Calculate 256 + 256'));assert(inspector.includes('&lt;b&gt;512&lt;/b&gt;'));assert(!inspector.includes('<b>512</b>'));
 assert(inspector.includes('Extra explanation or formatting'));assert(inspector.includes('contains the right result'));
 assert(inspector.includes('<details class="cap-audit-fields">'));assert(!inspector.includes('<details class="cap-audit-fields" open'));
 dashboard.selectCell('math-1::1');
 assert(node('#unit-dashboard #cap-inspector').innerHTML.includes('No observation has been recorded'));
 const limited={...audited,id:'budget-fixture',protocol:{...audited.protocol,max_tokens:256,timeout_s:120},results:[{item_id:'math-1',repeat:0,status:'error',diagnostic:'No visible answer was returned; streamed reasoning is not scored as the answer.',scoring:{scored:false}},{item_id:'math-1',repeat:1,status:'ok',response:'',finish_reason:'length',usage:{completion_tokens:256,reasoning_tokens:256},scoring:{scored:true,correct:false,truncated:true}}]};
 dashboard.render(limited);assert(node('#unit-dashboard').innerHTML.includes('256-token budget'));assert(node('#unit-dashboard').innerHTML.includes('older runner lack finish reasons'));assert(node('#unit-dashboard').innerHTML.includes('Reasoning observed'));
 dashboard.selectCell('math-1::1');assert(node('#unit-dashboard #cap-inspector').innerHTML.includes('budget-limited failure'));
 dashboard.selectCell('math-1::0');assert(node('#unit-dashboard #cap-inspector').innerHTML.includes('cannot distinguish a generation limit'));
 const empty={...audited,id:'empty-final-fixture',results:[{item_id:'math-1',repeat:0,status:'ok',response:'',finish_reason:'stop',scoring:{scored:true,correct:false}}]};
 dashboard.render(empty);dashboard.selectCell('math-1::0');assert(node('#unit-dashboard #cap-inspector').innerHTML.includes('Missing final answer'));assert(node('#unit-dashboard #cap-inspector').innerHTML.includes('request completed'));
 const arc={...audited,id:'arc-subset-fixture',suite_id:'arc-challenge-test-chat-v1',suite:{id:'arc-challenge-test-chat-v1',tasks:Array.from({length:1172},(_,i)=>({id:'arc-'+i,category:'science_reasoning',prompt:'Question '+i,scorer:{kind:'multiple_choice',expected:'A'}}))},protocol:{selected_item_ids:Array.from({length:50},(_,i)=>'arc-'+i),repeats:1},summary:{wilson_95:{low:.7863976856,high:.956524878,n:50,correct:45}},results:[]};
 dashboard.render(arc);const arcOverview=node('#unit-dashboard').innerHTML;
 assert(arcOverview.includes('AI2 ARC-Challenge'));assert(arcOverview.includes('not ARC-AGI grid puzzles'));assert(arcOverview.includes('50 / 1,172 questions'));assert(arcOverview.includes('78.6%'));assert(arcOverview.includes('95.7%'));assert(arcOverview.includes('n=50'));assert(arcOverview.includes('not a full-suite result'));
 console.log('Capability UI configuration, dashboard structure, audit explanation and escaping tests passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
