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
    for (let attempt = 0; attempt < 150 && !fs.existsSync(debugFile); attempt++) {
      if (browser.exitCode !== null) throw Error('Browser exited before startup: ' + browserErrors);
      await delay(100);
    }
    if (!fs.existsSync(debugFile)) throw Error('Browser did not expose its debugging port: ' + browserErrors);
    const debugPort = Number(fs.readFileSync(debugFile, 'utf8').split('\n')[0]);
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
    await check('document.querySelector("#app-version").textContent === "v2.4.0"', 'Version comes from the server');
    await check('!document.querySelector("#machine-card").open && !document.querySelector("#advanced-settings").open', 'Details start collapsed');
    await check('!document.querySelector("#size-quick, #concurrency-quick, #cpu-tdp, #other-idle")', 'No duplicate shortcuts or power inputs');
    await check('document.querySelector("#run-label").value.includes("RTX 5060 Ti") && document.querySelector("#run-label").value.includes("32 GB RAM")', 'Hardware name is filled automatically');
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
    await check('document.querySelectorAll(".topbar .tab").length === 2 && state.resultView === "summary"', 'Only Run and Results in the navigation; Summary opens first');
    await check('!!document.querySelector("#summary-body svg")', 'Summary has a chart');
    await screenshot('summary-desktop');
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
    await check('state.metrics.has("prefill_tps") && state.metrics.has("decode_tps") && window.__lastSeries.length===10', 'Prefill and decode are selected and plotted together');
    await check('document.querySelector(".measure-legend").textContent.includes("Decode speed") && new Set(window.__lastSeries.filter(series=>series.id.startsWith("smoke|model-a|")).map(series=>series.dash)).size===2', 'Measures have distinct line styles and a legend');
    await check('Math.abs(document.querySelector(".benchmark-library").getBoundingClientRect().height-document.querySelector(".results-workspace").getBoundingClientRect().height)<2', 'Benchmark library matches the right panel height');
    await evaluate('Array.from(document.querySelectorAll("#result-metrics input")).find(input=>input.value==="ttft_s").click()');
    await delay(100);
    await check('document.querySelectorAll("#summary-body .plot").length===2', 'Different units use separate summary charts');
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
    await check('document.querySelectorAll("#facet-grid .facet").length===6 && Array.from(document.querySelectorAll("#facet-grid svg[role=img]")).some(svg=>svg.getAttribute("aria-label").includes("Prefill speed, Decode speed"))', 'Compare overlays matching units and separates other units');
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

    // Fake extension endpoints exercise the complete dialog without running inference.
    await evaluate('selectRun("smoke")');
    for (let i=0; i<40 && !await evaluate('!!document.querySelector("#extend-run")'); i++) await delay(50);
    await evaluate('document.querySelector("#extend-run").click()');
    for (let i=0; i<40 && !await evaluate('!!document.querySelector("#extend-content input[data-model]")'); i++) await delay(50);
    await check('document.querySelector("#extend-dialog").open && document.querySelector("#extend-preview").textContent.includes("Choose models") && document.querySelector("#extend-start").disabled', 'Extension opens with no model selected');
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
    assert.equal(errors.length, 0, JSON.stringify(errors));
    console.log('Browser checks passed: range selection, metadata, equal panels, unified Results, comparisons, fitting tables, clipboard, deletion and responsive layout.');
    console.log('Screenshots: ' + artifacts);
    await send('Browser.close').catch(() => {});
  } finally {
    if (ws) ws.close();
    if (browser) browser.kill();
    fixture.kill();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
