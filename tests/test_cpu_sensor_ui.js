const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/app.js','utf8').replace(/\ninit\(\);\s*$/, '');
const node = {hidden:true,dataset:{},innerHTML:'',querySelectorAll:()=>[]};
const nodes = {'#cpu-sensor-setup':node};
const context = {Charts:{fmtNum:String}, document:{hidden:false,querySelector:s=>nodes[s],querySelectorAll:()=>[]},window:{},setTimeout:()=>1,clearTimeout:()=>{},confirm:()=>true};
vm.createContext(context);vm.runInContext(source,context);
const markup = (status,telemetry={}) => vm.runInContext('cpuSensorMarkup',context)(status,telemetry);
(async()=>{
  // Not Windows: never offered.
  assert.equal(markup({supported:false,phase:'idle'},{cpu_temp_c:null}),'');
  // Windows without any CPU reading: the one-click offer, which names the driver and the approval.
  const offer=markup({supported:true,installed:false,phase:'idle'},{cpu_temp_c:null});
  assert.match(offer,/data-cpu-sensor="install"/);
  assert.match(offer,/Enable CPU temperature/);
  assert.match(offer,/PawnIO/);
  assert.match(offer,/administrator approval once/);
  assert.match(offer,/remove it here at any time/);
  // Another tool already provides a reading: nothing to offer.
  assert.equal(markup({supported:true,installed:false,phase:'idle'},{cpu_temp_c:52,cpu_temp_source:'windows_hwinfo_shared_memory:CPU Package'}),'');
  // In progress: no buttons, a clear instruction.
  assert.match(markup({supported:true,phase:'waiting_for_approval'},{}),/Approve the Windows administrator prompt/);
  assert.doesNotMatch(markup({supported:true,phase:'downloading'},{}),/<button/);
  // Installed and reading: says where the value comes from and offers removal.
  const installed=markup({supported:true,installed:true,phase:'done',message:'CPU temperature is enabled.'},{cpu_temp_c:44,cpu_temp_source:'windows_wattever_pawnio:intel_package'});
  assert.match(installed,/read by the Wattever sensor/);
  assert.match(installed,/data-cpu-sensor="uninstall"/);
  assert.match(installed,/CPU temperature is enabled/);
  // Installed but failing: the sensor's own reason, escaped.
  assert.match(markup({supported:true,installed:true,phase:'idle'},{cpu_temp_c:null,cpu_temp_note:'The Wattever CPU sensor reports: <b>x</b>'}),/&lt;b&gt;x&lt;\/b&gt;/);
  // Declined UAC is reported and the offer stays available.
  const declined=markup({supported:true,installed:false,phase:'cancelled',message:'Administrator approval was declined; nothing was changed.'},{cpu_temp_c:null});
  assert.match(declined,/nothing was changed/);
  assert.match(declined,/data-cpu-sensor="install"/);
  // The panel asks the helper and shows the offer; browser mode never asks.
  let calls=0;context.apiResult={supported:true,installed:false,phase:'idle'};
  vm.runInContext('api=async(path)=>{globalThis.calls=(globalThis.calls||0)+1;return apiResult;}',context);
  await vm.runInContext('updateCpuSensorSetup({cpu_temp_c:null})',context);
  assert.equal(node.hidden,false);
  assert.match(node.innerHTML,/Enable CPU temperature/);
  calls=context.calls;
  await vm.runInContext('updateCpuSensorSetup({cpu_temp_c:null})',context);
  assert.equal(context.calls,calls,'Status is cached between one-second samples');
  console.log('CPU sensor setup is offered only when needed, explains itself and reports each outcome');
})().catch(error=>{console.error(error);process.exitCode=1});
