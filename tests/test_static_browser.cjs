// Remote CI only: built Pages assets and an isolated CORS mock, never a real provider.
// WATTEVER_PUBLIC_SITE_URL additionally tests deployed HTTPS -> mock loopback access.
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
  const artifacts = fs.mkdtempSync(path.join(os.tmpdir(), 'wattever-static-browser-'));
  const fixture = spawn(process.env.PYTHON || 'python', ['-u', path.join(__dirname, 'static_fixture.py')], {windowsHide:true});
  fixture.stderr.on('data', data => process.stderr.write(data));
  let browser, ws;
  try {
    const config = await new Promise((resolve,reject) => {
      let output = '';
      fixture.stdout.on('data', data => {
        output += data;
        if (output.includes('\n')) {
          try { resolve(JSON.parse(output.split('\n')[0])); } catch (error) { reject(error); }
        }
      });
      fixture.once('error',reject);
      fixture.once('exit',code => reject(Error('Static fixture exited: ' + code)));
    });
    const publicSite = process.env.WATTEVER_PUBLIC_SITE_URL;
    const requestedSite = new URL(publicSite || config.site);
    if (publicSite) assert.equal(requestedSite.protocol,'https:','Public-site check requires an HTTPS URL');
    assert.equal(new URL(config.provider).hostname,'127.0.0.1','Inference is confined to the isolated mock provider');
    const browserArgs = ['--headless=new','--no-first-run','--disable-gpu',
      '--remote-debugging-port=0','--user-data-dir=' + path.join(artifacts,'profile'),'about:blank'];
    // Hosted Linux runners can prohibit Chrome's user-namespace sandbox.
    // This flag applies only to this isolated CI fixture, never to the product.
    if (process.env.CI && process.platform === 'linux') browserArgs.unshift('--no-sandbox');
    let browserErrors='';
    browser = spawn(browserPath,browserArgs,{windowsHide:true,stdio:['ignore','ignore','pipe']});
    browser.stderr.on('data',chunk=>{browserErrors=(browserErrors+chunk).slice(-4000);});
    browser.on('error',error=>{browserErrors+=String(error);});
    const debugFile = path.join(artifacts,'profile','DevToolsActivePort');
    for(let i=0;i<150 && !fs.existsSync(debugFile);i++) {
      if(browser.exitCode!==null) throw Error('Browser exited before startup: '+browserErrors);
      await delay(100);
    }
    if(!fs.existsSync(debugFile)) throw Error('Browser did not expose its debugging port: '+browserErrors);
    const debugPort = Number(fs.readFileSync(debugFile,'utf8').split('\n')[0]);
    const pages = await (await fetch(`http://127.0.0.1:${debugPort}/json/list`)).json();
    ws = new WebSocket(pages.find(page => page.type === 'page').webSocketDebuggerUrl);
    await new Promise((resolve,reject) => { ws.addEventListener('open',resolve,{once:true}); ws.addEventListener('error',reject,{once:true}); });
    let next=0;
    const pending = new Map(), errors=[], requests=[], loadingFailures=[];
    const requestTargets = new Map();
    const safeTarget = value => { try { const url=new URL(value);return url.origin+url.pathname; } catch (_) { return '[invalid URL]'; } };
    ws.addEventListener('message',event => {
      const message = JSON.parse(event.data);
      if(message.id && pending.has(message.id)) {
        const request = pending.get(message.id); pending.delete(message.id);
        if(message.error) request.reject(Error(JSON.stringify(message.error)));
        else request.resolve(message.result);
      }
      if(message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails);
      if(message.method === 'Network.requestWillBeSent') {
        requests.push(message.params.request.url);
        requestTargets.set(message.params.requestId,safeTarget(message.params.request.url));
      }
      if(message.method === 'Network.loadingFailed') {
        const failure=message.params;
        loadingFailures.push({target:requestTargets.get(failure.requestId),errorText:failure.errorText,
          corsErrorStatus:failure.corsErrorStatus,blockedReason:failure.blockedReason,canceled:failure.canceled});
      }
    });
    const send=(method,params={})=>new Promise((resolve,reject)=>{
      const id=++next; pending.set(id,{resolve,reject}); ws.send(JSON.stringify({id,method,params}));
    });
    const evaluate=async expression=>{
      const result=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
      if(result.exceptionDetails) throw Error(JSON.stringify(result.exceptionDetails));
      return result.result.value;
    };
    const check=async(expression,message)=>assert(await evaluate(expression),message);
    const diagnose=async message=>{
      const diagnostics={message,loadingFailures};
      try { diagnostics.browser=await send('Browser.getVersion'); } catch (_) {}
      try { diagnostics.page=await evaluate(`(async()=>({
        origin:location.origin,secureContext:isSecureContext,
        connectStatus:document.querySelector('#browser-connect-status')?.textContent,
        loopbackPermission:await navigator.permissions.query({name:'loopback-network'}).then(result=>result.state).catch(error=>'unsupported: '+error.name),
        loopbackPolicy:(document.permissionsPolicy || document.featurePolicy)?.allowsFeature('loopback-network')
      }))()`); } catch(error) { diagnostics.pageError=error.message; }
      try {
        const counters=await (await fetch(new URL('/__fixture/stats',config.site))).json();
        diagnostics.provider={models:counters.models,completions:counters.completions,
          authorized:counters.authorized,preflights:counters.preflights,
          rejectedAuthorization:counters.rejected_authorization,origins:counters.origins};
      } catch(error) { diagnostics.providerError=error.message; }
      // Never include request headers, body, API credentials or URL queries.
      console.error('Static browser failure diagnostics: '+JSON.stringify(diagnostics).replace(/static-fixture-secret/g,'[redacted]'));
    };
    const until=async(expression,message)=>{
      for(let i=0;i<150;i++) { if(await evaluate(expression)) return; await delay(100); }
      await diagnose(message);
      throw Error(message);
    };
    await send('Runtime.enable'); await send('Page.enable'); await send('Network.enable');
    await send('Page.navigate',{url:requestedSite.href});
    await until('typeof state !== "undefined" && !!state.env && !!window.WatteverBrowser?.enabled','Built static UI booted');
    const site=await evaluate('new URL(".",document.baseURI).href');
    if(publicSite) {
      assert.equal(new URL(site).protocol,'https:','Public-site redirects must preserve HTTPS');
      try {
        await send('Browser.setPermission',{permission:{name:'loopback-network'},setting:'granted',origin:new URL(site).origin});
        const permission=await evaluate('navigator.permissions.query({name:"loopback-network"}).then(result=>result.state)');
        assert.equal(permission,'granted','Loopback Network permission is granted for the isolated browser context');
      } catch(error) {
        await diagnose('Cannot grant and verify Loopback Network permission');
        throw Error('Public HTTPS-to-loopback test cannot grant and verify Loopback Network permission in this Chrome build: '+error.message);
      }
      console.log('Testing deployed HTTPS site with granular Loopback Network permission granted to its origin; provider remains the isolated loopback mock.');
    }
    await check('!document.querySelector("#browser-connect").hidden','Static connection form is visible');
    const before=await (await fetch(new URL('/__fixture/stats',config.site))).json();
    assert.equal(before.models,0,'No provider request before explicit connection');
    assert.equal(before.completions,0,'No inference before explicit start');
    await evaluate(`document.querySelector('#browser-endpoint').value=${JSON.stringify(config.provider)};
      document.querySelector('#browser-key').value='static-fixture-secret';
      document.querySelector('#browser-gpu').value='Manual fixture GPU';
      document.querySelector('#browser-cpu').value='Manual fixture CPU';
      document.querySelector('#browser-ram').value='32'; document.querySelector('#browser-vram').value='8';
      document.querySelector('#browser-connect-button').click()`);
    await until('state.env.models.some(model=>model.id==="fixture-model")','Connect fetched mock models across origins');
    await check('document.querySelector("#browser-key").value === ""','API key input cleared after connection');
    await check('JSON.stringify(localStorage).includes("static-fixture-secret") === false && JSON.stringify(sessionStorage).includes("static-fixture-secret") === false','Credentials are absent from browser storage');
    await evaluate('state.models=new Set(["fixture-model"]);state.sizes=new Set([512]);state.concurrencyLevels=new Set([1,2]);document.querySelector("#repeats").value="1";document.querySelector("#max-tokens").value="3";updatePlan();document.querySelector("#start-btn").click()');
    await until('state.runs.some(run=>run.id.startsWith("browser-") && run.status==="completed")','Mock browser benchmark completed');
    const run=await evaluate('(async()=>{const rows=await api("/api/runs");return api("/api/runs/"+rows.runs.find(run=>run.id.startsWith("browser-")).id)})()');
    assert.equal(run.measurement_mode,'browser');
    assert.equal(run.application.protocol_version,'browser-1');
    assert.equal(run.hardware.source,'manual');
    assert.equal(run.gpu.name,'Manual fixture GPU');
    assert.equal(run.results.length,2);
    assert(run.results.every(row=>row.status==='ok'),'Exact mock streams succeed');
    assert(run.results.every(row=>!row.power && !row.group_power && row.vram_peak_mb == null),'No fabricated hardware telemetry');
    assert(run.results.every(row=>(row.requests || [row]).every(request=>request.prompt_tokens===12 && request.completion_tokens===3 && request.completion_tokens_estimated===false)),'SSE usage supplies exact counts');
    assert(!JSON.stringify(run).includes('static-fixture-secret'),'Credential not in run JSON');
    assert(!JSON.stringify(run).includes(config.provider),'Endpoint not in benchmark JSON');
    await check('(async()=>{try{await api("/api/import",{method:"POST",body:JSON.stringify({id:"malformed",results:[null]})});return false;}catch(error){return (await api("/api/runs")).runs.length===1;}})()','Malformed imports are rejected without damaging the library');
    // Capture the UI download artifact instead of asking the browser to write to the user's Downloads.
    await evaluate('window.__downloads=[];downloadBlob=(content,name,type)=>window.__downloads.push({content,name,type});showPanel("results")');
    await until(`!!document.querySelector('#run-detail a[href$="/download"]')`,'JSON download action available');
    await evaluate(`document.querySelector('#run-detail a[href$="/download"]').click()`);
    await until('window.__downloads.length===1','UI JSON export resolves through adapter');
    const artifact=await evaluate('window.__downloads[0]');
    const exported=JSON.parse(artifact.content);
    assert.equal(exported.id,run.id);
    assert(!artifact.content.includes('static-fixture-secret'));
    // Import through the actual file input, including an existing ID, and preserve provenance.
    await evaluate(`(()=>{const transfer=new DataTransfer();transfer.items.add(new File([${JSON.stringify(artifact.content)}],'fixture.json',{type:'application/json'}));const input=document.querySelector('#browser-import-file');input.files=transfer.files;input.dispatchEvent(new Event('change',{bubbles:true}));})()`);
    await until('state.runs.length===2 && document.querySelector("#browser-import-status").textContent.includes("Imported")','File import adds a separate benchmark without overwriting');
    await check('document.querySelector("#browser-import-status").textContent.includes("Imported")','Import UI reports success');
    await check('(async()=>{const data=await api("/api/export.json");return data.runs.length===2 && data.runs.every(run=>run.measurement_mode==="browser") && !JSON.stringify(data).includes("static-fixture-secret")})()','Bundle export preserves imported mode and keeps credentials out');
    const helper=JSON.parse(artifact.content);
    helper.id='fixture-helper'; delete helper.measurement_mode; delete helper.browser_schema_version;
    helper.application={name:'Wattever',protocol_version:'2'};
    await evaluate(`(()=>{const transfer=new DataTransfer();transfer.items.add(new File([${JSON.stringify(JSON.stringify(helper))}],'helper.json',{type:'application/json'}));const input=document.querySelector('#browser-import-file');input.files=transfer.files;input.dispatchEvent(new Event('change',{bubbles:true}));})()`);
    await until('state.runs.length===3','Helper result imports for comparison');
    await evaluate('state.compare=new Set(state.runs.map(run=>run.id));renderResultsView()');
    await until('!!document.querySelector(".measurement-notice") && document.querySelector(".measurement-notice").textContent.includes("different measurement modes")','Mixed helper/browser modes show comparison warning');
    const stats=await (await fetch(new URL('/__fixture/stats',config.site))).json();
    assert.equal(stats.models,1);
    assert.equal(stats.completions,3);
    assert.equal(stats.authorized,4);
    assert(stats.origins.every(origin=>origin===new URL(site).origin),'Provider received genuine cross-origin requests from the tested site');
    assert.equal(new Set(stats.prompts.map(prompt=>prompt.split('\n')[0])).size,3,'Every request gets a distinct leading cache-busting nonce');
    assert.deepEqual(stats.static_api,[],'Static host never receives Python helper API calls');
    assert(!requests.some(url=>{const parsed=new URL(url);return parsed.origin===new URL(site).origin && /(?:^|\/)api\//.test(parsed.pathname);}), 'Tested site never receives Python helper API calls');
    assert(requests.some(url=>url===new URL('corpus.md',site).href),'Corpus resolved under Pages subpath');
    assert(requests.some(url=>url.startsWith(site) && /browser\.js/.test(url)),'Adapter asset resolved under Pages subpath');
    assert(!requests.some(url=>url.includes('static-fixture-secret')),'Credentials never appear in request URLs');
    // Simulate a tab that closed after saving an in-progress checkpoint.
    await evaluate('(()=>{window.__reloadMarker=true;const key="wattever.browser.runs.v1";const saved=JSON.parse(localStorage.getItem(key));saved[0].status="running";localStorage.setItem(key,JSON.stringify(saved));})()');
    await send('Page.reload');
    await until('!window.__reloadMarker && typeof state !== "undefined" && !!state.env && state.runs.length===3','Browser-local results survive reload');
    await check('document.querySelector("#browser-key").value === "" && state.env.models.length===0','Reload retains results, not live credentials or connection');
    await check('state.runs.every(run=>run.status!=="running") && state.runs.some(run=>run.status==="interrupted")','Closed-tab checkpoints become interrupted and manageable');
    assert.equal(errors.length,0,JSON.stringify(errors));
    console.log((publicSite?'Public HTTPS':'Built static')+' browser checks passed: subpath assets, explicit CORS connection, mock SSE, credentials, manual hardware, JSON export/import and no helper requests.');
    await send('Browser.close').catch(()=>{});
  } finally {
    if(ws) ws.close(); if(browser) browser.kill(); fixture.kill();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
