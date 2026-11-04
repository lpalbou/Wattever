// CI fixture deadlines only. Never contacts an inference provider.
function withDeadline(promise, milliseconds, label, timers = globalThis) {
  let timer;
  const bounded = new Promise((resolve, reject) => {
    timer = timers.setTimeout(() => reject(Error(`${label} timed out after ${milliseconds} ms`)), milliseconds);
    Promise.resolve(promise).then(resolve, reject);
  });
  return bounded.finally(() => timers.clearTimeout(timer));
}

function pendingCommand(pending, id, method, transmit, milliseconds = 60000, timers = globalThis) {
  return new Promise((resolve, reject) => {
    let settled = false, timer;
    const finish = (complete, value) => {
      if (settled) return;
      settled = true;
      timers.clearTimeout(timer);
      pending.delete(id);
      complete(value);
    };
    const request = {resolve:value => finish(resolve, value), reject:error => finish(reject, error)};
    pending.set(id, request);
    timer = timers.setTimeout(() => request.reject(Error(`CDP ${method} timed out after ${milliseconds} ms`)), milliseconds);
    try { transmit(); } catch (error) { request.reject(error); }
  });
}

function rejectPending(pending, error) {
  for (const request of [...pending.values()]) request.reject(error);
}

function cleanupOwned(actions) {
  const errors = [];
  for (const action of actions) {
    try { action(); } catch (error) { errors.push(error); }
  }
  return errors;
}

module.exports = {withDeadline, pendingCommand, rejectPending, cleanupOwned};
