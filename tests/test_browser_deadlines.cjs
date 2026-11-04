const assert = require('node:assert/strict');
const {withDeadline, pendingCommand, rejectPending, cleanupOwned} = require('./browser_deadlines.cjs');

function fakeTimers() {
  let next = 0;
  const active = new Map();
  return {active, setTimeout(callback) {const id=++next;active.set(id,callback);return id;},
    clearTimeout(id) {active.delete(id);}, expire() {for(const callback of [...active.values()]) callback();}};
}

(async () => {
  let timers = fakeTimers();
  assert.equal(await withDeadline(Promise.resolve('ready'), 900000, 'Fixture startup', timers), 'ready');
  assert.equal(timers.active.size, 0, 'Successful startup clears its timer');
  timers = fakeTimers();
  const fixture = withDeadline(new Promise(()=>{}), 900000, 'Fixture startup / Pages build', timers);
  const fixtureFailure = assert.rejects(fixture, /Fixture startup \/ Pages build timed out after 900000 ms/);
  timers.expire(); await fixtureFailure;
  assert.equal(timers.active.size, 0);

  timers = fakeTimers();
  const pending = new Map();
  const command = pendingCommand(pending, 1, 'Runtime.evaluate', ()=>{}, 60000, timers);
  pending.get(1).resolve({value:42});
  assert.deepEqual(await command, {value:42});
  assert.equal(pending.size, 0); assert.equal(timers.active.size, 0);
  const stalled = pendingCommand(pending, 2, 'Page.navigate', ()=>{}, 60000, timers);
  const commandFailure = assert.rejects(stalled, /CDP Page.navigate timed out after 60000 ms/);
  timers.expire(); await commandFailure;
  assert.equal(pending.size, 0); assert.equal(timers.active.size, 0);

  const closed = pendingCommand(pending, 3, 'Browser.close', ()=>{}, 60000, timers);
  const socketFailure = assert.rejects(closed, /Owned socket closed/);
  rejectPending(pending, Error('Owned socket closed')); await socketFailure;
  assert.equal(pending.size, 0); assert.equal(timers.active.size, 0);
  const failedSend = pendingCommand(pending, 4, 'Runtime.enable', ()=>{throw Error('send failed');}, 60000, timers);
  await assert.rejects(failedSend, /send failed/);
  assert.equal(pending.size, 0); assert.equal(timers.active.size, 0);
  const cleaned = [];
  const cleanupErrors = cleanupOwned([()=>{cleaned.push('socket');throw Error('synthetic close failure');},
    ()=>cleaned.push('owned browser'), ()=>cleaned.push('owned fixture')]);
  assert.deepEqual(cleaned, ['socket', 'owned browser', 'owned fixture']);
  assert.equal(cleanupErrors.length, 1, 'One failed cleanup cannot skip remaining owned children');
  console.log('CI browser deadline checks passed with fake timers; no browser, server or GPU.');
})().catch(error=>{console.error(error);process.exitCode=1;});
