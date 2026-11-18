// A benchmark in the chart key is the switch for its curves.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const element = () => ({dataset: {}, style: {}, hidden: false, innerHTML: '', querySelectorAll: () => [], querySelector: () => ({})});
const elements = {'#compare-body': element(), '#chart-key': element()};
const drawn = [];
const context = {WatteverPhaseMetrics: require('../web/phase-metrics.js'),
  Charts: {fmtNum: String, MARKERS: ['circle', 'square', 'triangle', 'diamond'], DASHES: [null], release() {}, line: (node, options) => drawn.push(options)},
  document: {documentElement: {}, querySelector: selector => elements[selector] || null, querySelectorAll: () => []},
  getComputedStyle: () => ({getPropertyValue: () => '#123456'})};
vm.createContext(context);
vm.runInContext(fs.readFileSync('web/app.js', 'utf8').replace(/\ninit\(\);\s*$/, ''), context);
const run = code => vm.runInContext(code, context);
const body = () => elements['#compare-body'].innerHTML, key = () => elements['#chart-key'].innerHTML;

const row = (model, size, speed) => ({model, target_tokens: size, concurrency: 1, status: 'ok', repeat: 0, prompt_tokens: size, completion_tokens: 100,
  ttft_s: 1, decode_s: 2, decode_token_count: 99, wall_s: 3, prefill_tps: speed, decode_tps: 40});
context.a = {id: 'a', label: 'Alpha <b>', status: 'completed', results: [row('m', 512, 100), row('m', 5000, 200), row('n', 512, 300)]};
context.b = {id: 'b', label: 'Beta', status: 'completed', results: [row('m', 512, 150), row('m', 5000, 250)]};
run(`ensureRuns = async () => {}; redrawResultsWithoutScrolling = async () => {};
  state.runs = [a, b]; state.runCache = {a, b}; state.compare = new Set(['a', 'b']); state.conditions = null; state.visibleModels = null;
  state.metrics = new Set(['prefill_tps']); state.metricsTouched = true; state.levelsTouched = true; state.colorTouched = true;
  state.comparisonAxis = 'context'; state.comparisonConcurrency = 1; state.comparisonLayout = 'metrics'; state.resultView = 'compare';
  state.mtpFilter = 'all'; state.colorBy = 'benchmarks';`);
const render = async () => { drawn.length = 0; await run('renderCompare()'); return drawn.flatMap(chart => chart.series); };
const entries = () => [...key().matchAll(/<button type="button" class="key-item key-toggle" aria-pressed="(true|false)" data-curve="([^"]+)"([^>]*)>/g)]
  .map(match => ({on: match[1] === 'true', key: match[2], rest: match[3]}));

