// Every passed test must be either drawn or accounted for, with a way to reach it.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {WatteverConcurrency: require('../web/concurrency.js'), WatteverPhaseMetrics: require('../web/phase-metrics.js'),
  Charts: {fmtNum: String, MARKERS: ['circle', 'square', 'triangle', 'diamond'], DASHES: [null]},
  document: {documentElement: {}, querySelector: () => null, querySelectorAll: () => []}, getComputedStyle: () => ({getPropertyValue: () => '#123456'})};
vm.createContext(context);
vm.runInContext(fs.readFileSync('web/app.js', 'utf8').replace(/\ninit\(\);\s*$/, ''), context);
const run = code => vm.runInContext(code, context);
run('redrawResultsWithoutScrolling=async()=>{}');   // no page to redraw in this harness

// One model at three query counts. Group rows carry no request start offsets, like older records.
const single = size => ({model: 'm', target_tokens: size, concurrency: 1, status: 'ok', repeat: 0, prompt_tokens: size, completion_tokens: 100,
  ttft_s: 1, decode_s: 2, decode_token_count: 99, wall_s: 3.1, prefill_tps: size, decode_tps: 49.5});
const group = (size, count) => ({model: 'm', target_tokens: size, concurrency: count, status: 'ok', repeat: 0, wall_s: 6, completed_requests: count,
  requests: Array.from({length: count}, () => ({status: 'ok', prompt_tokens: size, completion_tokens: 100, ttft_s: 2, decode_s: 4, decode_token_count: 99, wall_s: 6}))});
context.bench = {id: 'b', label: 'Bench', models: ['m'], results: [single(512), single(5000), group(512, 2), group(5000, 2), group(512, 4),
  {model: 'm', target_tokens: 5000, concurrency: 4, status: 'failed', fail_kind: 'timeout', repeat: 0}]};
const reset = `state.runs=[bench];state.runCache={b:bench};state.compare=new Set(['b']);state.conditions=null;state.visibleModels=null;
  state.queryLevels=null;state.promptLevels=null;state.levelsTouched=false;state.metricsTouched=false;state.colorTouched=false;
  state.metrics=new Set(['prefill_tps','decode_tps']);state.comparisonAxis='context';state.resultView='compare';state.mtpFilter='all';state.colorBy='models';`;
const coverage = `(()=>{const views=splitRowConditions(measurementRuns([bench]));const c=graphCoverage(views,['m'],selectedMetricKeys(),state.comparisonAxis,state.comparisonAxis==='context'?activeQueryLevels():activePromptLevels());
  return JSON.stringify({total:c.total,drawn:c.drawn,levels:[...c.levels],unrecorded:c.unrecorded.length,at:[...c.unrecordedAt],suggest:c.suggest,offsets:c.needsOffsets,models:c.models});})()`;

// The group rates really are unavailable here; that policy is not what this test questions.
assert.equal(run('METRICS.decode_tps.get(bench.results[2])'), null);
assert.equal(run('METRICS.per_request_decode_tps.get(bench.results[2])'), 24.75);

// A user who ticks one level and the two default metrics sees the gap spelled out.
run(reset + `state.levelsTouched=true;state.metricsTouched=true;syncResultLevels(measurementRuns([bench]));`);
let seen = JSON.parse(run(coverage));
assert.deepEqual([seen.total, seen.drawn], [5, 2], 'five tests passed, two are drawn at one query');
assert.deepEqual(seen.levels, [[2, 2], [4, 1]], 'the rest are counted by the query count that hides them');
const banner = run(`coverageMarkup(graphCoverage(splitRowConditions(measurementRuns([bench])),['m'],selectedMetricKeys(),'context',activeQueryLevels()),selectedMetricKeys(),'context')`);
assert.match(banner, /draw 2 of 5 passed tests/);
assert.match(banner, /data-coverage="levels"/, 'the banner offers to show the hidden query counts');

