const assert = require('node:assert/strict');
const {Dashboard, catalog, nearest, axisLabels} = require('../web/system-dashboard.js');

// Minimal DOM: exercise the real dashboard handlers and rendered output without
// starting hardware monitoring or depending on a browser installation.
function makeNode() {
  const nodes = new Map();
  const presets = ['load', 'power', 'thermal', 'none'].map(preset => ({dataset:{preset}}));
  const makeElement = () => ({innerHTML:'', textContent:'', value:'', dataset:{}, style:{},
    querySelectorAll:()=>[], setAttribute(){}});
  const node = {classList:{add(){}}, ownerDocument:{activeElement:null},
    querySelector(selector) {
      if (!nodes.has(selector)) nodes.set(selector, makeElement());
      return nodes.get(selector);
    },
    querySelectorAll(selector) {return selector === '[data-preset]' ? presets : [];}};
  node.querySelector('[data-history-scale]').value = 'units';
  return {node, nodes, presets};
}
const {node, presets} = makeNode();
const d = new Dashboard(node);
const q = selector => node.querySelector(selector);
const sample = (time, extra={}) => ({timestamp:time, telemetry:{sample_age_s:0,
  cpu_util_percent:25, gpu_util:35, gpu_w:40, system_w_est:100,
  gpus:[{index:0,gpu_util:35,gpu_w:40}], ...extra}});

d.update(sample(100));
d.update(sample(101));
assert.match(q('.observatory-gauges').innerHTML, /Current utilization/);
assert.match(q('.observatory-gauges').innerHTML, /<path d=/);
presets.find(p=>p.dataset.preset==='power').onclick();
assert.match(q('.observatory-gauges').innerHTML, /data-gauge="cpu_util_percent" aria-pressed="false"/);
assert.match(q('.observatory-gauges').innerHTML, /data-gauge="gpu_w" aria-pressed="true"/);
q('[data-history-clear]').onclick();
assert.equal(d.points.length, 0);
assert.doesNotMatch(q('.observatory-gauges').innerHTML, /<path d=/);

// Temperatures preset: a missing CPU reading says why instead of drawing a silently empty series.
presets.find(p=>p.dataset.preset==='thermal').onclick();
d.update(sample(102, {temp_c:50, cpu_temp_c:null, cpu_temp_note:'No CPU temperature sensor is readable without elevation.'}));
assert.match(q('[data-history-note]').textContent, /CPU temperature unavailable: No CPU temperature sensor is readable without elevation\./);
d.update(sample(103, {temp_c:50, cpu_temp_c:48, cpu_temp_source:'windows_hwinfo_shared_memory:CPU Package'}));
assert.doesNotMatch(q('[data-history-note]').textContent, /CPU temperature unavailable/);
d.update(sample(104, {temp_c:50}));
assert.match(q('[data-history-note]').textContent, /CPU temperature unavailable: no CPU sensor reading\./);
presets.find(p=>p.dataset.preset==='power').onclick();
d.update(sample(200));
d.update(sample(210));
assert.equal(nearest(d.points,205), null);
d.inspect=205; d.draw();
assert.equal(q('[data-history-time]').textContent, 'No sample at this time');
assert.match(q('.observatory-readout').innerHTML, /— W/);
d.inspect=null;
d.tick(220);
assert.equal(d.end(),220);
const clockTicks = axisLabels(100, 120, 220);
assert(q('.observatory-plots').innerHTML.includes('>' + clockTicks[4] + '</text>'), 'The newest tick names the actual clock time');
assert.doesNotMatch(q('.observatory-plots').innerHTML, /s ago|>Now</, 'Ticks show clock times, not relative ages');
assert.match(q('.observatory-gauges').innerHTML, /Stale sensor reading/);
assert.equal(q('[data-history-time]').textContent, 'Waiting for a fresh sample');

d.update(sample(221));
const pause = q('[data-history-pause]');
pause.onclick({target:pause});
d.tick(300);
assert.equal(d.end(),221, 'Pause freezes the view while time continues');
d.tick(1122);
assert.equal(d.points.length,0, 'Retention still expires old points while paused');
pause.onclick({target:pause});
assert.equal(d.end(),1122);

d.update(sample(1200));
d.selected = new Set(['cpu_util_percent','gpu:0:gpu_util']);
d.controls(); d.draw();
assert.match(q('.observatory-plots').innerHTML, /stroke-dasharray="7 4"/);
assert.match(q('.observatory-readout').innerHTML, /metric-dashed/);
assert.match(q('.observatory-metrics').innerHTML, /metric-dashed/);

