const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/app.js', 'utf8').replace(/\ninit\(\);\s*$/, '');
const context = {Charts: {fmtNum: String}};
vm.createContext(context);
vm.runInContext(source, context);
vm.runInContext(`
  const cfg = {models:['m','missing'],repeats:2,mtp_modes:['off']};
  const models = [{id:'m'},{id:'missing',unavailable:true},{id:'new'}];
  const selection = originalModelSelection(cfg, models);
  if (JSON.stringify(selection)!==JSON.stringify({m:['off']})) throw Error('Only installed original models are preselected');
  const extension = {sourceConfig:cfg,modelModes:selection,sizes:new Set([512,5000]),concurrency:new Set([1,2]),source:{results:[
    {model:'m',target_tokens:512,concurrency:1,repeat:0,status:'ok'},
    {model:'m',target_tokens:512,concurrency:1,repeat:0,status:'failed'},
    {model:'m',target_tokens:512,concurrency:1,repeat:1,status:'cancelled'},
    {model:'m',target_tokens:512,concurrency:2,status:'failed'},
    {model:'m',target_tokens:5000,concurrency:1,repeat:0,status:'skipped'},
    {model:'m',target_tokens:5000,concurrency:1,repeat:1,status:'ok'},
  ]}};
  const progress = extensionModelProgress(extension,models[0]);
  if (JSON.stringify(progress)!==JSON.stringify({ok:2,failed:2,skipped:1,unfinished:3,total:8})) throw Error('Checkpoint accounting must include repeats and cancelled work without duplicate successes');
  extension.modelModes.m.push('on');
  if (extensionModelProgress(extension,models[0]).unfinished!==11) throw Error('Adding MTP only adds unmeasured cases');
  extension.sizes=new Set([512]);extension.concurrency=new Set([1]);
  const scoped=extensionModelProgress(extension,models[0]);
  if(scoped.total!==4 || scoped.ok!==1 || scoped.unfinished!==3) throw Error('Progress follows chosen workload');
  const fresh=extensionModelProgress(extension,models[2]);
  if(fresh.ok!==0 || fresh.unfinished!==2) throw Error('New models start without measured cases');
  if(!extensionProgressMarkup(extension,models[1]).includes('Not installed')) throw Error('Unavailable benchmark models remain visible');
  const append={sourceConfig:{models:['m'],repeats:1,mtp_modes:['off']},repeats:2,modelModes:{m:['off']},sizes:new Set([512]),concurrency:new Set([1]),source:{results:[{model:'m',target_tokens:512,concurrency:1,repeat:0,status:'ok'}]}};
  if(extensionModelProgress(append,{id:'m'}).unfinished!==1 || extendBody(append).repeats!==2) throw Error('Increasing repeats appends one slot');
  append.timeout=3600;
  if(extendBody(append).per_test_timeout_s!==3600) throw Error('Repair timeout is recorded in the new session payload');
  append.selectedCases=[['m',false,1,512,0]];append.action='rerun';
  if(extensionModelProgress(append,{id:'m'}).total!==1 || extensionModelProgress(append,{id:'m'}).ok!==1) throw Error('Individual repair progress stays scoped');
  if(extendBody(append).selected_cases[0][4]!==0 || !extendBody(append).rerun_existing) throw Error('Individual repair payload');
  if(!quantizationLabel({model:'google/gemma-4-12b-qat',quantization:'Q4_0'}).endsWith('QAT')) throw Error('Historical QAT label');
  if(!modelSubtitle([{results:[{model:'m',quantization:'Q4_0'}]},{results:[{model:'m',quantization:'Q8_0'}]}],'m').includes('Q8_0')) throw Error('Comparison retains all formats');
  const legacyCfg={models:['m'],repeats:1,mtp_modes:[true,'on']};
  const legacySelection=originalModelSelection(legacyCfg,models);
  if(legacySelection.m.join()!=='on') throw Error('Legacy technique aliases normalize before deduplication');
  const legacyProgress=extensionModelProgress({sourceConfig:legacyCfg,modelModes:legacySelection,sizes:new Set([512]),concurrency:new Set([1]),source:{results:[{model:'m',mtp:true,target_tokens:512,status:'ok'}]}},models[0]);
  if(legacyProgress.total!==1 || legacyProgress.ok!==1 || legacyProgress.unfinished!==0) throw Error('Legacy aliases must not invent unfinished work');
`, context);
console.log('Extension progress and selection tests passed');
