// A stopped benchmark and a skipped model explain themselves where the user is looking.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('web/app.js', 'utf8').replace(/\ninit\(\);\s*$/, '');
const element = () => ({dataset: {}, style: {}, hidden: false, textContent: '', innerHTML: '', className: '', classList: {add() {}, remove() {}}});
const elements = Object.fromEntries(['#live-card', '#live-card h3', '#progress-bar', '#live-phase', '#live-deadline',
  '#live-sample-note', '#pill-power', '#live-stats', '#live-table', '#live-issues'].map(key => [key, element()]));
const context = {WatteverPhaseMetrics: require('../web/phase-metrics.js'), Charts: {fmtNum: String},
  document: {querySelector: selector => elements[selector] || null}, clearInterval: () => {}};
vm.createContext(context);
vm.runInContext(source, context);
const run = code => vm.runInContext(code, context);
const issues = () => elements['#live-issues'];

const reply = 'HTTP 409 from external endpoint: {"error":{"message":"Model \'drafter\' failed to load: Model type qwen3_5_mtp not supported.","type":"invalid_request_error"}}';
context.rows = [
  {model: 'good', status: 'ok', target_tokens: 512, concurrency: 1, session_id: 's2'},
  {model: 'drafter', status: 'failed', fail_kind: 'error', message: reply, target_tokens: 512, concurrency: 1, session_id: 's1'},
  {model: 'drafter', status: 'failed', fail_kind: 'model_unavailable', message: reply, target_tokens: 5000, concurrency: 1, session_id: 's2'},
  {model: 'drafter', status: 'skipped', fail_kind: 'skipped_after_failure', message: 'Skipped: the endpoint could not serve this model.', target_tokens: 10000, concurrency: 1, session_id: 's2'},
  {model: 'drafter', status: 'skipped', fail_kind: 'skipped_after_failure', message: 'Skipped: the endpoint could not serve this model.', target_tokens: 20000, concurrency: 1, session_id: 's2'},
];

// The server's sentence is shown, not the JSON envelope it arrived in.
assert.equal(run('failureReason(rows[1])'), "HTTP 409: Model 'drafter' failed to load: Model type qwen3_5_mtp not supported.");
assert.equal(run('failureReason({message:"HTTP 500 from external endpoint: {\\"error\\":{\\"mess"})'), 'HTTP 500 from external endpoint: {"error":{"mess', 'a truncated reply is shown as received');
assert.equal(run('failureReason({message:"HTTP 502 from external endpoint: {\\"detail\\":\\"bad gateway\\"}"})'), 'HTTP 502: bad gateway');
assert.equal(run('failureReason({fail_kind:"load_failed"})'), 'load failed');
assert.equal(run('failureReason({message:"Request exceeded configured deadline; model unloaded before continuing."})'), 'Request exceeded configured deadline; model unloaded before continuing.');
assert.equal(run('failureReason({})'), '');

// One entry per unserved model; only the tests skipped in that session are counted.
assert.deepEqual(JSON.parse(run('JSON.stringify(unservedModels(rows))')), [{model: 'drafter', session: 's2',
  reason: "HTTP 409: Model 'drafter' failed to load: Model type qwen3_5_mtp not supported.", skipped: 2}]);
assert.equal(run('unservedNotes([rows[0]]).length'), 0);
assert.equal(run('unservedNotes(null).length'), 0);

// ---- the notes strip: one slim line, folded until asked ----
assert.equal(run('notesStrip("x", [])'), '', 'nothing to say draws nothing');
assert.equal(run('notesStrip("x", [null, undefined, false])'), '');
context.sample = [
  {kind: 'coverage', tone: 'info', gist: 'All 4 passed tests drawn', html: '<p>every test</p>'},
  {kind: 'incomplete', tone: 'warn', gist: 'Benchmark incomplete · 3 tests remaining', many: n => n + ' benchmarks incomplete', html: 'first'},
  null,
  {kind: 'incomplete', tone: 'warn', gist: 'Benchmark incomplete · 9 tests remaining', many: n => n + ' benchmarks incomplete', html: 'second'},
];
const strip = run('notesStrip("compare", sample)');
assert.match(strip, /^<details class="notes" data-notes="compare" data-tone="warn"><summary>/, 'folded by default, coloured by its most serious note');
assert.doesNotMatch(strip, /<details[^>]* open/);
assert.match(strip, /<span class="notes-count">3 notes<\/span>/);
assert.match(strip, /<span class="notes-gist">All 4 passed tests drawn · 2 benchmarks incomplete<\/span>/, 'notes of one kind share one phrase');
assert.equal((strip.match(/<div class="note">/g) || []).length, 3, 'unfolded, every note has its own row');
assert.ok(strip.indexOf('first') < strip.indexOf('second'));
const single = run('notesStrip("one", [sample[1]], "<button>Go</button>")');
assert.doesNotMatch(single, /notes-count/, 'a single note needs no count');
assert.match(single, /Benchmark incomplete · 3 tests remaining<\/span><button>Go<\/button><\/summary>/, 'an action sits on the folded line');
assert.match(run('notesStrip("e", [{kind:"a",tone:"info",gist:"<i>x</i>",html:"h"},{kind:"b",tone:"err",gist:"y",html:"h"}])'), /data-tone="err"[^]*&lt;i&gt;x&lt;\/i&gt; · y/, 'gists are escaped');
// A strip the reader unfolded stays unfolded when the view is drawn again.
run('state.openNotes.add("compare")');
assert.match(run('notesStrip("compare", sample)'), /^<details class="notes" data-notes="compare" data-tone="warn" open>/);
assert.doesNotMatch(run('notesStrip("other", sample)'), / open>/);
run('state.openNotes.clear()');

