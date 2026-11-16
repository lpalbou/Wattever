const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const vm = require('node:vm');

const run = {id:'cap-example',benchmark_kind:'capability',results:[],model_configuration:{}};
const raw = Buffer.from(JSON.stringify(run));
const catalog = {version:1,suites:[],runs:[{id:run.id,file:run.id+'.json',sha256:crypto.createHash('sha256').update(raw).digest('hex'),summary:{id:run.id}}]};
const window = {crypto:crypto.webcrypto};
const fetch = async url => url.pathname.endsWith('index.json')
  ? {ok:true,json:async()=>catalog}
  : {ok:true,arrayBuffer:async()=>raw.buffer.slice(raw.byteOffset,raw.byteOffset+raw.byteLength)};
const context = {window,document:{baseURI:'https://example.com/'},fetch,URL,Uint8Array,TextDecoder};
vm.runInNewContext(fs.readFileSync('web/capability-catalog.js','utf8'),context);

(async()=>{
  const api=window.WatteverCapabilityCatalog;
  assert.equal((await api.request('/api/capabilities/runs/cap-example')).community,true);
  const downloaded=await api.request('/api/capabilities/runs/cap-example/download');
  assert.equal(downloaded.community,undefined);
  assert.equal(JSON.stringify(downloaded),JSON.stringify(run));
  const bundle=await api.request('/api/capabilities/export.json?ids=cap-example');
  assert.equal(JSON.stringify(bundle.capability_runs[0]),JSON.stringify(run));
  assert.equal((await api.request('/api/capabilities/runs/cap-example')).community,true);
  console.log('Capability catalog public exports preserve the published run.');
})().catch(error=>{console.error(error);process.exitCode=1;});
