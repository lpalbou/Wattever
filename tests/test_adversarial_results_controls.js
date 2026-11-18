const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const concurrency = require('../web/concurrency.js');
const source = fs.readFileSync('web/app.js','utf8').replace(/\ninit\(\);\s*$/, '');

function fixture() {
  const nodes = new Map();
  const element = () => ({dataset:{}, style:{}, value:'', innerHTML:'', textContent:'', hidden:false,
    listeners:{}, addEventListener(type,fn){this.listeners[type]=fn;},
    querySelector(){return element();}, querySelectorAll(){return [];}, setAttribute(){}});
  const get = key => {if(!nodes.has(key)) nodes.set(key,element()); return nodes.get(key);};
  const query = get('.comparison-concurrency');
  const document = {documentElement:{},querySelector:get,
    querySelectorAll:s=>s==='.comparison-concurrency'?[query]:[]};
  const context = {document,Charts:{fmtNum:String,MARKERS:['circle'],DASHES:[null]},
    WatteverConcurrency:concurrency,getComputedStyle:()=>({getPropertyValue:()=> '#123456'})};
  vm.createContext(context); vm.runInContext(source,context);
  return {context,get,query,run:code=>vm.runInContext(code,context)};
}

// Run each reproduction independently so all four failures are visible at once.
let failures=0;
function check(name,fn) {try {fn();console.log('PASS '+name);} catch(e) {failures++;console.error('FAIL '+name+': '+e.message);}}
const row = (configuration, rate, count=1) => ({model:'m',target_tokens:512,concurrency:count,
  status:'ok',repeat:0,configuration_id:configuration,
  model_configuration:{configuration_id:configuration,quantization:{value:configuration}},
  prefill_tps:rate,prompt_tokens:rate,ttft_s:1,decode_tps:rate,completion_tokens:100,wall_s:10});

check('different configurations do not become a single repeat median',()=>{
  const f=fixture(); f.context.sampleRun={id:'run',label:'run',results:[row('Q4',10),row('Q8',30)]};
  const series=f.run(`state.runs=[sampleRun];state.runCache={run:sampleRun};state.compare=new Set(['run']);
    state.conditions=null;state.comparisonConcurrency=1;
    tracksFor([sampleRun]).flatMap(track=>pointsFor(track.run,'m',METRICS.prefill_tps,track.mtp).map(p=>p.y))`);
  assert.deepEqual(Array.from(series).sort((a,b)=>a-b),[10,30],
    'Q4=10 and Q8=30 must remain separate, not become a single 20 median');
});

check('changing axis keeps every ticked metric and its meaning',()=>{
  const f=fixture();
  f.context.sampleRun={id:'run',label:'run',results:[row('Q4',10,1),row('Q4',10,2)]};
  f.run(`state.runs=[sampleRun];state.runCache={run:sampleRun};state.compare=new Set(['run']);state.conditions=null;
    state.metrics=new Set(['per_request_decode_tps','wall_s']);syncResultLevels([sampleRun]);`);
  const before=Array.from(f.run('selectedMetricKeys()'));
  f.run('state.comparisonAxis="concurrency";syncResultLevels([sampleRun])');
  assert.equal(f.run('state.comparisonAxis'),'concurrency','two recorded query counts allow the concurrency axis');
  assert.deepEqual(Array.from(f.run('selectedMetricKeys()')),before,'the axis must not swap one metric for another');
  // The same getter serves both axes, so a metric cannot silently change definition.
  const along=f.run('pointsAcrossQueries(sampleRun,"m",METRICS.wall_s,false,512).map(point=>[point.x,point.y])');
  assert.deepEqual(Array.from(along).map(pair=>Array.from(pair)),[[1,10],[2,10]]);
  assert.deepEqual(Array.from(f.run('pointsFor(sampleRun,"m",METRICS.wall_s,false,2).map(point=>point.y)')),[10]);
});

