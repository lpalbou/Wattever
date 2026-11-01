const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const context = {window:{}, Charts:{fmtNum:String}, console,
  document:{querySelector(){return null;},querySelectorAll(){return [];}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync('web/app.js','utf8').replace(/\ninit\(\);\s*$/, ''), context);
const evaluate = expression => vm.runInContext(expression, context);
const plain = value => JSON.parse(JSON.stringify(value));

context.run = {
  id:'gpu',label:'RTX 5060 Ti',config:{provider:'mixed'},
  hardware:{storage:[{model:'SSD on this machine',bus_type:'NVMe'}]},
  engine_groups:[
    {id:'lm',label:'LM Studio',config:{provider:'lmstudio',models:['gemma'],mtp_modes:['off']}},
    {id:'strata',label:'Strata',config:{provider:'openai',models:['flash'],reported_mtp:'on'}}],
  sessions:[
    {id:'lm-primary',engine_group_id:'lm',config:{provider:'lmstudio'}},
    {id:'ssd',engine_group_id:'strata',storage_condition:'samsung',config:{provider:'openai'}},
    {id:'nvme',engine_group_id:'strata',storage_condition:'nvme',config:{provider:'openai'}}],
  results:[
    {model:'gemma',session_id:'lm-primary',engine_group_id:'lm',mtp:false,target_tokens:512,concurrency:1,repeat:0,status:'ok',prefill_tps:10},
    {model:'flash',session_id:'ssd',engine_group_id:'strata',mtp:null,target_tokens:512,concurrency:1,repeat:0,status:'ok',wall_s:100,prefill_tps:50},
    {model:'flash',session_id:'ssd',engine_group_id:'strata',mtp:null,target_tokens:512,concurrency:1,repeat:1,status:'ok',wall_s:110,prefill_tps:60}],
};
const beforeInventoryOnly = JSON.stringify(context.run);
let available = evaluate('availableMeasurementRuns([run])');
assert.deepEqual(plain(available.map(run=>run.id)), ['gpu::lm','gpu::strata']);
assert.equal(available.filter(run=>run.storage_alternative).length,0,
  'An installed NVMe is not evidence of an NVMe inference run');
assert.equal(JSON.stringify(context.run),beforeInventoryOnly);

context.run.storage_comparison = {
  protocol:'wattever-storage-comparison-v1',baseline_label:'Samsung SSD',alternative_label:'NVMe (F:)',
  baseline_results:context.run.results.filter(row=>row.model==='flash').map(row=>({...row})),
  alternate_results:[
    {model:'flash',session_id:'nvme',engine_group_id:'strata',mtp:null,target_tokens:512,concurrency:1,repeat:0,status:'ok',wall_s:50,prefill_tps:100},
    {model:'flash',session_id:'nvme',engine_group_id:'strata',mtp:null,target_tokens:512,concurrency:1,repeat:1,status:'ok',wall_s:55,prefill_tps:120}],
};
const before = JSON.stringify(context.run);
available = evaluate('availableMeasurementRuns([run])');
assert.equal(available.length,3);
assert.equal(new Set(available.map(run=>run.id)).size,3);
const alternate = available.find(run=>run.storage_alternative);
assert.equal(alternate.id,'gpu::strata::storage:alternate');
assert.equal(alternate.parent_run_id,'gpu');
assert.equal(alternate.config.provider,'openai');
assert.deepEqual(plain(alternate.results),context.run.storage_comparison.alternate_results);
assert(alternate.sessions.some(session=>session.id==='nvme'));
assert(alternate.sessions.every(session=>session.engine_group_id==='strata'));
assert(!alternate.results.some(row=>row.model==='gemma'), 'NVMe must not be applied to LM Studio');

evaluate('state.conditions=null');
assert.deepEqual(plain(evaluate('measurementRuns([run]).map(run=>run.id)')),['gpu::lm','gpu::strata'],
  'Default display retains primary conditions');
evaluate('state.conditions=new Set(["gpu::strata","gpu::strata::storage:alternate"])');
const selected = evaluate('measurementRuns([run])');
assert.equal(selected.length,2);
assert.equal(selected.filter(run=>run.storage_alternative).length,1);
assert.deepEqual(plain(selected.map(run=>run.results[0].prefill_tps)),[50,100],
  'Same-model primary and alternative measurements remain separate');
context.selected = selected;
assert.deepEqual(plain(evaluate('measurementRuns(selected).map(run=>run.id)')),plain(selected.map(run=>run.id)),
  'Applying the measurement projection twice must not duplicate conditions');

evaluate('state.comparisonConcurrency=1;state.mtpFilter="all";resolveColor=value=>value');
const tracks = evaluate('tracksFor([run])');
assert.equal(tracks.length,2);
assert.equal(new Set(tracks.map(track=>track.key)).size,2);
assert.notEqual(tracks[0].name,tracks[1].name);
assert.notEqual(tracks[0].dash,tracks[1].dash,
  'Same-model storage conditions need distinguishable strokes when color follows the model');
context.primary = selected.find(run=>!run.storage_alternative);
context.alternative = selected.find(run=>run.storage_alternative);
const primaryValue=evaluate('pick(primary,"flash",512,false,METRICS.prefill_tps).prefill_tps');
const alternateValue=evaluate('pick(alternative,"flash",512,false,METRICS.prefill_tps).prefill_tps');
assert(primaryValue>=50 && primaryValue<=60);
assert(alternateValue>=100 && alternateValue<=120);
evaluate('state.conditions=new Set()');
assert.equal(evaluate('measurementRuns([run]).length'),0,'Explicitly clearing conditions must stay empty');
assert.equal(JSON.stringify(context.run),before,'Views must preserve original measurements and session metadata');

const empty = {...context.run,storage_comparison:{...context.run.storage_comparison,alternate_results:[]}};
context.empty = empty;
assert.equal(evaluate('availableMeasurementRuns([empty]).length'),2,
  'An empty experiment must not create an alternative selector');
context.unsupported={...context.run,storage_comparison:{...context.run.storage_comparison,protocol:'unrecognized'}};
assert.equal(evaluate('availableMeasurementRuns([unsupported]).length'),2,
  'Unsupported metadata must not masquerade as measured conditions');
context.standalone={...context.run,id:'legacy',config:{provider:'openai'},engine_groups:undefined,
  results:context.run.results.filter(row=>row.model==='flash')};
assert.deepEqual(plain(evaluate('availableMeasurementRuns([standalone]).map(run=>run.id)')),
  ['legacy','legacy::storage:alternate'],'A single-engine benchmark keeps its original primary ID');
assert.deepEqual(plain(evaluate('availableMeasurementRuns(availableMeasurementRuns([standalone])).map(run=>run.id)')),
  ['legacy','legacy::storage:alternate'],'Single-engine condition projection must also be idempotent');

context.csvRun={...context.run,results:context.run.results.filter(row=>row.repeat===0),
  storage_comparison:{...context.run.storage_comparison,
    baseline_results:context.run.storage_comparison.baseline_results.filter(row=>row.repeat===0),
    alternate_results:context.run.storage_comparison.alternate_results.filter(row=>row.repeat===0)}};
context.csvRun.results.push({...context.run.results[1],concurrency:2});
context.csvRun.storage_comparison.baseline_results.push({...context.run.results[1],concurrency:2});
context.csvRun.storage_comparison.alternate_results.push({...context.run.storage_comparison.alternate_results[0],concurrency:2});
evaluate('state.conditions=new Set(["gpu::strata","gpu::strata::storage:alternate"]);state.visibleModels=new Set(["flash"]);state.comparisonConcurrency=1;state.mtpFilter="all"');
const csv=evaluate('selectedResultsCsv([csvRun])');
const parse=line=>{
  const cells=[];let field='',quoted=false;
  for(let i=0;i<line.length;i++){
    const char=line[i];
    if(char==='"'){if(quoted&&line[i+1]==='"'){field+='"';i++;}else quoted=!quoted;}
    else if(char===','&&!quoted){cells.push(field);field='';}else field+=char;
  }
  cells.push(field);return cells;
};
const csvLines=csv.split('\r\n'),columns=parse(csvLines[0]);
assert.equal(csvLines.length,3,'Selected CSV exports two conditions, excluding other query counts and engines');
const exported=csvLines.slice(1).map(line=>Object.fromEntries(columns.map((key,index)=>[key,parse(line)[index]])));
assert(exported.every(row=>row.model==='flash'&&row.concurrency==='1'));
assert(exported.some(row=>row.condition.includes('Samsung SSD')));
assert(exported.some(row=>row.condition.includes('NVMe (F:)')));
assert.deepEqual(exported.map(row=>Number(row.wall_s)),[100,50]);
assert(exported.every(row=>JSON.parse(row.request_metrics_json)[0].status==='ok'));
assert.doesNotMatch(csv,/gemma/);

for (const [disk,expected] of [
  [{media_type:'HDD'},'HDD'],[{rotational:true},'HDD'],
  [{device_model:'Samsung SSD 870 EVO 4TB',bus_type:'SATA'},'SSD'],
  [{media_type:'SSD',bus_type:'NVMe'},'NVMe'],
  [{interface:'SATA',model:'Unspecified'},null],[{volume:'C:'},null],
]) {context.disk=disk;assert.equal(evaluate('storageKind(disk)'),expected);}
context.unknown={hardware:{storage:[{model:'Unspecified',interface:'SATA'}]}};
const unknownBadges=evaluate('hardwareBadgesMarkup(unknown)');
assert.doesNotMatch(unknownBadges,/>Disk<|>HDD<|>SSD<|>NVMe</,
  'Unknown physical storage must not create a made-up type badge');
console.log('Recorded conditions, optional storage variants, source attribution, separate series and storage vocabulary passed');