// Notes built from the existing notices keep their full text for the unfolded view.
context.stoppedRun = {id: 'r1', label: 'Mac <b>', status: 'error', error: 'boom', remaining_tests: 23, results: []};
const note = JSON.parse(run('JSON.stringify(incompleteNote(stoppedRun, true))'));
assert.equal(note.gist, 'Benchmark incomplete · 23 tests remaining');
assert.match(note.html, /^<span class="note-source">Mac &lt;b&gt;<\/span>/, 'with several benchmarks each note names its own');
assert.match(note.html, /Benchmark incomplete<\/b> - The benchmark stopped before finishing\. 23 tests remaining\./);
assert.doesNotMatch(JSON.parse(run('JSON.stringify(incompleteNote(stoppedRun))')).html, /note-source/);
assert.equal(run('incompleteNote({id:"ok",status:"completed",results:[]})'), null);
assert.equal(run('incompleteNote(stoppedRun).many(3)'), '3 benchmarks incomplete');
assert.equal(JSON.parse(run('JSON.stringify(coverageNote({total:0}, [], "context"))')), null);
const partial = JSON.parse(run('JSON.stringify(coverageNote({total:1428,drawn:52,levels:new Map([[2,46]]),unrecorded:[],unrecordedAt:new Set(),suggest:[],models:1330}, ["prefill_tps"], "context"))'));
assert.deepEqual([partial.tone, partial.gist], ['warn', 'Charts draw 52 of 1428 passed tests']);
assert.match(partial.html, /data-coverage="levels"/, 'the buttons that fix it are one unfold away');
assert.match(partial.html, /data-coverage="models"/);
const whole = JSON.parse(run('JSON.stringify(coverageNote({total:60,drawn:60}, ["prefill_tps"], "context"))'));
assert.deepEqual([whole.tone, whole.gist], ['info', 'All 60 passed tests drawn']);

// ---- the live card ----
// While the benchmark still runs, the skipped model is explained and nothing else is claimed.
run('state.env = {capabilities: {extend: true}}; state.liveResults = rows;');
elements['#live-card'].dataset.runId = 'bench';
context.status = {running: true, run: {id: 'bench', status: 'running', n_results: 5}, live: {phase: 'prefill', done: 5, total: 8}};
run('renderLive(status)');
assert.equal(issues().hidden, false);
assert.match(issues().innerHTML, /^<details class="notes" data-notes="live" data-tone="warn"><summary>/, 'a slim folded line');
assert.match(issues().innerHTML, /<span class="notes-gist">drafter was skipped: the endpoint could not serve it<\/span>/);
assert.match(issues().innerHTML, /<b>drafter was skipped<\/b>/);
assert.match(issues().innerHTML, /2 of its tests were not run/);
assert.match(issues().innerHTML, /Other models were not affected/);
assert.match(issues().innerHTML, /Model type qwen3_5_mtp not supported/);
assert.doesNotMatch(issues().innerHTML, /benchmark stopped|data-continue-run/);

// It finished: a skipped model is not a stopped benchmark.
context.status = {running: false, run: {id: 'bench', status: 'completed', n_results: 8, n_failed: 2, n_skipped: 2, remaining_tests: 0}, live: {done: 8, total: 8}};
run('renderLive(status)');
assert.equal(elements['#live-phase'].textContent, 'Completed · 8/8 tests · 2 failed · 2 skipped');
assert.match(issues().innerHTML, /drafter was skipped/);
assert.doesNotMatch(issues().innerHTML, /benchmark stopped|data-continue-run/);

