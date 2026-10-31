const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const metrics = require('../web/capability-metrics.js');

function fixture() {
  const tasks = Array.from({length:24},(_,i) => ({id:'item-'+String(i).padStart(2,'0'),category:['arithmetic','logic','json','instructions','retrieval','code_output'][Math.floor(i/4)],prompt:'Question '+i,scorer:{kind:'exact_text',expected:'answer '+i}}));
  const results=[];
  tasks.slice(0,23).forEach(task => [0,1].forEach(repeat => results.push({item_id:task.id,category:task.category,repeat,status:'ok',response:'answer',scoring:{scored:true,correct:true}})));
  const row=(id,repeat) => results.find(value=>value.item_id==='item-'+String(id).padStart(2,'0') && value.repeat===repeat);
  row(0,1).scoring.correct=false;
  row(1,0).scoring.correct=false;row(1,1).scoring.correct=false;
  row(2,0).scoring={scored:true,correct:false,truncated:true};row(2,0).finish_reason='length';
  row(3,0).status='error';row(3,0).scoring={scored:false};row(3,0).diagnostic='Connection unavailable';
  row(4,0).status='cancelled';row(4,0).scoring={scored:false};
  row(5,0).scoring={scored:false};
  row(6,0).scoring={scored:false,truncated:true};row(6,0).finish_reason='length';
  return {id:'fixture',status:'interrupted',evaluation_key:'same-evaluation',suite_digest:'suite-digest',
    suite:{digest:'suite-digest',scoring_version:'rules-v1',tasks},
    protocol:{version:'protocol-v1',suite_digest:'suite-digest',scoring_version:'rules-v1',selected_item_ids:tasks.map(task=>task.id),repeats:2,max_tokens:256,
      token_limit_field:'max_tokens',sampling:{seed:0,temperature:0,top_p:null}},
    summary:{wilson_95:{low:.6,high:.99,n:19,scope:'first repeat of distinct items only'},category_scores:[{category:'arithmetic',wilson_95:{low:.1,high:.9,n:3}}]},results};
}
const copy = value => JSON.parse(JSON.stringify(value));

// Both browser/VM and CommonJS load without DOM or other application modules.
const context={window:{}};vm.createContext(context);
vm.runInContext(fs.readFileSync('web/capability-metrics.js','utf8'),context);
assert.equal(typeof context.window.WatteverCapabilityMetrics.summary,'function');

assert.equal(metrics.classifyOutcome({status:'ok',scoring:{correct:true}}),'pass');
assert.equal(metrics.classifyOutcome({status:'ok',scoring:{correct:false}}),'incorrect');
assert.equal(metrics.classifyOutcome({status:'ok',scoring:{correct:false},finish_reason:'length'}),'truncated');
assert.equal(metrics.classifyOutcome({status:'error',scoring:{correct:false}}),'error','Transport errors are not incorrect answers');
assert.equal(metrics.classifyOutcome({status:'cancelled',scoring:{correct:false}}),'cancelled');
assert.equal(metrics.classifyOutcome({status:'ok',scoring:{score:1}}),'unscored','Never infer correctness from an undocumented numerical score');
assert.equal(metrics.classifyOutcome({status:'ok',scoring:{correct:true,truncated:true}}),'pass','Do not override authoritative recorded correctness');
assert.equal(metrics.classifyOutcome({status:'pending'}),'pending');

const run=fixture(), before=JSON.stringify(run), cells=metrics.rows(run), totals=metrics.summary(run);
assert.equal(cells.length,48);assert.equal(cells.filter(row=>row.pending).length,2);
assert.deepEqual(cells.slice(0,4).map(row=>[row.item_id,row.repeat]),[['item-00',0],['item-00',1],['item-01',0],['item-01',1]]);
assert.equal(cells[0].task.prompt,'Question 0');
assert.deepEqual(totals.counts,{pass:38,incorrect:3,truncated:2,error:1,cancelled:1,unscored:1,pending:2});
assert.equal(totals.planned,48);assert.equal(totals.scored,42);assert.equal(totals.pass,38);
assert.equal(totals.coverage,42/48);assert.equal(totals.accuracy,38/42);
assert.equal(totals.failed_rule,4);assert.equal(totals.attempted,46);assert.equal(totals.truncated_unscored,1);
assert.equal(totals.first_repeat_ci.n,19,'Pass through the recorded first-repeat interval; repeats do not inflate n');
assert.equal(totals.repeats[0].scored,19);assert.equal(totals.repeats[1].scored,23);
assert.equal(totals.categories.find(row=>row.category==='arithmetic').accuracy,3/7);
assert.equal(totals.categories.find(row=>row.category==='arithmetic').coverage,7/8);
assert.equal(JSON.stringify(run),before,'All metrics must leave raw audit and scores unchanged');

