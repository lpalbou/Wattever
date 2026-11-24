// Nothing is connected until the user asks; what the page then says must be true for what happened.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const nodes=new Map();
const make=id=>({id,value:'',hidden:false,disabled:false,textContent:'',innerHTML:'',title:'',className:'',dataset:{},children:[],attributes:{},
  setAttribute(name,value){this.attributes[name]=value;},removeAttribute(name){delete this.attributes[name];},
  appendChild(child){this.children.push(child);child.parent=this;return child;},
  querySelector(selector){return node(id+' '+selector);},addEventListener(){}});
const node=id=>{if(!nodes.has(id))nodes.set(id,make(id));return nodes.get(id);};
const timers=[];
const context={window:{},Charts:{fmtNum:String},console,
  setTimeout:(fn,ms)=>{timers.push(fn);return timers.length;},clearTimeout:id=>{timers[id-1]=null;},
  document:{querySelector:node,querySelectorAll:()=>[],addEventListener(){},
    createElement:()=>{const element=make('veil');element.remove=function(){const list=this.parent?.children;if(list)list.splice(list.indexOf(this),1);this.removed=true;};return element;}}};
vm.createContext(context);vm.runInContext(fs.readFileSync('web/app.js','utf8').replace(/\ninit\(\);\s*$/,''),context);
const run=(expression,values={})=>{Object.assign(context,values);return vm.runInContext(expression,context);};
const url='http://127.0.0.1:8000/v1';
const problem=text=>run('endpointProblem(new Error(text),url)',{text,url});

