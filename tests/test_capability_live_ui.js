// Coordinator regressions only: fake DOM, clock and API; no server or inference.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const copy = value => structuredClone(value);
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
}
async function until(predicate, description) {
  // Drain queued fake requests without depending on machine speed or wall time.
  for (let attempt = 0; attempt < 100; attempt++) {
    if (predicate()) return;
    await new Promise(setImmediate);
  }
  assert.fail(description);
}

function harness() {
  const nodes = new Map(), timers = new Map(), renders = [], calls = [];
  let nextTimer = 0;
  function node(selector) {
    if (!nodes.has(selector)) {
      let html = '', inputs = [], buttons = [];
      const element = {value:'', checked:false, hidden:false, disabled:false,
        style:{}, dataset:{}, handlers:{}, textContent:'',
        addEventListener(type, handler) { this.handlers[type] = handler; },
        scrollIntoView() {}, focus() {},
        querySelectorAll(query) {
          return selector === '#cap-runs' ? query === 'input' ? inputs : query === '[data-cap-run]' ? buttons : [] : [];
        }};
      Object.defineProperty(element, 'innerHTML', {
        get() { return html; },
        set(value) {
          html = String(value);
          if (selector !== '#cap-runs') return;
          const control = properties => ({...properties, handlers:{},
            addEventListener(type, handler) { this.handlers[type] = handler; }});
          inputs = [...html.matchAll(/<input type="checkbox" value="([^"]+)"([^>]*)>/g)]
            .map(match => control({value:match[1], checked:/\bchecked\b/.test(match[2])}));
          buttons = [...html.matchAll(/data-cap-run="([^"]+)"/g)]
            .map(match => control({dataset:{capRun:match[1]}}));
        },
      });
      nodes.set(selector, element);
    }
    return nodes.get(selector);
  }
  const passed = id => ({item_id:id, repeat:0, category:'arithmetic', status:'ok',
    response:'1', scoring:{scored:true, correct:true}});
  const failed = id => ({...passed(id), response:'extra explanation', scoring:{scored:true, correct:false}});
  const summarize = rows => {
    const correct = rows.filter(row => row.scoring.correct).length;
    return {scored:rows.length, correct, percent:100*correct/rows.length, planned:3};
  };
  const initial = [passed('q1')];
  const runs = new Map(['live','older'].map(id => [id, {
    id, status:id === 'live' ? 'running' : 'completed',
    model_configuration:{model:{value:'org/'+id}, quantization:{value:'Q4'},
      endpoint:{id:'fixture-route', label:'Fixture endpoint'},
      reasoning:{requested:'none', user_reported:'unknown'}, mtp:{user_reported:'unknown'}},
    suite:{id:'fixture-suite', tasks:['q1','q2','q3'].map(taskId => ({
      id:taskId, category:'arithmetic', prompt:'Public question '+taskId,
      scorer:{kind:'exact_text', expected:'1'},
    }))},
    protocol:{selected_item_ids:['q1','q2','q3'], repeats:1},
    results:copy(initial), summary:summarize(initial),
  }]));
  const fullFetches = {live:0, older:0};
  const staleStatusSummary = copy(runs.get('live').summary);
  const eventResponses = [];
  let pausedEvents = null, cursor = 0;
  const api = async path => {
    calls.push(path);
    const url = new URL(path, 'http://fixture.invalid');
    if (url.pathname === '/api/capabilities/suites') return {runner:{completion_policy:'finished-stream-visible-answer-v2'},suites:[{id:'fixture-suite', task_count:3}]};
    if (url.pathname === '/api/capabilities/runs') {
      return {runs:[...runs.values()].map(run => ({id:run.id, status:run.status,
        model_configuration:copy(run.model_configuration), summary:copy(run.summary)}))};
    }
    if (url.pathname === '/api/capabilities/status') {
      return {running:true, run_id:'live', live:{phase:'request', done:1, total:3},
        run:{summary:copy(staleStatusSummary)}};
    }
    if (url.pathname === '/api/capabilities/events') {
      if (pausedEvents) {
        const pending = pausedEvents; pausedEvents = null;
        pending.entered = true;
        return pending.response.promise;
      }
      const next = eventResponses.shift();
      if (next) { cursor = next.cursor; return copy(next); }
      return {events:[], cursor, status:{run:{summary:copy(runs.get('live').summary)}}};
    }
    const id = url.pathname.match(/^\/api\/capabilities\/runs\/(live|older)$/)?.[1];
    if (id) { fullFetches[id]++; return copy(runs.get(id)); }
    throw Error('Unexpected fake API request: '+path);
  };
  const window = {
    WatteverUI:{config:()=>({provider:'openai', endpoint_reasoning_effort:'none'}),
      connection:()=>({provider:'openai'}), models:()=>[{id:'served-alias'}],
      selectedModels:()=>['served-alias'], ready:()=>true, browserMode:()=>false, api},
    WatteverCapabilityDashboard:{create:()=>({
      render(run) { renders.push(copy(run)); node('#cap-detail').hidden = false; },
      renderComparison() {},
    })},
  };
  const context = {window, document:{querySelector:node}, URL, Blob, console,
    setTimeout, clearTimeout,
    setInterval(callback) { const id=++nextTimer; timers.set(id,callback); return id; },
    clearInterval(id) { timers.delete(id); },
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync('web/capabilities.js','utf8'), context, {filename:'web/capabilities.js'});
  return {ui:window.WatteverCapabilities, nodes, node, renders, calls, runs, fullFetches,
    passed, failed, summarize, eventResponses,
    async tick() {
      assert.equal(timers.size, 1, 'One fake poll timer must remain registered');
      await [...timers.values()][0]();
    },
    async browse(id) {
      const button = node('#cap-runs').querySelectorAll('[data-cap-run]')
        .find(item => item.dataset.capRun === id);
      assert(button, 'Run must be selectable from the actual coordinator library');
      await button.handlers.click();
    },
    pauseNextEvents() {
      const pending = {entered:false, response:deferred()}; pausedEvents = pending; return pending;
    },
    latestRender() { return renders.at(-1); },
  };
}

