"""Independently check completed measurement evidence; no HTTP or engine operations."""
from pathlib import Path
import csv, datetime, hashlib, json, statistics

HERE=Path(__file__).resolve().parent
issues=[];warnings=[];cohorts=[];csv_rows=[];prompt_identities={}
def check(ok, label, message):
    if not ok:issues.append({'cohort':label,'failure':message})

for path in sorted([*HERE.glob('xs1159-cap1-*.json'),*HERE.glob('s24-cap1-*.json')]):
    d=json.loads(path.read_text(encoding='utf-8'))
    if not d.get('results') or not d.get('finished_unix') or d.get('error') or d.get('measurement_scope','').startswith('accuracy only'):
        continue
    if not all('target' in r for r in d['results']):continue
    label=d['label'];condition=d['engine_configuration']['control_condition'];env=d['engine_configuration']['env']
    check(condition['requested_mtp'] is True,label,'MTP was not requested')
    check(d['initial_status']['engine_info']['mtp']==1,label,'MTP head was not confirmed')
    check(condition['kv']=='int8',label,'Unexpected KV type')
    check(condition['context']==d['context'],label,'Context identity mismatch')
    check(len(condition['gpu_order'].split(','))==2,label,'Not a two-GPU condition')
    for prefix in ('MAYA_GLM_','STRATA_GLM_'):
        check(float(env.get(prefix+'DISK_SKIP_W','0'))==0 and float(env.get(prefix+'MISS_SKIP_W','0'))==0,label,'Expert skipping enabled')
    if not d.get('download_quiescence'):
        warnings.append({'cohort':label,'observation':'Historical download pause was operator-controlled; no embedded downloader acknowledgement in this client revision.'})
    cap_hits=0
    for r in d['results']:
        identity=f"target={r['target']}, repeat={r['repeat']}"
        check(r['prompt_tokens']+d['sampling']['max_tokens']<=d['context'],label,'Insufficient generation headroom: '+identity)
        check(abs(r['prompt_tokens']-r['target'])<=8,label,'Actual prompt differs substantially from target: '+identity)
        check(r['completion_tokens_estimated'] is False,label,'Output token count estimated: '+identity)
        check(r['response_model']==d['model'],label,'Wrong response model: '+identity)
        check(r['reported_timings']['prompt_n']==r['prompt_tokens'],label,'Native/client input token disagreement: '+identity)
        check(r['reported_timings']['predicted_n']==r['completion_tokens'],label,'Native/client output token disagreement: '+identity)
        check(r['reported_timings'].get('cache_n',0)==0,label,'Cached prompt reuse: '+identity)
        check(r['engine_prefill_s']>0 and r['engine_decode_s']>0,label,'Missing native phase durations: '+identity)
        check(r['wall_s']>=r['ttft_s']>0,label,'Invalid client wall/TTFT: '+identity)
        check({g['index'] for g in r['gpu'].get('gpus',[])}=={0,1},label,'Missing per-GPU measurement: '+identity)
        check(r['retrieval']['passed'],label,'Retrieval subtask failed: '+identity)
        # A correct ledger with repeated output/cap exhaustion remains a separate quality failure.
        if r['finish_reason']=='length':cap_hits+=1
        key=(r['target'],r['repeat'],d['sampling'].get('reasoning_effort','none'))
        previous=prompt_identities.setdefault(key,r['prompt_sha256'])
        check(previous==r['prompt_sha256'],label,'Frozen prompt bytes differ across matched target/repeat: '+identity)
    for target in sorted({r['target'] for r in d['results']}):
        rs=[r for r in d['results'] if r['target']==target]
        check(len(rs)>=3,label,'Fewer than three measured requests at '+str(target))
        check(len({r['repeat'] for r in rs})==len(rs),label,'Duplicate measured repeat at '+str(target))
        row={'cohort':label,'model':d['model'],'context':d['context'],'target':target,'n':len(rs),
             'output_cap':d['sampling']['max_tokens'],'reasoning':d['sampling'].get('reasoning_effort','none'),
             'ram_gib_applied':d['initial_status']['engine_info'].get('ram_gb'),
             'vram_reserve_mib':condition['memory_policy_requested']['vram_reserve_mib'],
             'retrieval_passes':sum(r['retrieval']['passed'] for r in rs),
             'natural_stops':sum(r['finish_reason']=='stop' for r in rs),'cap_hits':sum(r['finish_reason']=='length' for r in rs)}
        for key in ['prompt_tokens','completion_tokens','engine_prefill_s','engine_prefill_tps','engine_decode_s','engine_decode_tps','ttft_s','wall_s','disk_d_read_gib']:
            values=[r[key] for r in rs if r.get(key) is not None]
            for suffix,fn in [('median',statistics.median),('min',min),('max',max)]:row[key+'_'+suffix]=fn(values) if values else None
        csv_rows.append(row)
    cohorts.append({'label':label,'measured_requests':len(d['results']),'cap_hits':cap_hits,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
public=[]
plan=json.loads((HERE/'public-quality-plan.json').read_text(encoding='utf-8'))
for folder in sorted(HERE.glob('*public128')):
    if not folder.is_dir():continue
    for path in sorted(folder.glob('*-raw.json')):
        d=json.loads(path.read_text(encoding='utf-8'))
        if d.get('status')!='completed':continue
        label=folder.name+'/'+d['suite_id']
        reference=next(j for j in plan['jobs'] if j['suite_id']==d['suite_id'])
        ids=d['protocol']['selected_item_ids']
        check(d['suite_digest']==reference['suite_digest'],label,'Public suite digest mismatch')
        check(ids==reference['selected_item_ids'],label,'Frozen public selection changed')
        check(len(d['results'])==len(ids)==len(set(ids)),label,'Public attempt count differs or duplicate IDs')
        check([r['item_id'] for r in d['results']]==ids,label,'Public per-item order changed')
        check(d['config']['max_tokens']==2048 and d['config']['endpoint_reasoning_effort']=='low',label,'Public reasoning/output budget changed')
        for row in d['results']:
            check(row.get('repeat')==0,label,'Unexpected public repetition')
            if row.get('finish_reason')=='length':
                check(row['scoring']['correct'] is False and row['scoring']['truncated'] is True,label,'Truncated output counted as correct')
        public.append({'cohort':folder.name,'suite':d['suite_id'],'correct':d['summary']['correct'],
                       'attempts':len(d['results']),'truncated':d['summary']['truncated'],
                       'errors':d['summary']['errors'],'run_id':d['id'],'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
report={'updated_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'Read-only evidence audit; no inference. Historical manual pause observations cannot be retrospectively proven by this audit.',
        'cohorts':cohorts,'total_measured_requests':sum(c['measured_requests'] for c in cohorts),
        'public_cohorts':public,'issues':issues,'warnings':warnings,'passed':not issues}
(HERE/'completed-evidence-audit.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
if csv_rows:
    with (HERE/'completed-tuning.csv').open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(csv_rows[0]));writer.writeheader();writer.writerows(csv_rows)
print(json.dumps({k:report[k] for k in ['total_measured_requests','issues','passed']}))
raise SystemExit(bool(issues))
