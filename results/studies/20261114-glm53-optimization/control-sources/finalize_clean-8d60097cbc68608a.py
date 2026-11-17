"""Validate the completed monitored benchmark and export accepted-sample aggregates."""
import csv
import json
from pathlib import Path
import statistics
import sys
import httpx
from datetime import datetime

folder=Path(sys.argv[1])
data=json.loads((folder/'results.json').read_text())
if not data.get('completed_utc'):
    raise SystemExit('Benchmark is still running; final aggregation refused.')

def stat(values):
    values=[v for v in values if isinstance(v,(int,float))]
    return None if not values else {'median':statistics.median(values),'min':min(values),'max':max(values),'n':len(values)}

expected_stops=[f for f in data['failures'] if 'External CPU/IO did not settle' in f.get('orchestration','') or 'Reserved time' in f.get('orchestration','') or 'Insufficient time' in f.get('orchestration','')]
summary={'started_utc':data['started_utc'],'completed_utc':data['completed_utc'],
         'deadline_utc':data['deadline_utc'],'method':data['method'],
         'completed_sweeps':data.get('completed_sweeps',[]),'models':{},'failures':[f for f in data['failures'] if not f.get('resolved') and f not in expected_stops],
         'background_or_budget_stops':expected_stops,
         'resolved_interruptions':[f for f in data['failures'] if f.get('resolved')],
         'excluded_attempts':data['excluded'],'restored':data.get('restored'),
         'screening_label':'Accepted runs screened for low measured external CPU and process I/O; not proof of complete system/GPU isolation.',
         'cache_method':'Identical-prompt regeneration. Only first attempts enter repeat-prompt comparisons; a cache hit requires >=95% reported input reuse. Not a rolling multi-turn conversation test.'}
summary['exclusion_counts']={}
for model in data['models'].values():
    for row in model['requests']:
        if not row['accepted']:
            reasons=['insufficient_monitor_samples'] if row.get('monitor',{}).get('reason')=='no monitor samples' else row['exclusion_reasons']
            for reason in reasons:summary['exclusion_counts'][reason]=summary['exclusion_counts'].get(reason,0)+1
csv_rows=[]
valid_rows=[]
for key,model in data['models'].items():
    accepted=[r for r in model['requests'] if r['accepted']]
    out={'name':model['name'],'matrix':{},'loads':model['loads']}
    used_starts=set()
    for row in accepted:
        if row['phase']!='matrix':continue
        at=datetime.fromisoformat(row['started_utc']).timestamp()
        starts=[load['status']['started'] for load in model['loads']
                if load['sweep']==row['sweep'] and load.get('status',{}).get('started',at+1)<=at]
        if starts:used_starts.add(max(starts))
    out['matrix_load_start_timestamps']=sorted(used_starts)
    for target in (4096,16384,32768,60000):
        rows=[r for r in accepted if r['phase']=='matrix' and r['target_tokens']==target]
        for row in rows:
            assert row['monitor']['clean']
            assert row['reused_tokens'] is not None and row['reused_tokens']<=64
            assert row['generated_tokens']>=256
            assert row['prompt_tps_source']=='engine' and row['decode_tps_source']=='engine'
            assert row['drafts_offered']>0
        valid_rows.extend(rows)
        result={field:stat(r[field] for r in rows) for field in
                ('prompt_tokens','prompt_tps','decode_tps','ttft_s','total_s','generated_tokens','reused_tokens')}
        result['sweeps']=sorted(set(r['sweep'] for r in rows))
        result['runs_per_sweep']={str(s):sum(r['sweep']==s for r in rows) for s in result['sweeps']}
        result['by_sweep']={str(s):{field:stat(r[field] for r in rows if r['sweep']==s)
                                   for field in ('prompt_tps','decode_tps','ttft_s')}
                            for s in result['sweeps']}
        result['drafts_offered']=sum(r.get('drafts_offered') or 0 for r in rows)
        result['drafts_accepted']=sum(r.get('drafts_accepted') or 0 for r in rows)
        result['external_cpu_mean_percent']=stat(r['monitor']['mean_external_cpu_percent'] for r in rows)
        result['external_io_mean_mb_s']=stat(r['monitor']['mean_external_io_mb_s'] for r in rows)
        out['matrix'][str(target)]=result
        flat={'model':model['name'],'target_input_tokens':target,'sweeps':','.join(map(str,result['sweeps']))}
        for field in ('prompt_tokens','prompt_tps','decode_tps','ttft_s','total_s','generated_tokens','reused_tokens'):
            for name in ('median','min','max','n'):flat[field+'_'+name]=(result[field] or {}).get(name)
        csv_rows.append(flat)
    short=[r for r in accepted if r['phase']=='short']
    out['short_chat']={field:stat(r[field] for r in short) for field in ('decode_tps','ttft_s','generated_tokens')}
    repeated=[r for r in accepted if r['phase']=='cached' and r['attempt']==1]
    hits=[r for r in repeated if r.get('reused_tokens') is not None and r.get('prompt_tokens') and r['reused_tokens']/r['prompt_tokens']>=.95]
    out['repeat_prompt']={'requests':len(repeated),'observed_cache_hits':len(hits),
                          'ttft_s':stat(r['ttft_s'] for r in repeated),
                          'reused_tokens':stat(r['reused_tokens'] for r in repeated),
                          'cache_hit_ttft_s':stat(r['ttft_s'] for r in hits),
                          'decode_tps':stat(r['decode_tps'] for r in repeated)}
    continued=[r for r in accepted if r['phase']=='continuation']
    out['continuation_probe']={field:stat(r[field] for r in continued) for field in
                              ('prompt_tokens','reused_tokens','ttft_s','decode_tps','generated_tokens')}
    out['accepted_requests']=len(accepted)
    summary['models'][key]=out