const matrix=metrics.matrix(run);
assert.equal(matrix.rows.length,24);assert.deepEqual(matrix.repeats,[0,1]);
assert.equal(matrix.rows[23].cells[0].outcome,'pending');
const reversed=copy(run);reversed.results.reverse();reversed.suite.tasks.reverse();
assert.deepEqual(metrics.rows(reversed).map(row=>[row.item_id,row.repeat,row.outcome]),cells.map(row=>[row.item_id,row.repeat,row.outcome]),'Input row order cannot change the matrix');

assert.equal(metrics.filterRows(run,{category:'arithmetic'}).length,8);
assert.equal(metrics.filterRows(run,{repeat:'1',outcome:'incorrect'}).length,2);
assert.equal(metrics.filterRows(run,{outcome:'failed_rule'}).length,4);
assert.equal(metrics.filterRows(run,{query:'question 23'}).length,2);
assert.equal(metrics.filterRows(run,{category:'arithmetic',query:'CONNECTION'}).length,1);
assert.equal(metrics.summary(run,{outcome:'pass'}).first_repeat_ci,null,'An interval for all answers is invalid after selecting only passes');
assert.equal(metrics.summary(run,{category:'arithmetic'}).first_repeat_ci.n,3);
assert.equal(metrics.summary(run,{repeat:1}).first_repeat_ci,null);

const hot=metrics.hotspots(run), byId=id=>hot.find(row=>row.item_id===id);
assert.equal(hot[0].item_id,'item-01');assert.equal(hot[0].failed_rule_count,2);
assert.equal(byId('item-00').varies,true);assert.equal(byId('item-02').varies,true);
assert.equal(byId('item-03').varies,false,'An error followed by a pass does not establish answer variance');
assert.equal(byId('item-06').invalid_count,1);assert.equal(byId('item-06').failed_rule_count,0);
assert.equal(hot.some(row=>row.item_id==='item-23'),false,'Pending work alone is not a failed question');

const b=copy(run);b.results.forEach(row=>{if(row.item_id==='item-00' && row.repeat===1)row.scoring.correct=true;});
const paired=metrics.compareRuns(run,b);
assert.equal(paired.eligible,true);assert.equal(paired.difference_direction,'A minus B');
assert(Math.abs(paired.difference_pp - (-100/42))<1e-9);
assert.equal(paired.paired.only_b,1);assert.equal(paired.paired.only_a,0);
assert.equal(paired.paired.both_unscored,6);assert.equal(paired.paired.one_unscored,0);
assert.equal(paired.paired.both_scored,42);assert.equal(paired.paired.overlap,48);
const missing=copy(b);missing.results.find(row=>row.item_id==='item-00' && row.repeat===1).status='error';
assert.equal(metrics.compareRuns(run,missing).paired.one_unscored,1);
const different=copy(b);different.evaluation_key='different';
const rejected=metrics.compareRuns(run,different);
assert.equal(rejected.eligible,false);assert.equal(rejected.difference_pp,null);assert.equal(rejected.descriptive_only,true);
assert.equal(rejected.paired.only_b,1,'Retain descriptive overlap without implying a fair paired comparison');
const altered=copy(b);altered.protocol.max_tokens=1024;
assert.equal(metrics.compareRuns(run,altered).eligible,false,'Matching keys cannot hide contradictory declared policy');
const once=copy(run);once.protocol.repeats=1;once.results=once.results.filter(row=>row.repeat===0);
const overlap=metrics.compareRuns(run,once);
assert.equal(overlap.eligible,true);assert.equal(overlap.paired.overlap,24);assert.equal(overlap.paired.unmatched,24);

const legacy={summary:{percent:75,scored:4,wilson_95:{low:.3,high:.95,n:4}},results:[]};
const reported=metrics.summary(legacy);
assert.equal(reported.accuracy,.75);assert.equal(reported.pass,3);assert.equal(reported.scored,4);
assert.equal(reported.audit_available,false);assert.equal(metrics.rows(legacy).length,0);
assert.equal(metrics.summary({}).accuracy,null);assert.equal(metrics.summary({}).planned,0);
assert.equal(metrics.summary({}).audit_available,false);
assert.equal(metrics.summary({summary:{percent:60}}).accuracy,.6);
assert.equal(metrics.summary({summary:{scored:0,planned:24}}).coverage,0);
assert.equal(metrics.compareRuns(legacy,legacy).eligible,false,'Legacy aggregate summaries do not prove protocol equivalence');
assert.equal(metrics.summary(legacy,{query:'missing'}).accuracy,null);

console.log('Capability metric classification, matrix, coverage, filters, repeats and paired comparison tests passed');
