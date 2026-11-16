const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

const nodes=new Map(),downloads=[],requests=[];
let buttons=[],checks=[],shareStatus={textContent:''};
function node(selector){
  if(!nodes.has(selector))nodes.set(selector,{value:'',checked:false,hidden:false,disabled:false,style:{},dataset:{},handlers:{},innerHTML:'',textContent:'',
    addEventListener(type,fn){this.handlers[type]=fn;},scrollIntoView(){},
    querySelectorAll(query){if(selector==='#cap-runs' && query==='input')return checks;return [];}});
  return nodes.get(selector);
}
const parent={insertAdjacentElement(where,element){assert.equal(where,'afterend');nodes.set('#'+element.id,element);}};
node('#cap-export').parentElement=parent;
function makeElement(tag){
  if(tag==='a')return{click(){downloads.push({name:this.download,url:this.href});}};
  let html='';
  return {id:'',set innerHTML(value){html=value;buttons=[...html.matchAll(/data-cap-download="([^"]+)"([^>]*)>/g)].map(match=>({dataset:{capDownload:match[1]},disabled:match[2].includes('disabled'),handlers:{},addEventListener(type,fn){this.handlers[type]=fn;}}));},get innerHTML(){return html;},querySelectorAll(){return buttons;},querySelector(){return shareStatus;}};
}
const runs=['cap-one','cap-two'].map(id=>({id,status:'completed',model_configuration:{model:{value:id}},summary:{scored:1},results:[]}));
const ui={config:()=>({}),connection:()=>({}),models:()=>[],browserMode:()=>false,ready:()=>true,api:async path=>{
  requests.push(path);
  if(path.endsWith('/suites'))return{suites:[],runner:{completion_policy:'finished-stream-visible-answer-v2'}};
  if(path.endsWith('/runs'))return{runs};
  if(path.endsWith('/status'))return{running:false};
  if(path.endsWith('/export.json?ids=cap-one,cap-two'))return{capability_runs:runs};
  const match=path.match(/\/runs\/(cap-one|cap-two)(\/download)?$/);if(match)return runs.find(run=>run.id===match[1]);
  throw Error('Unexpected request '+path);
}};
const window={WatteverUI:ui};
const context={window,document:{querySelector:selector=>selector==='#cap-share-files'?(nodes.get(selector)||null):node(selector),createElement:makeElement},URL:{createObjectURL:()=> 'blob:test',revokeObjectURL(){}},Blob,setTimeout:()=>{},setInterval,clearInterval,console};
vm.runInNewContext(fs.readFileSync('web/capabilities.js','utf8'),context);

(async()=>{
  await window.WatteverCapabilities.activate();
  assert.deepEqual(buttons.map(button=>button.dataset.capDownload),['cap-one']);
  checks=[{value:'cap-two',checked:true,addEventListener(type,fn){this.change=fn;}}];
  // Re-render through the real library search handler, then select the second run.
  node('#cap-run-search').handlers.input({target:{value:''}});
  await checks[0].change();
  assert.deepEqual(buttons.map(button=>button.dataset.capDownload),['cap-one','cap-two']);
  buttons[1].handlers.click();
  await new Promise(setImmediate);
  assert(requests.includes('/api/capabilities/runs/cap-two/download'));
  assert.equal(downloads[0].name,'cap-two.json');
  node('#cap-export').handlers.click({preventDefault(){}});
  await new Promise(setImmediate);
  assert(requests.includes('/api/capabilities/export.json?ids=cap-one,cap-two'));
  assert.equal(downloads[1].name,'wattever-capabilities.json');
  console.log('Capability Share exports selected individual files and bundle.');
})().catch(error=>{console.error(error);process.exitCode=1;});
