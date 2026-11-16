const assert = require('node:assert/strict');
const {Dashboard, catalog, nearest} = require('../web/system-dashboard.js');

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

d.update(sample(200));
d.update(sample(210));
assert.equal(nearest(d.points,205), null);
d.inspect=205; d.draw();
assert.equal(q('[data-history-time]').textContent, 'No sample at this time');
assert.match(q('.observatory-readout').innerHTML, /— W/);
d.inspect=null;
d.tick(220);
assert.equal(d.end(),220);
assert.match(q('.observatory-plots').innerHTML, />Now<\/text>/);
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
console.log('Adversarial system history review passed');