// The exact texts the helper and the browser produce, sorted by what the user can do.
const cases=[
  ['Model discovery failed: <urlopen error [WinError 10061] No connection could be made because the target machine actively refused it>. You can enter model IDs manually.','unreachable',false,/^Nothing answered at http:\/\/127\.0\.0\.1:8000\/v1\. Start your inference engine/],
  ['Model discovery failed: timed out. You can enter model IDs manually.','unreachable',false,/^Nothing answered/],
  ['Model discovery failed: <urlopen error [Errno 11001] getaddrinfo failed>. You can enter model IDs manually.','unreachable',false,/^Nothing answered/],
  ['Model discovery failed: HTTP Error 401: Unauthorized. You can enter model IDs manually.','auth',true,/refused the request \(HTTP 401\)\. Enter the API key/],
  ['Model discovery failed: HTTP Error 403: Forbidden. You can enter model IDs manually.','auth',true,/HTTP 403/],
  ['Model discovery failed: HTTP Error 404: Not Found. You can enter model IDs manually.','no-list',true,/has no model list there \(HTTP 404\)/],
  ['Model discovery failed: HTTP Error 504: Gateway Timeout. You can enter model IDs manually.','http',true,/answered with an error \(HTTP 504\)/],
  ['Model discovery failed: HTTP Error 408: Request Timeout. You can enter model IDs manually.','http',true,/HTTP 408/],
  ['No model IDs found.','empty',true,/answered but serves no model yet\. Load a model/],
  ['Model discovery failed: Expected an OpenAI model list with a data array.. You can enter model IDs manually.','not-openai',true,/not with an OpenAI-style model list/],
  ['Model discovery failed: Expecting value: line 1 column 1 (char 0). You can enter model IDs manually.','not-openai',true,/OpenAI-style/],
  ['Model discovery failed: <urlopen error [SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:1032)>. You can enter model IDs manually.','not-openai',true,/http or https matches/],
  ['Endpoint must be an HTTP(S) base URL without credentials, query or fragment.','address',false,/is not a complete address\. Start it with http:\/\/ or https:\/\//],
  ['Use the API base URL, not an individual route.','address',false,/Enter the API base address/],
  ['Failed to fetch','helper',false,/^Wattever's own helper did not answer/],
  ['something nobody anticipated','other',false,/could not be listed: something nobody anticipated\. Check the address/],
];
for(const [text,kind,answered,wording] of cases){
  const found=problem(text);
  assert.equal(found.kind,kind,text);assert.equal(found.answered,answered,text);assert.match(found.message,wording,text);
  assert.doesNotMatch(found.message,/Model discovery failed|You can enter model IDs manually|urlopen/,'no raw helper text reaches the user: '+text);
}
// A model ID cannot fix a rejected key, a wrong address or an engine that is not there.
for(const [text,kind] of cases) if(['unreachable','auth','address','helper'].includes(kind)) assert.doesNotMatch(problem(text).message,/enter a model ID/,text);

// The veil: shown at once or after a delay, removed by the function setBusy returned, and never
// left behind by an earlier wait that finishes after a later one took the panel.
const panel=node('#panel');
let clear=run('setBusy(panel,"Loading <b>",0)',{panel});
assert.equal(panel.dataset.loading,'Loading <b>');assert.equal(panel.attributes['aria-busy'],'true');assert.equal(panel.inert,true);
assert.equal(panel.children.length,1);assert.match(panel.children[0].innerHTML,/class="spinner"[^>]*><\/span>Loading &lt;b&gt;/);
assert.equal(node('#busy-announcer').textContent,'Loading <b>','the wait is announced outside the inert panel');
clear();clear();
assert.equal('loading' in panel.dataset,false);assert.equal(panel.inert,false);assert.equal(panel.children.length,0);assert.equal('aria-busy' in panel.attributes,false);
const first=run('setBusy(panel,"first",0)'),second=run('setBusy(panel,"second",0)');
first();
assert.equal(panel.dataset.loading,'second','an earlier wait does not clear a later one');assert.equal(panel.children.length,1);
second();assert.equal('loading' in panel.dataset,false);assert.equal(panel.children.length,0);
timers.length=0;clear=run('setBusy(panel,"slow",150)');
assert.equal('loading' in panel.dataset,false,'a short wait shows nothing');
clear();timers.forEach(fn=>fn&&fn());assert.equal('loading' in panel.dataset,false,'a wait that ended before the delay never shows');
clear=run('setBusy(panel,"slow",150)');timers.forEach(fn=>fn&&fn());assert.equal(panel.dataset.loading,'slow');clear();
assert.equal(typeof run('setBusy("#missing-on-purpose","x",0)'),'function');
assert.match(run('loadingState("Loading <x>","Reading")'),/^<div class="empty loading" role="status"><h3><span class="spinner" aria-hidden="true"><\/span>Loading &lt;x&gt;<\/h3><div>Reading<\/div><\/div>$/);

// What each connection state says, in the header, in the card and in the model list.
node('#helper-provider').value='lmstudio';
node('#model-list .empty-note');
const shown=()=>({pill:node('#pill-lms span:last-child').textContent,dot:node('#pill-lms .dot').className,status:node('#helper-status').textContent,
  state:node('#helper-status').dataset.state,message:node('#helper-message').hidden?null:node('#helper-message-text').textContent,empty:node('#model-list .empty-note').textContent,
  action:!node('#helper-action').hidden,stale:node('#model-list').dataset.stale==='1',hint:node('#models-hint').textContent});
run('state.providerNeedsConnection=true;state.benchmarkRunning=false;setEngine("off")');
assert.deepEqual(shown(),{pill:'No engine connected',dot:'dot',status:'Not connected',state:'pending',message:null,
  empty:'No inference engine connected. Choose a provider in Inference engine above and click Connect to list its models.',action:false,stale:false,
  hint:'The models your connected engine serves. Tick the ones to measure.'});
run('setEngine("connecting")');
assert.deepEqual([shown().pill,shown().dot,shown().status,shown().state,shown().empty],['Connecting…','dot busy','Connecting…','connecting','']);
run('setEngine("error",{message:"LM Studio is not answering.",action:"start-lmstudio"})');
assert.deepEqual([shown().pill,shown().dot,shown().status,shown().state,shown().message,shown().action],['No engine connected','dot','Connection failed','error','LM Studio is not answering.',true]);
assert.equal(node('#helper-message').dataset.tone,'error');assert.match(shown().empty,/^No models to choose from yet/);
run('state.providerNeedsConnection=false;setEngine("on",{label:"LM Studio",models:12})');
assert.deepEqual([shown().pill,shown().dot,shown().status,shown().state,shown().message,shown().action,shown().stale],['LM Studio connected','dot on','Connected · LM Studio · 12 models','ready',null,false,false]);
run('setEngine("on",{label:"vLLM local endpoint",models:1})');assert.equal(shown().status,'Connected · vLLM local endpoint · 1 model');
// Connected, then the form changed: the page stops calling it connected and says what to do.
run('state.providerNeedsConnection=true;renderConnection()');
assert.deepEqual([shown().pill,shown().dot,shown().status,shown().state,shown().stale],['Reconnect needed','dot','Form changed · click Connect','pending',true]);
assert.match(shown().hint,/^These models were listed by the previous connection/);
// A model ID used as typed is never shown as a verified connection.
run('state.providerNeedsConnection=false;setEngine("on",{label:"Endpoint",models:1,unverified:true,message:"used as typed",tone:"info"})');
assert.deepEqual([shown().pill,shown().dot,shown().status,shown().state,shown().message],['Endpoint · not checked','dot','Model ID used as typed · not checked','pending','used as typed']);
assert.equal(node('#helper-message').dataset.tone,'info');
// A benchmark started elsewhere is running while this page has connected nothing.
run('state.providerNeedsConnection=true;state.benchmarkRunning=true;setEngine("off")');
assert.deepEqual([shown().pill,shown().dot],['Benchmark running','dot on']);
// Identical text is not written again: these are live regions and the form redraws on every keystroke.
const status=node('#helper-status');let writes=0,value=status.textContent;
Object.defineProperty(status,'textContent',{get:()=>value,set:next=>{writes++;value=next;}});
run('renderConnection();renderConnection();renderConnection()');assert.equal(writes,0);
console.log('Endpoint error sorting, loading veil ownership and connection state wording tests passed');
