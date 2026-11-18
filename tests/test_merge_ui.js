// Merge UI logic: the library bar, the dialog's plan-driven states, the merge request and undo.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/app.js', 'utf8').replace(/\ninit\(\);\s*$/, '');

function fixture({capabilities = {merge: true}, browser = false} = {}) {
  const nodes = new Map(), stored = new Map();
  const node = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {value: '', checked: false, disabled: false, hidden: false, open: false, innerHTML: '', textContent: '',
      dataset: {}, listeners: {}, showModal() { this.open = true; }, close() { this.open = false; }, addEventListener(type, handler) { this.listeners[type] = handler; }});
    return nodes.get(selector);
  };
  const calls = [];
  const context = {window: {WATTEVER_STATIC: browser}, Charts: {fmtNum: String}, console, calls,
    document: {querySelector: node, querySelectorAll: () => []},
    localStorage: {getItem: key => stored.has(key) ? stored.get(key) : null, setItem: (key, value) => stored.set(key, value)},
    confirm: () => true, alert: message => { throw Error('unexpected alert: ' + message); }};
  vm.createContext(context);
  vm.runInContext(source, context);
  context.capabilities = capabilities;
  vm.runInContext(`
    state.env = {capabilities};
    state.runs = [{id: 'lm', label: 'LM run', created_at: '2026-11-12'}, {id: 'strata', label: 'Strata <b>run</b>', created_at: '2026-11-13'},
                  {id: 'mac', label: 'Mac run', created_at: '2026-11-14'}];
    var reloads = 0; loadRuns = async () => { reloads++; };
    var answers = {}; api = async (path, options) => { const body = options?.body ? JSON.parse(options.body) : null; calls.push({path, body});
      const answer = answers[path]; if (answer instanceof Error) throw answer; return typeof answer === 'function' ? answer(body) : answer; };
  `, context);
  // Values built inside the vm belong to another realm; compare plain copies.
  const plain = value => value === undefined ? value : JSON.parse(JSON.stringify(value));
  return {context, node, calls, stored, last: () => plain(calls.at(-1)), evaluate: expression => vm.runInContext(expression, context), read: expression => plain(vm.runInContext(expression, context))};
}
const row = (id, extra = {}) => ({id, label: id, created_at: '2026-11-12T10:00:00+00:00', status: 'completed', engines: ['LM Studio'], tests: 3, models: 1,
  machine: 'PC', attribution: 'local', loopback: false, confirmed: false, issues: [], ...extra});
const plan = (runs, extra = {}) => ({ok: false, run_ids: runs.map(item => item.id), label: 'LM run', machine: 'PC', runs, issues: [],
  engines: [{label: 'LM Studio', benchmarks: 1}, {label: 'Strata', benchmarks: 1}], models: 2, tests: 6, superseded: 0, also_here: [], ...extra});