d.update({timestamp:1210, telemetry:null});
assert.equal(d.end(),1210);
const missingTelemetryIsStale = /Stale sensor reading/.test(q('.observatory-gauges').innerHTML);
const threeGpus = catalog({gpus:[{index:0},{index:1},{index:2}]});
const style = key => {const m=threeGpus.find(item=>item.key===key); return [m.color,m.dash];};
const uniqueStyles = JSON.stringify(style('gpu:0:vram_percent')) !== JSON.stringify(style('gpu:2:gpu_util'));
assert.deepEqual({missingTelemetryIsStale, uniqueStyles}, {missingTelemetryIsStale:true, uniqueStyles:true},
  'Missing telemetry marks gauges stale; selectable percent series need distinct line styles');

// Recorded history: the helper's log backfills the page and serves windows and earlier times the page never saw.
(async () => {
  const flush = () => new Promise(resolve => setImmediate(resolve));
  const {node: logNode} = makeNode();
  const h = new Dashboard(logNode), lq = selector => logNode.querySelector(selector), calls = [];
  let reply = (start, end) => ({start, end, bucket_s: 1, gap_s: 4, oldest: 2800,
    points: Array.from({length: 30}, (_, i) => ({t: end - 300 + i, cpu_util_percent: 10 + i}))});
  h.loader = (start, end, max) => { calls.push({start, end, max}); return Promise.resolve(reply(start, end)); };
  h.clock = () => 10000;
  h.update(sample(10000));
  h.activate();
  await flush();
  assert.deepEqual([calls[0].end - calls[0].start, calls[0].max], [900, 900], 'Opening System loads the last 15 minutes at full resolution');
  assert.equal(h.points.length, 31, 'Readings recorded while nobody watched join the live buffer');
  assert.equal(h.points[0].time, 9700);
  assert.equal(h.liveSince, 9100);
  h.window = 300; h.draw();
  assert.equal(calls.length, 1, 'Short live windows draw from the page buffer');
  assert.match(lq('.observatory-plots').innerHTML, /<path d=/);

  reply = (start, end) => ({start, end, bucket_s: 5, gap_s: 12.5, oldest: 2800,
    points: [{t: end - 600, n: 5, max: {cpu_util_percent: 90}, cpu_util_percent: 40}, {t: end - 595, n: 5, max: {cpu_util_percent: 50}, cpu_util_percent: 45}]});
  h.window = 3600; h.draw();
  assert.equal(lq('.observatory-plots').innerHTML.includes('Loading recorded history'), true);
  await flush();
  assert.equal(calls.length, 2, 'A window longer than the page buffer asks the helper');
  assert.equal(calls[1].end - calls[1].start, 3600);
  assert.match(lq('.observatory-plots').innerHTML, /<path d=/);
  assert.match(lq('[data-history-note]').textContent, /averages up to 5 s/);
  h.inspect = 10000 - 598; h.readout(h.visible(), h.metrics.filter(m => h.selected.has(m.key)), 5);
  assert.match(lq('[data-history-time]').textContent, /average of 5 readings/);
  assert.match(lq('.observatory-readout').innerHTML, /peak 90/, 'Averaged points still show the peak');
  h.inspect = null;
  h.draw(); await flush();
  assert.equal(calls.length, 2, 'An unchanged range is not fetched again');

  h.shift(-1);
  assert.equal(h.paused, true);
  assert.equal(h.end(), 10000 - 1800, 'Earlier steps back half a window');
  assert.match(lq('[data-history-pause]').textContent, /Resume/);
  await flush();
  assert.equal(calls.at(-1).end, 8200);
  h.shift(-1); h.shift(-1); h.shift(-1);
  assert.equal(h.end(), 10000 - 7200 + 3600, 'Earlier stops where the oldest recorded reading starts the window');
  assert.equal(lq('[data-history-earlier]').disabled, true);
  await flush();
  h.shift(1); h.shift(1); h.shift(1); h.shift(1);
  assert.equal(h.paused, false, 'Stepping past now resumes following live');
  h.window = 120; h.goTo(10000 - 5000);
  assert.equal(h.paused, true);
  assert.equal(h.end(), 10000 - 5000 + 60, 'Go to time centres the window on that time');
  assert.equal(h.inspect, 5000);
  await flush();
  assert.equal(calls.at(-1).end - calls.at(-1).start, 120, 'An earlier short window is read from the log at full resolution');
  reply = (start, end) => ({start, end, bucket_s: 1, gap_s: 4, points: []});
  h.goTo(10000 - 6000); await flush();
  assert.match(lq('.observatory-plots').innerHTML, /No readings were recorded in this interval/);
  h.loader = () => Promise.reject(new Error('helper stopped'));
  h.goTo(10000 - 7000); await flush();
  assert.match(lq('.observatory-plots').innerHTML, /Recorded history unavailable: helper stopped/);
  h.goTo(10000 - 99999);
  assert.equal(h.end(), 10000 - 86400 + 120, 'Navigation stays inside the recorded 24 hours');
  console.log('Adversarial system history review passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
