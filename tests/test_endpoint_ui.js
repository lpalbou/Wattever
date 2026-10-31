const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/app.js', 'utf8').replace(/\ninit\(\);\s*$/, '');
const elements = {
  '#helper-provider': {value:'openai'}, '#helper-url': {value:'http://localhost:9000/v1'},
  '#helper-key-env': {value:'PRIVATE_KEY_ENV'}, '#helper-name-override':{value:''}, '#helper-local': {checked:false}, '#helper-reasoning':{value:'none'},
  '#meta-engine': {value:'Strata'}, '#meta-context': {value:'32768'}, '#meta-mtp': {value:'4'},
  '#max-tokens': {value:'128'}, '#repeats': {value:'2'}, '#gpu-index': {value:'0'},
  '#runtime-gpu-placement': {value:''}, '#offload': {value:'max'}, '#fallback': {checked:true},
  '#stop-after-fail': {checked:true}, '#warmup': {checked:false}, '#no-think': {checked:true},
  '#load-timeout': {value:'900'}, '#per-test-timeout': {value:'1800'}, '#headroom': {value:'512'},
  '#run-label': {value:'Test'}, '#run-notes': {value:''}, '#context-policy': {value:'per_test'},
};
const context = {Charts:{fmtNum:String},document:{querySelector:s=>elements[s],querySelectorAll:()=>[]}};
vm.createContext(context); vm.runInContext(source,context);
vm.runInContext(`
  state.env={defaults:{},last_config:{endpoint_label:'Local Strata'}};state.models=new Set(['served-model']);state.sizes=new Set([512]);state.concurrencyLevels=new Set([1]);
  if(helperEndpointConfig().endpoint_label)throw Error('legacy saved label must not become an inferred name');
  rememberEndpoint({endpoint_identity:{id:'detected-fixture',label:'Example engine',source:'detected'}},'openai');
  const cfg=collectConfig();
  if(cfg.provider!=='openai'||cfg.endpoint_base_url!=='http://localhost:9000/v1'||cfg.endpoint_api_key_env!=='PRIVATE_KEY_ENV')throw Error('connection config');
  if(cfg.context_policy!=='provider_managed'||cfg.gpu_offload!=='provider_managed'||cfg.disable_thinking!==null||cfg.fallback_on_failure)throw Error('provider settings');
  if(cfg.reproducibility.engine!=='Strata'||cfg.reproducibility.context_tokens!==32768||cfg.reproducibility.mtp_draft_tokens!==4)throw Error('reported settings');
  if(cfg.endpoint_id!=='detected-fixture'||cfg.endpoint_identity.source!=='detected'||cfg.endpoint_reasoning_effort!=='none')throw Error('automatic identity and requested reasoning');
  const signature=helperConnectionSignature();document.querySelector('#helper-reasoning').value='high';document.querySelector('#helper-name-override').value='Custom display';
  if(helperConnectionSignature()!==signature||collectConfig().endpoint_reasoning_effort!=='high')throw Error('job settings must not invalidate connection');
  if(collectConfig().endpoint_name_override!=='Custom display'||collectConfig().endpoint_id!=='detected-fixture')throw Error('display override must preserve automatic route identity');
  if(generatedMetadata({hardware:{gpus:[{name:'Client GPU'}]}},cfg).label!=='Example engine')throw Error('remote machine attribution');
  const run={id:'generic',label:'Strata',config:cfg,results:[{model:'served-model',target_tokens:512,concurrency:1,status:'ok',mtp:null,decode_tps:10}]};
  state.mtpFilter='all';state.comparisonConcurrency=1;
  // Chart helpers need colors but must never call an unknown MTP state off.
  runColor=()=> '#00aaff'; resolveColor=x=>x;
  if(tracksFor([run]).length!==1||tracksFor([run])[0].name.includes('no MTP'))throw Error('unknown technique label');
  state.mtpFilter='off';if(tracksFor([run]).length)throw Error('unknown technique included in verified-off filter');
  const text=scientificMetadata({provider:{kind:'openai',label:'Strata'},measurement_client:{hardware:{}},reproducibility:{source:'user_reported',settings:{engine:'<unsafe>'}},performance_claims:[{precision:'INT4',value:100,unit:'TOPS',kind:'estimate',sparsity:'unknown',execution_path:'tensor',source:'manual'}]});
  if(text.includes('<unsafe>')||!text.includes('&lt;unsafe&gt;')||!text.includes('unverified')||!text.includes('inference hardware unknown'))throw Error('provenance rendering');
`,context);
assert.equal(vm.runInContext('helperEndpointConfig().endpoint_inference_local',context),false);
elements['#helper-provider'].value='lmstudio';
const native=vm.runInContext('helperEndpointConfig()',context);
assert.equal(native.provider,'lmstudio');assert(!('endpoint_base_url' in native));assert(!('endpoint_api_key_env' in native));assert.equal(native.endpoint_id,'');
console.log('Generic endpoint UI tests passed');