(async () => {
  {
    const h = harness();
    await h.ui.activate();
    assert.equal(h.latestRender().id, 'live');
    assert.equal(h.fullFetches.live, 1);

    const second = h.failed('q2');
    h.runs.get('live').results.push(second);
    h.runs.get('live').summary = h.summarize(h.runs.get('live').results);
    h.eventResponses.push({events:[{kind:'result', result:second,
      summary:copy(h.runs.get('live').summary)}], cursor:1});
    await h.tick();
    assert.equal(h.latestRender().summary.scored, 2);
    assert.equal(h.latestRender().summary.percent, 50,
      'Fresh event summary must survive the stale preceding /status snapshot');
    assert.equal(h.fullFetches.live, 1, 'Events must update the cached manifest without a full run fetch');

    h.eventResponses.push({events:[{kind:'result', result:second,
      summary:copy(h.runs.get('live').summary)}], cursor:2});
    await h.tick();
    assert.equal(h.latestRender().results.length, 2, 'Replayed item/repeat events must replace, not duplicate, rows');
    assert.equal(h.latestRender().results.filter(row => row.item_id === 'q2').length, 1);

    await h.browse('older');
    const rendersWhileBrowsing = h.renders.length;
    const third = h.passed('q3');
    h.runs.get('live').results.push(third);
    h.runs.get('live').summary = h.summarize(h.runs.get('live').results);
    h.eventResponses.push({events:[{kind:'result', result:third,
      summary:copy(h.runs.get('live').summary)}], cursor:3});
    await h.tick();
    assert.equal(h.renders.length, rendersWhileBrowsing, 'Live events must not steal the older run dashboard');
    assert.equal(h.latestRender().id, 'older');
    assert(h.node('#cap-runs').innerHTML.includes('66.7%'), 'The library must still receive the active run summary');
    await h.browse('live');
    assert.equal(h.latestRender().results.length, 3, 'Background events must remain available when returning to the live run');
    assert.equal(h.latestRender().summary.scored, 3);
    assert.equal(h.fullFetches.live, 1, 'Polling and revisiting the live run must reuse its cached suite');
    assert(h.calls.includes('/api/capabilities/events?cursor=2'), 'Polling must advance its event cursor');
  }

  {
    const h = harness();
    await h.ui.activate(); await h.browse('older');
    const pending = h.pauseNextEvents(), poll = h.tick();
    await until(() => pending.entered, 'Poll must reach its deferred /events request');
    h.node('#cap-refresh').handlers.click(); // Clears the real coordinator cache while /events is awaiting.
    await until(() => h.fullFetches.older === 2, 'Refresh must reload the focused older run');
    assert.equal(h.fullFetches.live, 1, 'Refresh has left the in-flight active run out of cache');

    const second = h.failed('q2');
    h.runs.get('live').results.push(second);
    h.runs.get('live').summary = h.summarize(h.runs.get('live').results);
    pending.response.resolve({events:[{kind:'result', result:second,
      summary:copy(h.runs.get('live').summary)}], cursor:1,
      status:{run:{summary:copy(h.runs.get('live').summary)}}});
    await poll;
    assert.equal(h.fullFetches.live, 2, 'A cache cleared during /events must recover the active snapshot');
    assert(!h.node('#cap-status').textContent.startsWith('Status unavailable'), 'Refresh race must not enter an error loop');
    assert.equal(h.latestRender().id, 'older', 'Recovery must preserve the user\'s chosen older run');
    await h.browse('live');
    assert.equal(h.latestRender().results.length, 2, 'Snapshot plus replayed event must still deduplicate');
    assert.equal(h.latestRender().summary.percent, 50);
    await h.tick(); await h.browse('live');
    assert.equal(h.fullFetches.live, 2, 'After recovery, event polling must resume without repeatedly fetching the suite');
    assert.equal(h.latestRender().summary.scored, 2, 'Fresh /events status must beat an older /status response');
  }
  console.log('Capability live coordinator cache, event, focus and refresh-race tests passed');
})().catch(error => { console.error(error); process.exitCode=1; });
