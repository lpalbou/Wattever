const assert=require('node:assert/strict');
const {Dashboard,catalog,value,append,segments,nearest,axisLabels,stamp,toPoint}=require('../web/system-dashboard.js');
const metrics=catalog({gpus:[{index:0}]});
const ram=metrics.find(m=>m.key==='system_memory_used_bytes');
assert.equal(value({system_memory_used_bytes:3*1073741824},ram),3);
const watts=metrics.find(m=>m.key==='gpu:0:gpu_w');
assert.equal(value({gpus:[{index:0,gpu_w:0}]},watts),0,'An observed zero is distinct from missing telemetry');
assert.equal(value({gpus:[{index:1,gpu_w:99}]},watts),null,'Do not copy another card\'s reading');
assert.equal(value({gpu_w:null},metrics.find(m=>m.key==='gpu_w')),null);
assert(!catalog({}).some(m=>m.key.startsWith('gpu:')),'No invented per-card device when inventory is missing');
const points=[];
const sample=(time,age=0)=>({timestamp:time,telemetry:{sample_age_s:age,cpu_util_percent:time%100}});
assert(append(points,sample(100)));
assert(!append(points,sample(100)),'Repeated sensor observations must not become new samples');
assert(!append(points,sample(105,5)),'Stale observations must not enter history as fresh values');
assert(!append(points,{timestamp:110,telemetry:null}));
for(let i=101;i<=1100;i++)append(points,sample(i));
assert.equal(points.length,900,'Bound history retention even during long sessions');
assert.equal(points.at(-1).time,1100);
const cpu=metrics.find(m=>m.key==='cpu_util_percent');
const gaps=[{time:0,telemetry:{cpu_util_percent:10}},{time:1,telemetry:{cpu_util_percent:20}},
  {time:2,telemetry:{}},{time:3,telemetry:{cpu_util_percent:30}},
  {time:10,telemetry:{cpu_util_percent:40}}];
const paths=segments(gaps,cpu,t=>t,v=>v);
assert.equal(paths.length,3,'Missing sensors and long gaps split line paths instead of interpolating');
assert.equal(paths[0],'M0.00,10.00 L1.00,20.00');
assert.equal(nearest(gaps,6),null,'An empty interval has no inspectable observation');
assert.equal(nearest(gaps,10).time,10);
assert.notEqual(cpu.dash,metrics.find(m=>m.key==='gpu:0:gpu_util').dash,'Overlapping colors must have distinct line patterns');
const state={now:2000,paused:false,points:[{time:1000},{time:1980}],window:120,end:Dashboard.prototype.end};
assert.deepEqual(Dashboard.prototype.visible.call(state),[{time:1980}],'The window advances with wall time even without fresh readings');
state.paused=true;state.pausedAt=1100;
assert.deepEqual(Dashboard.prototype.visible.call(state),[{time:1000}],'An explicitly paused window remains anchored to its pause time');
assert.equal(segments(gaps,cpu,t=>t,v=>v,10).length,2,'Downsampled history widens the gap threshold to its bucket size');
const helperTime=[];
assert(append(helperTime,{timestamp:500,telemetry:{t:499.5,sample_age_s:.5,timestamp:'1970-01-01T00:00:00Z',cpu_util_percent:1}}));
assert.equal(helperTime[0].time,499.5,"The helper's wall-clock time of the reading wins over the sensor's derived timestamp");
assert(!append(helperTime,{timestamp:501,telemetry:{t:499.5001,sample_age_s:1.5,cpu_util_percent:1}}),'The same reading polled again is not a new sample');

// Clock-time axis: real local times, with the date where a range is not today or crosses midnight.
const local=(...parts)=>new Date(...parts).getTime()/1000;
const clock=(t,seconds)=>new Date(t*1000).toLocaleTimeString(undefined,seconds?{hour:'2-digit',minute:'2-digit',second:'2-digit'}:{hour:'2-digit',minute:'2-digit'});
const dateOf=t=>new Date(t*1000).toLocaleDateString(undefined,{month:'short',day:'numeric'});
const today=local(2026,10,18,14,30,0);
assert.deepEqual(axisLabels(today-120,120,today),[0,30,60,90,120].map(s=>clock(today-120+s,true)),'Short windows label every tick with a clock time to the second');
assert(axisLabels(today-120,120,today).every(label=>!/ago|Now/.test(label)));
const midnight=local(2026,10,18,0,0,0),overnight=axisLabels(midnight-3*3600,6*3600,midnight+3*3600);
assert.deepEqual(overnight,[dateOf(midnight-3*3600)+' '+clock(midnight-3*3600,false),clock(midnight-5400,false),dateOf(midnight)+' '+clock(midnight,false),clock(midnight+5400,false),clock(midnight+3*3600,false)],
  'A window across midnight dates its first tick and the first tick of the new day');
const yesterday=axisLabels(today-86400-120,120,today);
assert(yesterday[0].startsWith(dateOf(today-86400)+' ') && !yesterday[1].includes(dateOf(today-86400)),'A range that is not today names its date once');
assert.equal(stamp(today-60,today),clock(today-60,true));
assert.equal(stamp(today-86400,today),dateOf(today-86400)+' '+clock(today-86400,true),'An inspected reading from another day shows its date');
assert.deepEqual(toPoint({t:5,n:3,max:{gpu_w:9},gpu_w:4}),{time:5,telemetry:{t:5,n:3,max:{gpu_w:9},gpu_w:4},n:3,max:{gpu_w:9}});
// CPU temperature names what the reading is; a non-package source is never shown as plain "CPU temperature".
const cpuTempLabel=t=>catalog(t).find(m=>m.key==='cpu_temp_c');
assert.equal(cpuTempLabel({}).label,'CPU temperature');
assert.equal(cpuTempLabel({}).title,undefined);
assert.equal(cpuTempLabel({cpu_temp_source:'windows_hwinfo_shared_memory:CPU Package'}).label,'CPU temperature');
assert.equal(cpuTempLabel({cpu_temp_source:'windows_hwinfo_shared_memory:CPU Package'}).title,'Source: windows_hwinfo_shared_memory:CPU Package');
assert.equal(cpuTempLabel({cpu_temp_source:'windows_acpi_thermal_zone:\\_TZ.TZ00'}).label,'CPU temperature (ACPI zone)');
assert.equal(cpuTempLabel({cpu_temp_source:'windows_coretemp_shared_memory:hottest_core'}).label,'CPU temperature (hottest core)');
console.log('System dashboard sensor scopes, units, history retention, gaps and clock-time axes passed');