(async () => {
  // --- the library bar -----------------------------------------------------
  for (const hidden of [fixture({capabilities: {}}), fixture({browser: true})]) {
    hidden.evaluate('state.compare = new Set(["lm", "strata"]); state.mergeSuggestions = [{key: "k", machine: "PC", engines: ["A", "B"], run_ids: ["lm", "strata"]}]; renderMergeBar()');
    assert.equal(hidden.node('#merge-bar').hidden, true, 'no merge controls without the helper capability');
    await hidden.evaluate('openMergeDialog(["lm", "strata"])');
    assert.equal(hidden.calls.length, 0);
  }
  const page = fixture();
  page.evaluate('state.compare = new Set(["lm", "strata", "deleted-elsewhere"]); renderMergeBar()');
  assert.equal(page.node('#merge-bar').hidden, false);
  assert.match(page.node('#merge-bar').innerHTML, /<b>2<\/b> ticked.*id="merge-ticked"/s);
  page.evaluate('state.compare = new Set(["mac"]); renderMergeBar()');
  assert.equal(page.node('#merge-bar').hidden, true, 'one ticked benchmark and no suggestion: nothing to offer');
  page.evaluate(`state.mergeSuggestions = [
    {key: "old:2", machine: "Old PC", engines: ["LM Studio", "Strata"], run_ids: ["lm", "gone"]},
    {key: "pc:2", machine: "2× RTX <5070>", engines: ["LM Studio", "Strata"], run_ids: ["lm", "strata"]},
    {key: "mac:2", machine: "Mac", engines: ["LM Studio", "MLX LM"], run_ids: ["mac", "lm"]}]; renderMergeBar()`);
  assert.match(page.node('#merge-bar').innerHTML, /MLX LM<\/b> ran on Mac\./, 'the suggestion about the ticked benchmark comes first');
  page.evaluate('state.compare = new Set(); renderMergeBar()');
  const suggestion = page.node('#merge-bar').innerHTML;
  assert.match(suggestion, /SAME MACHINE.*LM Studio \+ Strata<\/b> ran on 2× RTX &lt;5070&gt;\..*Merge 2 benchmarks/s);
  assert(!suggestion.includes('Old PC'), 'a suggestion whose benchmarks are gone is not offered');
  page.node('#merge-dismiss').listeners.click();
  assert.deepEqual(JSON.parse(page.stored.get('wattever-merge-dismissed')), ['pc:2']);
  assert.match(page.node('#merge-bar').innerHTML, /ran on Mac\./, 'dismissing one suggestion reveals the next');
  page.evaluate('state.mergeSuggestions.push({key: "pc:2", machine: "PC", engines: ["A", "B"], run_ids: ["lm", "strata"]}); state.mergeSuggestions = state.mergeSuggestions.filter(item => item.key !== "mac:2"); renderMergeBar()');
  assert.equal(page.node('#merge-bar').hidden, true, 'a dismissed suggestion stays dismissed after a reload');

  // --- the dialog follows the helper's plan --------------------------------
  const blocked = plan([row('lm'), row('mac', {label: 'Mac <i>run</i>', issues: [{level: 'block', code: 'machine', message: 'Hardware differs: <mac> ran elsewhere.'}]})],
    {also_here: [row('strata', {engines: ['Strata'], attribution: 'client'})]});
  page.evaluate('answers["/api/bench/merge-plan"] = body => plans.shift()');
  page.context.plans = [blocked];
  await page.evaluate('openMergeDialog(["lm", "mac"])');
  assert.equal(page.node('#merge-dialog').open, true);
  assert.deepEqual(page.last(), {path: '/api/bench/merge-plan', body: {run_ids: ['lm', 'mac'], assume_local: []}});
  let html = page.node('#merge-content').innerHTML;
  assert.match(html, /data-state="block"[^]*Mac &lt;i&gt;run&lt;\/i&gt;[^]*Hardware differs: &lt;mac&gt; ran elsewhere\./);
  assert(!html.includes('<i>run</i>') && !html.includes('<mac>'), 'benchmark names and reasons are escaped');
  assert.match(html, /One benchmark cannot join\..*id="merge-leave-out"/);
  assert.match(html, /<details class="merge-more" id="merge-more" >.*Also on this machine · 1 benchmark/s, 'other candidates start folded');
  assert(!/data-merge-confirm/.test(html), 'a benchmark that is not ticked asks for nothing');
  assert.equal(page.node('#merge-start').disabled, true);
  assert.equal(page.node('#merge-start').textContent, 'Merge');

  // Leave the blocked one out, then add the endpoint run from the same machine.
  page.context.plans = [plan([row('lm')], {also_here: [row('strata', {engines: ['Strata'], attribution: 'client'})]})];
  page.evaluate('mergeDialogChanged({type: "click", target: {id: "merge-leave-out", dataset: {}}})');
  await new Promise(setImmediate);
  assert.deepEqual(page.last().body, {run_ids: ['lm'], assume_local: []});
  html = page.node('#merge-content').innerHTML;
  assert.match(html, /Tick at least two benchmarks\./);
  assert.match(html, /data-picked="false"[^]*data-merge-run="mac"/, 'a benchmark left out can be ticked again');
  const needsWord = plan([row('lm'), row('strata', {engines: ['Strata'], attribution: 'client', loopback: true, issues: [{level: 'confirm', code: 'confirm_local', message: 'Confirm local inference'}]})]);
  page.context.plans = [needsWord];
  page.evaluate('mergeDialogChanged({type: "change", target: {id: "", checked: true, dataset: {mergeRun: "strata"}}})');
  await new Promise(setImmediate);
  assert.deepEqual(page.last().body, {run_ids: ['lm', 'strata'], assume_local: []});
  html = page.node('#merge-content').innerHTML;
  assert.match(html, /data-state="confirm"[^]*data-merge-confirm="strata" >[^]*Inference ran on this machine[^]*local address/);
  assert.match(html, /Confirm where inference ran for the benchmark marked above\./);
  assert(!html.includes('merge-confirm-all'), 'one confirmation needs no shortcut');
  assert.equal(page.node('#merge-start').disabled, true, 'nothing is attributed to this machine without the operator saying so');

  // Clicking or typing elsewhere never asks the helper again.
  const before = page.calls.length;
  page.evaluate('mergeDialogChanged({type: "click", target: {id: "", dataset: {mergeRun: "strata"}}}); mergeDialogChanged({type: "click", target: {id: "merge-label", value: "LM run", dataset: {}}})');
  assert.equal(page.calls.length, before);
  assert.equal(page.evaluate('state.merge.labelEdited'), false, 'focusing the name is not editing it');

  const ready = plan([row('lm'), row('strata', {engines: ['Strata'], attribution: 'client', loopback: true, confirmed: true})],
    {ok: true, label: 'Suggested by helper', superseded: 1, issues: [{level: 'warn', code: 'settings', message: 'LM Studio benchmarks used a different answer length.', run_ids: ['lm']}]});
  page.context.plans = [ready];
  page.evaluate('mergeDialogChanged({type: "input", target: {id: "merge-label", value: "  My PC  ", dataset: {}}}); mergeDialogChanged({type: "change", target: {id: "", checked: true, dataset: {mergeConfirm: "strata"}}})');
  await new Promise(setImmediate);
  assert.deepEqual(page.last().body, {run_ids: ['lm', 'strata'], assume_local: ['strata']});
  html = page.node('#merge-content').innerHTML;
  assert.match(html, /data-ready="true"><b>One benchmark<\/b> · LM Studio \+ Strata · 2 models · 6 tests/);
  assert.match(html, /1 test was measured more than once\..*the other stays in the benchmark's history/);
  assert.match(html, /different answer length/);
  assert.match(html, /You can undo the merge afterwards\./);
  assert.match(html, /id="merge-label" maxlength="160" value="  My PC  "/, 'a typed name survives a new plan');
  assert.equal(page.node('#merge-start').disabled, false);
  assert.equal(page.node('#merge-start').textContent, 'Merge 2 benchmarks');

  // --- merging --------------------------------------------------------------
  page.evaluate('answers["/api/bench/consolidate"] = new Error("Hardware differs: checked again by the helper")');
  await page.evaluate('startMerge()');
  assert.match(page.node('#merge-content').innerHTML, /Could not merge.*Hardware differs: checked again by the helper/);
  assert.equal(page.node('#merge-dialog').open, true, 'a refused merge keeps the dialog and its choices');
  assert.equal(page.evaluate('reloads'), 0);
  page.evaluate('state.runCache = {lm: {id: "lm"}, strata: {id: "strata"}, mac: {id: "mac"}}; answers["/api/bench/consolidate"] = {id: "lm", label: "My PC"}');
  await page.evaluate('startMerge()');
  assert.deepEqual(page.last(), {path: '/api/bench/consolidate',
    body: {run_ids: ['lm', 'strata'], label: 'My PC', assume_local: ['strata'], overlap: 'newest'}});
  assert.equal(page.node('#merge-dialog').open, false);
  assert.equal(page.evaluate('state.merge'), null);
  assert.equal(page.evaluate('reloads'), 1);
  assert.deepEqual(page.read('[[...state.compare].join(), state.selected, Object.keys(state.runCache).join()]'), ['lm', 'lm', 'mac']);

  // Several endpoint benchmarks: one click states where all of them ran.
  const many = plan([row('lm'), row('strata', {attribution: 'client'}), row('mac', {attribution: 'client'})]);
  page.context.plans = [many, plan(many.runs.map(item => ({...item, confirmed: item.attribution === 'client'})), {ok: true})];
  await page.evaluate('openMergeDialog(["lm", "strata", "mac"])');
  assert.match(page.node('#merge-content').innerHTML, /the 2 benchmarks marked above\..*id="merge-confirm-all"/);
  page.evaluate('mergeDialogChanged({type: "click", target: {id: "merge-confirm-all", dataset: {}}})');
  await new Promise(setImmediate);
  assert.deepEqual(page.last().body, {run_ids: ['lm', 'strata', 'mac'], assume_local: ['strata', 'mac']});
  assert.equal(page.node('#merge-start').textContent, 'Merge 3 benchmarks');

  // A slow answer for an older selection never replaces the newer one.
  let release;
  page.evaluate('answers["/api/bench/merge-plan"] = body => body.run_ids.length === 3 ? new Promise(done => { late = done; }) : plans.shift()');
  page.context.plans = [plan([row('lm'), row('strata')], {ok: true})];
  const slow = page.evaluate('refreshMergePlan()');
  page.evaluate('mergeDialogChanged({type: "change", target: {id: "", checked: false, dataset: {mergeRun: "mac"}}})');
  await new Promise(setImmediate);
  page.evaluate('late(null)'); await slow;
  assert.equal(page.node('#merge-start').textContent, 'Merge 2 benchmarks');

  // --- where a benchmark came from, and undo --------------------------------
  page.evaluate('state.runs = [{id: "lm", label: "My PC", merge: {sources: 2, at: "2026-11-17T08:45:29+00:00", undo: true}}, {id: "old", merge: {sources: 4, at: "2026-11-01T00:00:00+00:00", undo: false}}, {id: "plain", merge: null}]');
  assert.match(page.evaluate('mergeOriginMarkup({id: "lm"})'), /Merged from 2 benchmarks on 2026-11-17\..*id="undo-merge"/);
  assert.match(page.evaluate('mergeOriginMarkup({id: "old"})'), /^<p class="hint merge-origin">Merged from 4 benchmarks on 2026-11-01\.<\/p>$/);
  assert.equal(page.evaluate('mergeOriginMarkup({id: "plain"})'), '');
  page.evaluate('answers["/api/bench/merge-undo"] = {restored: ["lm", "strata"]}; state.runCache = {lm: {id: "lm"}, mac: {id: "mac"}}');
  await page.evaluate('undoMerge({id: "lm", label: "My PC"})');
  assert.deepEqual(page.last(), {path: '/api/bench/merge-undo', body: {run_id: 'lm'}});
  assert.deepEqual(page.read('[[...state.compare].join(), Object.keys(state.runCache).join(), reloads]'), ['lm,strata', 'mac', 2]);
  page.context.confirm = () => false;
  const quiet = page.calls.length;
  await page.evaluate('undoMerge({id: "lm"})');
  assert.equal(page.calls.length, quiet, 'undo asks first');

  const markup = fs.readFileSync('web/index.html', 'utf8');
  for (const id of ['merge-bar', 'merge-dialog', 'merge-content', 'merge-start', 'merge-cancel', 'merge-close']) assert(markup.includes(`id="${id}"`), id);
  console.log('Merge UI: library bar, plan-driven dialog, merge request and undo passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
