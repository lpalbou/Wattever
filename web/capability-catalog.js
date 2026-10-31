/* Read-only, digest-checked community capability results for GitHub Pages. */
((root)=>{
  'use strict';
  let catalog=null;
  const cache=new Map(), safe=/^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$/;
  const base=()=>new URL('capabilities/',document.baseURI);
  async function getCatalog(){
    if(catalog)return catalog;
    const response=await fetch(new URL('index.json',base()),{credentials:'omit',redirect:'error',cache:'no-cache'});
    if(!response.ok)throw new Error('Shared capability catalog is unavailable.');
    const data=await response.json();
    if(data.version!==1||!Array.isArray(data.runs)||!Array.isArray(data.suites))throw new Error('Invalid capability catalog.');
    const seen=new Set();
    for(const entry of data.runs){if(!safe.test(entry.id)||entry.file!==entry.id+'.json'||!/^[a-f0-9]{64}$/.test(entry.sha256)||seen.has(entry.id))throw new Error('Invalid shared capability entry.');seen.add(entry.id);}
    catalog=data;return data;
  }
  async function load(id){
    if(cache.has(id))return cache.get(id);
    const entry=(await getCatalog()).runs.find(item=>item.id===id);
    if(!entry)throw new Error('No such shared capability result.');
    const response=await fetch(new URL(entry.file,base()),{credentials:'omit',redirect:'error'});
    if(!response.ok)throw new Error('Shared capability file is unavailable.');
    const raw=await response.arrayBuffer(), bytes=new Uint8Array(await root.crypto.subtle.digest('SHA-256',raw));
    const digest=Array.from(bytes,x=>x.toString(16).padStart(2,'0')).join('');
    if(digest!==entry.sha256)throw new Error('Shared capability result checksum mismatch.');
    const run=JSON.parse(new TextDecoder().decode(raw));
    if(run.id!==id||run.benchmark_kind!=='capability'||!Array.isArray(run.results)||!run.model_configuration)throw new Error('Invalid shared capability result.');
    const display={...run,community:true};cache.set(id,display);return display;
  }
  async function request(path,options={}){
    const url=new URL(path,'http://local.invalid'),route=url.pathname,method=(options.method||'GET').toUpperCase();
    if(method!=='GET')throw new Error('Use the local helper to run or validate imported capability benchmarks. Shared results can be browsed here.');
    if(route==='/api/capabilities/suites')return{suites:(await getCatalog()).suites};
    if(route==='/api/capabilities/status')return{running:false,live:{}};
    if(route==='/api/capabilities/runs')return{runs:(await getCatalog()).runs.map(item=>({...item.summary,community:true}))};
    if(route==='/api/capabilities/export.json'){
      const ids=(url.searchParams.get('ids')||'').split(',').filter(Boolean), entries=(await getCatalog()).runs.filter(item=>!ids.length||ids.includes(item.id));
      return{capability_runs:await Promise.all(entries.map(item=>load(item.id)))};
    }
    const match=route.match(/^\/api\/capabilities\/runs\/([^/]+)(?:\/download)?$/);
    if(match)return load(decodeURIComponent(match[1]));
    throw new Error('Unsupported capability catalog operation.');
  }
  root.WatteverCapabilityCatalog={request,refresh(){catalog=null;cache.clear();}};
})(window);