check('a single recorded query count cannot be put on the axis',()=>{
  const f=fixture();f.context.sampleRun={id:'run',results:[row('Q4',10,1)]};
  f.run('state.comparisonAxis="concurrency";syncResultLevels([sampleRun])');
  assert.equal(f.run('state.comparisonAxis'),'context','with one query count there is nothing to plot against');
});

check('a failed test does not invent a recorded condition',()=>{
  const f=fixture();
  const failed={model:'m',target_tokens:80000,concurrency:1,status:'failed',fail_kind:'timeout',repeat:0,
    configuration_id:'Q4',model_configuration:{configuration_id:'Q4',quantization:{value:'Q4'}}};
  f.context.sampleRun={id:'run',label:'run',results:[{...row('Q4',10),gpu_offload:'max',max_tokens:256},failed]};
  const views=f.run('splitRowConditions([sampleRun])');
  assert.equal(views.length,1,'a failure that recorded fewer settings stays with the condition it was attempted in');
  assert.equal(f.run('failuresFor(splitRowConditions([sampleRun])[0],"m",false,1).length'),1,'and is still drawn as a failure');
});

check('conditions for different models are not variants of each other',()=>{
  const f=fixture();
  const other={...row('Q8',30),model:'n'};
  f.context.sampleRun={id:'run',label:'Bench',results:[row('Q4',10),other]};
  const labels=Array.from(f.run('splitRowConditions([sampleRun]).map(view=>view.label)'));
  assert.deepEqual(labels,['Bench','Bench'],'two models with their own settings keep the plain benchmark name');
  f.context.sampleRun={id:'run',label:'Bench',results:[row('Q4',10),row('Q8',30)]};
  const variants=Array.from(f.run('splitRowConditions([sampleRun]).map(view=>view.label)')).sort();
  assert.equal(variants.length,2);
  assert(variants.every(label=>label.startsWith('Bench / ')) && variants[0]!==variants[1],'two settings for one model are named by what differs: '+variants.join(' | '));
  assert(!variants.some(label=>label.includes(' / m')),'the shared model name is not repeated in a variant name');
});

check('a switched-off benchmark remains recoverable from the key',()=>{
  const f=fixture();f.context.sampleRun={id:'remaining',label:'Remaining',results:[row('Q4',10)]};
  const html=f.run(`chartKeyMarkup({allModels:['m'],models:[],runs:[],levels:[],mtp:new Set(),curves:[{run:sampleRun,on:false,cases:1,measured:1}]},'context')`);
  assert.match(html,/<button type="button" class="key-item key-toggle" aria-pressed="false" data-curve="remaining"/,
    'the last switched-off benchmark must stay in the key for re-selection');
  assert.match(html,/1 matching case · 1 measured/);
});

check('a recorded query count of 11 is selectable',()=>{
  const f=fixture();f.context.sampleRun={id:'run',results:[row('Q4',10,1),row('Q4',10,11)]};
  f.run(`state.runs=[sampleRun];state.runCache={run:sampleRun};state.compare=new Set(['run']);
    state.conditions=null;state.resultView='table';syncResultLevels([sampleRun]);renderResultControls([sampleRun],['m'])`);
  const html=f.get('#result-levels').innerHTML;
  assert.match(html,/data-level="11"/,'the query-level toggles must include the recorded count 11');
  assert.doesNotMatch(html,/data-level="(2|3|100)"/,'and must not offer counts that were never recorded');
  assert.doesNotMatch(html,/<select|<option/,'levels are toggle buttons, not a menu');
  f.run('state.queryLevels=new Set([1,11])');
  assert.deepEqual(Array.from(f.run('tracksFor([sampleRun]).length ? activeQueryLevels() : []')),[1,11],'several levels can be shown together');
});

if(failures) {console.error(`${failures} Results control regression(s) failed`);process.exitCode=1;}
else console.log('Adversarial Results controls passed');