(async () => {
  // Every ticked benchmark is drawn to begin with, and its key entry carries its own colour.
  let series = await render();
  assert.deepEqual([...new Set(series.map(item => item.run.id))].sort(), ['a', 'b']);
  assert.deepEqual(entries().map(entry => [entry.key, entry.on]), [['a', true], ['b', true]]);
  assert.ok(entries().every(entry => /style="--key-color:[^"]+"/.test(entry.rest)), 'the wash behind an entry uses that benchmark\'s colour');
  assert.ok(entries().every(entry => /data-hl-tag="run"/.test(entry.rest)), 'a drawn benchmark can still be picked out by hovering');
  assert.match(key(), /Alpha &lt;b&gt;/, 'names are escaped');
  assert.match(key(), /title="[^"]*3 matching cases · 3 measured[^"]*Click to hide these curves"/, 'the entry says how much it holds');
  assert.doesNotMatch(body(), /switched off/);
  const colourOfA = series.find(item => item.run.id === 'a').color;

  // Switching one off removes only its curves; the entry stays, dimmed, to switch it back on.
  run('state.curveChoice.set("b", false)');
  series = await render();
  assert.deepEqual([...new Set(series.map(item => item.run.id))], ['a']);
  assert.deepEqual(entries().map(entry => [entry.key, entry.on]), [['a', true], ['b', false]]);
  assert.doesNotMatch(entries()[1].rest, /data-hl-tag/, 'a hidden benchmark has no lines to pick out');
  assert.match(entries()[1].rest, /Hidden\. Click to show these curves/);
  assert.equal(series.find(item => item.run.id === 'a').color, colourOfA, 'hiding a benchmark does not recolour the others');
  // The passed tests it holds are accounted for, with one click to bring them back.
  assert.match(body(), /Charts draw 3 of 5 passed tests/);
  assert.match(body(), /<b>2<\/b> belong to benchmarks switched off in the key\. <button type="button" class="btn-sm" data-coverage="curves">Show every benchmark<\/button>/);

  // With everything off the key remains, or nothing could be switched back on.
  run('state.curveChoice.set("a", false)');
  series = await render();
  assert.equal(series.length, 0);
  assert.match(body(), /Every benchmark is switched off/);
  assert.doesNotMatch(body(), /Not recorded/, 'a metric is not reported missing when its benchmarks are merely switched off');
  assert.deepEqual(entries().map(entry => [entry.key, entry.on]), [['a', false], ['b', false]]);

  run('applyCoverageAction({dataset: {coverage: "curves"}})');
  assert.equal(run('state.curveChoice.size'), 0);
  series = await render();
  assert.deepEqual(entries().map(entry => entry.on), [true, true]);

  // Colour by model: benchmarks are told apart by marker, so the wash is neutral, and the switch still works.
  run('state.colorBy = "models"; state.curveChoice.set("a", false)');
  series = await render();
  assert.deepEqual([...new Set(series.map(item => item.run.id))], ['b']);
  assert.ok(entries().length === 2 && entries().every(entry => !/--key-color/.test(entry.rest)));
  run('state.colorBy = "benchmarks"; state.curveChoice.clear()');

  // A benchmark with nothing for the ticked models is not offered at all.
  run('state.visibleModels = new Set(["n"])');
  await render();
  assert.deepEqual(entries().map(entry => entry.key), ['a'], 'Beta never measured model n');
  run('state.visibleModels = null');

  // A recorded condition outside the current selection is offered switched off, and one click draws it.
  run('state.conditions = new Set(["a"])');
  series = await render();
  assert.deepEqual([...new Set(series.map(item => item.run.id))], ['a']);
  assert.deepEqual(entries().map(entry => [entry.key, entry.on]), [['a', true], ['b', false]]);
  assert.doesNotMatch(body(), /switched off in the key/, 'an optional condition that was never on is not reported as hidden');
  run('state.curveChoice.set("b", true)');
  series = await render();
  assert.deepEqual([...new Set(series.map(item => item.run.id))].sort(), ['a', 'b']);
  assert.deepEqual(entries().map(entry => entry.on), [true, true]);
  run('state.conditions = null; state.curveChoice.clear()');

  // A choice made for a benchmark that is no longer ticked is forgotten.
  run('state.curveChoice.set("b", false); state.compare = new Set(["a"])');
  await render();
  assert.equal(run('state.curveChoice.has("b")'), false);
  run('state.compare = new Set(["a", "b"])');
  series = await render();
  assert.deepEqual(entries().map(entry => entry.on), [true, true], 'ticking it again shows it again');

  // A ticked benchmark with nothing to draw here must not hide the way back for the others.
  context.c = {id: 'c', label: 'Gamma', status: 'completed', results: [row('z', 512, 50)]};
  run('state.runs = [a, b, c]; state.runCache = {a, b, c}; state.compare = new Set(["a", "b", "c"]); state.visibleModels = new Set(["m"]);');
  series = await render();
  assert.deepEqual(entries().map(entry => entry.key), ['a', 'b'], 'Gamma never measured model m, so it has no switch');
  run('state.curveChoice.set("a", false); state.curveChoice.set("b", false)');
  series = await render();
  assert.equal(series.length, 0);
  assert.match(body(), /Every benchmark is switched off/);
  assert.deepEqual(entries().map(entry => [entry.key, entry.on]), [['a', false], ['b', false]]);
  run('state.runs = [a, b]; state.runCache = {a, b}; state.compare = new Set(["a", "b"]); state.visibleModels = null; state.curveChoice.clear()');

  // A lone benchmark that was switched off keeps its entry however the charts are coloured.
  for (const colour of ['benchmarks', 'models', 'levels']) {
    run(`state.compare = new Set(["a"]); state.colorBy = ${JSON.stringify(colour)}; state.curveChoice.set("a", false)`);
    series = await render();
    assert.equal(series.length, 0);
    assert.deepEqual(entries().map(entry => [entry.key, entry.on]), [['a', false]], 'coloured by ' + colour);
  }
  run('state.compare = new Set(["a", "b"]); state.colorBy = "benchmarks"; state.curveChoice.clear()');

  // The panel this replaces is gone.
  assert.equal(run('typeof renderRecordedConditions'), 'undefined');
  assert.doesNotMatch(fs.readFileSync('web/index.html', 'utf8'), /recorded-conditions/);
  console.log('chart key toggles: ok');
})().catch(error => { console.error(error); process.exit(1); });
