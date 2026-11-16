/* Client-observed decoding windows. TTFT is never part of decode duration. */
(function(root) {
  'use strict';
  const number=v=>typeof v==='number' && Number.isFinite(v);
  const count=v=>Number.isSafeInteger(v) && v>=0;
  const protocol='observed-output-phases-v2', endBoundary='last_observed_nonempty_output';
  const currentScope=q=>q.timing_scope?.phase_protocol===protocol && q.timing_scope?.decode_end_boundary===endBoundary;
  const validWall=q=>q.wall_s==null || number(q.wall_s) && q.wall_s>=0;
  function requests(row,partial=false) {
    const n=row.concurrency == null ? 1 : row.concurrency;
    if (!Number.isSafeInteger(n) || n<1) return [];
    const all=Array.isArray(row.requests) && row.requests.length ? row.requests : n===1?[row]:[];
    const ok=all.filter(q=>q && q.status==='ok');
    if(!ok.length || row.status==='ok' && (all.length!==n || ok.length!==n) || row.status!=='ok' && !partial) return [];
    return ok;
  }
  function unionDuration(intervals) {
    const sorted=intervals.map(v=>v.slice()).sort((a,b)=>a[0]-b[0]);
    let total=0,end=null;
    for(const [a,b] of sorted) {total+=end===null?b-a:Math.max(0,b-Math.max(a,end));end=end===null?b:Math.max(end,b);}
    return total;
  }
  function decodePhase(row,{partial=false}={}) {
    const ok=requests(row,partial),n=row.concurrency || 1;
    if(!ok.length) return null;
    const intervals=[];let tokens=0;
    for(const q of ok) {
      const offset=n===1?0:q.start_offset_s;
      if(!number(offset) || offset<0 || !number(q.ttft_s) || q.ttft_s<0 || !number(q.decode_s) || q.decode_s<=0 || !count(q.decode_token_count) || !validWall(q)) return null;
      const first=offset+q.ttft_s;
      if(!number(first) || q.ttft_s>0 && first<=offset || !number(first+q.decode_s) || first+q.decode_s<=first || number(q.wall_s) && q.ttft_s+q.decode_s>q.wall_s+0.000001) return null;
      intervals.push([first,first+q.decode_s]);tokens+=q.decode_token_count;
      if(!Number.isSafeInteger(tokens))return null;
    }
    const seconds=unionDuration(intervals);
    return number(seconds) && seconds>0 && number(tokens/seconds) ? {tps:tokens/seconds,decode_s:seconds,decode_tokens:tokens,intervals,
      estimated_boundary:ok.some(q=>q.timing_scope?.decode_count_estimated_boundary===true),
      legacy_boundary:ok.some(q=>!currentScope(q))} : null;
  }
  function outputRate(row,{partial=false}={}) {
    const ok=requests(row,partial);
    if(!ok.length || !number(row.wall_s) || row.wall_s<=0 || !ok.every(q=>count(q.completion_tokens))) return null;
    const total=ok.reduce((sum,q)=>sum+q.completion_tokens,0), rate=total/row.wall_s;
    return Number.isSafeInteger(total) && number(rate) ? rate : null;
  }
  function prefillPhase(row,{partial=false}={}) {
    const ok=requests(row,partial),n=row.concurrency || 1;
    if(!ok.length)return null;
    let tokens=0;const intervals=[];
    for(const q of ok) {
      const start=n===1?0:q.start_offset_s;
      if(!number(start) || start<0 || !number(q.ttft_s) || q.ttft_s<=0 || !count(q.prompt_tokens) || !validWall(q))return null;
      if(!number(start+q.ttft_s) || start+q.ttft_s<=start || number(q.wall_s) && q.ttft_s>q.wall_s+0.000001)return null;
      intervals.push([start,start+q.ttft_s]);tokens+=q.prompt_tokens;
      if(!Number.isSafeInteger(tokens))return null;
    }
    const seconds=unionDuration(intervals);
    return number(seconds) && seconds>0 && number(tokens/seconds)?{tps:tokens/seconds,prefill_s:seconds,prompt_tokens:tokens,intervals}:null;
  }
  const api={decodePhase,prefillPhase,outputRate,unionDuration,currentScope};
  if(typeof module!=='undefined' && module.exports)module.exports=api;
  root.WatteverPhaseMetrics=api;
})(typeof globalThis!=='undefined'?globalThis:this);
