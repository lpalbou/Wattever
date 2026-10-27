const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/app.js', 'utf8').replace(/\ninit\(\);\s*$/, '');
const liveCard = {dataset:{}};
const context = {Charts:{fmtNum:String},document:{querySelector:s=>s==='#live-card'?liveCard:null}};
vm.createContext(context);
vm.runInContext(source,context);
(async()=>{
  const result = await vm.runInContext(`(async()=>{
    let reads=0, redraws=0;
    const run={id:'live',status:'running',results:[{model:'m',status:'ok'}]};
    api=async()=>{reads++;return run};
    renderLiveTable=()=>{}; loadRuns=async()=>{redraws++};
    const status={running:true,run_id:'live',run:{id:'live',n_results:1,status:'running'}};
    await syncLiveBenchmark(status);
    await syncLiveBenchmark(status);
    if(reads!==1||redraws!==1) throw Error('unchanged status was reloaded');
    run.results.push({model:'m',status:'failed'});status.run.n_results=2;
    await syncLiveBenchmark(status);
    if(reads!==2||redraws!==2||state.liveResults.length!==2) throw Error('new test did not refresh');
    run.status='completed';status.running=false;status.run.status='completed';
    await syncLiveBenchmark(status);
    if(reads!==3||state.runCache.live.status!=='completed') throw Error('completion was not refreshed');
    return true;
  })()`,context);
  assert.equal(result,true);
  console.log('Live results refresh tests passed');
})().catch(error=>{console.error(error);process.exitCode=1});
