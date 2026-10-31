const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const metadata=require('../web/model-metadata.js');
const source=fs.readFileSync('web/app.js','utf8').replace(/\ninit\(\);\s*$/,'');

function fixture(provider='openai'){
 const nodes=new Map();
 const node=selector=>{
  if(!nodes.has(selector))nodes.set(selector,{value:'',checked:false,disabled:false,open:false,innerHTML:'',textContent:'',dataset:{},showModal(){this.open=true;},close(){this.open=false;},addEventListener(){}});
  return nodes.get(selector);
 };
 node('#helper-provider').value=provider;
 node('#helper-url').value='http://127.0.0.1:8086/v1';
 node('#helper-key-env').value='CURRENT_ENDPOINT_API_KEY';
 node('#helper-local').checked=true;
 const effects={renders:0,plans:0,panels:[],forms:0,previews:0};
 const context={window:{WATTEVER_STATIC:false,WatteverModelMetadata:metadata},Charts:{fmtNum:String},document:{querySelector:node,querySelectorAll:()=>[]},console,effects};
 vm.createContext(context);vm.runInContext(source,context);
 vm.runInContext(`
  state.env={models:[{id:'native/stale-model',type:'llm'}],default_models:['native/stale-model'],capabilities:{extend:true},lms:{server:true}};
  state.models=new Set(['native/stale-model']);state.providerNeedsConnection=false;
  state.detectedEndpoint={id:'current-route',label:'Strata',source:'detected'};
  state.endpointSignature=helperConnectionSignature();
  state.selected='historical';
  state.runCache.historical={id:'historical',config:{provider:'lmstudio',models:['native/stale-model'],mtp_modes:['off']},results:[{model:'native/stale-model',status:'ok'}]};
  renderModels=()=>effects.renders++;updatePlan=()=>effects.plans++;showPanel=panel=>effects.panels.push(panel);
 `,context);
 return{context,node,effects,evaluate:expression=>vm.runInContext(expression,context)};
}