// Ticking those levels alone is not enough: the ticked metrics were never recorded there.
run(`applyCoverageAction({dataset:{coverage:'levels'}})`);
seen = JSON.parse(run(coverage));
assert.deepEqual([seen.drawn, seen.unrecorded, seen.at], [2, 3, [2, 4]]);
assert.equal(seen.offsets, true, 'the reason given is the missing request start times');
assert.deepEqual(seen.suggest.slice(0, 2), ['per_request_decode_tps', 'ttft_s'], 'metrics that were recorded for those tests are offered');
run(`applyCoverageAction({dataset:{coverage:'metric',metricAdd:'per_request_decode_tps'}})`);
seen = JSON.parse(run(coverage));
assert.deepEqual([seen.total, seen.drawn, seen.unrecorded], [5, 5, 0], 'two clicks reach every passed test');
assert.match(run(`coverageMarkup(graphCoverage(splitRowConditions(measurementRuns([bench])),['m'],selectedMetricKeys(),'context',activeQueryLevels()),selectedMetricKeys(),'context')`), /All 5 passed tests appear in at least one chart/);

// Untouched, the view chooses levels, metrics and colours that already show everything.
run(reset + `{const measured=measurementRuns([bench]);syncResultLevels(measured);autoResultDefaults(measured,['m']);}`);
seen = JSON.parse(run(coverage));
assert.deepEqual([seen.total, seen.drawn], [5, 5], 'the default view draws every passed test');
assert.deepEqual(Array.from(run('activeQueryLevels()')), [1, 2, 4]);
assert(run('state.metrics.has("prefill_tps") && state.metrics.has("per_request_decode_tps")'), 'the defaults are kept and the recorded metrics added');
assert.equal(run('state.colorBy'), 'levels', 'with one model, colour tells the query counts apart');
assert.equal(new Set(run(`JSON.stringify(buildSeries(splitRowConditions(measurementRuns([bench])),['m'],['m'],'per_request_decode_tps','context',activeQueryLevels()).series.map(item=>item.color))`).slice(1, -1).split('","')).size, 3);

// The user's own choices are never overridden.
run(reset + `state.levelsTouched=true;state.metricsTouched=true;state.colorTouched=true;state.comparisonConcurrency=1;{const measured=measurementRuns([bench]);syncResultLevels(measured);autoResultDefaults(measured,['m']);}`);
assert.deepEqual(Array.from(run('activeQueryLevels()')), [1]);
assert.equal(run('state.metrics.size'), 2);
assert.equal(run('state.colorBy'), 'models');

// Many models: the default stays readable and says what it left out.
context.wide = {id: 'w', label: 'Wide', results: Array.from({length: 6}, (_, index) => [{...single(512), model: 'm' + index}, {...group(512, 2), model: 'm' + index}]).flat()};
run(`state.runs=[wide];state.runCache={w:wide};state.compare=new Set(['w']);state.queryLevels=null;state.levelsTouched=false;syncResultLevels(measurementRuns([wide]));`);
assert.deepEqual(Array.from(run('activeQueryLevels()')), [1], 'twelve lines would be too many to tick by default');
const wide = run(`coverageMarkup(graphCoverage(splitRowConditions(measurementRuns([wide])),allModelsOf([wide]),['ttft_s'],'context',activeQueryLevels()),['ttft_s'],'context')`);
assert.match(wide, /draw 6 of 12 passed tests/);
assert.match(wide, /Show every query count/);

// The same settings reported by a different source, or run under a different test plan, are one condition.
const sourced = (source, plan) => ({...single(512), session_id: plan, configuration_id: 'hash-' + source,
  model_configuration: {configuration_id: 'hash-' + source, quantization: {value: 'Q4', source}}});
context.mixed = {id: 'x', label: 'Mixed', sessions: [{id: 'a', config: {repeats: 1, sizes: [512], gpu_offload: 'max'}}, {id: 'b', config: {repeats: 2, sizes: [512, 5000], gpu_offload: 'max'}}],
  results: [sourced('endpoint_reported', 'a'), {...sourced('control_plane_verified', 'b'), repeat: 1}]};
assert.equal(run('splitRowConditions([mixed]).length'), 1, 'provenance and test plan do not create a variant');
context.mixed.sessions[1].config.gpu_offload = 0.5;
assert.equal(run('splitRowConditions([mixed]).length'), 2, 'a setting that changes speed still does');

// Overview: no clean sweep is claimed when a test failed, and models are counted once.
const rows = JSON.parse(run(`JSON.stringify(overviewRows([bench]).map(row=>({largest:row.largest,blocked:row.blocked,misses:row.misses,base:row.base})))`));
assert.equal(rows.length, 1);
assert.deepEqual(rows[0].blocked, {x: 5000, count: 4, reason: 'timeout'}, 'a failure at another query count is still reported');
console.log('Results coverage tests passed');
