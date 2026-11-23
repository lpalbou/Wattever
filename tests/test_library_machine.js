// The library describes each benchmark's machine from its recorded inventory,
// says a test count once, and offers one dialog to rename a benchmark.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const nodes=new Map(),listeners=new Map();
const node=id=>{if(!nodes.has(id))nodes.set(id,{id,value:'',hidden:false,disabled:false,open:false,textContent:'',innerHTML:'',dataset:{},
  showModal(){this.open=true;},close(){this.open=false;},select(){this.selected=true;},addEventListener(type,handler){listeners.set(id+':'+type,handler);}});return nodes.get(id);};
const context={window:{},Charts:{fmtNum:String},console,document:{querySelector:node,querySelectorAll:()=>[],addEventListener(){}}};
vm.createContext(context);vm.runInContext(fs.readFileSync('web/app.js','utf8').replace(/\ninit\(\);\s*$/,''),context);
const run=(expression,values={})=>{Object.assign(context,values);return vm.runInContext(expression,context);};
const labels=html=>[...html.matchAll(/<span class="badge[^>]*>([^<]*)<\/span>/g)].map(match=>match[1]);

const GiB=1073741824;
const cards=[{name:'NVIDIA GeForce RTX 5070 Ti',memory_bytes:17094934528,memory_architecture:'discrete',sensor_index:0},
  {name:'Microsoft Remote Display Adapter',memory_bytes:null,sensor_index:null},
  {name:'NVIDIA GeForce RTX 5070 Ti',memory_bytes:17094934528,memory_architecture:'discrete',sensor_index:1}];
const inventory={gpus:cards,cpu:{name:'Intel(R) Core(TM) i7-5960X CPU @ 3.00GHz'},memory:{installed_capacity_bytes:64*GiB},system:{memory_bytes:63.9*GiB}};
const unknown={os:{name:'unknown remote host'},cpu:{name:null},system:{memory_bytes:null},gpus:[]};
const endpoint=(location,extra={})=>({id:'endpoint',label:'Local endpoint',gpu:{name:'Remote endpoint: hardware unverified'},host:{platform:'remote-unverified'},hardware:unknown,
  classification:{hardware_class:'remote-unverified'},provider:{kind:'openai',inference_location:location},
  measurement_client:{hardware:inventory,host:{cpu:'i7'},gpu:{name:'NVIDIA GeForce RTX 5070 Ti',vram_total_mb:16303}},...extra});

// A benchmark that recorded its own machine: cards, their memory and RAM, each said once.
const local={id:'local',gpu:{name:'NVIDIA GeForce RTX 5070 Ti',vram_total_mb:16303},host:{},hardware:inventory,config:{provider:'openai',endpoint_inference_local:true}};
assert.deepEqual(labels(run('machineSummaryMarkup(item)',{item:local})),['2× RTX 5070 Ti','2× 15.9 GiB VRAM','64 GiB RAM']);
assert.equal(run('machineRecord(item).confirmed',{item:local}),true);
assert.doesNotMatch(run('machineSummaryMarkup(item)',{item:local}),/31\.8|Remote Display/,'separate cards are never pooled and a display adapter is not a card');

// An endpoint benchmark saved as remote, reached at a local address: the computer
// that ran it is the recorded machine, and the page says it was not confirmed.
let html=run('machineSummaryMarkup(item)',{item:endpoint('loopback')});
assert.deepEqual(labels(html),['2× RTX 5070 Ti','2× 15.9 GiB VRAM','64 GiB RAM','Host not confirmed']);
assert.match(html,/saved without confirming that inference ran on this computer/);
assert.equal(run('machineRecord(item).confirmed',{item:endpoint('loopback')}),false);
html=run('hardwareBadgesMarkup(item)',{item:endpoint('loopback',{config:{provider:'openai',endpoint_inference_local:false}})});
assert.doesNotMatch(html,/Remote hardware unknown|Remote Display/);
assert.deepEqual(labels(html),['RTX 5070 Ti','15.9 GiB VRAM','RTX 5070 Ti','15.9 GiB VRAM','64 GiB RAM','Channels unreported','Host not confirmed']);

// Any other address, or no recorded client, stays unknown: nothing is guessed.
for(const item of [endpoint('unverified_remote'),endpoint('loopback',{measurement_client:undefined}),endpoint(undefined)]) {
  assert.equal(run('machineRecord(item)',{item}),null);
  assert.deepEqual(labels(run('machineSummaryMarkup(item)',{item})),['Hardware not recorded']);
  assert.deepEqual(labels(run('hardwareBadgesMarkup(item)',{item})),['Remote hardware unknown']);
}
assert.equal(run('machineRecord(item)',{item:{config:{provider:'openai',endpoint_inference_local:false},provider:{inference_location:'unverified_remote'},measurement_client:{hardware:inventory}}}),null,
  'a benchmark without a classification is still read from its settings');

// Different cards keep their memory beside their name; shared memory is not repeated as RAM.
const mixed={hardware:{gpus:[{name:'NVIDIA GeForce RTX 5070 Ti',memory_bytes:16303*1048576},{name:'NVIDIA GeForce RTX 5060 Ti',memory_bytes:16311*1048576}],system:{memory_bytes:64*GiB}}};
assert.deepEqual(labels(run('machineSummaryMarkup(item)',{item:mixed})),['RTX 5070 Ti · 15.9 GiB VRAM','RTX 5060 Ti · 15.9 GiB VRAM','64 GiB RAM']);
const mac={gpu:{name:'Apple M5 Max'},hardware:{gpus:[{name:'Apple M5 Max',memory_bytes:128*GiB,memory_architecture:'unified'}],system:{memory_bytes:128*GiB}}};
assert.deepEqual(labels(run('machineSummaryMarkup(item)',{item:mac})),['Apple M5 Max','128 GiB unified']);
assert.deepEqual(labels(run('machineSummaryMarkup(item)',{item:{gpu:{name:'NVIDIA GeForce GTX 1070 Ti',vram_total_mb:8192},host:{ram_gb:64}}})),['GTX 1070 Ti','8 GiB VRAM','64 GiB RAM'],'a legacy record is read from its GPU and host');
assert.deepEqual(labels(run('machineSummaryMarkup(item)',{item:{host:{cpu:'AMD Ryzen 9 CPU @ 4GHz',ram_gb:32}}})),['AMD Ryzen 9','32 GiB RAM']);
assert.deepEqual(labels(run('machineSummaryMarkup({})')),['Hardware not recorded']);
assert.doesNotMatch(run('machineSummaryMarkup(item)',{item:{gpu:{name:'<img src=x>'}}}),/<img/);

// The library card names the machine instead of repeating the test count, and
// endpoint benchmarks of one computer sit with its other benchmarks.
const summary=(id,extra)=>({id,label:id,created_at:'2026-11-23T14:07:03+00:00',status:'completed',models:['m'],n_results:23,n_ok:23,n_failed:0,...extra});
run('state.env={capabilities:{}};state.runs=items;state.compare=new Set(["a"]);renderRunsList()',{items:[
  summary('a',endpoint('loopback',{id:'a',label:'a'})),summary('b',{...local,id:'b',label:'b',n_ok:21,n_failed:2}),summary('c',endpoint('unverified_remote',{id:'c',label:'c'})),
  summary('d',{...local,id:'d',label:'d',community:true})]});
const list=node('#runs-list').innerHTML;
assert.deepEqual([...list.matchAll(/run-group-label">([^<]*)</g)].map(match=>match[1]),['GeForce RTX 5070 Ti','Remote endpoint: hardware unverified']);
assert.doesNotMatch(list,/tests? passed/);
assert.equal((list.match(/1 model · 23 tests/g)||[]).length,4);
assert.equal((list.match(/>2× RTX 5070 Ti</g)||[]).length,3);
assert.match(list,/>2 failed</);
assert.equal((list.match(/class="run-edit" data-edit-run="/g)||[]).length,4,'every benchmark can be renamed from its card');
assert.match(list,/data-edit-run="d" title="Shared benchmarks are read-only[^"]*" disabled/);
assert.doesNotMatch(list.split('data-edit-run="d"')[0],/class="run-edit"[^>]* disabled/,'a benchmark of this computer is editable, running or not');

// One readable line: the count is said once.
const rows=statuses=>({results:statuses.map(status=>({model:'m',target_tokens:5000,concurrency:1,status}))});
assert.match(run('coverageLine(item)',{item:rows(['ok','ok','ok'])}),/ · 3 tests passed$/);
assert.match(run('coverageLine(item)',{item:rows(['ok'])}),/ · 1 test passed$/);
assert.match(run('coverageLine(item)',{item:rows(['ok','failed','skipped'])}),/ · 3 tests: 1 passed, 1 failed, 1 skipped$/);
assert.match(run('coverageLine(item)',{item:rows([])}),/0 tests: 0 passed$/);

// Renaming: one dialog for name and description, opened from any [data-edit-run] button.
const requests=[];let fail=null;
context.api=async(path,options)=>{requests.push([path,JSON.parse(options.body)]);if(fail)throw Error(fail);return {};};
context.loadRuns=async()=>{requests.push(['reload']);};
run('state.runs=[{id:"a/1",label:"Local endpoint",notes:"generated"},{id:"shared",label:"Shared",community:true}];state.runCache={"a/1":{id:"a/1"}};openMetaDialog("shared")');
assert.equal(node('#meta-dialog').open,false,'a shared benchmark is read-only');
(async()=>{
  run('openMetaDialog("a/1")');
  assert.equal(node('#meta-dialog').open,true);
  assert.deepEqual([node('#meta-label').value,node('#meta-notes').value,node('#meta-label').selected],['Local endpoint','generated',true]);
  node('#meta-label').value='   ';
  await run('saveMeta({preventDefault(){}})');
  assert.deepEqual(requests,[],'an empty name is never sent');
  node('#meta-label').value='  vLLM 188k  ';node('#meta-notes').value=' two cards ';fail='Benchmark name must contain 1..160 characters.';
  await run('saveMeta({preventDefault(){}})');
  assert.deepEqual(requests,[['/api/runs/a%2F1/meta',{label:'vLLM 188k',notes:'two cards'}]]);
  assert.equal(node('#meta-dialog').open,true,'a refused edit keeps the dialog and what was typed');
  assert.match(node('#meta-error').textContent,/Could not save: Benchmark name must contain/);
  assert.deepEqual([node('#meta-error').hidden,node('#meta-save').disabled,run('!!state.runCache["a/1"]')],[false,false,true]);
  fail=null;requests.length=0;
  await run('saveMeta({preventDefault(){}})');
  assert.deepEqual(requests.map(request=>request[0]),['/api/runs/a%2F1/meta','reload']);
  assert.deepEqual([node('#meta-dialog').open,run('"a/1" in state.runCache')],[false,false],'a saved edit closes the dialog and refreshes the benchmark');

  const page=fs.readFileSync('web/index.html','utf8');
  for(const id of ['meta-dialog','meta-form','meta-label','meta-notes','meta-error','meta-save','meta-cancel','meta-close'])assert(page.includes(`id="${id}"`),id);
  assert.doesNotMatch(fs.readFileSync('web/app.js','utf8'),/\bprompt\(/,'renaming never uses a browser prompt');
  console.log('Library machine badges, single test count and rename dialog tests passed');
})().catch(error=>{console.error(error);process.exit(1);});