(async()=>{
 const current=fixture();
 const calls=[];
 current.context.transport=async(path,options)=>{
  calls.push({path,options});
  assert.equal(path,'/api/endpoint/check','A connected generic endpoint must never query native LM Studio catalogs');
  return{ok:true,endpoint_identity:{label:'Strata'},models:[{id:'served/qwen-flash',type:'llm',quantization:'IQ3_S',max_context_length:32768,loaded_context_length:16384},{id:'embedder',type:'embeddings'}]};
 };
 current.evaluate('api=transport');
 const historical=current.evaluate('JSON.stringify(state.runCache.historical)');
 current.node('#extend-run').textContent='Add / rerun tests';
 current.evaluate('updateExtendAvailability()');
 assert.match(current.node('#extend-run').textContent,/New benchmark/,'Cached native Results actions must adapt to the current provider');
 await current.evaluate('openExtendDialog(state.runCache.historical)');
 assert.equal(calls.length,1);
 assert.equal(calls[0].options.method,'POST');
 const request=JSON.parse(calls[0].options.body);
 assert.equal(request.endpoint_base_url,'http://127.0.0.1:8086/v1');
 assert.equal(request.endpoint_api_key_env,'CURRENT_ENDPOINT_API_KEY');
 assert.equal(request.provider,'openai');
 assert.equal(current.evaluate('state.extension.mode'),'new');
 assert.equal(current.evaluate('state.extension.models.map(model=>model.id).join()'),'served/qwen-flash');
 assert(!current.node('#extend-content').innerHTML.includes('native/stale-model'));
 assert.match(current.node('#extend-content').innerHTML,/IQ3_S/);
 assert.match(current.node('#extend-content').innerHTML,/ctx 32\.768k/);
 assert.match(current.node('#extend-content').innerHTML,/loaded ctx 16\.384k/);
 assert.match(current.node('#extend-start').textContent,/Review new benchmark/);
 await current.evaluate('startExtension()');
 assert.equal(calls.length,1,'Reviewing a new benchmark must not start inference or invoke native extension');
 assert.equal(current.evaluate('[...state.models].join()'),'served/qwen-flash');
 assert.equal(current.effects.panels.join(),'run');
 assert.equal(current.effects.renders,1);
 assert.equal(current.evaluate('JSON.stringify(state.runCache.historical)'),historical);
 assert.equal(current.evaluate('allModelsOf([state.runCache.historical]).join()'),'native/stale-model','Historical Results filters must remain historical');

 const native=fixture('lmstudio'), nativeCalls=[];
 native.context.transport=async path=>{
  nativeCalls.push(path);
  if(path.startsWith('/api/bench/extend-plan?'))return{models:[{id:'native/stale-model',type:'llm'},{id:'native/new-model',type:'llm'}],sizes:[512],concurrency_levels:[1]};
  if(path==='/api/runs/historical')return native.evaluate('state.runCache.historical');
  throw Error('Unexpected native request '+path);
 };
 native.evaluate('api=transport;renderExtendForm=()=>effects.forms++;previewExtension=()=>effects.previews++');
 await native.evaluate('openExtendDialog(state.runCache.historical)');
 assert.equal(nativeCalls.sort().join(),'\/api/bench/extend-plan?run_id=historical,\/api/runs/historical');
 assert.equal(native.evaluate('state.extension.models.map(model=>model.id).join()'),'native/stale-model,native/new-model');
 assert.equal(native.effects.forms,1);assert.equal(native.effects.previews,1);

 native.evaluate(`
  state.compare=new Set(['historical']);
  state.visibleModels=new Set(['native/stale-model']);
  state.runCache.historical.config.models.push('native/new-model');
  state.runCache.historical.results.push({model:'native/new-model',status:'ok'});
  state.runCache.historical.config.model_modes={'native/stale-model':['off','on'],'native/new-model':['off']};
 `);
 const savedNative=native.evaluate('JSON.stringify(state.runCache.historical)');
 native.evaluate('updateModelRerunAvailability()');
 assert.equal(native.node('#rerun-model').disabled,false);
 assert.equal(native.node('#rerun-model').hidden,false);
 await native.evaluate('openExtendDialog(state.runCache.historical, selectedModelRerun().model)');
 assert.equal(native.node('#extend-title').textContent,'Rerun selected model');
 assert.equal(native.evaluate('JSON.stringify(extendBody(state.extension))'),JSON.stringify({
  run_id:'historical',model_modes:{'native/stale-model':['off','on']},repeats:1,per_test_timeout_s:1800,fallback_on_failure:true,skip_after_timeout:false,sizes:[512],concurrency_levels:[1],retry_failed:true,rerun_existing:true,
 }),'Single-model reruns select only that model and its saved techniques');
 assert.equal(native.evaluate('JSON.stringify(state.runCache.historical)'),savedNative,'Previewing a rerun preserves the source');
 native.evaluate('state.benchmarkRunning=true;updateModelRerunAvailability()');
 assert.equal(native.node('#rerun-model').disabled,true,'Reruns cannot compete with the active benchmark');
 native.evaluate('state.benchmarkRunning=false;state.visibleModels=null;updateModelRerunAvailability()');
 assert.equal(native.node('#rerun-model').disabled,true,'A rerun needs exactly one selected model');
 native.evaluate("state.visibleModels=new Set(['native/stale-model']);state.compare.add('other');updateModelRerunAvailability()");
 assert.equal(native.node('#rerun-model').disabled,true,'A rerun needs exactly one selected benchmark');
 native.evaluate("state.compare.delete('other')");
 await native.evaluate("openExtendDialog(state.runCache.historical,'uninstalled/model')");
 assert.match(native.node('#extend-content').innerHTML,/no longer installed/);
 assert.equal(native.node('#extend-start').disabled,true);
 assert(nativeCalls.every(path=>path.startsWith('/api/bench/extend-plan?') || path==='/api/runs/historical'),'Review never starts inference');

 const parked=fixture('lmstudio');let parkedCalls=0;
 parked.context.transport=async()=>{parkedCalls++;throw Error('Native extension must not be reached after switching providers');};
 parked.evaluate('api=transport;state.extension={source:state.runCache.historical,connectionKey:resultsConnectionKey(),modelModes:{"native/stale-model":["off"]},revision:0}');
 parked.node('#helper-provider').value='openai';
 parked.evaluate('state.endpointSignature=helperConnectionSignature()');
 await parked.evaluate('previewExtension()');await parked.evaluate('startExtension()');
 assert.equal(parkedCalls,0,'A stale native dialog cannot preview or execute against LM Studio after switching to a generic endpoint');
 assert.match(parked.node('#extend-preview').innerHTML,/Endpoint changed/);

 const stale=fixture();
 let resolve;
 stale.context.transport=()=>new Promise(done=>{resolve=done;});
 stale.evaluate('api=transport');
 const pending=stale.evaluate('openExtendDialog(state.runCache.historical)');
 stale.node('#helper-url').value='http://127.0.0.1:9999/v1';
 resolve({models:[{id:'served/old-connection',type:'llm'}]});await pending;
 assert.match(stale.node('#extend-content').innerHTML,/connection changed/i);
 assert(!stale.node('#extend-content').innerHTML.includes('served/old-connection'));
 assert.equal(stale.node('#extend-start').disabled,true);

 const submit=fixture();let submitCalls=0;
 submit.context.transport=async()=>{submitCalls++;return{models:[{id:'served/qwen',type:'llm'}]};};
 submit.evaluate('api=transport');await submit.evaluate('openExtendDialog(state.runCache.historical)');
 submit.node('#helper-key-env').value='DIFFERENT_ENDPOINT_KEY';
 await submit.evaluate('startExtension()');
 assert.match(submit.node('#extend-preview').innerHTML,/Endpoint changed/);
 assert.equal(submitCalls,1);assert.equal(submit.effects.panels.length,0);

 const failure=fixture();failure.context.transport=async()=>{throw Error('Current endpoint unavailable');};
 failure.evaluate('api=transport');await failure.evaluate('openExtendDialog(state.runCache.historical)');
 assert.match(failure.node('#extend-content').innerHTML,/Current endpoint unavailable/);
 assert(!failure.node('#extend-content').innerHTML.includes('native/stale-model'));
 console.log('Results catalogs follow the current endpoint, preserve metadata/history, reject stale connections and retain managed extension behavior');
})().catch(error=>{console.error(error);process.exitCode=1;});
