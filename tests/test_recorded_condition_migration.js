const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const context = {window:{}, Charts:{fmtNum:String}, console, URLSearchParams,
  document:{querySelector(){return null;},querySelectorAll(){return [];}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync('web/app.js','utf8').replace(/\ninit\(\);\s*$/, ''), context);
const evaluate = expression=>vm.runInContext(expression,context);
const plain = value=>JSON.parse(JSON.stringify(value));
context.before={id:'dual',config:{provider:'lmstudio'},results:[{model:'lfm',status:'ok',session_id:'lm'}]};
context.after={id:'dual',config:{provider:'mixed'},engine_groups:[
  {id:'lm-studio',config:{provider:'lmstudio'}},{id:'strata',config:{provider:'openai'}}],
  sessions:[{id:'lm',engine_group_id:'lm-studio'},{id:'native',engine_group_id:'strata'}],
  results:[{model:'lfm',status:'ok',session_id:'lm'},{model:'flash',status:'ok',session_id:'native'}]};
context.baseline={...context.after,id:'single'};
evaluate('state.conditions=new Set(["dual","single::lm-studio","single::strata"])');
assert.equal(evaluate('measurementRuns([before]).length'),1);
assert.equal(evaluate('measurementRuns([after,baseline]).length'),4,
  'Refreshing a consolidated benchmark must retain its newly grouped measurements');
assert.deepEqual(plain(evaluate('[...state.conditions].sort()')),
  ['dual::lm-studio','dual::strata','single::lm-studio','single::strata']);
assert.deepEqual(plain(evaluate('allModelsOf(measurementRuns([after,baseline]))')),['flash','lfm']);
evaluate('state.conditions=new Set()');
assert.equal(evaluate('measurementRuns([after,baseline]).length'),0,'Explicit None must stay empty');
evaluate('state.conditions=new Set(["dual::strata"])');
assert.deepEqual(plain(evaluate('measurementRuns([after]).map(run=>run.id)')),['dual::strata'],
  'An intentional engine subset must not select other engines');
evaluate('state.conditions=new Set(["dual::missing-engine"])');
assert.equal(evaluate('measurementRuns([after]).length'),0,'Unknown stale IDs must not silently select all');
assert.equal(evaluate('measurementRuns([after]).length'),0,'Reconciliation is idempotent');
assert.deepEqual(plain(evaluate('comparisonLinkIds("?compare=single,dual",[{id:"single"},{id:"dual"}])')),['single','dual']);
assert.equal(evaluate('comparisonLinkIds("?compare=single,unknown",[{id:"single"},{id:"dual"}])'),null,
  'Unknown URL IDs must not select or fetch arbitrary resources');
assert.equal(evaluate('comparisonLinkIds("?compare=single,single",[{id:"single"}])'),null);
context.partial={status:'completed',experiment_scope:{status:'partial_user_deadline',planned_cases:180,recorded_cases:168,
  pending_stages:[{model:'native-qwen35',planned_cases:12}]}};
assert.match(evaluate('benchmarkIncompleteNotice(partial)'),/12 planned tests not recorded/);
assert.match(evaluate('benchmarkIncompleteNotice(partial)'),/native-qwen35/);
console.log('Recorded-condition consolidation migration, explicit None and engine subsets passed');