// It stopped: the folded line gives the cause and what is owed, with one way to go on.
context.status = {running: false, run: {id: 'bench', status: 'error', remaining_tests: 3,
  error: 'The endpoint stopped <b> responding and did not return within 60 seconds. Restart the inference server, then continue this benchmark with Add / rerun tests.'}, live: {done: 5, total: 8}};
run('renderLive(status)');
assert.match(issues().innerHTML, /data-tone="err"/);
assert.match(issues().innerHTML, /<span class="notes-count">2 notes<\/span>/);
assert.match(issues().innerHTML, /<span class="notes-gist">The benchmark stopped: The endpoint stopped &lt;b&gt; responding and did not return within 60 seconds\. 3 tests were not run\. · drafter was skipped/, 'the first sentence of the cause is readable while folded');
assert.match(issues().innerHTML, /<\/span><button type="button" class="btn-sm" data-continue-run="bench"[^>]*>Continue this benchmark…<\/button><\/summary>/);
assert.match(issues().innerHTML, /<div class="note"><b>The benchmark stopped<\/b> — The endpoint stopped &lt;b&gt; responding[^<]*Restart the inference server[^<]*3 tests were not run\./, 'unfolded, the whole reason is there');
const before = issues().innerHTML;
issues().innerHTML = 'kept';
run('renderLive(status)');
assert.equal(issues().innerHTML, 'kept', 'an unchanged notice is not redrawn under a focused button');
issues().innerHTML = before;

// Stopped by the user with tests left: no error, still a way to continue.
context.status = {running: false, run: {id: 'bench', status: 'cancelled', remaining_tests: 1}, live: {done: 7, total: 8}};
run('renderLive(status)');
assert.match(issues().innerHTML, /Not finished · 1 test was not run/);
assert.match(issues().innerHTML, /data-continue-run="bench"/);
assert.doesNotMatch(issues().innerHTML, /benchmark stopped/);

// Without the helper's add-tests ability there is nothing to click.
run('state.env = {capabilities: {}}');
run('renderLive(status)');
assert.doesNotMatch(issues().innerHTML, /data-continue-run/);
run('state.env = {capabilities: {extend: true}}');

// Another benchmark's rows never explain this one, and a clean finish shows nothing.
elements['#live-card'].dataset.runId = 'other';
context.status = {running: false, run: {id: 'bench', status: 'completed', remaining_tests: 0}, live: {done: 8, total: 8}};
run('renderLive(status)');
assert.equal(issues().hidden, true);
assert.equal(issues().innerHTML, '');
context.status = {running: false, run: null, live: {}};
run('renderLive(status)');
assert.equal(issues().hidden, true);

// The live table uses a failed test's empty cells to say why; a passed test keeps its numbers.
elements['#live-card'].dataset.runId = 'bench';
run('renderLiveTable()');
const table = elements['#live-table'].innerHTML;
const cells = row => (row.match(/<td/g) || []).length;
const bodyRows = table.split('<tbody>')[1].split('</tr>').filter(row => row.includes('<td'));
assert.equal(bodyRows.length, 5);
const headers = (table.split('<tbody>')[0].match(/<th[ >]/g) || []).length;
assert.equal(cells(bodyRows[0]), headers, 'a measured row fills every column');
assert.doesNotMatch(bodyRows[0], /fail-reason/);
for (const row of bodyRows.slice(1)) {
  assert.match(row, /<td class="fail-reason" colspan="(\d+)">/);
  assert.equal(cells(row) - 1 + Number(row.match(/colspan="(\d+)"/)[1]), headers, 'the reason spans exactly the metric columns');
}
assert.match(bodyRows[0], /<tr data-status="ok">/);
assert.match(bodyRows[1], /<tr data-status="failed" title="error">/, 'a failed test is marked on its row, which is drawn in red');
assert.match(bodyRows[3], /<tr data-status="skipped" title="skipped">/);
assert.doesNotMatch(table, /class="st /, 'the status column is gone');
assert.match(bodyRows[2], /HTTP 409: Model &#39;drafter&#39; failed to load|HTTP 409: Model 'drafter' failed to load/);
assert.match(bodyRows[3], /Skipped: the endpoint could not serve this model\./);

// Continuing opens the benchmark in Results with the add-tests dialog on the same run.
(async () => {
  const calls = await run(`(async () => {
    const calls = [];
    showPanel = name => calls.push(['panel', name]);
    selectRun = async id => { calls.push(['select', id]); state.runCache[id] = {id, label: 'Bench'}; };
    openExtendDialog = run => calls.push(['dialog', run.id]);
    await continueBenchmark('bench');
    return JSON.stringify(calls);
  })()`);
  assert.deepEqual(JSON.parse(calls), [['panel', 'results'], ['select', 'bench'], ['dialog', 'bench']]);
  console.log('run issues: ok');
})().catch(error => { console.error(error); process.exit(1); });