summary['accepted_fresh_matrix_requests']=len(valid_rows)
summary['outside_cpu_percent_across_fresh_runs']=stat(r['monitor']['mean_external_cpu_percent'] for r in valid_rows)
summary['complete_two_sweeps']=len(summary['models'])==3 and all(
    cell['runs_per_sweep']==data['method'].get('repeats_by_sweep',{'1':2,'2':2})
    for model in summary['models'].values() for cell in model['matrix'].values())
summary['independent_matrix_loads_confirmed']=len(summary['models'])==3 and all(len(model['matrix_load_start_timestamps'])>=2 for model in summary['models'].values())
try:
    with httpx.Client(timeout=90,trust_env=False) as client:
        status=client.get('http://127.0.0.1:8081/v1/status').json()
        assert status.get('loaded') and 'ud-iq4_xs' in status.get('model','') and status.get('cache_max_tokens')==32768,status
        reply=client.post('http://127.0.0.1:8086/v1/chat/completions',json={
            'model':'qwen3.8-flash-next','messages':[{'role':'user','content':'Reply exactly OK.'}],
            'max_completion_tokens':8,'reasoning_effort':'none','temperature':0},headers={'Origin':'https://browser-client.example'})
        reply.raise_for_status()
        summary['independent_restore_check']={'model':status['model'],'context':status['cache_max_tokens'],
                                              'http_status':reply.status_code,'reply':reply.json()['choices'][0]['message']['content'],
                                              'cors_origin':reply.headers.get('access-control-allow-origin')}
except Exception as exc:summary['restore_verification_error']=repr(exc)
(folder/'final-summary.json').write_text(json.dumps(summary,indent=2))
with (folder/'final-summary.csv').open('w',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=list(csv_rows[0]));writer.writeheader();writer.writerows(csv_rows)
print('Two complete sweeps:',summary['complete_two_sweeps'])
print('Accepted fresh matrix requests:',len(valid_rows),'excluded attempts:',len(data['excluded']))
for key,model in summary['models'].items():
    print(model['name'],'short',model['short_chat'])
    for target,row in model['matrix'].items():
        print(target,json.dumps({k:row[k] for k in ('prompt_tokens','prompt_tps','decode_tps','ttft_s')}))
    print('Repeat prompt:',model['repeat_prompt'])
print('Restored:',summary['restored'])
