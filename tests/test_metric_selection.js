const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let inputs = [], builds = 0, position = {left: 0, top: 480};
const view = {hidden: false, style: {}, getBoundingClientRect: () => ({height: 900})};
const container = {set innerHTML(html) {
  builds++;
  inputs = [...html.matchAll(/value="([^"]+)"/g)].map(match => ({
    value: match[1], checked: false, chip: {dataset: {}}, handlers: {},
    closest() { return this.chip; }, addEventListener(type, handler) { this.handlers[type] = handler; },
  }));
}};
const context = {Charts: {fmtNum: String}, window: {scrollX: 0, scrollY: 480,
  scrollTo(value) { position = {left: value.left, top: value.top}; }},
document: {querySelector(selector) { return selector === '#result-metrics' ? container : view; },
  querySelectorAll() { return inputs; }}};
vm.createContext(context);
vm.runInContext(fs.readFileSync('web/app.js', 'utf8').replace(/\ninit\(\);\s*$/, ''), context);
(async () => {
  vm.runInContext('renderResultsView=async()=>{window.scrollY=0}; renderMetricSeg()', context);
  const decode = inputs.find(input => input.value === 'decode_tps');
  decode.checked = true;
  await decode.handlers.change();
  assert.equal(builds, 1, 'Measure controls must retain their DOM nodes');
  assert.equal(inputs.find(input => input.value === 'decode_tps'), decode);
  assert.equal(decode.chip.dataset.on, '1');
  assert.equal(view.style.minHeight, '900px', 'Shrinking views must not clamp the viewport');
  assert.deepEqual(position, {left: 0, top: 480});
  assert.equal(vm.runInContext('state.metrics.has("decode_tps")', context), true);
  console.log('Measure selection scroll and DOM stability tests passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
