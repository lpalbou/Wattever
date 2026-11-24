// Optional end-to-end check: Node 22+ and a local Edge/Chrome. No npm packages.
// WATTEVER_BROWSER can select a browser executable on any platform.
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');
const {pendingCommand,rejectPending}=require('./browser_deadlines.cjs');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

(async () => {
  const browserPath = process.env.WATTEVER_BROWSER || [
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/usr/bin/google-chrome', '/usr/bin/chromium',
  ].find(candidate => fs.existsSync(candidate));
  assert(browserPath, 'Set WATTEVER_BROWSER to an installed Chrome/Edge executable.');
  const artifacts = fs.mkdtempSync(path.join(os.tmpdir(), 'wattever-ui-'));
  const fixture = spawn(process.env.PYTHON || 'python', ['-u', path.join(__dirname, 'ui_fixture.py')], {windowsHide: true});
  fixture.stderr.on('data', data => process.stderr.write(data));
  let browser, ws;
  const pending = new Map();
  try {
    const port = await new Promise((resolve, reject) => {
      fixture.stdout.once('data', data => resolve(Number(String(data).trim())));
      fixture.once('error', reject);
      fixture.once('exit', code => reject(Error('Fixture exited: ' + code)));
    });
    const browserArgs = ['--headless=new', '--no-first-run', '--disable-gpu',
      '--remote-debugging-port=0', '--user-data-dir=' + path.join(artifacts, 'profile'), 'about:blank'];
    // Hosted Linux runners can prohibit Chrome's user-namespace sandbox.
    // This flag is confined to the isolated CI fixture, never the application.
    if (process.env.CI && process.platform === 'linux') browserArgs.unshift('--no-sandbox');
    let browserErrors = '';
    browser = spawn(browserPath, browserArgs, {windowsHide: true, stdio: ['ignore', 'ignore', 'pipe']});
    browser.stderr.on('data', chunk => { browserErrors = (browserErrors + chunk).slice(-4000); });
    browser.on('error', error => { browserErrors += String(error); });
    const debugFile = path.join(artifacts, 'profile', 'DevToolsActivePort');
    let debugPort;
    for (let attempt = 0; attempt < 450 && !debugPort; attempt++) {
      if (browser.exitCode !== null) throw Error('Browser exited before startup: ' + browserErrors);
      try { const value=Number(fs.readFileSync(debugFile,'utf8').split('\n')[0]); if(Number.isInteger(value) && value>0 && value<65536) debugPort=value; }
      catch(error) { if(!['ENOENT','EBUSY','EACCES','EPERM'].includes(error.code)) throw error; }
      if(debugPort) break;
      await delay(100);
    }
    if (!debugPort) throw Error('Browser did not expose its debugging port: ' + browserErrors);
    const pages = await (await fetch(`http://127.0.0.1:${debugPort}/json/list`)).json();
    ws = new WebSocket(pages.find(page => page.type === 'page').webSocketDebuggerUrl);
    await new Promise(resolve => ws.addEventListener('open', resolve, {once: true}));
    let next = 0;
    const errors = [];
    ws.addEventListener('close',()=>rejectPending(pending,Error('Owned UI browser closed')));
    ws.addEventListener('error',()=>rejectPending(pending,Error('Owned UI browser socket failed')));
    ws.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if (message.id) {
        const request = pending.get(message.id);
        if (request) {
          pending.delete(message.id);
          if (message.error) request.reject(Error(JSON.stringify(message.error)));
          else request.resolve(message.result);
        }
      }
      if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails);
    });
    const send = (method, params = {}) => {
      const id = ++next;
      return pendingCommand(pending,id,method,()=>ws.send(JSON.stringify({id,method,params})),60000);
    };
    const evaluate = async expression => {
      const result = await send('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
      if (result.exceptionDetails) throw Error(JSON.stringify(result.exceptionDetails));
      return result.result.value;
    };
    const check = async (expression, description) => assert(await evaluate(expression), description);
    const clickValue = (selector, value) => evaluate(`Array.from(document.querySelectorAll(${JSON.stringify(selector)})).find(input => Number(input.value) === ${value}).click()`);
    const screenshot = async name => {
      const shot = await send('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false});
      fs.writeFileSync(path.join(artifacts, name + '.png'), Buffer.from(shot.data, 'base64'));
    };
    await send('Runtime.enable');
    await send('Page.enable');
    await send('Emulation.setDeviceMetricsOverride', {width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false});
    await send('Page.navigate', {url: `http://127.0.0.1:${port}`});
    for (let attempt = 0; attempt < 100; attempt++) {
      if (await evaluate('typeof state !== "undefined" && !!state.env && !!document.querySelector("#run-detail h3")')) break;
      await delay(100);
    }
    await check('!!document.querySelector("#run-detail h3")', 'UI boots');
    // The page opens without contacting any inference engine: nothing is listed until Connect is clicked.
    await check('(async()=>{const asked=await(await fetch("/test/env-requests")).json();return asked.length===1 && asked[0].connect===false;})()', 'Opening the page asks for the environment without connecting to an engine');
    await check('state.engine.status==="off" && document.querySelector("#pill-lms span:last-child").textContent==="No engine connected" && !document.querySelector("#pill-lms .dot").classList.contains("on") && !document.querySelector("#pill-lms .dot").classList.contains("off")', 'The header says no engine is connected, without an error colour');
    await check('document.querySelector("#helper-status").textContent==="Not connected" && document.querySelector("#helper-message").hidden && !/LM Studio|model/i.test(document.querySelector("#env-banners").textContent)', 'No warning about an engine nobody asked for: '+await evaluate('document.querySelector("#env-banners").textContent'));
    await check('!document.querySelector("#model-list .model") && document.querySelector("#model-list .empty-note").textContent.startsWith("No inference engine connected") && document.querySelector("#start-btn").disabled && document.querySelector("#plan-summary").textContent.startsWith("Connect an inference engine")', 'The model list explains that an engine must be connected first');
    await check('!document.querySelector("[data-loading]") && !document.querySelector(".busy-veil") && !document.querySelector("#panel-run").inert', 'No loading veil remains once the page has its data');
    await check('document.querySelector("#run-condition-badges").textContent.trim()===""', 'No engine is named before one is connected: '+await evaluate('document.querySelector("#run-condition-badges").textContent'));
    await screenshot('not-connected-desktop');
    // LM Studio selected but not started: that is not "no models".
    await evaluate('fetch("/test/lmstudio-down",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({enabled:true})}).then(()=>document.querySelector("#helper-connect").click())');
    for (let attempt = 0; attempt < 80 && !await evaluate('state.engine.status==="error"'); attempt++) await delay(50);
    await check('state.engine.status==="error" && /^LM Studio.s local server is not answering/.test(document.querySelector("#helper-message-text").textContent) && !document.querySelector("#helper-message").hidden && document.querySelector("#helper-message").dataset.tone==="error" && document.querySelector("#helper-status").textContent==="Connection failed" && !document.querySelector("#helper-action").hidden && !/no models indexed|Download at least|started automatically/i.test(document.querySelector("#panel-run").textContent)', 'LM Studio that is not running is reported as not running, not as having no models: '+await evaluate('document.querySelector("#helper-message").textContent'));
    await check('document.querySelector("#model-list .empty-note").textContent.startsWith("No models to choose from yet") && document.querySelector("#pill-lms span:last-child").textContent==="No engine connected" && !document.querySelector("#helper-connect").disabled', 'A failed connection leaves the page not connected and Connect usable');
    await screenshot('lmstudio-not-running-desktop');
    // Starting LM Studio's server is offered, and happens only on that click.
    await evaluate('document.querySelector("#helper-action").click()');
    for (let attempt = 0; attempt < 80 && !await evaluate('state.engine.status==="on" && state.models.size>0'); attempt++) await delay(50);
    await check('state.engine.status==="on" && document.querySelector("#pill-lms span:last-child").textContent==="LM Studio connected" && document.querySelector("#pill-lms .dot").classList.contains("on") && document.querySelector("#helper-status").textContent==="Connected · LM Studio · 12 models" && document.querySelector("#helper-message").hidden && document.querySelectorAll("#model-list .model").length===12 && !document.querySelector("[data-loading]")', 'Connect lists the engine models and the header names the connected engine: '+await evaluate('document.querySelector("#helper-status").textContent+" | "+document.querySelector("#helper-message").textContent+" | "+JSON.stringify(state.engine)'));
    await check('document.querySelector("#panel-run").classList.contains("active")', 'Run is the default screen');
    await check('(async()=>{const info=await(await fetch("/api/version")).json();return document.querySelector("#app-version").textContent === "v"+info.version})()', 'Version comes from the server');
    await check('!document.querySelector("#machine-card").open && !document.querySelector("#advanced-settings").open', 'Details start collapsed');
    await check('!document.querySelector("#size-quick, #concurrency-quick, #cpu-tdp, #other-idle")', 'No duplicate shortcuts or power inputs');
    await check('document.querySelector("#run-label").value === "RTX 5060 Ti" && [...document.querySelectorAll("#machine-badges .machine-header-item small")].map(node=>node.textContent).join() === "CPU,GPU,RAM,VRAM" && [...document.querySelectorAll("#machine-badges .machine-header-item strong")].map(node=>node.textContent).join().includes("Smoke,RTX 5060 Ti,32 GiB,16 GiB")', 'Collapsed header contains structured CPU, GPU, RAM and VRAM values');
    await evaluate('showPanel("system")');
    await check('!document.querySelector("#machine-card").open && [...document.querySelectorAll("#machine-badges .machine-header-item")].every(node=>node.getBoundingClientRect().width>0 && node.getBoundingClientRect().height>0) && document.querySelector("#machine-badges .machine-header-item").title==="Smoke CPU" && document.querySelector("#machine-name").parentElement.title.includes("NVIDIA GeForce RTX 5060 Ti")', 'All four machine values are visible while collapsed and full processor/card names remain accessible');
    await evaluate('state.env.hardware.gpus.push({name:"NVIDIA GeForce RTX 5070 Ti",sensor_index:1,memory_bytes:16*1073741824}); renderHardware()');
    await check('document.querySelector("#machine-name").textContent.includes("RTX 5060 Ti") && document.querySelector("#machine-name").textContent.includes("RTX 5070 Ti") && document.querySelectorAll("#machine-name .machine-device").length===2 && document.querySelectorAll("#machine-memory .machine-device").length===2 && document.querySelector("#machine-memory").parentElement.textContent.includes("VRAM / card") && document.querySelectorAll("#gpu-index option").length===2', 'Both cards and separate memory rows are visible in the summary and power-sensor selector');
    await evaluate('state.env.gpu_setup={strata:{available:true,choice:{mode:"auto"}}};renderHardware();document.querySelector("#machine-card").open=true;document.querySelector("#strata-gpu-choice").value="1";document.querySelector("#strata-gpu-choice").dispatchEvent(new Event("change"))');
    for(let attempt=0;attempt<100;attempt++){if(await evaluate('document.querySelector("#gpu-setup-status").textContent.startsWith("Saved.")'))break;await delay(30)}
    await check('state.env.gpu_setup.strata.choice.mode==="single" && state.env.gpu_setup.strata.choice.gpu_index===1 && !document.querySelector("#strata-gpu-choice").disabled && document.querySelector("#gpu-setup-status").textContent.includes("Strata Control")', 'Strata card selection saves a next-desktop-load choice without model lifecycle commands');
    await evaluate('document.querySelector(".gpu-setup").scrollIntoView({block:"center"})');
    await screenshot('strata-gpu-setup-desktop');
    await evaluate('showPanel("system");pollSystem(systemPollGeneration)');
    for(let attempt=0;attempt<100;attempt++){if(await evaluate('document.querySelectorAll("#system-stats .gpu-live-card").length===2'))break;await delay(30)}
    await check('document.querySelectorAll("#system-stats .gpu-live-card").length===2 && document.querySelector("#system-stats").textContent.includes("190") && document.querySelectorAll("#system-stats .gpu-live-card")[1].textContent.includes("70 W")', 'System shows combined and separate per-card telemetry without a benchmark');
    for(let attempt=0;attempt<60 && !await evaluate('systemDashboard?.points.length>=2');attempt++) await delay(100);
    await check('systemDashboard.points.length>=2 && document.querySelectorAll(".observatory-gauge[data-gauge]").length===6 && document.querySelector("[data-gauge=cpu_util_percent]").textContent.includes("20") && document.querySelector("[data-gauge=system_memory_percent]").textContent.includes("25") && document.querySelector(".observatory-plot svg path")', 'Live samples build history and gauges display CPU and RAM readings');
    await evaluate('document.querySelector("[data-preset=power]").click()');
    await check('[...document.querySelectorAll(".observatory-metrics input:checked")].map(x=>x.value).sort().join() === "gpu_w,system_w_est" && document.querySelector(".observatory-plot h4").textContent==="W"', 'Power preset selects measured and estimated power on their shared unit axis');
    await evaluate('document.querySelector("[data-history-scale]").value="relative";document.querySelector("[data-history-scale]").dispatchEvent(new Event("change"))');
    await check('document.querySelector(".observatory-plot h4").textContent.includes("visible peak") && document.querySelector("[data-history-note]").textContent.includes("not physical magnitudes")', 'Relative overlay explains its normalization');
    await evaluate('document.querySelector("[data-preset=thermal]").click()');
    await check('[...document.querySelectorAll(".observatory-metrics input:checked")].map(x=>x.value).sort().join() === "cpu_temp_c,temp_c"', 'Temperature preset selects both temperature sensors');
    await evaluate('document.querySelector("[data-preset=none]").click()');
    await check('document.querySelectorAll(".observatory-metrics input:checked").length===0 && !!document.querySelector(".history-empty")', 'Clear preset removes the plotted selection');
    await evaluate('document.querySelector("[data-gauge=cpu_util_percent]").click()');
    await check('document.querySelector(".observatory-metrics input[value=cpu_util_percent]").checked && document.querySelector("[data-gauge=cpu_util_percent]").getAttribute("aria-pressed")==="true"', 'A gauge toggles its corresponding history metric');
    await evaluate('document.querySelector("[data-preset=load]").click();document.querySelector("[data-history-window]").value="60";document.querySelector("[data-history-window]").dispatchEvent(new Event("change"));document.querySelector("[data-history-pause]").click();window.__pausedHistoryLength=systemDashboard.points.length');
    await delay(1200);
    await check('systemDashboard.points.length===window.__pausedHistoryLength && document.querySelector("[data-history-pause]").textContent.includes("Resume") && document.querySelector("[data-history-note]").textContent.includes("gauges remain live")', 'Pause freezes history while preserving live gauges');
    await evaluate('document.querySelector("[data-history-scrub]").value="0";document.querySelector("[data-history-scrub]").dispatchEvent(new Event("input"))');
    await check('document.querySelector("[data-history-time]").textContent.includes("Inspecting") && !!document.querySelector(".history-cursor")', 'History scrub selects an earlier sample');
    await evaluate('document.querySelector("[data-history-live]").click()');
    await check('document.querySelector("[data-history-time]").textContent.includes("Latest") && !systemDashboard.paused && document.querySelector("[data-history-pause]").textContent.includes("Pause")', 'Follow live resumes and returns history to live mode');
    await check('systemDashboard.points[0].time < Date.now()/1000 - 300 && systemDashboard.liveSince !== null', 'Opening System backfills readings the helper recorded while nobody watched');
    await check('[...document.querySelectorAll(".observatory-plot svg text[y=\'205\']")].length===5 && [...document.querySelectorAll(".observatory-plot svg text[y=\'205\']")].every(t=>/\\d:\\d\\d:\\d\\d/.test(t.textContent) && !/ago|Now/.test(t.textContent)) && !document.querySelector("#system-status").textContent.includes(" s old") && document.querySelector("#system-status").textContent.includes("Recording continuously")', 'History ticks and status name clock times, not relative ages');
    await evaluate('document.querySelector("[data-history-window]").value="86400";document.querySelector("[data-history-window]").dispatchEvent(new Event("change"))');
    for(let attempt=0;attempt<60 && !await evaluate('systemDashboard.series?.window===86400');attempt++) await delay(50);
    await check('systemDashboard.series.bucket===120 && document.querySelector(".observatory-plot svg path") && document.querySelector("[data-history-note]").textContent.includes("averages up to 120 s") && [...document.querySelectorAll(".observatory-plot svg text[y=\'205\']")].every(t=>!/\\d:\\d\\d:\\d\\d/.test(t.textContent))', 'A 24-hour window draws averaged recorded history with minute ticks');
    await evaluate('document.querySelector("[data-history-window]").value="3600";document.querySelector("[data-history-window]").dispatchEvent(new Event("change"));document.querySelector("[data-history-earlier]").click();document.querySelector("[data-history-earlier]").click();document.querySelector("[data-history-earlier]").click();document.querySelector("[data-history-earlier]").click()');
    for(let attempt=0;attempt<60 && !await evaluate('systemDashboard.series?.end===systemDashboard.end() && !systemDashboard.loading');attempt++) await delay(50);
    await check('systemDashboard.paused && Math.abs(systemDashboard.end() - (Date.now()/1000 - 7200)) < 30 && document.querySelector(".observatory-plot svg path") && document.querySelector("[data-history-pause]").textContent.includes("Resume")', 'Earlier steps back through the recorded hours');
    await evaluate('document.querySelector("[data-history-window]").value="120";document.querySelector("[data-history-window]").dispatchEvent(new Event("change"));document.querySelector("[data-history-live]").click()');
    await check('!systemDashboard.paused && systemDashboard.useLive()', 'Follow live returns from an earlier time to the live page buffer');
    await evaluate('document.querySelector(".observatory-gauges").scrollIntoView({block:"start"});window.scrollBy(0,-100)');
    await screenshot('system-observatory-desktop');
    await screenshot('two-gpu-live-desktop');
    await send('Emulation.setDeviceMetricsOverride', {width:390,height:844,deviceScaleFactor:1,mobile:true});
    await evaluate('document.querySelector("#system-stats .gpu-live-grid").scrollIntoView({block:"start"});window.scrollBy(0,-100)');
    await check('document.documentElement.scrollWidth<=innerWidth', 'Per-card telemetry fits phone width');
    await evaluate('document.querySelector(".observatory-gauges").scrollIntoView({block:"start"});window.scrollBy(0,-110)');
    await check('document.documentElement.scrollWidth<=innerWidth && [...document.querySelectorAll(".observatory-gauge")].every(el=>el.getBoundingClientRect().width>0)', 'System gauges fit and remain visible on a phone');
    await screenshot('system-observatory-phone');
    await screenshot('two-gpu-live-phone');
    await send('Emulation.setDeviceMetricsOverride', {width:1440,height:1000,deviceScaleFactor:1,mobile:false});
    await evaluate('document.querySelector("#live-card").classList.add("hidden")');
    await evaluate('delete state.env.gpu_setup;renderHardware();document.querySelector("#machine-card").open=false;window.scrollTo(0,0)');
    await screenshot('run-two-gpus-desktop');
    await send('Emulation.setDeviceMetricsOverride', {width:390,height:844,deviceScaleFactor:1,mobile:true});
    await check('document.documentElement.scrollWidth<=innerWidth && [...document.querySelectorAll("#machine-name .machine-device, #machine-memory .machine-device")].every(node=>node.getBoundingClientRect().width>0 && node.getBoundingClientRect().height>0)', 'Both GPU names and capacities fit the collapsed phone header');
    await screenshot('run-two-gpus-phone');
    await send('Emulation.setDeviceMetricsOverride', {width:1440,height:1000,deviceScaleFactor:1,mobile:false});
    await evaluate('state.env.hardware.gpus.pop(); renderHardware()');
    await check('document.querySelector("#run-notes").value.includes("Reasoning: off")', 'Settings notes are filled automatically');
    await evaluate('showPanel("run")');
    await clickValue('#concurrency-chips input', 5);
    await check('[...state.concurrencyLevels].join() === "1,5"', 'Five adds only five');
    await check('document.querySelector("#run-notes").value.includes("Simultaneous queries: 1, 5")', 'Notes follow workload changes');
    await clickValue('#concurrency-chips input', 2);
    await check('[...state.concurrencyLevels].join() === "1,5,2"', 'Two adds only two and preserves five');
    await clickValue('#size-chips input', 150000);
    await check('!state.sizes.has(120000) && state.sizes.has(150000) && !state.sizes.has(200000)', 'Large context selection does not add intermediate sizes');
    await clickValue('#size-chips input', 20000);
    await check('!state.sizes.has(20000) && state.sizes.has(150000) && state.sizes.has(40000)', 'One selected context can be removed independently');
    await evaluate('Array.from(document.querySelectorAll("#concurrency-chips input")).find(input => Number(input.value) === 3).focus()');
    await send('Input.dispatchKeyEvent', {type: 'keyDown', key: ' ', code: 'Space'});
    await send('Input.dispatchKeyEvent', {type: 'keyUp', key: ' ', code: 'Space'});
    await check('[...state.concurrencyLevels].join() === "1,5,2,3"', 'Keyboard toggles one independent selection');
    await evaluate('selectModels("none")');
    await check('state.models.size === 0', 'None stays empty');
    await evaluate('selectModels("all")');
    await check('state.models.size === state.env.models.length', 'All selects all models');
    await evaluate('document.querySelector("#run-label").value="Custom name"; document.querySelector("#run-label").dispatchEvent(new Event("input")); document.querySelector("#run-notes").value="Custom notes"; document.querySelector("#run-notes").dispatchEvent(new Event("input"))');
    await clickValue('#concurrency-chips input', 2);
    await check('document.querySelector("#run-label").value === "Custom name" && document.querySelector("#run-notes").value === "Custom notes"', 'Custom text is preserved');
    await evaluate('document.querySelector("#run-notes").value=""; document.querySelector("#run-notes").dispatchEvent(new Event("blur"))');
    await check('document.querySelector("#run-notes").value.includes("Simultaneous queries: 1, 3, 5")', 'Clearing notes restores automatic text');
    await evaluate('document.querySelector("#clean-run").click()');
    await check('[...state.concurrencyLevels].join() === "1" && state.models.size === state.env.models.length', 'Clean reset works without a prompt');
    await check('document.querySelector("#run-label").value.includes("RTX 5060 Ti") && document.querySelector("#run-notes").value.includes("all 12 installed models")', 'Reset regenerates metadata');
    await check('[...document.querySelectorAll("input,select,textarea")].every(input => !!input.title)', 'Every field has a tooltip');
    await check('document.documentElement.scrollWidth <= innerWidth', 'Desktop has no horizontal overflow');
    await check('Math.abs(document.querySelector(".models-card").getBoundingClientRect().height - document.querySelector(".workload-stack").getBoundingClientRect().height) < 2', 'Setup columns have equal height');
    await evaluate(`{
      const hw=state.env.hardware;
      Object.assign(hw.cpu,{physical_cores:6,logical_cores:12});
      hw.memory={installed_capacity_bytes:32*1073741824,module_count:4,configured_clock_mhz_reported:[2133]};
      hw.storage=[{model:'Fixture SATA SSD',capacity_bytes:4000000000000,media_type:'SSD',bus_type:'SATA'},
        {model:'Fixture NVMe',capacity_bytes:256000000000,media_type:'SSD',bus_type:'NVMe'},
        {model:'Fixture HDD',capacity_bytes:10000000000000,media_type:'HDD',bus_type:'SATA'}];
      hw.motherboard={manufacturer:'Fixture vendor',model:'Fixture board'};hw.firmware={version:'1.0'};
      const sample=(rate,extra={})=>({status:'ok',bytes_per_second:rate,samples:[{},{},{}],note:'Simulated fixture measurement',...extra});
      hw.transfer_bandwidth={captured_at:'2026-11-01T12:00:00+00:00',protocol:'fixture',
        storage_to_ram:sample(540000000,{volume:'C:',device_model:'Fixture SATA SSD',bus_type:'SATA'}),
        storage_volumes:[sample(2800000000,{volume:'F:',device_model:'Fixture NVMe',bus_type:'NVMe'})],
        ram_copy_single:sample(9000000000,{workers:1}),ram_copy_parallel:sample(20000000000,{workers:6}),ram_to_vram:sample(6500000000,{host_memory:'pinned'})};
      state.runCache.smoke.hardware=JSON.parse(JSON.stringify(hw));state.detailSignature=null;renderHardware();
    }`);
    await screenshot('run-desktop');
    await evaluate('document.querySelector("#machine-card summary").click()');
    await check('document.querySelector("#machine-card").open', 'Machine details expand');
    await check('[...document.querySelectorAll("#hardware-inventory .hardware-section h4")].map(node=>node.textContent).join() === "Compute & memory,Storage inventory,Transfer measurements,Platform" && document.querySelectorAll("#hardware-inventory .hardware-tile").length>=12 && !!document.querySelector("#hardware-inventory .hardware-visual > .nvme-coverage") && !document.querySelector("#hardware-inventory .hardware-visual > p:not(.nvme-coverage)")', 'Expanded inventory uses shared grouped compute, storage, transfer and platform tiles without a paragraph wall: '+JSON.stringify(await evaluate('({headings:[...document.querySelectorAll("#hardware-inventory .hardware-section h4")].map(x=>x.textContent),tiles:document.querySelectorAll("#hardware-inventory .hardware-tile").length})')));
    await check('[...document.querySelectorAll("#hardware-inventory .storage-inventory .hardware-tile-label")].map(node=>node.textContent).join() === "SSD,NVMe,HDD" && document.querySelector("#hardware-inventory .transfer-grid").textContent.includes("0.54 GB/s") && document.querySelector("#hardware-inventory .transfer-grid").textContent.includes("2.80 GB/s") && !document.querySelector("#storage-probe-field").open', 'Reported drive types and measured rates remain explicit while optional probe settings stay collapsed');
    await evaluate('showPanel("system");document.querySelector("#machine-card").scrollIntoView({block:"start"});window.scrollBy(0,-90)');
    await screenshot('run-machine-expanded-desktop');
    await evaluate('document.querySelector("#hardware-inventory .transfer-profile").scrollIntoView({block:"start"});window.scrollBy(0,-90)');
    await screenshot('run-transfers-desktop');
    await evaluate('showPanel("run")');
    await check('!document.querySelector("#advanced-settings").open && !document.querySelector("#advanced-settings").contains(document.querySelector("#mtp-chips")) && document.querySelector("#mtp-chips").getBoundingClientRect().height>0 && [...document.querySelectorAll("#mtp-chips input")].map(i=>i.value).join()==="off,on,both" && [...document.querySelectorAll("#mtp-chips input")].every(i=>!i.disabled)', 'MTP Off / On / Compare both is visible without opening Advanced settings');
    await evaluate('document.querySelector("#mtp-chips input[value=both]").click()');
    await check('collectConfig().mtp_modes.join()==="off,on" && [...document.querySelectorAll("#mtp-chips .chip")].map(c=>c.dataset.on).join()==="0,0,1"', 'Compare both schedules a standard and an MTP pass');
    await evaluate('document.querySelector("#mtp-chips input[value=on]").click()');
    await check('collectConfig().mtp_modes.join()==="on"', 'On schedules the MTP pass only');
    await evaluate('document.querySelector("#helper-provider").value="openai";document.querySelector("#helper-provider").dispatchEvent(new Event("change"))');
    await check('!document.querySelector("#mtp-field").hidden && document.querySelector("#mtp-chips").getBoundingClientRect().height>0 && [...document.querySelectorAll("#mtp-chips input")].every(i=>!i.disabled) && document.querySelector("#mtp-note").textContent.includes("LM Studio (managed)") && collectConfig().mtp_modes.join()==="on"', 'With an endpoint the MTP choice stays selectable and the request is sent for the helper to resolve');
    await check('mtpPlan().passes.length===0 && document.querySelector("#plan-summary").textContent.includes("MTP-only benchmark cannot run") && document.querySelector("#start-btn").disabled', 'MTP only on an endpoint says why nothing can run instead of starting an empty benchmark');
    await evaluate('document.querySelector("#mtp-chips input[value=both]").click()');
    await check('mtpPlan().passes.join()==="off" && collectConfig().mtp_modes.join()==="off,on" && document.querySelector("#plan-summary").textContent.includes("MTP pass not run")', 'Compare both on an endpoint plans the standard pass and announces the MTP pass that will not run: ' + JSON.stringify(await evaluate('document.querySelector("#plan-summary").textContent')));
    await evaluate('document.querySelector("#helper-provider").value="lmstudio";document.querySelector("#helper-provider").dispatchEvent(new Event("change"));state.providerNeedsConnection=false;document.querySelector("#mtp-chips input[value=off]").click()');
    await check('[...document.querySelectorAll("#mtp-chips input")].every(i=>!i.disabled) && collectConfig().mtp_modes.join()==="off" && !document.querySelector("#mtp-note").textContent.includes("LM Studio (managed)")', 'Returning to LM Studio re-enables the MTP choice');
    await evaluate('document.querySelector("#machine-card summary").click();showPanel("run"); document.querySelector("#advanced-settings summary").click()');
    await check('document.querySelector("#advanced-settings").open', 'Advanced settings expand');
    await evaluate('document.querySelector("#advanced-settings summary").click(); showPanel("results")');
    await delay(150);
    await check('document.querySelectorAll(".topbar .tab").length === 5 && state.resultView === "summary"', 'System, Benchmarks, Results, Capabilities and Share are available; Overview opens first');
    await check('[...document.querySelectorAll("#result-navigation [data-result-view]")].map(button=>button.textContent.trim()).join() === "Overview,Graphs & comparisons,Tables & CSV" && !!(document.querySelector("#result-navigation").compareDocumentPosition(document.querySelector("#run-detail")) & Node.DOCUMENT_POSITION_FOLLOWING)', 'Overview, graphs and exportable tables navigation appears before result content');
    await check('document.querySelector("#panel-summary").contains(document.querySelector("#run-detail")) && document.querySelector("#results-controls").hidden && !document.querySelector("#summary-body .plot, #summary-body svg[role=img]")', 'Overview owns benchmark metadata without chart controls');
    await check('!document.querySelector("#result-navigation select, #results-controls select, #panel-compare select, #panel-summary select")', 'Results offers no drop-down menus');
    await check('document.querySelectorAll("#summary-body .ov-kpi").length===4 && document.querySelector("#summary-body .ov-kpi strong").textContent.includes("200") && document.querySelector("#summary-body .ov-kpi p").textContent.includes("model-c")', 'Overview leads with the fastest prefill and names its model: '+await evaluate('document.querySelector("#summary-body").innerText.slice(0,300)'));
    await check('document.querySelectorAll("#summary-body .ov-table tbody tr").length===3 && document.querySelector("#run-detail .ov-coverage").textContent.includes("3 models") && [...document.querySelectorAll("#summary-body .ov-table th")].some(cell=>cell.textContent==="With concurrent queries")', 'Overview lists every model with its coverage and concurrency column');
    await check('!/tests? passed/.test(document.querySelector("#runs-list").textContent) && [...document.querySelectorAll("#runs-list .run-item")].every(item=>item.querySelector(".counts .gpu-badge") && item.querySelector(".run-edit") && item.querySelector(".run-delete"))', 'Library cards describe the machine, say the test count once and offer Rename beside Delete: '+await evaluate('document.querySelector("#runs-list").innerText.slice(0,300)'));
    await check('[...document.querySelectorAll("#runs-list .run-item")].every(item=>{const edit=item.querySelector(".run-edit").getBoundingClientRect(),remove=item.querySelector(".run-delete").getBoundingClientRect(),card=item.getBoundingClientRect();return edit.bottom<=remove.top+1 && remove.bottom<=card.bottom+1 && edit.height>0;})', 'Rename and Delete stack inside every library card without overlapping');
    await evaluate('(async()=>{window.__kept=[new Set(state.compare),state.selected];state.compare.clear();state.selected=null;renderRunsList();await renderResultsView();})()');
    for(let attempt=0;attempt<80 && !await evaluate('!!document.querySelector("#run-detail .empty")');attempt++)await delay(50);
    await check('document.querySelector("#run-detail .empty") && (()=>{const card=document.querySelector(".benchmark-library").getBoundingClientRect(),list=document.querySelector("#runs-list").getBoundingClientRect();return card.bottom>=innerHeight-40-Math.max(0,scrollY) && list.height>=innerHeight*0.4;})()', 'With nothing selected the library still fills the window to browse recorded benchmarks: '+await evaluate('JSON.stringify([document.querySelector(".benchmark-library").getBoundingClientRect().bottom,document.querySelector("#runs-list").getBoundingClientRect().height,innerHeight])'));
    await screenshot('nothing-selected-desktop');
    await evaluate('(async()=>{state.compare=window.__kept[0];state.selected=window.__kept[1];renderRunsList();await renderResultsView();})()');
    await delay(100);
    // Saving re-reads the benchmark from the helper; keep the inventory this test placed in the page.
    const originalLabel = await evaluate('window.__placedHardware=state.runCache[state.runs[0].id]?.hardware,state.runs[0].label');
    await evaluate('document.querySelector("#runs-list .run-edit").click()');
    await check('document.querySelector("#meta-dialog").open && document.querySelector("#meta-label").value===state.runs[0].label && document.activeElement===document.querySelector("#meta-label")', 'Rename opens one dialog holding the current name');
    await screenshot('rename-dialog');
    await evaluate('document.querySelector("#meta-label").value="   ";document.querySelector("#meta-form").requestSubmit()');
    await delay(100);
    await check('document.querySelector("#meta-dialog").open && state.runs[0].label==='+JSON.stringify(originalLabel), 'An empty name is not saved');
    await evaluate('document.querySelector("#meta-label").value="Renamed <b>benchmark</b>";document.querySelector("#meta-notes").value="Two cards, 188k context";document.querySelector("#meta-form").requestSubmit()');
    for(let attempt=0;attempt<80 && !await evaluate('state.runs[0].label==="Renamed <b>benchmark</b>" && !document.querySelector("#meta-dialog").open');attempt++)await delay(50);
    await check('!document.querySelector("#meta-dialog").open && state.runs[0].notes==="Two cards, 188k context" && document.querySelector("#runs-list .run .t").textContent==="Renamed <b>benchmark</b>" && !document.querySelector("#runs-list .run .t b")', 'A saved name and description appear in the library, escaped');
    for(let attempt=0;attempt<80 && !await evaluate('state.selected!==state.runs[0].id || (document.querySelector("#run-detail h3")?.textContent==="Renamed <b>benchmark</b>" && document.querySelector("#run-detail .saved-notes")?.textContent==="Two cards, 188k context")');attempt++)await delay(50);
    await check('state.selected!==state.runs[0].id || (document.querySelector("#run-detail h3").textContent==="Renamed <b>benchmark</b>" && document.querySelector("#run-detail .saved-notes").textContent==="Two cards, 188k context")', 'The open benchmark shows its new name and description');
    for(let attempt=0;attempt<80 && !await evaluate('!!document.querySelector("#run-detail [data-edit-run], #runs-list .run-edit")');attempt++)await delay(50);
    await evaluate('document.querySelector("#run-detail [data-edit-run], #runs-list .run-edit").click()');
    await evaluate('document.querySelector("#meta-label").value='+JSON.stringify(originalLabel)+';document.querySelector("#meta-notes").value="";document.querySelector("#meta-form").requestSubmit()');
    for(let attempt=0;attempt<80 && !await evaluate('state.runs[0].label==='+JSON.stringify(originalLabel)+' && !document.querySelector("#meta-dialog").open');attempt++)await delay(50);
    await check('state.runs[0].label==='+JSON.stringify(originalLabel)+' && !state.runs[0].notes', 'The heading Rename button edits the same benchmark');
    // Saving redraws the heading last, after the benchmark is fetched again. Put the placed inventory back only then,
    // or that fetch replaces it.
    for(let attempt=0;attempt<160 && !await evaluate('state.selected!==state.runs[0].id || document.querySelector("#run-detail h3")?.textContent==='+JSON.stringify(originalLabel));attempt++)await delay(50);
    await evaluate('(async()=>{await ensureRuns([state.runs[0].id]);if(window.__placedHardware)state.runCache[state.runs[0].id].hardware=window.__placedHardware;state.detailSignature=null;await renderResultsView();})()');
    await evaluate('document.querySelector("#summary-body .ov-table tbody tr td:last-child .ov-link").click()');
    await delay(150);
    await check('state.resultView==="compare" && state.comparisonAxis==="concurrency" && state.visibleModels.size===1 && state.visibleModels.has("model-a") && state.metrics.has("ttft_s")', 'An overview figure opens its graph for that model and axis');
    await evaluate('state.visibleModels=null;state.metrics=new Set(["prefill_tps","decode_tps"]);state.comparisonAxis="context";showResultsView("summary")');
    await delay(100);
    await check('!document.querySelector("[data-compare-storage]") && !document.querySelector("#recorded-conditions") && ![...document.querySelectorAll("#chart-key [data-curve]")].some(entry=>entry.textContent.includes("NVMe"))', 'A benchmark without storage observations does not offer invented storage comparisons, and the conditions panel is gone');
    await evaluate('document.querySelector("#run-detail details").open=true');
    await check('document.querySelectorAll("#run-detail .hardware-visual .hardware-tile").length>=12 && [...document.querySelectorAll("#run-detail .hardware-section h4")].some(node=>node.textContent==="Storage inventory") && !!document.querySelector("#run-detail .hardware-visual > .nvme-coverage") && !document.querySelector("#run-detail .hardware-visual > p:not(.nvme-coverage)")', 'Saved Results uses the same grouped visual hardware inventory');
    await evaluate('document.querySelector("#run-detail details").open=false');
    await screenshot('summary-desktop');
    await evaluate('document.querySelector("#run-detail details").open=true;document.querySelector("#run-detail .telemetry-report").open=true');
    await delay(100);
    await check('document.querySelectorAll("#run-detail .telemetry-history svg[role=img]").length===2 && document.querySelector("#run-detail .telemetry-report").textContent.includes("95.0%") && document.querySelector("#run-detail .telemetry-report").textContent.includes("not proof of throttling")', 'Saved telemetry has coverage-aware summaries and usage/temperature histories: '+JSON.stringify(await evaluate('({count:document.querySelectorAll("#run-detail .telemetry-history svg[role=img]").length,text:document.querySelector("#run-detail .telemetry-report").textContent,open:document.querySelector("#run-detail .telemetry-report").open})'))+' errors='+JSON.stringify(errors));
    await evaluate('document.querySelector("#run-detail details").open=false');
    await evaluate('window.__originalLine=Charts.line;Charts.line=(container,options)=>{window.__lastSeries=options.series;return window.__originalLine(container,options)};showResultsView("compare")');
    await delay(100);
    const pressed = selector => `[...document.querySelectorAll(${JSON.stringify(selector)})].filter(node=>node.getAttribute("aria-pressed")==="true").map(node=>node.dataset.metric || node.dataset.comparisonAxis || node.dataset.level || node.dataset.colorBy).join()`;
    await check(`state.comparisonAxis === "context" && ${pressed("[data-comparison-axis]")} === "context" && !document.querySelector("#results-controls").hidden && document.querySelector("#panel-summary").hidden`, 'Graphs defaults to prompt length');
    await check(`document.querySelectorAll("#result-metrics button[data-metric]").length===Object.keys(METRICS).length && ${pressed("#result-metrics [data-metric]")} === "prefill_tps,decode_tps"`, 'Every metric is a toggle button; prefill and decode start ticked');
    await check('document.querySelectorAll("#compare-body .graph-card .plot svg").length===2 && [...document.querySelectorAll("#compare-body .graph-card h3")].map(node=>node.textContent).join()==="Prefill speed,Decode speed"', 'Each ticked metric draws its own chart, titled like its button');
    await check('document.querySelector("#result-metrics [data-metric=prefill_gpu_w]").dataset.empty==="1" && !document.querySelector("#result-metrics [data-metric=prefill_tps]").dataset.empty', 'Metrics that were not recorded are marked');
    await check(`${pressed("#result-levels [data-level]")} === "1" && [...document.querySelectorAll("#result-levels [data-level]")].map(node=>node.dataset.level).join()==="1,2,4,8" && !document.querySelector("#rc-levels-row").hidden`, 'Concurrent query levels are toggles limited to recorded values');
    await check('document.querySelectorAll("#visible-models label.chip").length===3 && document.querySelectorAll("#chart-key .key-item").length===3', 'Models are toggle chips and the chart key names each drawn model');
    await screenshot('results-graphs-desktop');
    await evaluate('document.querySelectorAll("#result-levels [data-level]")[1].click()');
    await delay(100);
    await check(`${pressed("#result-levels [data-level]")} === "1,2" && window.__lastSeries.some(series=>series.level===2 && series.dash) && document.querySelector("#chart-key").textContent.includes("2 queries")`, 'A second query level adds its own dashed line and key entry');
    await evaluate('document.querySelectorAll("#result-levels [data-level]")[1].click()');
    await delay(100);
    await check(`${pressed("#result-levels [data-level]")} === "1"`, 'A ticked level can be cleared again');
    await evaluate('document.querySelectorAll("#result-levels [data-level]")[0].click()');
    await check(`${pressed("#result-levels [data-level]")} === "1"`, 'Clicking the only ticked level leaves it ticked');
    await evaluate('Array.from(document.querySelectorAll("[data-run-select]")).find(input => !input.checked).click()');
    await delay(100);
    await check('state.compare.size === 2', 'Two benchmark configurations can be compared');
    await check('state.colorBy==="models" && new Set(window.__lastSeries.filter(series=>series.model==="model-a").map(series=>series.color)).size===1 && new Set(window.__lastSeries.filter(series=>series.model==="model-a").map(series=>series.marker)).size===2', 'Colouring by model gives each benchmark its own marker');
    await evaluate('document.querySelector("[data-color-by=benchmarks]").click()');
    await delay(100);
    await check('state.colorBy==="benchmarks" && ["smoke","comparison"].every(id=>{const series=window.__lastSeries.find(item=>item.id.startsWith(id+"|"));const swatch=document.querySelector(`#runs-list .run[data-id="${id}"] .swatch`);const probe=document.createElement("span");probe.style.color=series.color;document.body.appendChild(probe);const matches=getComputedStyle(probe).color===getComputedStyle(swatch).backgroundColor;probe.remove();return matches})', 'Colouring by benchmark matches the sidebar swatches');
    await check('!document.querySelector("#chart-key").textContent.includes(" ? ") && document.querySelector("#chart-key").textContent.includes("GTX 1080 Ti")', 'The chart key names each benchmark readably');
    await evaluate('document.querySelector("#visible-models input[value=model-b]").click()');
    await delay(100);
    await check('state.visibleModels.size===2 && !state.visibleModels.has("model-b") && !window.__lastSeries.some(series=>series.model==="model-b")', 'A model chip hides that model');
    await evaluate('document.querySelector("#show-all-models").click();document.querySelector("#result-metrics [data-metric=prefill_tps]").click()');
    await delay(100);
    await check('state.metrics.size===1 && state.metrics.has("decode_tps") && document.querySelectorAll("#compare-body .graph-card").length===1', 'A metric button removes its chart');
    await evaluate('document.querySelector("[data-comparison-layout=models]").click()');
    await delay(100);
    await check('state.comparisonLayout==="models" && document.querySelectorAll("#compare-body .graph-card").length===3 && !!document.querySelector("#compare-body .chart-readout")', 'One chart per model splits the view and reads out the benchmarks side by side');
    await evaluate('document.querySelector("[data-comparison-layout=overview]").click();document.querySelector("#result-metrics [data-metric=decode_tps]").click();document.querySelector("#result-metrics [data-metric=group_output_tps]").click();document.querySelector("#result-metrics [data-metric=wall_s]").click();document.querySelector("[data-comparison-axis=concurrency]").click()');
    await delay(150);
    await check(`state.comparisonAxis==="concurrency" && state.metrics.size===2 && state.metrics.has("group_output_tps") && ${pressed("[data-comparison-axis]")} === "concurrency"`, 'Changing the axis keeps the ticked metrics');
    await check('document.querySelector("#rc-levels-row").hidden && document.querySelector("#rc-levels-label").textContent==="Prompt length" && document.querySelector("#compare-body svg[aria-label]").getAttribute("aria-label").includes("concurrent queries") && document.querySelector("#compare-body .graph-direction").textContent.includes("512 prompt")', 'The concurrency axis holds the single recorded prompt length fixed and says so');
    await check('window.__lastSeries.some(series=>series.model==="model-a" && series.points.map(point=>point.x).join()==="1,2,4" && series.failures.some(failure=>failure.x===8))', 'A concurrency line spans the recorded query counts and marks the failed one');
    await check('["smoke","comparison"].every(id=>{const series=window.__lastSeries.find(item=>item.id.startsWith(id+"|"));const swatch=document.querySelector(`#runs-list .run[data-id="${id}"] .swatch`);const probe=document.createElement("span");probe.style.color=series.color;document.body.appendChild(probe);const matches=getComputedStyle(probe).color===getComputedStyle(swatch).backgroundColor;probe.remove();return matches})', 'Concurrency colours match the same sidebar swatches');
    await check('!document.querySelector("#concurrency-comparison").hidden && document.querySelector("#concurrency-comparison .sc-chart").hidden && !document.querySelector("#concurrency-comparison select") && !document.querySelector("#concurrency-comparison .sc-compact-details").open', 'Request evidence is offered collapsed, without a second chart or menu');
    await evaluate('document.querySelector("#concurrency-comparison .sc-compact-details").open=true');
    await check('document.querySelector("#concurrency-comparison .sc-matrix") && document.querySelector("#concurrency-comparison").textContent.includes("timeout")', 'Detailed concurrency evidence retains the failed workload');
    await screenshot('concurrency-desktop');
    await evaluate('document.querySelector("[data-comparison-axis=context]").click();showResultsView("table")');
    await delay(100);
    await check('state.comparisonAxis==="context" && state.metrics.has("wall_s") && document.querySelector("#rc-axis-row").hidden && !document.querySelector("#rc-levels-row").hidden', 'Tables keep the ticked metrics and offer query levels as rows');
    await check('!document.querySelector("#result-table-view").hidden && !!document.querySelector("#results-table table")', 'Table view opens');
    await evaluate('Object.defineProperty(navigator, "clipboard", {configurable:true, value:{writeText: async text => { window.__copied = text; }}}); document.querySelector("#results-table .copy-table").click()');
    await delay(50);
    await check('window.__copied.includes("Model") && window.__copied.includes("model-a") && window.__copied.includes("\\t")', 'Copy produces tab-separated headers and data');
    await screenshot('table-desktop');
    await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
    await check('document.documentElement.scrollWidth<=innerWidth+1', 'Results table fits phone width');
    await screenshot('table-phone');
    await evaluate('showResultsView("compare")');
    await delay(100);
    await check('document.documentElement.scrollWidth<=innerWidth+1', 'Results graphs fit phone width');
    await screenshot('results-graphs-phone');
    await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
    await evaluate('showResultsView("table")');

    // Fake extension endpoints exercise the complete dialog without running inference.
    await evaluate('selectRun("smoke")');
    for (let i=0; i<40 && !await evaluate('!!document.querySelector("#extend-run")'); i++) await delay(50);
    await evaluate('state.visibleModels=new Set(["model-a"]);state.compare=new Set(["smoke"]);renderResultsView()');
    await check('!document.querySelector("#rerun-model").hidden && !document.querySelector("#rerun-model").disabled', 'One selected model has a direct rerun action');
    await evaluate('document.querySelector("#rerun-model").click()');
    for (let i=0; i<40 && !await evaluate('!!document.querySelector("#extend-content input[data-model]")'); i++) await delay(50);
    await check('state.extension.action==="rerun" && Object.keys(state.extension.modelModes).join()==="model-a" && document.querySelector("#extend-content input[name=extend-action][value=rerun]").checked', 'Direct rerun previews only the chosen model');
    for (let i=0; i<40 && !await evaluate('document.querySelector("#extend-preview").textContent.includes("1 successful tests to rerun")'); i++) await delay(50);
    await check('document.querySelector("#extend-content").textContent.includes("previous attempts are kept") && document.querySelector("#extend-preview").textContent.includes("1 successful tests to rerun")', 'Single-model rerun previews replacements before starting');
    await screenshot('rerun-one-model');
    await evaluate('document.querySelector("#extend-close").click()');
    await evaluate('showResultsView("summary")');
    await check('!document.querySelector("#panel-summary").hidden && document.querySelector("#extend-run").getBoundingClientRect().height>0', 'Benchmark actions are visible in Summary');
    await evaluate('document.querySelector("#extend-run").click()');
    for (let i=0; i<40 && !await evaluate('!!document.querySelector("#extend-content input[data-model]")'); i++) await delay(50);
    if (!await evaluate('!!document.querySelector("#extend-content input[data-model]")')) throw Error('Extension rendering: '+await evaluate('document.querySelector("#extend-content").textContent'));
    await check(`document.querySelector('#extend-dialog').open && document.querySelector('#extend-content input[data-model="0"][data-mode=off]').checked && !document.querySelector('#extend-content input[data-model="1"][data-mode=off]').checked`, 'Extension preselects benchmark models while leaving additions unchecked');
    await check(`document.querySelector('[data-progress-model="0"] .extend-model-progress').textContent.includes('1 successful') && document.querySelector('[data-progress-model="0"] .extend-model-progress').textContent.includes('0 unfinished')`, 'Saved model progress is visible before selecting additions');
    await evaluate('document.querySelector("#extend-timeout").value="3600";document.querySelector("#extend-timeout").dispatchEvent(new Event("change"))');
    await check('extendBody(state.extension).per_test_timeout_s===3600', 'Repairs can increase the timeout without changing saved measurements');
    await evaluate('document.querySelector("#extend-repeats").value="2";document.querySelector("#extend-repeats").dispatchEvent(new Event("change"))');
    await check('extendBody(state.extension).repeats===2 && document.querySelector(".extend-model-progress").textContent.includes("1 unfinished")', 'Repeat extension keeps existing success and adds one slot');
    await evaluate('document.querySelector("#extend-case").selectedIndex=1;document.querySelector("#extend-case").dispatchEvent(new Event("change"))');
    await check('state.extension.selectedCases.length===1 && extendBody(state.extension).selected_cases[0][4]===0 && document.querySelector("#extend-repeats").disabled && document.querySelector("#extend-content").textContent.includes("Repairing one test only")', 'Individual repair locks one context, concurrency and repeat');
    await evaluate('document.querySelector("#extend-case").value="";document.querySelector("#extend-case").dispatchEvent(new Event("change"))');
    await check('!state.extension.selectedCases && !document.querySelector("#extend-repeats").disabled', 'Repair selector returns to full workload extension');
    await evaluate('document.querySelector("#extend-clear-selection").click()');
    await check('document.querySelector("#extend-preview").textContent.includes("Choose models") && document.querySelector("#extend-start").disabled && state.extension.source.results.length > 0', 'Clearing workload selection preserves the existing measurements');
    await evaluate('Array.from(document.querySelectorAll("#extend-content input[data-model]")).find(input=>input.dataset.model==="0" && input.dataset.mode==="off").click();Array.from(document.querySelectorAll("#extend-content input[data-model]")).find(input=>input.dataset.model==="1" && input.dataset.mode==="on").click()');
    for (let i=0; i<40 && !await evaluate('document.querySelector("#extend-preview").textContent.includes("1 new tests")'); i++) await delay(50);
    await check('document.querySelector("#extend-preview").textContent.includes("1 new tests") && document.querySelector("#extend-preview").textContent.includes("1 already present") && document.querySelector("#extend-preview").textContent.includes("0 successful tests to rerun")', 'Preview distinguishes missing and existing model-specific techniques');
    await evaluate('document.querySelector("#extend-content input[name=extend-action][value=retry]").click()');
    await evaluate('Array.from(document.querySelectorAll("#extend-content input[data-model]")).find(input=>input.dataset.model==="1" && input.dataset.mode==="off").click()');
    for (let i=0; i<40 && !await evaluate('document.querySelector("#extend-preview").textContent.includes("1 retries")'); i++) await delay(50);
    await check('document.querySelector("#extend-preview").textContent.includes("1 retries") && !document.querySelector("#extend-start").disabled', 'Retry action previews failed and missing tests');
    await evaluate('document.querySelector("#extend-content input[name=extend-action][value=rerun]").click()');
    for (let i=0; i<40 && !await evaluate('document.querySelector("#extend-start").textContent.includes("Rerun 3")'); i++) await delay(50);
    await check('document.querySelector("#extend-preview").textContent.includes("1 successful tests to rerun") && document.querySelector("#extend-content").textContent.includes("previous attempts are kept")', 'Rerun warns and previews successful replacements');
    await evaluate('document.querySelector("#extend-start").click()');
    for (let i=0; i<40 && await evaluate('document.querySelector("#extend-dialog").open'); i++) await delay(50);
    await check('!document.querySelector("#extend-dialog").open && document.querySelector("#live-card").dataset.runId === "smoke"', 'Rerun closes and launches against the same benchmark');
    await check('(async()=>{const body=await (await fetch("/test/last-extension")).json();return body.run_id==="smoke" && body.rerun_existing && body.retry_failed && body.model_modes["model-a"].join() === "off" && body.model_modes["model-b"].includes("off") && body.model_modes["model-b"].includes("on") && body.sizes.join() === "512" && body.concurrency_levels.join() === "1"})()', 'Fake backend receives only the chosen model techniques and rerun options');
    for (let i=0; i<40 && await evaluate('!!state.poller'); i++) await delay(50);

    await evaluate('state.benchmarkRunning=false;state.compare=new Set(["smoke"]);state.visibleModels=new Set(["model-b"]);selectRun("smoke");showResultsView("table")');
    await check('!document.querySelector("#remove-model").hidden && !document.querySelector("#remove-model").disabled', 'One selected model has an explicit removal action');
    await evaluate('window.confirm=()=>false;document.querySelector("#remove-model").click()');
    await check('state.runCache.smoke.results.some(row=>row.model==="model-b")', 'Cancelling removal retains the model');
    await evaluate('window.confirm=()=>true;document.querySelector("#remove-model").click()');
    for(let attempt=0;attempt<80 && !await evaluate('state.runCache.smoke && !state.runCache.smoke.results.some(row=>row.model==="model-b")');attempt++)await delay(50);
    await check('state.runCache.smoke && !state.runCache.smoke.results.some(row=>row.model==="model-b") && state.runCache.smoke.results.some(row=>row.model==="model-a") && state.runs.length===2', 'Removing one model preserves other measurements and both benchmark entries');
    await evaluate('window.confirm=()=>false; document.querySelector("#runs-list .run-delete").click()');
    await check('state.runs.length === 2', 'Cancel delete keeps the benchmarks');
    await evaluate('window.confirm=()=>true; document.querySelector("#runs-list .run-delete").click()');
    for (let attempt = 0; attempt < 30 && await evaluate('state.runs.length > 1'); attempt++) await delay(50);
    await check('state.runs.length === 1 && state.compare.size === 1', 'Deleting selection opens the remaining benchmark');
    await evaluate('document.querySelector("#runs-list .run-delete").click()');
    for (let attempt = 0; attempt < 30 && await evaluate('state.runs.length > 0'); attempt++) await delay(50);
    await check('state.runs.length === 0', 'List delete removes the last benchmark');

    await evaluate('state.comparisonConcurrency=1;state.queryLevels=null;state.comparisonAxis="context";state.comparisonLayout="overview";state.colorBy="models";state.metrics=new Set(["prefill_tps"]);renderMetricSeg();state.visibleModels=null;fetch("/test/start",{method:"POST"}).then(response=>response.json())');
    await evaluate('showPanel("results");showResultsView("table")');
    for(let attempt=0;attempt<80 && !await evaluate('!!state.runCache.live && state.compare.has("live") && state.runs.some(run=>run.id==="live" && run.status==="running")');attempt++) await delay(50);
    await check('state.compare.has("live") && state.runs.some(run=>run.id==="live" && run.status==="running")', 'An ongoing benchmark appears before its first completed test');
    await check('!!document.querySelector("#runs-list .badge-live") && !!document.querySelector("#run-detail .live-result-notice") && document.querySelector("#run-detail .live-result-notice").textContent.includes("0 completed")', 'Library marks the run Live and Summary retains its progress while Table waits for the first test');
    await check('document.querySelector("#run-detail #delete-run").disabled && document.querySelector("#runs-list .run-delete").disabled', 'Active benchmark cannot be deleted');
    await evaluate('document.querySelector("#runs-list [data-edit-run=live]").click()');
    await evaluate('document.querySelector("#meta-label").value="Ongoing, renamed";document.querySelector("#meta-form").requestSubmit()');
    for(let attempt=0;attempt<80 && !await evaluate('state.runs.some(run=>run.id==="live" && run.label==="Ongoing, renamed")');attempt++) await delay(50);
    await check('state.runs.some(run=>run.id==="live" && run.label==="Ongoing, renamed" && run.status==="running") && !!document.querySelector("#runs-list .badge-live") && document.querySelector("#runs-list .run[data-id=live] .t").textContent==="Ongoing, renamed"', 'A running benchmark can be renamed and stays Live');
    await evaluate('state.visibleModels=new Set(["model-a"]);fetch("/test/result",{method:"POST"}).then(response=>response.json())');
    for(let attempt=0;attempt<80 && !await evaluate('!!document.querySelector("#results-table [data-measure=prefill_tps]") && document.querySelector("#results-table tbody").textContent.includes("321")');attempt++) await delay(50);
    await check('state.runCache.live.results.length===1 && state.liveResults.length===1 && document.querySelector("#results-table tbody").textContent.includes("321")', 'Completed tests appear automatically in the running Results table');
    await check('state.metrics.size===1 && state.metrics.has("prefill_tps") && state.visibleModels.has("model-a") && state.colorBy==="models"', 'Live updates preserve the chosen metric, models and colour mode');
    await evaluate('state.compare.clear();state.selected=null;renderRunsList();renderResultsView();fetch("/test/result",{method:"POST"}).then(response=>response.json())');
    for(let attempt=0;attempt<80 && !await evaluate('state.runCache.live.results.length===2');attempt++) await delay(50);
    await check('state.compare.size===0 && state.runCache.live.results.length===2', 'Live updates respect an intentionally deselected benchmark');
    await evaluate('selectRun("live");showResultsView("summary")');
    await delay(80);
    await check('!document.querySelector("#panel-summary").hidden && !document.querySelector("#summary-body .plot") && document.querySelector("#run-detail .live-result-notice").textContent.includes("2 completed")', 'Summary shows partial live coverage without replacing the overview with charts');
    await screenshot('live-results-desktop');
    await evaluate('fetch("/test/finish",{method:"POST"}).then(response=>response.json())');
    for(let attempt=0;attempt<80 && !await evaluate('state.runCache.live.status==="completed" && state.poller===null');attempt++) await delay(50);
    await check('state.runCache.live.status==="completed" && !document.querySelector("#run-detail .live-result-notice") && !document.querySelector("#run-detail #delete-run").disabled', 'Completion refreshes final data and clears the Live state');
    await check('document.querySelector("#live-card h3").textContent==="Benchmark finished" && document.querySelector("#live-phase").textContent.startsWith("Completed") && document.querySelector("#live-phase").classList.contains("st-ok")', 'The progress panel shows the completed job rather than the idle worker phase');
    await check('document.querySelector("#live-deadline").hidden && !document.querySelector("#live-sample-note").hidden && document.querySelector("#live-sample-note").textContent.startsWith("Last recorded sample") && document.querySelector("#pill-power").hidden', 'Finished telemetry is a labelled snapshot with no running deadline or live power pill');
    await evaluate('showPanel("run");document.querySelector("#helper-provider").value="openai";document.querySelector("#helper-provider").dispatchEvent(new Event("change"))');
    await check('document.querySelector("#start-btn").disabled && document.querySelector("#offload").closest(".field").hidden', 'Provider change requires connection and hides managed model controls');
    await evaluate('document.querySelector("#helper-url").value="http://localhost:9000/v1";document.querySelector("#helper-model").value="manual/model";connectHelperEndpoint()');
    await check('state.models.has("external/model") && !state.models.has("manual/model") && !document.querySelector("#start-btn").disabled', 'Successful generic discovery uses served models and ignores the manual fallback ID');
    await check('document.querySelector("#model-list").textContent.includes("IQ3_S") && document.querySelector("#model-list").textContent.includes("ctx 32.768k")', 'Run displays endpoint quantization and context metadata');
    await check('!document.querySelector("#helper-id, #helper-label, #browser-public-id, #browser-public-label") && !document.querySelector("#helper-status").textContent.includes("Strata")', 'Endpoint identity is automatic; missing detection uses a generic name');
    await evaluate('showPanel("results");selectRun("live");openExtendDialog(state.runCache.live)');
    for(let attempt=0;attempt<60 && !await evaluate('state.extension?.external && !!document.querySelector("#extend-content [data-mode]")');attempt++)await delay(50);
    await check('document.querySelector("#extend-dialog").open && state.extension.runId==="live" && document.querySelector("#extend-title").textContent.includes("Add or rerun") && document.querySelector("#extend-content").textContent.includes("external/model") && !document.querySelector("#extend-content").textContent.includes("model-a")', 'Generic additions target the existing benchmark using the currently served models: ' + JSON.stringify(await evaluate('({title:document.querySelector("#extend-title").textContent,html:document.querySelector("#extend-content").textContent,extension:state.extension})')));
    await check('document.querySelector("#extend-content").textContent.includes("Provider controlled") && document.querySelector("#extend-content [data-mode=on]").closest("label").hidden', 'External MTP stays provider controlled');
    await check('document.querySelector("#extend-content").textContent.includes("IQ3_S")', 'Results retains endpoint quantization');
    await evaluate('document.querySelector("#extend-content [data-mode=off]").click()');
    await check('extendBody(state.extension).run_id==="live" && extendBody(state.extension).connection.provider==="openai" && extendBody(state.extension).model_modes["external/model"].join()==="off"', 'Adding a served model keeps the destination benchmark and connection');
    await evaluate('document.querySelector("#extend-dialog").close();state.extension=null;showPanel("run")');
    await evaluate('document.querySelector("#meta-engine").value="Strata";document.querySelector("#meta-context").value="32768";document.querySelector("#add-compute-claim").click()');
    await check('collectConfig().reproducibility.engine==="Strata" && collectConfig().reproducibility.context_tokens===32768 && document.querySelectorAll(".compute-claim").length===1', 'Public engine settings and compute claims are editable');
    await evaluate('(async()=>{await fetch("/test/discovery-failure",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({enabled:true})});document.querySelector("#helper-model").value="manual/model";await connectHelperEndpoint()})()');
    await check('state.models.has("manual/model") && !state.models.has("external/model") && !document.querySelector("#start-btn").disabled && !state.providerNeedsConnection && collectConfig().models.join()==="manual/model" && document.querySelector("#helper-status").textContent==="Model ID used as typed · not checked" && document.querySelector("#helper-status").dataset.state!=="ready" && document.querySelector("#pill-lms span:last-child").textContent.endsWith("· not checked") && !document.querySelector("#pill-lms .dot").classList.contains("on") && !document.querySelector("#helper-message").hidden && document.querySelector("#helper-message").dataset.tone==="info" && document.querySelector("#helper-message-text").textContent.includes("is used as typed") && document.querySelector("#helper-action").hidden', 'Manual model fallback enables requests while clearly reporting unavailable discovery: '+await evaluate('document.querySelector("#helper-status").textContent+" | "+document.querySelector("#helper-message").textContent'));
    await evaluate('document.querySelector("#helper-provider").value="lmstudio";document.querySelector("#helper-provider").dispatchEvent(new Event("change"))');
    await check('document.querySelector("#start-btn").disabled', 'Switching back cannot send stale remote model IDs to LM Studio');
    await evaluate('connectHelperEndpoint()');
    await check('!state.models.has("external/model") && !state.models.has("manual/model") && !document.querySelector("#start-btn").disabled && document.querySelector("#helper-provider").value==="lmstudio" && collectConfig().endpoint_id==="lmstudio" && state.env.requested_provider==="lmstudio"', 'Reconnecting restores LM Studio inventory: ' + JSON.stringify(await evaluate('({models:[...state.models],provider:document.querySelector("#helper-provider").value,needs:state.providerNeedsConnection,status:document.querySelector("#helper-status").textContent,start:document.querySelector("#start-btn").disabled,running:state.benchmarkRunning,endpoint:collectConfig().endpoint_id,requested:state.env.requested_provider,sizes:[...state.sizes],plan:document.querySelector("#plan-summary").textContent})')));
    // An endpoint address that is this computer's own LM Studio connects as managed, visibly and reversibly.
    await evaluate('fetch("/test/discovery-failure",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({enabled:false})}).then(response=>response.json())');
    await evaluate('window.__levels=[...state.concurrencyLevels];window.__sizes=[...state.sizes].sort().join();state.models=new Set(["model-b"]);renderModels();document.querySelector("#mtp-chips input[value=both]").click();state.concurrencyLevels.add(4);renderConcurrency();document.querySelector("#helper-provider").value="openai";document.querySelector("#helper-provider").dispatchEvent(new Event("change"));document.querySelector("#helper-url").value="http://127.0.0.1:1234/v1";document.querySelector("#helper-model").value="";connectHelperEndpoint()');
    for(let attempt=0;attempt<60 && !await evaluate('document.querySelector("#helper-status").dataset.state==="ready" && !document.querySelector("#helper-connect").disabled');attempt++)await delay(50);
    await check('document.querySelector("#helper-provider").value==="lmstudio" && collectConfig().provider==="lmstudio" && !document.querySelector("#helper-switch-note").hidden && document.querySelector("#helper-switch-text").textContent.includes("http://127.0.0.1:1234/v1") && [...state.models].join()==="model-b" && !document.querySelector("#offload").closest(".field").hidden', 'LM Studio on this computer, reached through its endpoint address, connects as managed and says so: ' + JSON.stringify(await evaluate('({provider:document.querySelector("#helper-provider").value,note:document.querySelector("#helper-switch-text").textContent,status:document.querySelector("#helper-status").textContent,models:[...state.models]})')));
    await check('collectConfig().mtp_modes.join()==="off,on" && collectConfig().concurrency_levels.includes(4) && [...state.sizes].sort().join()===window.__sizes && mtpPlan().passes.length===2 && !document.querySelector("#start-btn").disabled', 'Connecting keeps the chosen models, workload and MTP mode, and managed LM Studio runs both passes');
    await evaluate('document.querySelector("#helper-plain-endpoint").click()');
    for(let attempt=0;attempt<60 && !await evaluate('state.models.has("external/model") && !document.querySelector("#helper-connect").disabled');attempt++)await delay(50);
    await check('document.querySelector("#helper-provider").value==="openai" && document.querySelector("#helper-switch-note").hidden && state.models.has("external/model") && collectConfig().provider==="openai" && collectConfig().mtp_modes.join()==="off,on" && mtpPlan().passes.join()==="off" && localStorage.getItem("wattever-plain-endpoint")==="http://127.0.0.1:1234/v1"', 'The opt-out measures it as a plain endpoint, where only the standard pass can run, and is remembered for that address');
    await evaluate('document.querySelector("#mtp-chips input[value=off]").click();state.concurrencyLevels=new Set(window.__levels);renderConcurrency();document.querySelector("#helper-provider").value="lmstudio";document.querySelector("#helper-provider").dispatchEvent(new Event("change"));connectHelperEndpoint()');
    for(let attempt=0;attempt<60 && !await evaluate('state.models.has("model-a") && !document.querySelector("#helper-connect").disabled');attempt++)await delay(50);
    await check('localStorage.getItem("wattever-plain-endpoint")===null && document.querySelector("#helper-provider").value==="lmstudio"', 'Connecting LM Studio (managed) by hand ends the plain-endpoint choice');
    await evaluate('fetch("/test/discovery-failure",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({enabled:true})}).then(response=>response.json())');
    await evaluate(`
      const categories=['arithmetic','logic','json','instructions','retrieval','code_output'];
      const tasks=categories.flatMap(category=>Array.from({length:4},(_,index)=>{
        const expected=category==='json'?{answer:512}:category==='logic'?'YES':category==='retrieval'?'ORBIT':category==='instructions'?'cobalt|birch|7':'512';
        return{id:category+'-'+(index+1),category,prompt:category==='arithmetic'?'Calculate 256 + 256. Reply with only the integer.':category==='json'?'Return only a JSON object with answer set to the integer 512.':category==='logic'?'All norks are tavs. Is each nork a tav? Reply YES or NO.':'Fixture '+category+' task '+(index+1)+'. Reply exactly with the requested answer.',scorer:{kind:category==='json'?'json_exact':'exact_text',expected}};
      }));
      const results=[];
      for(let repeat=0;repeat<2;repeat++)tasks.forEach((task,index)=>{
        if(repeat===1 && index>=22)return;
        const row={item_id:task.id,category:task.category,repeat,status:'ok',response:typeof task.scorer.expected==='object'?JSON.stringify(task.scorer.expected):task.scorer.expected,finish_reason:'stop',elapsed_s:1+index/20,scoring:{scored:true,correct:true,score:1,rule:task.scorer.kind,expected:task.scorer.expected,reason:'exact_match'}};
        if((repeat===0 && index===3)||(repeat===1 && index===10)){row.status='error';delete row.response;row.scoring={scored:false,reason:'request_error'};row.error_type='TimeoutError';}
        else if(repeat===0 && index===2){row.finish_reason='length';row.scoring={...row.scoring,correct:false,score:0,truncated:true,reason:'output_token_limit_reached'};}
        else if(index===7 || index===9 || repeat===0 && index===1){
          row.response=index===9?'{"answer":"512"}':index===7?'NO':'512 <img src=x onerror="window.__capInjected=true">';
          row.scoring={...row.scoring,correct:false,score:0,reason:index===9?'json_value_or_type_mismatch':'answer_mismatch'};
        }
        results.push(row);
      });
      const digest='f'.repeat(64);
      window.__capFixture={id:'cap-ui-fixture',benchmark_kind:'capability',schema_version:1,status:'interrupted',created_at:'2026-11-01T12:00:00Z',finished_at:'2026-11-01T12:02:00Z',evaluation_key:'e'.repeat(64),suite_id:'wattever-core-v1',suite_version:'1.0.0',suite_digest:digest,suite:{id:'wattever-core-v1',name:'Core dashboard fixture',version:'1.0.0',digest,scoring_version:'wattever-rule-scorers-v1',tasks},protocol:{version:'wattever-capability-v1',scoring_version:'wattever-rule-scorers-v1',suite_digest:digest,selected_item_ids:tasks.map(task=>task.id),selected_item_digest:'a'.repeat(64),repeats:2,max_tokens:256,token_limit_field:'max_tokens',timeout_s:120,reasoning_effort_requested:'none',sampling:{temperature:0,top_p:null,seed:0,send_seed:false}},model_configuration:{model:{value:'org/dashboard-model'},quantization:{value:'Q4_K_M'},endpoint:{label:'Fixture endpoint'},reasoning:{requested:'none',user_reported:'off'},mtp:{user_reported:'on'}},summary:{planned:48,attempts:46,scored:44,correct:38,percent:100*38/44,unique_items:24,errors:2,truncated:1,wilson_95:{low:.63,high:.91},category_scores:categories.map(category=>{const rows=results.filter(row=>row.category===category && row.scoring.scored);const correct=rows.filter(row=>row.scoring.correct).length;return{category,scored:rows.length,correct,percent:100*correct/rows.length};})},results};
      window.__capFixture.model_configuration.endpoint.id='fixture-route';
      window.__capFixture.summary.wilson_95={low:.6286,high:.9302,n:23,correct:19};
      window.__capComparison=structuredClone(window.__capFixture);
      window.__capComparison.id='cap-ui-comparison';window.__capComparison.created_at='2026-11-01T11:00:00Z';window.__capComparison.model_configuration.model.value='org/second-model';
      window.__capComparison.model_configuration.quantization.value='Q8_0';
      for(const row of window.__capComparison.results)if(row.item_id==='json-2' || row.repeat===0 && ['arithmetic-2','arithmetic-4'].includes(row.item_id)){
        const task=tasks.find(task=>task.id===row.item_id);
        row.status='ok';row.response=typeof task.scorer.expected==='object'?JSON.stringify(task.scorer.expected):task.scorer.expected;row.finish_reason='stop';delete row.error_type;
        row.scoring={scored:true,correct:true,score:1,rule:task.scorer.kind,expected:task.scorer.expected,reason:'exact_match'};
      }
      const comparisonScored=window.__capComparison.results.filter(row=>row.scoring.scored),comparisonCorrect=comparisonScored.filter(row=>row.scoring.correct).length;
      Object.assign(window.__capComparison.summary,{scored:comparisonScored.length,correct:comparisonCorrect,percent:100*comparisonCorrect/comparisonScored.length,errors:1,wilson_95:{low:.7415,high:.9768,n:24,correct:22},category_scores:categories.map(category=>{const rows=comparisonScored.filter(row=>row.category===category),correct=rows.filter(row=>row.scoring.correct).length;return{category,scored:rows.length,correct,percent:100*correct/rows.length};})});
      window.__capDifferent=structuredClone(window.__capComparison);window.__capDifferent.id='cap-ui-different';window.__capDifferent.created_at='2026-11-01T10:00:00Z';window.__capDifferent.evaluation_key='d'.repeat(64);window.__capDifferent.protocol.max_tokens=512;window.__capDifferent.model_configuration.model.value='org/different-protocol';
      window.__capArcFixture=structuredClone(window.__capFixture);
      window.__capArcFixture.id='cap-ui-arc-scope';window.__capArcFixture.created_at='2026-11-01T09:00:00Z';window.__capArcFixture.status='completed';window.__capArcFixture.suite_id='arc-challenge-test-chat-v1';window.__capArcFixture.model_configuration.model.value='org/arc-display-fixture';
      window.__capArcFixture.suite={id:'arc-challenge-test-chat-v1',name:'ARC display fixture',version:'1.0.0',source:{kind:'bundled_dataset'},tasks:Array.from({length:1172},(_,index)=>({id:'arc-test-'+String(index).padStart(4,'0'),category:'science_reasoning',prompt:'Synthetic science display question '+index,scorer:{kind:'multiple_choice',expected:'A'}}))};
      window.__capArcFixture.protocol={...window.__capFixture.protocol,selected_item_ids:window.__capArcFixture.suite.tasks.slice(0,50).map(task=>task.id),repeats:1,sample_size_requested:50,max_tokens:32768};
      window.__capArcFixture.results=window.__capArcFixture.protocol.selected_item_ids.map(item_id=>({item_id,category:'science_reasoning',repeat:0,status:'ok',response:'ANSWER: A',finish_reason:'stop',scoring:{scored:true,correct:true,score:1,rule:'multiple_choice',reason:'choice_match'}}));
      window.__capArcFixture.summary={planned:50,attempts:50,scored:50,correct:50,percent:100,unique_items:50,errors:0,truncated:0,wilson_95:{low:.9286,high:1,n:50,correct:50},category_scores:[{category:'science_reasoning',scored:50,correct:50,percent:100}]};
      window.__capLegacyHelper=false;
      window.__originalUIApi=window.WatteverUI.api;
      window.WatteverUI.api=async(path,options)=>{
        if(path==='/api/capabilities/start'){window.__capStartConfig=JSON.parse(options.body);return{run_id:'cap-ui-fixture'};}
        if(path==='/api/capabilities/status')return{running:false,run_id:window.__capStartConfig?'cap-ui-fixture':null,live:{}};
        if(path==='/api/capabilities/suites')return{...(window.__capLegacyHelper?{}:{runner:{completion_policy:'finished-stream-visible-answer-v2'}}),suites:[{id:'wattever-core-v1',name:'Core dashboard fixture',version:'1.0.0',task_count:24,categories}]};
        if(path==='/api/capabilities/runs')return{runs:[window.__capFixture,window.__capComparison,window.__capDifferent,window.__capArcFixture]};
        if(path==='/api/capabilities/runs/cap-ui-fixture')return window.__capFixture;
        if(path==='/api/capabilities/runs/cap-ui-comparison')return window.__capComparison;
        if(path==='/api/capabilities/runs/cap-ui-different')return window.__capDifferent;
        if(path==='/api/capabilities/runs/cap-ui-arc-scope')return window.__capArcFixture;
        return window.__originalUIApi(path,options);
      };
      showPanel('capabilities');
    `);
    await send('Emulation.setDeviceMetricsOverride', {width: 1440, height: 1100, deviceScaleFactor: 1, mobile: false});
    for(let attempt=0;attempt<60 && !await evaluate('document.querySelector("#cap-suite").options.length>0 && !!document.querySelector("#cap-metric-accuracy")');attempt++)await delay(50);
    await check('document.querySelector("#cap-start").textContent.includes("capability") && document.querySelector("#cap-unavailable").hidden', 'Capability launch and saved results are available');
    await check('!document.querySelector("#cap-start").disabled && document.querySelector("#cap-helper-compatibility").hidden', 'Current helper completion policy allows capability launch without a compatibility warning');
    await check('document.querySelector("#cap-detail-header .cap-evaluation-scope").textContent.includes("24 / 24 questions") && document.querySelector("#cap-detail-header .cap-evaluation-scope").textContent.includes("full suite") && document.querySelector("#cap-detail-header .cap-evaluation-scope").textContent.includes("2 repeats") && document.querySelector("#cap-detail-header .cap-evaluation-scope").textContent.includes("n=23")', 'Core scope strip distinguishes the full question selection, repeats and first-repeat confidence sample');
    await evaluate('document.querySelector("#cap-runs [data-cap-run=\\"cap-ui-arc-scope\\"]").click()');
    for(let attempt=0;attempt<60 && !await evaluate('document.querySelector("#cap-detail-header h2")?.textContent.includes("org/arc-display-fixture")');attempt++)await delay(50);
    await check('document.querySelector("#cap-detail-header").textContent.includes("AI2 ARC-Challenge") && document.querySelector("#cap-detail-header").textContent.includes("science MCQ") && document.querySelector("#cap-detail-header .cap-evaluation-scope").textContent.includes("50 / 1,172 questions") && document.querySelector("#cap-detail-header .cap-evaluation-scope").textContent.includes("subset") && document.querySelector("#cap-detail-header .cap-evaluation-scope").textContent.includes("n=50")', 'ARC scope identifies the science dataset, its 1,172-question split, the 50-question subset and first-repeat interval');
    await check('document.querySelector("#cap-detail-header .cap-evaluation-scope").textContent.includes("92.9%") && document.querySelector("#cap-detail-header .cap-evaluation-scope").textContent.includes("100.0%") && document.querySelector("#cap-detail-header .cap-scope-note").textContent.includes("not a full-suite result")', 'Subset confidence interval remains scoped to its questions and does not imply a full-suite score');
    await evaluate('document.querySelector("#cap-detail-header").scrollIntoView({block:"start"})');
    await screenshot('capabilities-arc-scope-desktop');
    await evaluate('document.querySelector("#cap-runs [data-cap-run=\\"cap-ui-fixture\\"]").click()');
    for(let attempt=0;attempt<60 && !await evaluate('document.querySelector("#cap-detail-header h2")?.textContent.includes("org/dashboard-model")');attempt++)await delay(50);
    await evaluate('window.__capLegacyHelper=true;window.WatteverCapabilities.activate()');
    await check('document.querySelector("#cap-start").disabled && !document.querySelector("#cap-helper-compatibility").hidden && document.querySelector("#cap-helper-compatibility").textContent.includes("Relaunch Wattever") && document.querySelector("#cap-runs [data-cap-run=\\"cap-ui-fixture\\"]")', 'An older helper without the completion-policy marker blocks new launches and explains how to update without hiding saved results');
    await evaluate('document.querySelector("#cap-start").click();document.querySelector("#cap-runs [data-cap-run=\\"cap-ui-comparison\\"]").click()');
    for(let attempt=0;attempt<60 && !await evaluate('document.querySelector("#cap-detail-header h2")?.textContent.includes("org/second-model")');attempt++)await delay(50);
    await check('!window.__capStartConfig && document.querySelector("#cap-start").disabled && document.querySelector("#cap-detail-header h2").textContent.includes("org/second-model") && !!document.querySelector("#cap-question-grid")', 'Legacy helper launch stays blocked while another saved evaluation and its question evidence remain browsable');
    await evaluate('window.scrollTo({top:0,behavior:"instant"})');
    await screenshot('capabilities-legacy-helper-desktop');
    await evaluate('window.__capLegacyHelper=false;window.WatteverCapabilities.activate()');
    await check('!document.querySelector("#cap-start").disabled && document.querySelector("#cap-helper-compatibility").hidden', 'Restoring the current completion policy re-enables capability launch and clears the warning');
    await evaluate('document.querySelector("#cap-runs [data-cap-run=\\"cap-ui-fixture\\"]").click()');
    for(let attempt=0;attempt<60 && !await evaluate('document.querySelector("#cap-detail-header h2")?.textContent.includes("org/dashboard-model")');attempt++)await delay(50);
    await check('document.querySelector("#cap-max-tokens").value==="32768" && document.querySelector("#cap-timeout").value==="0" && document.querySelector("#cap-reasoning").value==="none" && document.querySelector("#cap-temperature").value==="0"', 'Capability settings have a generous total generation budget and no wall-clock deadline, independent of speed settings');
    await check('!document.querySelector("#cap-status").closest("#cap-launch-panel") && !document.querySelector("#cap-cancel").closest("#cap-launch-panel")', 'Capability status and Stop remain outside the collapsed launch settings');
    await check('document.querySelector("#cap-detail-header h2").textContent.includes("org/dashboard-model")', 'The latest saved capability run opens its visual dashboard automatically');
    await check('document.querySelector("#cap-metric-accuracy").textContent.includes("86.4%") && document.querySelector("#cap-metric-coverage").textContent.includes("91.7%") && document.querySelector("#cap-metric-issues strong").textContent==="8"', 'Accuracy, evaluation coverage and issues have distinct honest denominators');
    await check('document.querySelectorAll("#cap-category-chart button[data-category]").length===6 && !!document.querySelector("#cap-outcome-chart svg") && document.querySelectorAll("#cap-repeat-chart button[data-repeat]").length===2', 'Category, outcome and repeat views render visual evidence rather than only a score table');
    await check('document.querySelectorAll("#cap-question-grid button[data-cell]").length===24 && !document.querySelector("#cap-question-next").disabled && document.querySelector("#cap-question-prev").disabled', 'Question heatmap paginates twelve questions across both repeats');
    await check('document.querySelector("#cap-detail-header .cap-suite-origin").dataset.origin==="custom" && document.querySelector("#cap-detail-header .cap-suite-origin").textContent.includes("not an established benchmark")', 'The custom Core diagnostic is explicitly distinguished from established benchmark datasets');
    await evaluate('window.scrollTo({top:0,behavior:"instant"})');
    await check('Math.abs(document.querySelector(".capability-library").getBoundingClientRect().top-document.querySelector("#cap-metric-accuracy").getBoundingClientRect().top)<2', 'Evaluation library and scorecards share the same top edge');
    await check('document.querySelector(".capability-library").getBoundingClientRect().height>=800 && Math.abs(document.querySelector("#panel-capabilities").getBoundingClientRect().left-(document.documentElement.clientWidth-document.querySelector("#panel-capabilities").getBoundingClientRect().right))<2', 'Desktop workspace is centered and the evaluation library uses the available window height');
    await evaluate(`(async()=>{window.__capLayoutAPI=WatteverUI.api;WatteverUI.api=async function(route,options){if(route==='/api/capabilities/runs')return{runs:[window.__capFixture,window.__capComparison,window.__capDifferent,...Array.from({length:30},(_,i)=>({...window.__capFixture,id:'layout-'+i}))]};return window.__capLayoutAPI(route,options)};await WatteverCapabilities.activate()})()`);
    await check('document.querySelector("#cap-runs").scrollHeight>document.querySelector("#cap-runs").clientHeight && document.querySelector("#cap-runs").clientHeight>600', 'Many evaluation entries scroll within a tall list without expanding the page');
    await evaluate('document.querySelector("#cap-runs").scrollTop=document.querySelector("#cap-runs").scrollHeight');
    await check('(()=>{const list=document.querySelector("#cap-runs"),last=list.querySelector("[data-cap-run=layout-29]"),a=list.getBoundingClientRect(),b=last.getBoundingClientRect();return b.top>=a.top && b.bottom<=a.bottom})()', 'The last evaluation remains reachable by scrolling the library');
    await evaluate('(async()=>{WatteverUI.api=window.__capLayoutAPI;await WatteverCapabilities.activate();document.querySelector("#cap-runs").scrollTop=0})()');
    await send('Emulation.setDeviceMetricsOverride', {width:1920,height:1100,deviceScaleFactor:1,mobile:false});
    await check('Math.abs(document.querySelector("#panel-capabilities").getBoundingClientRect().left-(document.documentElement.clientWidth-document.querySelector("#panel-capabilities").getBoundingClientRect().right))<2 && Math.abs(document.querySelector(".capability-library").getBoundingClientRect().top-document.querySelector("#cap-metric-accuracy").getBoundingClientRect().top)<2', 'Wide desktop layout remains centered with aligned scorecards');
    await send('Emulation.setDeviceMetricsOverride', {width:1440,height:1100,deviceScaleFactor:1,mobile:false});
    await evaluate('document.querySelector("#cap-detail-header").scrollIntoView({block:"start"})');
    await screenshot('capabilities-dashboard-desktop');

    await evaluate('document.querySelector("#cap-detail .cap-actions-strip [data-action=issues]").click()');
    await check('document.querySelector("#cap-filter-outcome").value==="issues" && document.querySelector("#cap-filter-status").textContent.includes("8 matching attempts") && !!document.querySelector("#cap-inspector .cap-answer-inspector")', 'Review issues filters incorrect, truncated and request errors and opens the first issue');
    await check('document.querySelector("#cap-inspector .cap-prompt pre").textContent.includes("256 + 256") && document.querySelector("#cap-inspector .cap-expected pre").textContent==="512" && document.querySelector("#cap-inspector .cap-response pre").textContent.includes("<img")', 'Inspector exposes the original question, expected answer and actual response');
    await check('!window.__capInjected && !document.querySelector("#cap-inspector img") && document.querySelector("#cap-inspector .cap-grade-explanation").textContent.includes("formatting") && !document.querySelector("#cap-inspector .cap-audit-fields").open', 'Response markup stays inert; a rule mismatch is explained without claiming the arithmetic is wrong, and raw audit fields start collapsed');
    await evaluate('document.querySelector("#cap-inspector").scrollIntoView({block:"center"})');
    await screenshot('capabilities-inspector-desktop');
    await evaluate('document.querySelector("#cap-inspector .cap-audit-fields").open=true;document.querySelector("#cap-detail .cap-reproducibility").open=true;window.WatteverCapabilities.activate()');
    await check('document.querySelector("#cap-inspector .cap-audit-fields").open && document.querySelector("#cap-detail .cap-reproducibility").open', 'Refreshing the same run preserves open grading and configuration disclosures');
    await evaluate('document.querySelector("#cap-inspector .cap-audit-fields").open=false;document.querySelector("#cap-detail .cap-reproducibility").open=false');

    await evaluate('document.querySelector("#cap-metric-accuracy").click();document.querySelector("#cap-metric-coverage").click()');
    await check('document.querySelector("#cap-filter-outcome").value==="not_scored" && document.querySelector("#cap-filter-status").textContent.includes("4 matching attempts")', 'Coverage tile reveals errors and missing observations without treating them as wrong answers');
    await evaluate('document.querySelector("#cap-clear-filters").click();document.querySelector("#cap-question-grid [data-cell=\\"arithmetic-2::0\\"]").click();document.querySelector("#cap-category-chart [data-category=json]").click()');
    await check('document.querySelector("#cap-filter-category").value==="json" && document.querySelector("#cap-filter-status").textContent.includes("8 matching attempts") && [...document.querySelectorAll("#cap-question-grid [data-cell]")].every(node=>node.dataset.cell.startsWith("json-")) && (!document.querySelector("#cap-inspector h3") || document.querySelector("#cap-inspector h3").textContent.startsWith("json-"))', 'Category bars filter the question matrix and do not leave stale out-of-filter evidence');
    await evaluate('document.querySelector("#cap-clear-filters").click();document.querySelector("#cap-outcome-chart button[data-outcome=truncated]").click()');
    await check('document.querySelector("#cap-filter-outcome").value==="truncated" && document.querySelector("#cap-filter-status").textContent.includes("1 matching attempts")', 'Outcome legend distinguishes output limits from ordinary rule mismatches');
    await evaluate('document.querySelector("#cap-question-grid button[data-cell]:not(.cap-cell-dim)").click()');
    await check('document.querySelector("#cap-inspector .cap-grade-explanation").textContent.includes("token limit")', 'Truncation inspector describes the output limit');
    await evaluate('document.querySelector("#cap-clear-filters").click();document.querySelector("#cap-repeat-chart g[data-repeat=\\"1\\"]").focus()');
    await send('Input.dispatchKeyEvent', {type:'keyDown',key:'Enter',code:'Enter'});
    await send('Input.dispatchKeyEvent', {type:'keyUp',key:'Enter',code:'Enter'});
    await check('document.querySelector("#cap-filter-repeat").value==="1" && [...document.querySelectorAll("#cap-question-grid button[data-cell]")].every(node=>node.dataset.cell.endsWith("::1"))', 'Repeat chart is keyboard-operable and filters matching observations');
    await evaluate('document.querySelector("#cap-clear-filters").click();document.querySelector("#cap-detail .cap-actions-strip [data-action=unstable]").click()');
    await check('document.querySelector("#cap-filter-outcome").value==="unstable" && document.querySelector("#cap-filter-status").textContent.includes("4 matching attempts across 2 questions")', 'Repeat variation identifies changed scored outcomes without inventing changes from request errors');
    await evaluate('document.querySelector("#cap-clear-filters").click();document.querySelector("#cap-filter-query").value="json-2";document.querySelector("#cap-filter-query").dispatchEvent(new Event("input",{bubbles:true}))');
    await check('document.querySelector("#cap-filter-status").textContent.includes("2 matching attempts") && document.querySelectorAll("#cap-question-grid button[data-cell]").length===2', 'Question search filters the audit evidence across repeats');
    await evaluate('document.querySelector("#cap-clear-filters").click();window.__capFirstCell=document.querySelector("#cap-question-grid [data-cell]").dataset.cell;document.querySelector("#cap-question-next").click()');
    await check('document.querySelector("#cap-question-grid [data-cell]").dataset.cell!==window.__capFirstCell && document.querySelector("#cap-question-next").disabled && !document.querySelector("#cap-question-prev").disabled', 'Next page shows the remaining questions');
    await evaluate('document.querySelector("#cap-question-prev").click()');
    await check('document.querySelector("#cap-question-grid [data-cell]").dataset.cell===window.__capFirstCell', 'Previous page restores the first question set');

    await evaluate('document.querySelector("#cap-start").click()');
    for(let attempt=0;attempt<60 && !await evaluate('!!window.__capStartConfig && !document.querySelector("#cap-detail").hidden');attempt++)await delay(50);
    await check('window.__capStartConfig.endpoint_id===state.detectedEndpoint.id && window.__capStartConfig.endpoint_label===state.detectedEndpoint.label && window.__capStartConfig.endpoint_reasoning_effort==="none" && window.__capStartConfig.sample_size===24', 'Capability launch reuses automatic endpoint identity and deterministic sample settings without manual IDs: '+JSON.stringify(await evaluate('({cfg:window.__capStartConfig,endpoint:state.detectedEndpoint})')));
    await check('window.__capStartConfig.max_tokens===32768 && window.__capStartConfig.timeout_s===0 && window.__capStartConfig.temperature===0 && window.__capStartConfig.token_limit_field==="max_tokens"', 'Capability launch sends its own quality-generation settings to the helper');
    await evaluate('const box=document.querySelector("#cap-runs input[value=\\"cap-ui-comparison\\"]");if(!box.checked)box.click()');
    for(let attempt=0;attempt<60 && !await evaluate('document.querySelectorAll("#cap-comparison .cap-comparison-bar").length===2');attempt++)await delay(50);
    await check('!document.querySelector("#cap-comparison").hidden && document.querySelectorAll("#cap-comparison .cap-comparison-bar").length===2 && document.querySelector("#cap-comparison .cap-paired-table").textContent.includes("6.8 pp") && !document.querySelector("#cap-comparison .cap-paired-table").textContent.includes("7.0 pp") && !document.querySelector("#cap-comparison .cap-paired-table").textContent.includes("Not comparable")', 'Matching protocols use the paired outcome change rather than the raw accuracy difference when coverage differs');
    await check('[...document.querySelectorAll("#cap-comparison .cap-paired-table tbody tr small")].some(node=>/unscored|ungraded/.test(node.textContent) && /(?:^|\\D)4(?:\\D|$)/.test(node.textContent))', 'Paired evidence includes both-unscored observations rather than silently reporting none');
    await evaluate('document.querySelector("#cap-runs input[value=\\"cap-ui-different\\"]").click()');
    for(let attempt=0;attempt<60 && !await evaluate('!!document.querySelector("#cap-comparison .cap-notice")');attempt++)await delay(50);
    await check('document.querySelector("#cap-comparison .cap-notice").textContent.includes("Different evaluation protocols") && [...document.querySelectorAll("#cap-comparison .cap-paired-table tbody tr")].some(row=>row.textContent.includes("org/different-protocol") && row.textContent.includes("Not comparable") && !row.querySelector(".cap-positive,.cap-negative"))', 'Different token budgets or protocols show a warning and no fabricated paired delta');
    // Async comparison rendering can replace the scrolled node; wait for its final layout.
    for(let attempt=0;attempt<20;attempt++) {
      await evaluate('document.querySelector("#cap-comparison").scrollIntoView({block:"start"})');
      await delay(50);
      if(await evaluate('document.querySelector("#cap-comparison > header").getBoundingClientRect().top>=document.querySelector(".topbar").getBoundingClientRect().bottom')) break;
    }
    await check('document.querySelector("#cap-comparison > header").getBoundingClientRect().top>=document.querySelector(".topbar").getBoundingClientRect().bottom', 'Comparison heading stays visible below the fixed navigation');
    await screenshot('capabilities-comparison-desktop');
    await check('document.documentElement.scrollWidth<=innerWidth+1', 'Capability overview and comparisons fit desktop width');

    await send('Emulation.setDeviceMetricsOverride', {width:390,height:844,deviceScaleFactor:1,mobile:true});
    await evaluate('document.querySelector("#cap-runs [data-cap-run=\\"cap-ui-fixture\\"]").click()');
    await delay(100);
    await evaluate('document.querySelector("#cap-clear-filters").click();document.querySelector("#cap-detail-header").scrollIntoView({block:"start"})');
    await check('document.documentElement.scrollWidth<=innerWidth+1', 'Capability dashboard has no horizontal page overflow at390px');
    await screenshot('capabilities-dashboard-phone');
    await evaluate('document.querySelector("#cap-outcome-chart").closest(".cap-card").scrollIntoView({block:"start"});window.scrollBy(0,-(document.querySelector(".topbar").getBoundingClientRect().height+12))');
    await screenshot('capabilities-charts-phone');
    await evaluate('document.querySelector("#cap-metric-issues").click()');
    await delay(500);
    await evaluate('window.scrollTo({top:window.scrollY+document.querySelector("#cap-inspector .cap-answer-inspector").getBoundingClientRect().top-document.querySelector(".topbar").getBoundingClientRect().height-12,behavior:"instant"})');
    await delay(100);
    await check('document.querySelector("#cap-filter-outcome").value==="issues" && !!document.querySelector("#cap-inspector .cap-answer-inspector") && !document.querySelector("#cap-inspector .cap-audit-fields").open && document.documentElement.scrollWidth<=innerWidth+1', 'Issue review and collapsed raw evidence remain usable without overflow on a phone');
    await screenshot('capabilities-inspector-phone');
    await send('Emulation.setDeviceMetricsOverride', {width:1440,height:1100,deviceScaleFactor:1,mobile:false});
    await evaluate(`(async()=>{
      const make=(size,c,rep,wall,scale=1,status='ok')=>({model:'test/model',quantization:'IQ3_S',target_tokens:size,concurrency:c,repeat:rep,status,wall_s:wall,
        ...(status==='failed'?{fail_kind:'timeout',timeout_budget_s:600}:{}),
        requests:Array.from({length:c},()=>({status,prompt_tokens:size+1,completion_tokens:128,wall_s:wall,ttft_s:10*scale,prefill_tps:100/scale,decode_tps:20/scale}))});
      const baseline=[make(512,1,0,100),make(512,1,1,120),make(80000,1,0,300),make(80000,1,1,320),make(80000,2,0,600,1,'failed'),make(80000,2,1,null,1,'skipped')];
      state.runCache.live.results=baseline;
      Object.assign(state.runCache.live.config,{provider:'openai',endpoint_inference_local:true,models:['test/model'],sizes:[512,80000],concurrency_levels:[1,2],repeats:2});
      state.runCache.live.storage_comparison={protocol:'wattever-storage-comparison-v1',baseline_label:'Samsung SSD',alternative_label:'NVMe',
        baseline_results:JSON.parse(JSON.stringify(baseline)),
        alternate_results:[make(512,1,0,50,.5),make(512,1,1,60,.5),make(80000,1,0,150,.5),make(80000,1,1,160,.5),make(80000,2,0,350,.5),make(80000,2,1,352,.5)]};
      state.detailSignature=null;state.visibleModels=null;state.conditions=null;state.comparisonConcurrency=1;
      showPanel('results');await selectRun('live');showResultsView('summary');
    })()`);
    for(let i=0;i<100&&!await evaluate('!!document.querySelector("[data-compare-storage=live]")');i++)await delay(50);
    await check('!!document.querySelector("[data-compare-storage=live]") && document.querySelector("#results-controls").hidden && !document.querySelector("#panel-summary .storage-numbers, #summary-body .plot")', 'Summary reports an available experiment without duplicating comparison controls or tables');
    await evaluate('document.querySelector("[data-compare-storage=live]").click()');
    for(let i=0;i<100&&!await evaluate('state.resultView==="compare" && document.querySelectorAll("#chart-key [data-curve][aria-pressed=true]").length===2');i++)await delay(50);
    await check('state.resultView==="compare" && document.querySelectorAll("#chart-key [data-curve][aria-pressed=true]").length===2 && !document.querySelector("[data-storage-filter]")', 'One action opens Compare with both actually recorded storage conditions switched on in the key and one shared filter set');
    for(let i=0;i<100&&!await evaluate('window.__lastSeries?.length===2 && window.__lastSeries.some(series=>series.name.includes("Samsung")) && window.__lastSeries.some(series=>series.name.includes("NVMe"))');i++)await delay(50);
    await check('window.__lastSeries.length===2 && window.__lastSeries.some(series=>series.name.includes("Samsung")) && window.__lastSeries.some(series=>series.name.includes("NVMe"))', 'The chart contains separately named SSD and NVMe series: '+JSON.stringify(await evaluate('window.__lastSeries?.map(series=>({name:series.name,points:series.points}))')));
    await check('window.__lastSeries.some(series=>series.points.some(point=>point.x===512 && point.y===110)) && window.__lastSeries.some(series=>series.points.some(point=>point.x===512 && point.y===55))', 'Storage charts use actual two-repeat medians, including the middle of both values');
    await check('[...document.querySelectorAll("#chart-key [data-curve]")].length===2 && [...document.querySelectorAll("#chart-key [data-curve]")].every(entry=>{const rect=entry.getBoundingClientRect(),style=getComputedStyle(entry);return entry.tagName==="BUTTON" && rect.height>=20 && style.cursor==="pointer" && style.backgroundColor!=="rgba(0, 0, 0, 0)" && entry.title.includes("4 matching cases") && entry.title.includes("4 measured")})', 'Each condition is a visible switch in the key with a wash behind it and current-model/query case counts');
    await check('document.querySelector("#compare-body .chart-readout-title").textContent.includes("80k") && document.querySelector("#compare-body .chart-readout").textContent.includes("310.00 s") && document.querySelector("#compare-body .chart-readout").textContent.includes("155.00 s") && document.querySelector("#compare-body .chart-readout").textContent.includes("2 matched") && /-50(?:\\.0)?% time/.test(document.querySelector("#compare-body .chart-readout").textContent) && !!(document.querySelector("#compare-body .chart-readout").compareDocumentPosition(document.querySelector("#compare-body .plot")) & Node.DOCUMENT_POSITION_FOLLOWING)', 'Chart-integrated largest-context readout explains actual values, matching repeats and time delta before the plot');
    await evaluate('document.querySelector("#chart-key [data-curve$=\\"storage:alternate\\"]").click()');
    for(let i=0;i<100&&!await evaluate('window.__lastSeries?.length===1');i++)await delay(50);
    await check('window.__lastSeries.length===1 && window.__lastSeries[0].run.label.includes("Samsung") && document.querySelectorAll("#chart-key [data-curve][aria-pressed=true]").length===1 && (()=>{const off=document.querySelector("#chart-key [data-curve$=\\"storage:alternate\\"]");return off.getAttribute("aria-pressed")==="false" && off.textContent.includes("NVMe")})()', 'An optional NVMe condition can be switched off in the key; its entry stays to switch it back on');
    for(let i=0;i<100&&!await evaluate('document.activeElement?.dataset?.curve?.endsWith("storage:alternate")');i++)await delay(50);
    await check('document.activeElement===document.querySelector("#chart-key [data-curve$=\\"storage:alternate\\"]")', 'The switch keeps keyboard focus after the charts are redrawn');
    await evaluate('document.activeElement.blur()');
    await check('getComputedStyle(document.querySelector("#chart-key [data-curve$=\\"storage:alternate\\"]")).backgroundColor==="rgba(0, 0, 0, 0)" && getComputedStyle(document.querySelector("#chart-key [data-curve][aria-pressed=true]")).backgroundColor!=="rgba(0, 0, 0, 0)"', 'Only a benchmark that is switched on carries the wash of colour');
    await check('/switched off in the key/.test(document.querySelector("#compare-body .notes").textContent) && !document.querySelector("#compare-body .notes").open', 'The tests of a switched-off condition are accounted for in the folded notes');
    await evaluate('document.querySelector("#chart-key [data-curve$=\\"storage:alternate\\"]").click()');
    for(let i=0;i<100&&!await evaluate('window.__lastSeries?.length===2');i++)await delay(50);
    await check('window.__lastSeries.length===2 && new Set(window.__lastSeries.map(series=>series.color)).size===1 && new Set(window.__lastSeries.map(series=>series.dash+"/"+series.marker)).size===2 && document.querySelector("#chart-key").textContent.includes("Samsung") && document.querySelector("#chart-key").textContent.includes("NVMe")', 'Recorded variants share their benchmark color and have distinct line or marker styles with named legend entries');
    await evaluate('document.querySelector("[data-comparison-axis=concurrency]").click()');
    await delay(150);
    await evaluate('(()=>{const chip=[...document.querySelectorAll("#result-levels [data-level]")].find(node=>node.dataset.level==="80000");if(chip && chip.getAttribute("aria-pressed")!=="true")chip.click();})()');
    await delay(150);
    await evaluate('(()=>{const chip=[...document.querySelectorAll("#result-levels [data-level]")].find(node=>node.dataset.level!=="80000" && node.getAttribute("aria-pressed")==="true");if(chip)chip.click();})()');
    await delay(150);
    await check('state.comparisonAxis==="concurrency" && window.__lastSeries.length===2 && new Set(window.__lastSeries.map(series=>series.color)).size===1 && new Set(window.__lastSeries.map(series=>series.dash+"/"+series.marker)).size===2 && document.querySelector("#chart-key").textContent.includes("Samsung") && document.querySelector("#chart-key").textContent.includes("NVMe")', 'The concurrency axis preserves the same distinct recorded-condition encodings and key: '+JSON.stringify(await evaluate('({axis:state.comparisonAxis,levels:activePromptLevels(),series:window.__lastSeries?.map(series=>[series.name,series.color,series.dash,series.marker])})')));
    await evaluate('document.querySelector("[data-comparison-axis=context]").click()');
    await delay(150);
    await evaluate(`(async()=>{
      const unrelated={id:'unrelated',label:'Unrelated LM Studio',created_at:'2026-10-25T12:00:00+00:00',status:'completed',gpu:{name:'NVIDIA GeForce RTX 5060 Ti'},config:{provider:'lmstudio',models:['different/model']},results:[{model:'different/model',target_tokens:512,concurrency:1,status:'ok',wall_s:12}]};
      state.runCache.unrelated=unrelated;state.runs.push({...unrelated,n_results:1,n_ok:1});state.compare.add('unrelated');state.conditions.add('unrelated');renderRunsList();await renderResultsView();
    })()`);
    await check('document.querySelectorAll("#chart-key [data-curve]").length===2 && !document.querySelector("#chart-key").textContent.includes("Unrelated LM Studio") && window.__lastSeries.filter(series=>series.points.length || series.failures?.length).length===2', 'A recorded source without the selected model has nothing to switch and stays out of the key');
    await check('!document.querySelector("#compare-body .graph-blurb").textContent.includes("Quant unknown")', 'A source without this model does not invent unknown quantization metadata in the chart subtitle');
    await screenshot('storage-comparison-desktop');
    await evaluate('document.querySelector("#compare-body .graph-card").scrollIntoView({block:"center"})');
    await screenshot('storage-chart-desktop');
    await evaluate('showResultsView("table")');
    await delay(100);
    await check('document.querySelector("#panel-summary").hidden && document.querySelector("#results-table").textContent.includes("Samsung") && document.querySelector("#results-table").textContent.includes("NVMe") && document.querySelector("#results-table").textContent.includes("110") && document.querySelector("#results-table").textContent.includes("55")', 'Table uses the same selected conditions and shows both measured values');
    await evaluate('[...document.querySelectorAll("#result-levels [data-level]")].find(node=>node.dataset.level==="2").click()');
    await delay(100);
    await evaluate('[...document.querySelectorAll("#result-levels [data-level]")].find(node=>node.dataset.level==="1").click()');
    await delay(100);
    await check('activeQueryLevels().join()==="2" && state.comparisonConcurrency===2', 'Query level toggles choose the two-query workload');
    await check('document.querySelector("#results-table").textContent.includes("351") && document.querySelector("#results-table").textContent.includes("timeout")', 'NVMe completion remains visible beside the SSD timeout without a fabricated result');
    await evaluate('showResultsView("compare")');
    await delay(100);
    await check('document.querySelector("#chart-key").textContent.includes("Samsung") && document.querySelector("#chart-key").textContent.includes("NVMe") && !document.querySelector("#chart-key").textContent.includes("Unrelated LM Studio") && document.querySelector("#compare-body .chart-readout").textContent.includes("600 s limit") && document.querySelector("#compare-body .chart-readout").textContent.includes("351.00 s") && document.querySelector("#compare-body .chart-readout").textContent.includes("unpaired") && !document.querySelector("#compare-body .chart-readout .metric-delta")', 'Failure-only SSD remains in the real legend and unpaired NVMe timing has no invented percentage delta');
    await check('[...document.querySelectorAll("#chart-key [data-curve]")].some(node=>node.textContent.includes("Samsung") && node.title.includes("2 matching cases") && node.title.includes("0 measured")) && [...document.querySelectorAll("#chart-key [data-curve]")].some(node=>node.textContent.includes("NVMe") && node.title.includes("2 matching cases") && node.title.includes("2 measured"))', 'Key entry counts update to the selected two-query workload and retain failed/skipped evidence');
    await evaluate('showResultsView("table")');
    await evaluate('document.querySelector("#results-all-measurements").parentElement.open=true');
    await delay(80);
    await check('document.querySelector("#results-all-measurements").textContent.includes("NVMe") && document.querySelector("#results-all-measurements").textContent.includes("Samsung") && [...document.querySelectorAll("#results-all-measurements tbody tr")].some(row=>/prompt tokens/i.test(row.cells[4]?.textContent || "") && Number(row.cells[5]?.textContent.replace(/[,\\s]/g,""))===80001) && document.querySelector("#results-all-measurements").textContent.includes("128")', 'Raw Table evidence retains both conditions, repeats, and exact actual per-query token counts');
    await send('Emulation.setDeviceMetricsOverride', {width:390,height:844,deviceScaleFactor:1,mobile:true});
    await evaluate('document.querySelector("#results-all-measurements").parentElement.open=false;showResultsView("compare");document.querySelector("#result-navigation").scrollIntoView({block:"start"});window.scrollBy(0,-90)');
    await delay(100);
    await check('document.documentElement.scrollWidth<=innerWidth+1', 'Shared storage comparison controls fit phone width');
    await screenshot('storage-comparison-phone');
    await evaluate('document.querySelector("#compare-body .graph-card").scrollIntoView({block:"start"});window.scrollBy(0,-(document.querySelector(".topbar").getBoundingClientRect().height+12))');
    await screenshot('storage-chart-phone');
    await send('Emulation.setDeviceMetricsOverride', {width:1440,height:1000,deviceScaleFactor:1,mobile:false});
    await evaluate(`(async()=>{
      const run={id:'mixed-config-test',label:'Mixed configuration test',status:'completed',config:{provider:'lmstudio'},results:[
        {model:'same-model',status:'ok',target_tokens:512,concurrency:1,configuration_id:'config-alpha',prompt_tokens:10,ttft_s:1,prefill_tps:10},
        {model:'same-model',status:'ok',target_tokens:512,concurrency:1,configuration_id:'config-beta',prompt_tokens:20,ttft_s:1,prefill_tps:20}
      ]};
      state.runCache[run.id]=run;state.runs.push({...run,n_results:2,n_ok:2});state.compare=new Set([run.id]);state.selected=run.id;
      state.conditions=null;state.visibleModels=null;state.comparisonAxis='context';state.comparisonConcurrency=1;state.queryLevels=null;state.metrics=new Set(['prefill_tps']);
      renderRunsList();showResultsView('compare');await renderResultsView();
    })()`);
    await check('window.__lastSeries.length===2 && window.__lastSeries.map(series=>series.points[0]?.y).sort((a,b)=>a-b).join() === "10,20" && document.querySelector("#chart-key").textContent.includes("config-alpha") && document.querySelector("#chart-key").textContent.includes("config-beta")', 'Configuration graphs keep same-workload measurements separate and identify both recorded settings');
    await evaluate('document.querySelector("[data-color-by=models]").click()');
    await delay(100);
    await check('document.querySelector("#chart-key").textContent.includes("Mixed configuration test") && new Set(window.__lastSeries.map(series=>series.color)).size===1 && new Set(window.__lastSeries.map(series=>series.marker)).size===2 && window.__lastSeries.length===2 && window.__lastSeries.map(series=>series.points[0]?.y).sort((a,b)=>a-b).join() === "10,20" && document.querySelector("#chart-key").textContent.includes("config-alpha") && document.querySelector("#chart-key").textContent.includes("config-beta")', 'Colouring by model still identifies the parent benchmark and keeps recorded conditions separate by marker');
    await evaluate('showResultsView("table")');
    await check('(()=>{const rows=[...document.querySelectorAll("#results-table tbody tr")].map(row=>row.textContent);return rows.some(row=>row.includes("10")) && rows.some(row=>row.includes("20")) && !rows.some(row=>/\\b15(?:\\.0+)?\\b/.test(row))})()', 'The table retains separate same-workload measurements rather than a mixed median');
    // Merging: one machine, two engines. The helper explains, the operator confirms, and undo restores both files.
    await evaluate(`(async()=>{
      await fetch('/test/merge-pair',{method:'POST'});
      state.env.capabilities.merge=true;state.compare=new Set();state.selected=null;state.conditions=null;state.visibleModels=null;
      showResultsView('summary');await loadRuns();
    })()`);
    for(let attempt=0;attempt<100 && !await evaluate('!!document.querySelector("#merge-suggested")');attempt++) await delay(50);
    await check('!document.querySelector("#merge-bar").hidden && /LM Studio \\+ Strata.*ran on 2× RTX 5070 Ti/.test(document.querySelector("#merge-bar").textContent) && document.querySelector("#merge-suggested").textContent.includes("Merge 2 benchmarks")', 'The library suggests merging two engines measured on one machine');
    await evaluate('(async()=>{await selectRun(state.runs.find(run=>!run.id.startsWith("merge-")).id);const box=document.querySelector(\'[data-run-select="merge-lm"]\');box.checked=true;box.dispatchEvent(new Event("change"));})()');
    for(let attempt=0;attempt<100 && !await evaluate('!!document.querySelector("#merge-ticked")');attempt++) await delay(50);
    await evaluate('document.querySelector("#merge-ticked").click()');
    for(let attempt=0;attempt<100 && !await evaluate('!!state.merge?.plan');attempt++) await delay(50);
    await check('document.querySelector("#merge-dialog").open && document.querySelector("#merge-start").disabled && document.querySelectorAll("#merge-content .merge-row[data-state=block]").length===1 && document.querySelector("#merge-content .merge-issue").textContent.includes("hardware") && !!document.querySelector("#merge-leave-out") && document.querySelector("#merge-more").textContent.includes("merge-strata")', 'A benchmark from another machine is refused with its reason, and same-machine candidates are offered');
    await evaluate('document.querySelector("#merge-leave-out").click()');
    for(let attempt=0;attempt<100 && !await evaluate('state.merge?.plan?.runs.length===1');attempt++) await delay(50);
    await evaluate('document.querySelector(\'#merge-more [data-merge-run="merge-strata"]\').click()');
    for(let attempt=0;attempt<100 && !await evaluate('state.merge?.plan?.runs.length===2');attempt++) await delay(50);
    await check('document.querySelector("#merge-start").disabled && document.querySelector("#merge-content .merge-row[data-state=confirm]") && document.querySelector(".merge-outcome").textContent.includes("Confirm where inference ran")', 'An endpoint benchmark is not attributed to this machine before the operator says so');
    await screenshot('merge-dialog-confirm');
    await evaluate('document.querySelector("[data-merge-confirm]").click()');
    for(let attempt=0;attempt<100 && !await evaluate('state.merge?.plan?.ok===true');attempt++) await delay(50);
    await check('!document.querySelector("#merge-start").disabled && document.querySelector("#merge-start").textContent==="Merge 2 benchmarks" && document.querySelector(".merge-outcome").textContent.includes("LM Studio + Strata") && (()=>{const dialog=document.querySelector("#merge-dialog").getBoundingClientRect(),button=document.querySelector("#merge-start").getBoundingClientRect();return button.bottom<=dialog.bottom && button.top>=dialog.top})()', 'The confirmed plan states the result and keeps the merge action in view');
    await evaluate('const name=document.querySelector("#merge-label");name.value="One machine";name.dispatchEvent(new Event("input",{bubbles:true}));document.querySelector("#merge-start").click()');
    for(let attempt=0;attempt<200 && !await evaluate('!state.merge && !!document.querySelector("#undo-merge")');attempt++) await delay(50);
    await check('!document.querySelector("#merge-dialog").open && state.selected==="merge-lm" && !state.runs.some(run=>run.id==="merge-strata") && document.querySelector("#run-detail h3").textContent==="One machine" && [...document.querySelectorAll("#run-detail .engine-badge")].map(node=>node.textContent).join()==="LM Studio,Strata" && document.querySelector(".merge-origin").textContent.includes("Merged from 2 benchmarks") && !document.querySelector("#run-detail").textContent.includes("Remote hardware unknown")', 'Merged engines share one benchmark attributed to the machine, with undo offered');
    await screenshot('merge-result');
    await evaluate('window.confirm=()=>true;document.querySelector("#undo-merge").click()');
    for(let attempt=0;attempt<200 && !await evaluate('state.runs.some(run=>run.id==="merge-strata") && state.compare.size===2');attempt++) await delay(50);
    await check('["merge-lm","merge-strata"].every(id=>state.compare.has(id)) && state.runs.find(run=>run.id==="merge-lm").merge===null && state.runs.find(run=>run.id==="merge-lm").label==="merge-lm" && !document.querySelector("#undo-merge")', 'Undo brings both original benchmarks back');
    // Similar models share a box that shows or hides them together.
    await evaluate(`(async()=>{
      const names=['org/Alpha-9B-Q4_K_M','alpha-9b@q8_0','other--Alpha-9B-4bit','beta-2b','beta-7b','solo'];
      const grouped={id:'grouped',label:'Grouped models',created_at:'2026-11-17T12:00:00+00:00',status:'completed',gpu:{name:'Fixture GPU'},config:{provider:'lmstudio',models:names},
        results:names.flatMap((model,index)=>[512,5000].map(size=>({model,target_tokens:size,concurrency:1,status:'ok',repeat:0,wall_s:10+index,prefill_tps:100+index,decode_tps:20+index,ttft_s:1,prompt_tokens:size,completion_tokens:50})))};
      state.runCache.grouped=grouped;state.runs.push({...grouped,models:names,n_results:12,n_ok:12});
      state.compare=new Set(['grouped']);state.selected='grouped';state.conditions=null;state.visibleModels=null;state.curveChoice.clear();
      renderRunsList();showResultsView('compare');await renderResultsView();
    })()`);
    for(let i=0;i<100&&!await evaluate('document.querySelectorAll("#visible-models .model-group").length===2');i++)await delay(50);
    const groupState='[...document.querySelectorAll("#visible-models [data-model-group]")].map(button=>button.getAttribute("aria-pressed")+":"+button.textContent.trim()).join(" | ")';
    await check('document.querySelector("#visible-models").classList.contains("grouped") && document.querySelectorAll("#visible-models .model-group").length===2 && document.querySelectorAll("#visible-models label.chip").length===6 && document.querySelectorAll("#visible-models .model-loose label.chip").length===1 && document.querySelector("#visible-models .model-loose").textContent.includes("solo")', 'Six models are boxed as one model in three variants, one family in two sizes, and one loose model');
    await check(`${groupState} === "true:Alpha-9B3 variants · all shown | true:beta2 sizes · all shown"`, 'Each box is named for what its models share and says how many are shown: '+await evaluate(groupState));
    await check('[...document.querySelectorAll("#visible-models .model-group")].every(box=>{const style=getComputedStyle(box),head=box.querySelector("[data-model-group]");return parseFloat(style.borderTopWidth)>=1 && style.borderTopStyle==="solid" && head.tagName==="BUTTON" && head.getBoundingClientRect().height>=16 && box.querySelector("[data-model-group-only]").textContent==="only"})', 'A box has a visible outline, a header button and an only button');
    await evaluate('document.querySelector("#visible-models [data-model-group=\\"0\\"]").click()');
    for(let i=0;i<100&&!await evaluate('state.visibleModels?.size===3');i++)await delay(50);
    await check(`state.visibleModels.size===3 && !["org/Alpha-9B-Q4_K_M","alpha-9b@q8_0","other--Alpha-9B-4bit"].some(model=>state.visibleModels.has(model)) && ${groupState}.startsWith("false:Alpha-9B3 variants · none shown") && !window.__lastSeries.some(series=>/alpha/i.test(series.model)) && document.querySelector("#visible-model-count").textContent==="3 of 6 shown"`, 'A box header hides all of its models at once: '+await evaluate(groupState));
    await evaluate('document.querySelector("#visible-models input[value=\\"alpha-9b@q8_0\\"]").click()');
    for(let i=0;i<100&&!await evaluate('state.visibleModels?.size===4');i++)await delay(50);
    await check(`${groupState}.startsWith("mixed:Alpha-9B3 variants · 1 shown")`, 'A box with some of its models shown says so: '+await evaluate(groupState));
    await evaluate('document.querySelector("#visible-models [data-model-group=\\"0\\"]").click()');
    for(let i=0;i<100&&!await evaluate('state.visibleModels?.size===6');i++)await delay(50);
    await check(`state.visibleModels.size===6 && ${groupState}.startsWith("true:Alpha-9B3 variants · all shown")`, 'Clicking a partly shown box shows all of its models');
    await evaluate('document.querySelector("#visible-models [data-model-group-only=\\"1\\"]").click()');
    for(let i=0;i<100&&!await evaluate('state.visibleModels?.size===2');i++)await delay(50);
    await check(`[...state.visibleModels].sort().join()==="beta-2b,beta-7b" && ${groupState} === "false:Alpha-9B3 variants · none shown | true:beta2 sizes · all shown" && window.__lastSeries.every(series=>/^beta-/.test(series.model))`, 'Only leaves just that box on the charts');
    await screenshot('model-groups');
    // Reopening the page: the last benchmark was an endpoint, the last Connect decides where it opens.
    const reopen = async (storage, ready) => {
      await evaluate(`(async()=>{localStorage.removeItem("wattever-plain-endpoint");for(const [key,value] of Object.entries(${JSON.stringify(storage)}))localStorage.setItem(key,value);for(const [path,body] of [["/test/discovery-failure",{enabled:false}],["/test/last-config",{config:{provider:"openai",endpoint_id:"endpoint-local-fixture",models:["external/model"],sizes:[512],concurrency_levels:[2],mtp_modes:["off"],gpu_offload:"provider_managed",context_policy:"provider_managed"}}]])await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})})()`);
      await send('Page.navigate', {url: `http://127.0.0.1:${port}`});
      for (let attempt = 0; attempt < 100 && !await evaluate('typeof state !== "undefined" && !!state.env && (' + ready + ')'); attempt++) await delay(100);
    };
    const opened = '({provider:document.querySelector("#helper-provider")?.value,models:[...(state.models||[])],note:!document.querySelector("#helper-switch-note")?.hidden,status:document.querySelector("#helper-status")?.textContent})';
    const notConnected = 'state.engine.status==="off" && state.models.size===0 && !document.querySelector("#model-list .model") && document.querySelector("#pill-lms span:last-child").textContent==="No engine connected" && document.querySelector("#start-btn").disabled';
    const connect = async ready => { await evaluate('document.querySelector("#helper-connect").click()'); for (let attempt = 0; attempt < 100 && !await evaluate(ready); attempt++) await delay(50); };
    await reopen({'wattever-helper-provider': 'lmstudio'}, 'state.runs.length>0');
    await check(notConnected + ' && document.querySelector("#helper-provider").value==="lmstudio" && collectConfig().gpu_offload!=="provider_managed" && [...state.sizes].join()==="512" && collectConfig().concurrency_levels.join()==="2" && document.querySelector("#helper-switch-note").hidden', 'After connecting LM Studio the page reopens on the LM Studio form with the saved workload, and contacts nothing: ' + JSON.stringify(await evaluate(opened)));
    await check('(async()=>{const asked=await(await fetch("/test/env-requests")).json();return asked[asked.length-1].connect===false;})()', 'Reopening asks for the environment without connecting');
    await connect('state.engine.status==="on"');
    await check('state.env.requested_provider==="lmstudio" && state.models.has("model-a") && !state.models.has("external/model") && [...state.sizes].join()==="512" && collectConfig().concurrency_levels.join()==="2"', 'Connect then lists LM Studio, not the endpoint of the last benchmark, and keeps the workload: ' + JSON.stringify(await evaluate(opened)));
    await reopen({'wattever-helper-provider': 'openai', 'wattever-openai-base-url': 'http://127.0.0.1:1234/v1'}, 'state.runs.length>0');
    await check(notConnected + ' && document.querySelector("#helper-provider").value==="openai" && document.querySelector("#helper-url").value==="http://127.0.0.1:1234/v1" && document.querySelector("#helper-switch-note").hidden', 'A saved endpoint address reopens filled in but not connected: ' + JSON.stringify(await evaluate(opened)));
    await connect('document.querySelector("#helper-status").dataset.state==="ready" && !document.querySelector("#helper-connect").disabled');
    await check('document.querySelector("#helper-provider").value==="lmstudio" && !document.querySelector("#helper-switch-note").hidden && state.models.has("model-a") && !document.querySelector("#start-btn").disabled', 'Connecting a saved address that is LM Studio on this computer connects it as managed, with the notice: ' + JSON.stringify(await evaluate(opened)));
    await evaluate('document.querySelector("#helper-plain-endpoint").click()');
    for(let attempt=0;attempt<60 && !await evaluate('state.models.has("external/model") && !document.querySelector("#helper-connect").disabled');attempt++)await delay(50);
    await reopen({'wattever-helper-provider': 'openai', 'wattever-openai-base-url': 'http://127.0.0.1:1234/v1', 'wattever-plain-endpoint': 'http://127.0.0.1:1234/v1'}, 'state.runs.length>0');
    await check(notConnected + ' && document.querySelector("#helper-provider").value==="openai"', 'The plain-endpoint form reopens not connected: ' + JSON.stringify(await evaluate(opened)));
    await connect('state.models.has("external/model") && !document.querySelector("#helper-connect").disabled');
    await check('document.querySelector("#helper-provider").value==="openai" && state.models.has("external/model") && document.querySelector("#helper-switch-note").hidden && collectConfig().provider==="openai"', 'The plain-endpoint choice survives reopening the page: ' + JSON.stringify(await evaluate(opened)));
    // Connect changes where models come from, not the settings: what the saved form and the user set stays.
    await evaluate(`(async()=>{localStorage.clear();localStorage.setItem("wattever-helper-provider","lmstudio");await fetch("/test/last-config",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({config:{provider:"lmstudio",models:["model-b"],sizes:[512],concurrency_levels:[2],mtp_modes:["off"],gpu_offload:"off",per_test_timeout_s:777,load_timeout_s:333,context_headroom:2048,warmup:false,fallback_on_failure:false,stop_model_after_failure:false}})})})()`);
    await send('Page.navigate', {url: `http://127.0.0.1:${port}`});
    for (let attempt = 0; attempt < 100 && !await evaluate('typeof state !== "undefined" && !!state.env && state.runs.length>0'); attempt++) await delay(100);
    const settings = 'JSON.stringify([document.querySelector("#per-test-timeout").value,document.querySelector("#load-timeout").value,document.querySelector("#headroom").value,document.querySelector("#offload").value,document.querySelector("#warmup").checked,document.querySelector("#fallback").checked,document.querySelector("#stop-after-fail").checked,[...state.sizes].join(),[...state.concurrencyLevels].join()])';
    const saved = await evaluate(settings);
    assert.equal(saved, JSON.stringify(['777','333','2048','off',false,false,false,'512','2']), 'The saved settings fill the form before any connection');
    await evaluate('document.querySelector("#headroom").value="4096";document.querySelector("#headroom").dispatchEvent(new Event("input",{bubbles:true}))');
    await connect('state.engine.status==="on"');
    assert.equal(await evaluate(settings), JSON.stringify(['777','333','4096','off',false,false,false,'512','2']), 'Connect keeps the saved settings and what was typed before it');
    await check('[...state.models].join()==="model-b" && !document.querySelector("#start-btn").disabled', 'Connect ticks the models of the saved form, not the defaults: '+await evaluate('[...state.models].join()'));
    // Start follows the connection: off while connecting, off after a failed reconnect, and nothing stale is offered.
    await evaluate('document.querySelector("#helper-connect").click()');
    await check('state.engine.status==="connecting" && document.querySelector("#start-btn").disabled', 'Start is unavailable while a connection is being made');
    for (let attempt = 0; attempt < 100 && !await evaluate('state.engine.status==="on"'); attempt++) await delay(50);
    await evaluate('fetch("/test/lmstudio-down",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({enabled:true})}).then(()=>document.querySelector("#helper-connect").click())');
    for (let attempt = 0; attempt < 100 && !await evaluate('state.engine.status==="error"'); attempt++) await delay(50);
    await check('document.querySelector("#start-btn").disabled && state.models.size===0 && !document.querySelector("#model-list .model") && document.querySelector("#pill-lms span:last-child").textContent==="No engine connected" && document.querySelector("#run-condition-badges").textContent.trim()===""', 'A failed reconnect leaves no models of the earlier connection and Start off');
    await evaluate('fetch("/test/lmstudio-down",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({enabled:false})})');
    // Changing the provider while a connection is in flight ends that attempt on screen too.
    await evaluate('document.querySelector("#helper-connect").click();document.querySelector("#helper-provider").value="openai";document.querySelector("#helper-provider").dispatchEvent(new Event("change"))');
    await delay(600);
    await check('state.engine.status==="off" && document.querySelector("#pill-lms span:last-child").textContent==="No engine connected" && document.querySelector("#helper-status").textContent==="Not connected" && !document.querySelector("#helper-connect").disabled && document.querySelector("#start-btn").disabled && !document.querySelector(".busy-veil")', 'A connection abandoned by changing the provider does not stay on "Connecting": '+await evaluate('document.querySelector("#helper-status").textContent'));
    assert.equal(errors.length, 0, JSON.stringify(errors));
    console.log('Browser checks passed: Results, metadata, responsive layout, capability charts, audit inspector, filters, pagination, keyboard controls, paired protocol comparisons and merging.');
    console.log('Screenshots: ' + artifacts);
    await send('Browser.close').catch(() => {});
  } finally {
    rejectPending(pending,Error('Owned UI browser cleanup'));
    if (ws) ws.close();
    if (browser) browser.kill();
    fixture.kill();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
