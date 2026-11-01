const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const {TextDecoder, TextEncoder} = require('util');
const storage = new Map();
let calls = [], responseFn;
const window = {WATTEVER_STATIC:true, localStorage:{getItem:k=>storage.get(k) || null, setItem:(k,v)=>storage.set(k,v)}, crypto:require('crypto').webcrypto};
const ctx = {window, URL, document:{baseURI:'https://example.test/site/'}, fetch:(...args)=>{calls.push(args); return responseFn(...args)}, performance, TextDecoder, TextEncoder, Uint8Array, AbortController, setTimeout, clearTimeout, console};
vm.runInNewContext(fs.readFileSync('web/model-metadata.js','utf8'),ctx);
vm.runInNewContext(fs.readFileSync('web/browser.js','utf8'),ctx);
const adapter=window.WatteverBrowser;
const modelResponse=()=>({ok:true,json:async()=>({data:[{id:'test-model',quantization:'Q4_K_M',meta:{n_ctx:32768}},
 {id:'qwen3.8-flash-next-unsloth-ud-iq4_xs'}, {id:'qwen3.8-flash-next-iq3_s'}, {id:'qwen3.6-35b-a3b-ud-q4_k_xl'}]})});
(async()=>{
 assert.strictEqual(adapter.enabled,true);
 assert.strictEqual(calls.length,0);
 assert.strictEqual((await adapter.request('/api/env')).models.length,0);
 assert.throws(()=>adapter.configure({endpoint:'http://user:password@localhost/v1'}));
 adapter.configure({endpoint:'http://localhost:1234/',apiKey:'secret',hardware:{gpu:'GPU'}});
 responseFn=modelResponse;
 await adapter.connect();
 assert.strictEqual(calls[0][0], 'http://localhost:1234/v1/models');
 assert.strictEqual(calls[0][1].headers.Authorization, 'Bearer secret');
 const connectedModels=(await adapter.request('/api/env')).models;
 assert.strictEqual(connectedModels[0].quantization,'Q4_K_M');
 assert.strictEqual(connectedModels[0].quantization_source,'endpoint_reported');
 assert.strictEqual(connectedModels[0].loaded_context_length,32768);
 assert.deepStrictEqual(Array.from(connectedModels.slice(1),m=>m.quantization),['UD-IQ4_XS','IQ3_S','UD-Q4_K_XL']);
 assert(connectedModels.slice(1).every(m=>m.quantization_source==='model_identifier'));
 const a=adapter.importRun({id:'test',results:[]});
 const b=adapter.importRun({id:'test',results:[]});
 assert.notStrictEqual(a.id,b.id);
 const outcomes=adapter.importRun({id:'outcomes',results:['ok','ok','failed','skipped','cancelled'].map(status=>({model:'m',target_tokens:512,status}))});
 const outcomesSummary=(await adapter.request('/api/runs')).runs.find(run=>run.id===outcomes.id);
 assert.deepStrictEqual(['n_results','n_ok','n_failed','n_skipped','n_cancelled'].map(key=>outcomesSummary[key]),[5,2,1,1,1]);
 assert.strictEqual((await adapter.request('/api/import',{method:'POST',body:JSON.stringify({runs:[{id:'other',results:[]}]})})).runs.length,1);
 assert(![...storage.values()].join('').includes('secret'));
 const before=storage.get('wattever.browser.runs.v1');
 assert.throws(()=>adapter.importRun({id:'bad',results:[null]}), /Invalid benchmark/);
 assert.throws(()=>adapter.importRun({id:'bad',gpu:[],results:[]}), /Invalid benchmark/);
 assert.strictEqual(storage.get('wattever.browser.runs.v1'),before);
 await assert.rejects(adapter.request('/api/import',{method:'POST',body:JSON.stringify({runs:[{id:'valid',results:[]},{id:'bad',results:[null]}]})}), /Invalid benchmark/);
 assert.strictEqual(storage.get('wattever.browser.runs.v1'),before);
 responseFn=async (url)=> {
  if (String(url).endsWith('/corpus.md')) return {ok:true,text:async()=> 'corpus text '};
  const encoder = new TextEncoder(), data=encoder.encode('data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}\n\ndata: {"choices":[{"delta":{"content":" world"},"finish_reason":"stop"}],"usage":{"prompt_tokens":12,"completion_tokens":3}}\n\ndata: [DONE]\n\n');
  let index=0; return {ok:true,body:{getReader:()=>({read:async()=> index>=data.length ? {done:true} : {done:false,value:data.slice(index,index+=7)}})}};
 };
 const started=await adapter.request('/api/bench/start',{method:'POST',body:JSON.stringify({models:['test-model'],sizes:[2],concurrency_levels:[1],repeats:1,max_tokens:3,per_test_timeout_s:5,mtp_modes:['off'],reproducibility:{engine:'Strata',context_tokens:32768},performance_claims:[{precision:'INT4',value:100,kind:'advertised_peak',sparsity:'dense',execution_path:'Tensor cores',source:'https://www.nvidia.com/en-us/geforce/'}]})});
 for (let n=0;n<100 && (await adapter.request('/api/status')).running;n++) await new Promise(resolve=>setTimeout(resolve,1));
 const run=await adapter.request('/api/runs/'+started.run_id);
 assert.strictEqual(run.status,'completed'); assert.strictEqual(run.results[0].prompt_tokens,12); assert.strictEqual(run.results[0].completion_tokens,3); assert.strictEqual(run.results[0].completion_tokens_estimated,false); assert.strictEqual(run.measurement_mode,'browser'); assert.strictEqual(run.results[0].mtp,null); assert.strictEqual(run.corpus.sha256.length,64); assert(run.results[0].aggregate_completion_tps > 0);
 assert(!JSON.stringify(run).includes('secret'));
 assert.strictEqual(run.reproducibility.source,'user_reported');
 assert.strictEqual(run.reproducibility.settings.context_tokens,32768);
 assert.strictEqual(run.performance_claims[0].unit,'TOPS');
 assert.strictEqual((await adapter.request('/api/runs/'+run.id+'/download')).performance_claims[0].source,'https://www.nvidia.com/en-us/geforce/');
 // Imported extras are untrusted: every public JSON/CSV download is projected.
 const imported=adapter.importRun({id:'privacy-run',results:[{model:'C:\\private-model\\weights.gguf',target_tokens:512,status:'ok',decode_tps:28.1}],
   config:{models:['unsloth/Qwen3.6-35B-A3B'],OPENAI_API_KEY:'private-key',AWS_SESSION_TOKEN:'private-token'},
   extras:[{apiKey:'private-api',Authorization:'Bearer private-auth',hostname:'private-host',gpu_uuid:'private-uuid',serial_number:'private-serial',user:'private-user'}],
   paths:['/srv/private/weights.gguf','https://private-host/v1','10.20.30.40','2001:db8::5','private@example.org']});
 const exported=await adapter.request('/api/runs/'+imported.id+'/download');
 assert(!JSON.stringify(exported).includes('private'));
 assert.strictEqual(exported.id,imported.id);
 assert.strictEqual(exported.config.models[0],'unsloth/Qwen3.6-35B-A3B');
 assert.strictEqual(exported.results[0].decode_tps,28.1);
 const bundle=await adapter.request('/api/export.json?ids='+imported.id);
 assert(!JSON.stringify(bundle).includes('private'));
 const csv=await adapter.request('/api/export.csv?ids='+imported.id);
 assert(!csv.includes('private')); assert(csv.includes('28.1'));
 // Export leaves the private local copy intact for local inspection.
 assert.strictEqual((await adapter.request('/api/runs/'+imported.id)).config.OPENAI_API_KEY,'private-key');
 // A newly loaded adapter marks stale running entries interrupted.
 storage.set('wattever.browser.runs.v1', JSON.stringify([{id:'stale',status:'running',results:[]},{id:'bad',results:[null]}]));
 const freshWindow={WATTEVER_STATIC:true,localStorage:window.localStorage,crypto:window.crypto};
 vm.runInNewContext(fs.readFileSync('web/browser.js','utf8'),{...ctx,window:freshWindow});
 const fresh=freshWindow.WatteverBrowser;
 assert.strictEqual((await fresh.request('/api/runs')).runs[0].status,'interrupted');
 assert.strictEqual((await fresh.request('/api/runs')).runs.length,1);
 await fresh.request('/api/runs/stale',{method:'DELETE'});
 // Quota failure retains exportable results and reports a warning.
 const quotaWindow={WATTEVER_STATIC:true,crypto:window.crypto,localStorage:{getItem:()=>null,setItem:()=>{throw new Error('quota')}}};
 vm.runInNewContext(fs.readFileSync('web/browser.js','utf8'),{...ctx,window:quotaWindow});
 quotaWindow.WatteverBrowser.importRun({id:'memory-only',results:[]});
 const mem=await quotaWindow.WatteverBrowser.request('/api/runs');
 assert.strictEqual(mem.runs[0].id,'memory-only'); assert(mem.storage_error);
 assert.strictEqual((await quotaWindow.WatteverBrowser.request('/api/export.json')).runs.length,1);
 // Authentication failure stops the session before subsequent cases.
 let inferenceCalls=0;
 responseFn=async url => String(url).endsWith('/corpus.md') ? {ok:true,text:async()=> 'corpus'} : (inferenceCalls++, {ok:false,status:401});
 const denied=await adapter.request('/api/bench/start',{method:'POST',body:JSON.stringify({models:['test-model'],sizes:[2,4],concurrency_levels:[1],repeats:1,max_tokens:3,per_test_timeout_s:5,mtp_modes:['off']})});
 for (let n=0;n<100 && (await adapter.request('/api/status')).running;n++) await new Promise(resolve=>setTimeout(resolve,1));
 const deniedRun=await adapter.request('/api/runs/'+denied.run_id);
 assert.strictEqual(inferenceCalls,1); assert.strictEqual(deniedRun.status,'error'); assert.strictEqual(deniedRun.results[0].failure,'authentication');
 responseFn=async()=>{throw new TypeError('blocked')};
 await assert.rejects(adapter.connect(),/CORS/);
 console.log('browser backend tests passed');
})().catch(e=>{console.error(e);process.exitCode=1});
