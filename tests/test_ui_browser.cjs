// Optional end-to-end check: Node 22+ and a local Edge/Chrome. No npm packages.
// WATTEVER_BROWSER can select a browser executable on any platform.
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');
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
    const pending = new Map(), errors = [];
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
    const send = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++next;
      pending.set(id, {resolve, reject});
      ws.send(JSON.stringify({id, method, params}));
    });
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
    await check('document.querySelector("#panel-run").classList.contains("active")', 'Run is the default screen');
    await check('document.querySelector("#app-version").textContent === "v2.4.1"', 'Version comes from the server');
    await check('!document.querySelector("#machine-card").open && !document.querySelector("#advanced-settings").open', 'Details start collapsed');
    await check('!document.querySelector("#size-quick, #concurrency-quick, #cpu-tdp, #other-idle")', 'No duplicate shortcuts or power inputs');
    await check('document.querySelector("#run-label").value === "RTX 5060 Ti" && document.querySelector("#machine-badges").textContent.includes("32 GiB RAM") && document.querySelector("#machine-badges").textContent.includes("16 GiB VRAM")', 'Concise hardware name and machine badges are filled automatically');
    await check('document.querySelector("#run-notes").value.includes("Reasoning: off")', 'Settings notes are filled automatically');
    await clickValue('#concurrency-chips input', 5);
    await check('[...state.concurrencyLevels].join() === "1,2,3,4,5"', 'Five selects one through five');
    await check('document.querySelector("#run-notes").value.includes("Simultaneous queries: 1, 2, 3, 4, 5")', 'Notes follow workload changes');
    await clickValue('#concurrency-chips input', 2);
    await check('[...state.concurrencyLevels].join() === "1,2"', 'Two removes larger values');
    await clickValue('#size-chips input', 150000);
    await check('state.sizes.has(120000) && state.sizes.has(150000) && !state.sizes.has(200000)', 'Context includes lower sizes');
    await clickValue('#size-chips input', 20000);
    await check('[...state.sizes].join() === "512,5000,10000,20000"', 'Context can be reduced');
    await evaluate('Array.from(document.querySelectorAll("#concurrency-chips input")).find(input => Number(input.value) === 3).focus()');
    await send('Input.dispatchKeyEvent', {type: 'keyDown', key: ' ', code: 'Space'});
    await send('Input.dispatchKeyEvent', {type: 'keyUp', key: ' ', code: 'Space'});
    await check('[...state.concurrencyLevels].join() === "1,2,3"', 'Keyboard selection works');
    await evaluate('selectModels("none")');
    await check('state.models.size === 0', 'None stays empty');
    await evaluate('selectModels("all")');
    await check('state.models.size === state.env.models.length', 'All selects all models');
    await evaluate('document.querySelector("#run-label").value="Custom name"; document.querySelector("#run-label").dispatchEvent(new Event("input")); document.querySelector("#run-notes").value="Custom notes"; document.querySelector("#run-notes").dispatchEvent(new Event("input"))');
    await clickValue('#concurrency-chips input', 2);
    await check('document.querySelector("#run-label").value === "Custom name" && document.querySelector("#run-notes").value === "Custom notes"', 'Custom text is preserved');
    await evaluate('document.querySelector("#run-notes").value=""; document.querySelector("#run-notes").dispatchEvent(new Event("blur"))');
    await check('document.querySelector("#run-notes").value.includes("Simultaneous queries: 1, 2")', 'Clearing notes restores automatic text');
    await evaluate('document.querySelector("#clean-run").click()');
    await check('[...state.concurrencyLevels].join() === "1" && state.models.size === state.env.models.length', 'Clean reset works without a prompt');
    await check('document.querySelector("#run-label").value.includes("RTX 5060 Ti") && document.querySelector("#run-notes").value.includes("all 12 installed models")', 'Reset regenerates metadata');
    await check('[...document.querySelectorAll("input,select,textarea")].every(input => !!input.title)', 'Every field has a tooltip');
    await check('document.documentElement.scrollWidth <= innerWidth', 'Desktop has no horizontal overflow');
    await check('Math.abs(document.querySelector(".models-card").getBoundingClientRect().height - document.querySelector(".workload-stack").getBoundingClientRect().height) < 2', 'Setup columns have equal height');
    await screenshot('run-desktop');
    await evaluate('document.querySelector("#machine-card summary").click()');
    await check('document.querySelector("#machine-card").open', 'Machine details expand');
    await evaluate('document.querySelector("#machine-card summary").click(); document.querySelector("#advanced-settings summary").click()');
    await check('document.querySelector("#advanced-settings").open', 'Advanced settings expand');
    await evaluate('document.querySelector("#advanced-settings summary").click(); showPanel("results")');
    await delay(150);
    await check('document.querySelectorAll(".topbar .tab").length === 3 && state.resultView === "summary"', 'Run, Results and Capabilities are available; Summary opens first');
    await check('!!document.querySelector("#summary-body svg")', 'Summary has a chart');
    await screenshot('summary-desktop');
    await evaluate('document.querySelector("#run-detail details").open=true;document.querySelector("#run-detail .telemetry-report").open=true');
    await delay(100);
    await check('document.querySelectorAll("#run-detail .telemetry-history svg[role=img]").length===2 && document.querySelector("#run-detail .telemetry-report").textContent.includes("95.0%") && document.querySelector("#run-detail .telemetry-report").textContent.includes("not proof of throttling")', 'Saved telemetry has coverage-aware summaries and usage/temperature histories: '+JSON.stringify(await evaluate('({count:document.querySelectorAll("#run-detail .telemetry-history svg[role=img]").length,text:document.querySelector("#run-detail .telemetry-report").textContent,open:document.querySelector("#run-detail .telemetry-report").open})'))+' errors='+JSON.stringify(errors));
    await evaluate('document.querySelector("#run-detail details").open=false');
    await check('!Array.from(document.querySelectorAll("#summary-body .legend-key")).some(node=>node.textContent==="Models")', 'Model names are not repeated in the chart legend');
    await evaluate('document.querySelector("#run-detail details").open=true; window.__modelInput=document.querySelector("#visible-models input");window.__detailsNode=document.querySelector("#run-detail").firstElementChild; const rect=document.querySelector("#visible-models").getBoundingClientRect();window.scrollTo(0,window.scrollY+rect.top-140);window.__modelScrollY=window.scrollY');
    const modelPosition = await evaluate('(()=>{const rect=document.querySelector("#visible-models .chip").getBoundingClientRect();return{x:rect.left+rect.width/2,y:rect.top+rect.height/2}})()');
    await send('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',buttons:1,clickCount:1,...modelPosition});
    await send('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',buttons:0,clickCount:1,...modelPosition});
    await delay(100);
    await check('Math.abs(window.scrollY-window.__modelScrollY)<1 && window.__modelInput===document.querySelector("#visible-models input")', 'Clicking a model preserves the page position and the actual input node');
    await check('window.__detailsNode===document.querySelector("#run-detail").firstElementChild && document.querySelector("#run-detail details").open', 'Filtering does not rebuild or collapse benchmark details');
    await evaluate('document.querySelector("#hide-all-models").click()');
    await delay(100);
    await check('Math.abs(window.scrollY-window.__modelScrollY)<1', 'Hiding all models also preserves the viewport');
    await evaluate('document.querySelector("#show-all-models").click()');
    await delay(100);
    await check('Math.abs(window.scrollY-window.__modelScrollY)<1', 'Showing all models preserves the viewport');
    await evaluate('document.querySelector("#run-detail details").open=false;window.scrollTo(0,0)');

    await evaluate('document.querySelector("[data-result-view=table]").click()');
    await delay(100);
    await check('!document.querySelector("#result-table-view").hidden && !!document.querySelector("#results-table table")', 'Table view opens');
    await evaluate('Object.defineProperty(navigator, "clipboard", {configurable:true, value:{writeText: async text => { window.__copied = text; }}}); document.querySelector("#results-table .copy-table").click()');
    await delay(50);
    await check('window.__copied.includes("Model") && window.__copied.includes("model-a") && window.__copied.includes("100") && window.__copied.includes("\\t")', 'Copy produces tab-separated headers and data');
    await evaluate('Array.from(document.querySelectorAll("[data-run-select]")).find(input => !input.checked).click()');
    await delay(100);
    await check('state.compare.size === 2 && document.querySelector("#results-table").textContent.includes("GTX 1080 Ti")', 'Multi-selection updates the same table');
    await check('document.querySelector("#results-table table").scrollWidth <= document.querySelector("#results-table table").clientWidth + 1', 'Comparison table fits without horizontal scrolling');
    await screenshot('table-desktop');
    await evaluate('showResultsView("summary"); window.__originalLine=Charts.line; Charts.line=(container,options)=>{window.__lastSeries=options.series; return window.__originalLine(container,options)}');
    await check('new Set(Array.from(document.querySelectorAll("#runs-list .run")).map(node => getComputedStyle(node).borderLeftColor)).size === 2', 'Benchmark colors are restored and distinct');
    await evaluate('document.querySelector("#hide-all-models").click()');
    await delay(50);
    await check('document.querySelector("#summary-body").textContent.includes("Choose models") && state.visibleModels.size===0', 'None hides all models rather than resetting to all');
    await evaluate('Array.from(document.querySelectorAll("#visible-models input")).find(input=>input.value==="model-b").click()');
    await delay(50);
    await evaluate('Array.from(document.querySelectorAll("#visible-models input")).find(input=>input.value==="model-c").click()');
    await delay(50);
    await check('state.visibleModels.size===2 && window.__lastSeries.length===3 && window.__lastSeries.every(series=>!series.id.includes("model-a"))', 'Exactly the selected models appear across benchmarks');
    await check('new Set(window.__lastSeries.filter(series=>series.id.includes("model-b")).map(series=>series.color)).size===1 && new Set(window.__lastSeries.map(series=>series.color)).size===2', 'Model coloring shares each model color across benchmarks');
    await evaluate('document.querySelector("[data-color-by=benchmarks]").click()');
    await delay(50);
    await check('window.__lastSeries.filter(series=>series.id.startsWith("smoke|")).every(series=>series.color===resolveColor(runColor("smoke"))) && new Set(window.__lastSeries.map(series=>series.color)).size===2', 'Benchmark coloring matches sidebar colors');
    await screenshot('benchmark-colors-desktop');
    await evaluate('selectRun("comparison")');
    await delay(50);
    await check('state.visibleModels.size===2 && shownModels(allModelsOf([state.runCache.comparison])).join() === "model-b"', 'Selection is preserved when a benchmark lacks one chosen model');
    await evaluate('Array.from(document.querySelectorAll("[data-run-select]")).find(input=>input.dataset.runSelect==="smoke").click()');
    await delay(50);
    await check('state.compare.size===2 && window.__lastSeries.length===3', 'Re-adding a benchmark restores its chosen model without showing other models');

    await evaluate('showResultsView("table")');
    await delay(50);
    await check('!document.querySelector("#results-table tbody").textContent.includes("model-a") && document.querySelector("#results-table tbody").textContent.includes("model-b")', 'Model selection applies to tables');
    await evaluate('showResultsView("compare")');
    await delay(50);
    await check('document.querySelectorAll("#facet-grid .facet").length===2 && state.visibleModels.size===2', 'Model selection persists in Compare');
    await evaluate('document.querySelector("#show-all-models").click(); document.querySelector("[data-color-by=models]").click(); showResultsView("table")');
    await delay(50);
    await evaluate('showResultsView("summary"); Array.from(document.querySelectorAll("#result-metrics input")).find(input=>input.value==="decode_tps").click()');
    await delay(100);
    await check('state.metrics.has("prefill_tps") && state.metrics.has("decode_tps") && window.__lastSeries.length===5 && document.querySelectorAll("#summary-body .plot").length===2', 'Prefill and decode each have their own chart');
    await check('Array.from(document.querySelectorAll("#summary-body h3")).map(node=>node.textContent).join("|").includes("Prefill speed|Decode speed") && !document.querySelector(".measure-legend")', 'Separate metrics have clear chart headings');
    await check('Math.abs(document.querySelector(".benchmark-library").getBoundingClientRect().height-document.querySelector(".results-workspace").getBoundingClientRect().height)<2', 'Benchmark library matches the right panel height');
    await evaluate('Array.from(document.querySelectorAll("#result-metrics input")).find(input=>input.value==="ttft_s").click()');
    await delay(100);
    await check('document.querySelectorAll("#summary-body .plot").length===3', 'Every selected metric uses a separate summary chart');
    await check('Math.abs(document.querySelector(".benchmark-library").getBoundingClientRect().height-document.querySelector(".results-workspace").getBoundingClientRect().height)<2', 'Sidebar follows the expanded multi-chart height');
    await screenshot('multiple-measures-desktop');
    await evaluate('showResultsView("table")');
    await delay(100);
    await check('document.querySelectorAll("#results-table table").length===1 && document.querySelectorAll("#results-table .copy-table").length===1 && document.querySelectorAll("#results-table thead [data-measure]").length===3', 'Selected measures become columns in one copyable table');
    await check('document.querySelector("#results-table thead tr").cells.length===7', 'Three identity columns, three measures, one status column');
    await evaluate('document.querySelector("#results-table .copy-table").click()');
    await delay(30);
    await check('window.__copied.split("\\n")[0].includes("Prefill speed") && window.__copied.split("\\n")[0].includes("Decode speed") && window.__copied.split("\\n")[0].includes("Time to first token") && window.__copied.split("\\n")[1].split("\\t").length===7', 'Clipboard keeps all measures aligned on the same rows');
    await check('new Set(Array.from(document.querySelectorAll("#results-table tr[data-model=model-a] .measure-cell")).map(cell=>getComputedStyle(cell).backgroundColor)).size===1 && new Set(Array.from(document.querySelectorAll("#results-table tbody tr")).map(row=>getComputedStyle(row.querySelector(".measure-cell")).backgroundColor)).size===3', 'Cells use model colors across benchmarks');
    await evaluate('document.querySelector("[data-color-by=benchmarks]").click()');
    await delay(50);
    await check('new Set(Array.from(document.querySelectorAll("#results-table tr[data-benchmark=smoke] .measure-cell")).map(cell=>getComputedStyle(cell).backgroundColor)).size===1 && new Set(Array.from(document.querySelectorAll("#results-table tr[data-model=model-a] .measure-cell")).map(cell=>getComputedStyle(cell).backgroundColor)).size===2', 'Cells switch to benchmark colors across models');
    await check('document.querySelector("#results-table table").scrollWidth<=document.querySelector("#results-table table").clientWidth+1', 'The combined table fits without horizontal scrolling');
    await evaluate('document.querySelector("#results-table").scrollIntoView({block:"center"})');
    await screenshot('combined-table-desktop');
    await evaluate('window.scrollTo(0,0)');
    await evaluate('document.querySelector("[data-color-by=models]").click()');
    await delay(30);
    await evaluate('showResultsView("compare"); document.querySelector("[data-compare-view=table]").click()');
    await delay(100);
    await check('document.querySelectorAll("#compare-body table").length===1 && document.querySelectorAll("#compare-body thead [data-measure]").length===3', 'Compare also uses one combined table');
    await evaluate('document.querySelector("[data-compare-view=charts]").click()');
    await delay(100);
    await check('document.querySelectorAll("#facet-grid .facet").length===9 && !Array.from(document.querySelectorAll("#facet-grid svg[role=img]")).some(svg=>svg.getAttribute("aria-label").includes("Prefill speed, Decode speed"))', 'Compare separates every selected metric for each model');
    await evaluate('document.querySelector("#results-controls .comparison-concurrency").value="2"; document.querySelector("#results-controls .comparison-concurrency").dispatchEvent(new Event("change"))');
    await delay(50);
    await check('state.metrics.size===3 && state.metrics.has("decode_tps")', 'Changing query count preserves a multi-measure selection');
    await evaluate('state.comparisonConcurrency=1;document.querySelector(".comparison-concurrency").value="1";state.metrics.clear();renderMetricSeg();showResultsView("summary")');
    await delay(50);
    await check('document.querySelector("#summary-body").textContent.includes("Choose measures")', 'An empty measure selection stays empty');
    await evaluate('Array.from(document.querySelectorAll("#result-metrics input")).find(input=>input.value==="prefill_tps").click();showResultsView("table")');
    await delay(50);

    await evaluate('for(let i=0;i<4;i++){const run=JSON.parse(JSON.stringify(state.runCache.smoke));run.id="extra"+i;run.label="Hardware configuration with a long GPU and CPU name "+i;run.results.push({model:"model-a",target_tokens:10000,concurrency:1,status:"failed",fail_kind:"load_failed"});state.runCache[run.id]=run;state.runs.push({...state.runs[0],id:run.id,label:run.label});state.compare.add(run.id)}; renderResultsView()');
    await delay(100);
    await check('document.querySelector("#results-table table thead tr").cells.length === 5 && document.querySelector("#results-table").textContent.includes("load failed")', 'Many benchmarks use a fitting long table and preserve failures');
    await check('document.querySelector("#results-table table").scrollWidth <= document.querySelector("#results-table table").clientWidth + 1', 'Many-benchmark table fits');
    await evaluate('loadRuns()');
    await delay(100);

    await check('Array.from(document.querySelectorAll("table")).every(table => table.parentElement.querySelector(".copy-table"))', 'Every table shares the copy control');
    await evaluate('document.querySelector("[data-result-view=compare]").click(); document.querySelector("[data-compare-view=table]").click()');
    await delay(100);
    await check('!!document.querySelector("#compare-body table") && !document.querySelector("#compare-body .plot svg")', 'Comparison table has its own view');
    await screenshot('compare-table-desktop');
    await evaluate('document.querySelector("[data-compare-view=charts]").click()');
    await delay(100);
    await check('!!document.querySelector("#compare-body .plot svg") && !document.querySelector("#compare-body table")', 'Comparison charts have their own view');
    await evaluate('showPanel("compare"); document.querySelector("#results-controls .comparison-concurrency").value="2"; document.querySelector("#results-controls .comparison-concurrency").dispatchEvent(new Event("change"))');
    await delay(200);
    await check('state.metrics.has("aggregate_completion_tps")', 'Concurrent comparison selects throughput');
    await evaluate('showPanel("results")');
    await screenshot('results-desktop');
    await check('!!document.querySelector("#runs-list .run-delete")', 'Delete is visible in the benchmark list');
    await evaluate('showPanel("run")');
    await send('Emulation.setDeviceMetricsOverride', {width: 390, height: 844, deviceScaleFactor: 1, mobile: true});
    await check('document.documentElement.scrollWidth <= innerWidth', 'Phone has no horizontal overflow');
    await screenshot('run-phone');
    await evaluate('showPanel("results")');
    await check('document.documentElement.scrollWidth <= innerWidth', 'Phone results have no horizontal overflow');
    await screenshot('results-phone');
    await evaluate('showResultsView("table")');
    await delay(100);
    await check('document.documentElement.scrollWidth <= innerWidth && document.querySelector("#results-table table").scrollWidth <= document.querySelector("#results-table table").clientWidth + 1', 'Phone table fits without horizontal scrolling');
    await screenshot('table-phone');
    await evaluate('document.querySelector("#results-all-measurements").parentElement.open=true');
    await delay(50);
    await check('Array.from(document.querySelectorAll("#results-all-measurements table")).every(table => table.scrollWidth <= table.clientWidth + 1)', 'Detailed tables also fit');

    // Shrinking measure groups must keep both pointer position and keyboard focus stable.
    for (const view of ['summary', 'table', 'compare']) {
      await evaluate(`state.metrics=new Set(['prefill_tps','decode_tps','ttft_s']);renderMetricSeg();showResultsView(${JSON.stringify(view)})`);
      await delay(100);
      for (const key of ['ttft_s', 'decode_tps', 'prefill_tps', 'prefill_tps', 'decode_tps']) {
        await evaluate(`Array.from(document.querySelectorAll('#result-metrics input')).find(input=>input.value===${JSON.stringify(key)}).closest('.chip').scrollIntoView({block:'center'});window.__metricTop=scrollY;window.__metricInput=Array.from(document.querySelectorAll('#result-metrics input')).find(input=>input.value===${JSON.stringify(key)})`);
        const metricPosition = await evaluate(`(()=>{const r=window.__metricInput.closest('.chip').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);
        await send('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',buttons:1,clickCount:1,...metricPosition});
        await send('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',buttons:0,clickCount:1,...metricPosition});
        await delay(120);
        await check('Math.abs(scrollY-window.__metricTop)<1 && window.__metricInput.isConnected && document.activeElement===window.__metricInput', 'Measure toggles preserve scroll and focus in '+view+' for '+key);
      }
    }
    await evaluate('showResultsView("summary")');

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

    await evaluate('state.benchmarkRunning=false;state.compare=new Set(["smoke"]);state.visibleModels=new Set(["model-b"]);selectRun("smoke")');
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

    await evaluate('state.comparisonConcurrency=1;document.querySelector(".comparison-concurrency").value="1";state.metrics=new Set(["prefill_tps"]);renderMetricSeg();state.visibleModels=null;fetch("/test/start",{method:"POST"}).then(response=>response.json())');
    await evaluate('showPanel("results");showResultsView("table")');
    for(let attempt=0;attempt<80 && !await evaluate('!!state.runCache.live && state.compare.has("live") && state.runs.some(run=>run.id==="live" && run.status==="running")');attempt++) await delay(50);
    await check('state.compare.has("live") && state.runs.some(run=>run.id==="live" && run.status==="running")', 'An ongoing benchmark appears before its first completed test');
    await check('!!document.querySelector("#run-detail .live-result-notice") && document.querySelector("#run-detail .live-result-notice").textContent.includes("0 completed")', 'Results marks the run Live while waiting for its first test');
    await check('document.querySelector("#run-detail #delete-run").disabled && document.querySelector("#runs-list .run-delete").disabled', 'Active benchmark cannot be deleted');
    await evaluate('state.metrics.add("decode_tps");renderMetricSeg();state.visibleModels=new Set(["model-a"]);state.colorBy="benchmarks";fetch("/test/result",{method:"POST"}).then(response=>response.json())');
    for(let attempt=0;attempt<80 && !await evaluate('!!document.querySelector("#results-table [data-measure=prefill_tps]") && document.querySelector("#results-table tbody").textContent.includes("321")');attempt++) await delay(50);
    await check('state.runCache.live.results.length===1 && state.liveResults.length===1 && document.querySelector("#results-table tbody").textContent.includes("321")', 'Completed tests appear automatically in the running Results table');
    await check('state.metrics.size===2 && state.visibleModels.has("model-a") && state.colorBy==="benchmarks"', 'Live updates preserve measures, models and color mode');
    await evaluate('state.compare.clear();state.selected=null;renderRunsList();renderResultsView();fetch("/test/result",{method:"POST"}).then(response=>response.json())');
    for(let attempt=0;attempt<80 && !await evaluate('state.runCache.live.results.length===2');attempt++) await delay(50);
    await check('state.compare.size===0 && state.runCache.live.results.length===2', 'Live updates respect an intentionally deselected benchmark');
    await evaluate('selectRun("live");showResultsView("summary")');
    await delay(80);
    await check('!!document.querySelector("#summary-body svg") && document.querySelector("#run-detail .live-result-notice").textContent.includes("2 completed")', 'Summary shows partial live results');
    await screenshot('live-results-desktop');
    await evaluate('fetch("/test/finish",{method:"POST"}).then(response=>response.json())');
    for(let attempt=0;attempt<80 && !await evaluate('state.runCache.live.status==="completed" && state.poller===null');attempt++) await delay(50);
    await check('state.runCache.live.status==="completed" && !document.querySelector("#run-detail .live-result-notice") && !document.querySelector("#run-detail #delete-run").disabled', 'Completion refreshes final data and clears the Live state');
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
    await evaluate('document.querySelector("#helper-provider").value="lmstudio";document.querySelector("#helper-provider").dispatchEvent(new Event("change"))');
    await check('document.querySelector("#start-btn").disabled', 'Switching back cannot send stale remote model IDs to LM Studio');
    await evaluate('connectHelperEndpoint()');
    await check('!state.models.has("external/model") && !state.models.has("manual/model") && !document.querySelector("#start-btn").disabled', 'Reconnecting restores LM Studio inventory: ' + JSON.stringify(await evaluate('({models:[...state.models],provider:document.querySelector("#helper-provider").value,needs:state.providerNeedsConnection,status:document.querySelector("#helper-status").textContent})')));
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
    await check('window.__capStartConfig.endpoint_id===state.detectedEndpoint.id && window.__capStartConfig.endpoint_label===state.detectedEndpoint.label && window.__capStartConfig.endpoint_reasoning_effort==="none" && window.__capStartConfig.sample_size===24', 'Capability launch reuses automatic endpoint identity and deterministic sample settings without manual IDs');
    await check('window.__capStartConfig.max_tokens===32768 && window.__capStartConfig.timeout_s===0 && window.__capStartConfig.temperature===0 && window.__capStartConfig.token_limit_field==="max_tokens"', 'Capability launch sends its own quality-generation settings to the helper');
    await evaluate('const box=document.querySelector("#cap-runs input[value=\\"cap-ui-comparison\\"]");if(!box.checked)box.click()');
    for(let attempt=0;attempt<60 && !await evaluate('document.querySelectorAll("#cap-comparison .cap-comparison-bar").length===2');attempt++)await delay(50);
    await check('!document.querySelector("#cap-comparison").hidden && document.querySelectorAll("#cap-comparison .cap-comparison-bar").length===2 && document.querySelector("#cap-comparison .cap-paired-table").textContent.includes("6.8 pp") && !document.querySelector("#cap-comparison .cap-paired-table").textContent.includes("7.0 pp") && !document.querySelector("#cap-comparison .cap-paired-table").textContent.includes("Not comparable")', 'Matching protocols use the paired outcome change rather than the raw accuracy difference when coverage differs');
    await check('[...document.querySelectorAll("#cap-comparison .cap-paired-table tbody tr small")].some(node=>/unscored|ungraded/.test(node.textContent) && /(?:^|\\D)4(?:\\D|$)/.test(node.textContent))', 'Paired evidence includes both-unscored observations rather than silently reporting none');
    await evaluate('document.querySelector("#cap-runs input[value=\\"cap-ui-different\\"]").click()');
    for(let attempt=0;attempt<60 && !await evaluate('!!document.querySelector("#cap-comparison .cap-notice")');attempt++)await delay(50);
    await check('document.querySelector("#cap-comparison .cap-notice").textContent.includes("Different evaluation protocols") && [...document.querySelectorAll("#cap-comparison .cap-paired-table tbody tr")].some(row=>row.textContent.includes("org/different-protocol") && row.textContent.includes("Not comparable") && !row.querySelector(".cap-positive,.cap-negative"))', 'Different token budgets or protocols show a warning and no fabricated paired delta');
    await evaluate('document.querySelector("#cap-comparison").scrollIntoView({block:"start"})');
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
        requests:Array.from({length:c},()=>({status,prompt_tokens:size+1,completion_tokens:128,wall_s:wall,ttft_s:10*scale,prefill_tps:100/scale,decode_tps:20/scale}))});
      state.runCache.live.storage_comparison={protocol:'wattever-storage-comparison-v1',baseline_label:'Samsung SSD',alternative_label:'NVMe',
        baseline_results:[make(512,1,0,100),make(512,1,1,120),make(80000,1,0,300),make(80000,1,1,320),make(80000,2,0,600,1,'failed'),make(80000,2,1,null,1,'skipped')],
        alternate_results:[make(512,1,0,50,.5),make(512,1,1,60,.5),make(80000,1,0,150,.5),make(80000,1,1,160,.5),make(80000,2,0,350,.5),make(80000,2,1,352,.5)]};
      state.detailSignature=null;showPanel('results');await selectRun('live');
    })()`);
    for(let i=0;i<100&&!await evaluate('!!document.querySelector("[data-storage-comparison=live]")');i++)await delay(50);
    await check('!!document.querySelector("[data-storage-comparison=live]") && !document.querySelector("[data-storage-comparison=live]").closest("details")', 'Storage comparison is visible outside machine disclosures');
    await check('document.querySelector("[data-storage-filter=size]").value==="80000" && document.querySelectorAll(".storage-numbers tbody tr").length===2', 'Largest prompt defaults to both query counts');
    await check('document.querySelectorAll(".storage-numbers tbody tr")[1].textContent.includes("351.00") && document.querySelectorAll(".storage-numbers tbody tr")[1].textContent.includes("unpaired") && document.querySelectorAll(".storage-numbers tbody tr")[1].children[4].textContent===String.fromCharCode(0x2014)', 'NVMe succeeds beside a primary timeout without an invented speedup');
    await evaluate('document.querySelector(".storage-comparison").scrollIntoView({block:"start"});window.scrollBy(0,-90)');
    await screenshot('storage-comparison-desktop');
    await evaluate('document.querySelector("[data-storage-filter=size]").value="512";document.querySelector("[data-storage-filter=size]").dispatchEvent(new Event("change",{bubbles:true}))');
    await check('document.querySelectorAll(".storage-numbers tbody tr").length===1 && document.querySelector(".storage-numbers").textContent.includes("110.00") && document.querySelector(".storage-numbers").textContent.includes("55.00") && document.activeElement.dataset.storageFilter==="size"', 'Prompt filter updates actual numbers and preserves keyboard focus');
    await evaluate('document.querySelector("[data-storage-filter=metric]").value="ttft_s";document.querySelector("[data-storage-filter=metric]").dispatchEvent(new Event("change",{bubbles:true}))');
    await check('document.querySelector(".storage-numbers").textContent.includes("10.00") && document.querySelector(".storage-numbers").textContent.includes("5.00") && document.querySelector(".storage-numbers").textContent.includes("2.00")', 'TTFT metric uses matched per-query values and correct speedup direction');
    await evaluate('document.querySelector(".storage-repeats").open=true;document.querySelector(".storage-repeats tbody details").open=true');
    await check('document.querySelectorAll(".storage-repeats tbody tr").length===4 && document.querySelector(".storage-repeats .query-evidence").textContent.includes("513 input / 128 output")', 'Both repeats and raw query counts are available on demand');
    await send('Emulation.setDeviceMetricsOverride', {width:390,height:844,deviceScaleFactor:1,mobile:true});
    await evaluate('document.querySelector(".storage-repeats").open=false;document.querySelector(".storage-comparison").scrollIntoView({block:"start"});window.scrollBy(0,-90)');
    await check('document.documentElement.scrollWidth<=innerWidth+1', 'Storage comparison fits phone width with local table scrolling');
    await screenshot('storage-comparison-phone');
    assert.equal(errors.length, 0, JSON.stringify(errors));
    console.log('Browser checks passed: Results, metadata, responsive layout, capability charts, audit inspector, filters, pagination, keyboard controls and paired protocol comparisons.');
    console.log('Screenshots: ' + artifacts);
    await send('Browser.close').catch(() => {});
  } finally {
    if (ws) ws.close();
    if (browser) browser.kill();
    fixture.kill();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
