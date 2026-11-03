const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('web/app.js','utf8').replace(/\ninit\(\);\s*$/,'');
const context={Charts:{fmtNum:String},document:{querySelector:()=>null}};
vm.createContext(context);vm.runInContext(source,context);
vm.runInContext(`
 const historical={model:'m',target_tokens:512,concurrency:1,repeat:0,status:'ok',session_id:'old',wall_s:10,decode_tps:40};
 const control={...historical,session_id:'control',wall_s:30,decode_tps:8};
 const alternate={...control,session_id:'nvme',wall_s:15,decode_tps:16};
 const run={id:'gpu',label:'GPU',results:[historical],config:{provider:'lmstudio'},
   engine_groups:[{id:'strata',label:'Strata',config:{provider:'openai'}}],
   sessions:[{id:'old',engine_group_id:'strata',config:{notes:'automatic residency'}},
    {id:'control',engine_group_id:'strata',config:{notes:'40 GiB control'}},
    {id:'nvme',engine_group_id:'strata',config:{notes:'40 GiB NVMe'}}],
   storage_comparison:{protocol:'wattever-storage-comparison-v1',baseline_label:'Samsung',alternative_label:'NVMe',baseline_results:[control],alternate_results:[alternate]}};
 const variants=availableMeasurementRuns([run]);
 if(variants.length!==3)throw Error('Fresh storage baseline must be a separate measured condition');
 const original=variants.find(v=>!v.storage_baseline&&!v.storage_alternative);
 const baseline=variants.find(v=>v.storage_baseline), nvme=variants.find(v=>v.storage_alternative);
 if(original.results[0].wall_s!==10 || original.storage_condition)throw Error('Historical measurements were relabelled or replaced');
 if(baseline.results[0].wall_s!==30 || baseline.config.notes!=='40 GiB control')throw Error('Wrong controlled baseline evidence');
 if(nvme.storage_baseline_id!==baseline.id)throw Error('NVMe must pair with the fresh baseline');
 state.conditions=null;if(measurementRuns([run]).length!==1)throw Error('Fresh comparisons must not change the default historical view');
 state.conditions=new Set([baseline.id,nvme.id]);
 if(measurementRuns([run]).map(v=>v.results[0].wall_s).join()!=='30,15')throw Error('Comparison substituted historical values');
 const frozen={...run,storage_comparison:{...run.storage_comparison,baseline_results:[historical]}};
 const reused=availableMeasurementRuns([frozen]);
 if(reused.length!==2 || reused.some(v=>v.storage_baseline))throw Error('Identical frozen baselines should reuse their original view');
 if(reused.find(v=>v.storage_alternative).storage_baseline_id!==reused[0].id)throw Error('Frozen baseline pairing');
 const pairRun={storage_comparison:{baseline_results:[{...control,repeat:0,wall_s:1,prefill_tps:1},{...control,repeat:1,wall_s:100,prefill_tps:100}],
   alternate_results:[{...alternate,repeat:0,wall_s:1,prefill_tps:1},{...alternate,repeat:1,wall_s:10,prefill_tps:10}]}};
 const wall=storageComparisonGroups(pairRun,'wall_s')[0];
 if(wall.primary!==50.5 || wall.alternative!==5.5 || wall.speedup!==5.5)throw Error('Use median of paired ratios, not ratio of pooled medians');
 if(storageComparisonGroups(pairRun,'prefill_tps')[0].speedup!==.55)throw Error('Higher-is-better metrics need paired inverse ratios');
`,context);
console.log('Fresh storage controls stay separate from historical runs; NVMe pairs with its actual measured baseline');
