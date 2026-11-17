"""Two monitored sweeps of all three installed models, bounded by user deadline."""

from desktop_policy import require_desktop
require_desktop()
import csv
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

import httpx
import psutil

ROOT = Path(__file__).resolve().parent
OUT = Path(sys.argv[1]) if len(sys.argv)>1 else ROOT / 'benchmark-clean' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
OUT.mkdir(parents=True, exist_ok=True)
os.environ['BENCHMARK_ZERO_PENALTIES'] = '1'
sys.argv = [sys.argv[0], str(OUT)]
import benchmark_matrix as bm

DEADLINE = datetime.fromisoformat('2026-10-30T12:03:07+00:00').timestamp()
RESTORE_RESERVE = 480
MODELS = [
    ('ud', 'unsloth', 'UD-IQ4_XS', 'CleanUD64K', 'Qwen3.8-Flash-Next / Unsloth UD-IQ4_XS'),
    ('iq3s', 'qwen', 'IQ3_S', 'CleanIQ3S64K', 'Qwen3.8-Flash-Next / ISTA-DASLab GSQ-RCO IQ3_S'),
    ('qwen36', 'qwen36', 'UD-Q4_K_XL', 'CleanQwen3664K', 'Qwen3.6-35B-A3B / Unsloth UD-Q4_K_XL'),
]
bm.NAMES.update(model[3].lower() for model in MODELS)
bm.OUT = OUT
DATA = {'started_utc': datetime.now(timezone.utc).isoformat(),
        'deadline_utc': datetime.fromtimestamp(DEADLINE, timezone.utc).isoformat(),
        'method': {'allocated_context':65536,'input_targets':[4096,16384,32768,60000],
                   'sweeps_requested':2,'fresh_repeats_per_cell_per_sweep':2,'max_output_tokens':512,
                   'temperature':0,'presence_penalty':0,'frequency_penalty':0,'repetition_penalty':1,
                   'thinking':False,'parallel':1,'vision':False,
                   'fresh_cache_max_tokens':64,'minimum_sustained_output_tokens':256,
                   'external_cpu_mean_limit_percent':5,'external_cpu_burst_limit_percent':15,
                   'external_io_mean_limit_mb_s':5,
                   'limitations':'Ordinary Windows apps remain open. Process CPU/IO monitoring cannot prove zero external GPU contention under WDDM. This is a speed workload, not a quality benchmark.'},
        'models':{},'failures':[],'excluded':[],'events':[]}
bm.REPORT = DATA
if (OUT/'results.json').exists():
    DATA=json.loads((OUT/'results.json').read_text())
    DATA.setdefault('resumptions',[]).append({'at':datetime.now(timezone.utc).isoformat(),
        'prior_stop':DATA.pop('completed_utc',None),'reason':os.getenv('CLEAN_RESUME_REASON','Resume unfinished blocks after a background-activity interruption.')})
    DATA.pop('restored',None)
    for failure in DATA['failures']:
        if 'External CPU/IO did not settle' in failure.get('orchestration',''):
            failure['resolved']='Resumed unfinished blocks; busy-interval samples remain excluded.'
    bm.REPORT=DATA
    repeats=1 if DEADLINE-time.time()<55*60 else 2
    DATA['method']['repeats_by_sweep']={'1':2,'2':repeats}
    DATA['method']['fresh_repeats_per_cell_per_sweep']=DATA['method']['repeats_by_sweep']

def save():
    temp = OUT / 'results.json.tmp'
    temp.write_text(json.dumps(DATA,indent=2),encoding='utf-8')
    os.replace(temp,OUT/'results.json')

def log(message):
    stamp = datetime.now(timezone.utc).isoformat()
    print(stamp, message, flush=True)
    DATA['events'].append({'at':stamp,'message':message})
    save()

class Monitor:
    def __init__(self):
        self.samples=[]
        self.stop_event=threading.Event()
        self.previous={}
        self.owned={}
        self.last=None
        self.thread=threading.Thread(target=self.loop,daemon=True)
    def loop(self):
        logical=psutil.cpu_count() or 12
        with (OUT/'system-samples.jsonl').open('a',encoding='utf-8') as sink:
            while not self.stop_event.is_set():
                now=time.time()
                delta=now-self.last if self.last else None
                cpu=io=0
                contributors=[]
                current={}
                for proc in psutil.process_iter(['pid','name','create_time','cpu_times']):
                    try:
                        info=proc.info
                        identity=(info['pid'],info['create_time'])
                        if identity not in self.owned:
                            command=' '.join(proc.cmdline()).lower().replace('\\','/')
                            self.owned[identity]=(str(ROOT).lower().replace('\\','/') in command or '/strata/' in command)
                        if info['pid'] in (0,4) or self.owned[identity] or info['name'] in ('Registry','Memory Compression'):
                            continue
                        times=info['cpu_times']
                        counters=proc.io_counters()
                        values=(times.user+times.system,counters.read_bytes+counters.write_bytes)
                        current[identity]=values
                        previous=self.previous.get(identity)
                        if previous and delta:
                            pct=max(0,values[0]-previous[0])/delta/logical*100
                            rate=max(0,values[1]-previous[1])/delta
                            cpu+=pct; io+=rate
                            if pct>0.2 or rate>100000:
                                contributors.append({'pid':info['pid'],'name':info['name'],'cpu_percent':round(pct,2),'io_mb_s':round(rate/1e6,2)})
                    except (psutil.Error,OSError,AttributeError):
                        continue
                if delta:
                    sample={'time':now,'external_cpu_percent':cpu,'external_io_mb_s':io/1e6,
                            'available_ram_bytes':psutil.virtual_memory().available,
                            'top_external':sorted(contributors,key=lambda x:x['cpu_percent'],reverse=True)[:5]}
                    self.samples.append(sample)
                    sink.write(json.dumps(sample)+'\n'); sink.flush()
                self.previous=current; self.last=now
                self.stop_event.wait(2)
    def summary(self,start,end):
        samples=[s for s in self.samples if start<=s['time']<=end]
        if not samples:
            return {'clean':False,'reason':'no monitor samples'}
        cpu=[s['external_cpu_percent'] for s in samples]
        io=[s['external_io_mb_s'] for s in samples]
        mean_cpu=statistics.mean(cpu); mean_io=statistics.mean(io)
        busy_seconds=sum(v>15 for v in cpu)*2
        clean=mean_cpu<=5 and mean_io<=5 and busy_seconds<max(6,(end-start)*.2)
        return {'clean':clean,'samples':len(samples),'mean_external_cpu_percent':mean_cpu,
                'max_external_cpu_percent':max(cpu),'external_cpu_over15_seconds':busy_seconds,
                'mean_external_io_mb_s':mean_io,'max_external_io_mb_s':max(io),
                'minimum_available_ram_bytes':min(s['available_ram_bytes'] for s in samples),
                'top_external_at_peak':max(samples,key=lambda s:s['external_cpu_percent'])['top_external']}
    def quiet(self,initial=False):
        end=min(time.time()+600,DEADLINE-RESTORE_RESERVE-240)
        if initial:
            self.stop_event.wait(16)
        while time.time()<end:
            status=self.summary(time.time()-12,time.time())
            if status.get('clean') and status.get('samples',0)>=3:
                return status
            self.stop_event.wait(6)
        raise RuntimeError('External CPU/IO did not settle within the bounded quiet wait')

monitor=Monitor()

def stats(values):
    return bm.stat(values)

def refresh_summary():
    summary={'method':DATA['method'],'models':{},'failures':DATA['failures'],
             'excluded_runs':len(DATA['excluded']),'restored':DATA.get('restored',False)}
    table=[]
    for key,model in DATA['models'].items():
        out={'name':model['name'],'matrix':{},'short_chat':{},'prefix_reuse':{}}
        for target in ('4096','16384','32768','60000'):
            rows=[r for r in model['requests'] if r['phase']=='matrix' and str(r['target_tokens'])==target and r['accepted']]
            row={field:stats(r[field] for r in rows) for field in
                 ('prompt_tokens','prompt_tps','decode_tps','ttft_s','total_s','generated_tokens','reused_tokens')}
            row['sweeps']=sorted(set(r['sweep'] for r in rows))
            row['drafts_offered']=sum(r.get('drafts_offered') or 0 for r in rows)
            row['drafts_accepted']=sum(r.get('drafts_accepted') or 0 for r in rows)
            row['external_cpu_mean_percent']=stats(r['monitor']['mean_external_cpu_percent'] for r in rows)
            out['matrix'][target]=row
            flat={'model':model['name'],'target_input_tokens':target,'sweeps':','.join(map(str,row['sweeps']))}
            for field in ('prompt_tokens','prompt_tps','decode_tps','ttft_s','total_s','generated_tokens','reused_tokens'):
                for metric in ('median','min','max','n'):
                    flat[field+'_'+metric]=(row[field] or {}).get(metric)
            table.append(flat)
        for phase,label in [('short','short_chat'),('cached','prefix_reuse')]:
            rows=[r for r in model['requests'] if r['phase']==phase and r['accepted']]
            out[label]={field:stats(r[field] for r in rows) for field in
                         ('prompt_tokens','decode_tps','ttft_s','generated_tokens','reused_tokens')}
        out['loads']=model['loads']
        summary['models'][key]=out
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2))
    if table:
        with (OUT/'summary.csv').open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(table[0]));writer.writeheader();writer.writerows(table)

def measured(key,sweep,phase,target,run,messages,limit):
    for attempt in range(1,4):
        if time.time()>DEADLINE-RESTORE_RESERVE-240:
            raise TimeoutError('Reserved time for endpoint restoration')
        monitor.quiet()
        start=time.time()
        raw=bm.chat(bm.BASE,'',messages,limit,timeout=min(900,max(60,DEADLINE-time.time()-RESTORE_RESERVE)))
        end=time.time()
        row=bm.digest(raw)
        row.update(model=key,sweep=sweep,phase=phase,target_tokens=target,run=run,attempt=attempt,
                   started_utc=datetime.fromtimestamp(start,timezone.utc).isoformat(),raw=raw,
                   monitor=monitor.summary(start,end))
        reasons=[]
        if not row['ok']: reasons.append('request_error')
        if not row['monitor']['clean']: reasons.append('external_activity')
        if row.get('decode_tps_source')!='engine': reasons.append('missing_engine_decode_timing')
        if phase=='matrix':
            if row.get('reused_tokens') is None or row['reused_tokens']>64: reasons.append('not_fresh')
            if row.get('prompt_tps_source')!='engine': reasons.append('missing_engine_prefill_timing')
            if row['generated_tokens']<256: reasons.append('short_output')
            if not row.get('drafts_offered'): reasons.append('mtp_inactive')
        row['accepted']=not reasons;row['exclusion_reasons']=reasons
        DATA['models'][key]['requests'].append(row)
        with (OUT/'requests.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
        if reasons: DATA['excluded'].append({k:row[k] for k in ('model','sweep','phase','target_tokens','run','attempt','exclusion_reasons')})
        save();refresh_summary()
        log(f'sweep{sweep} {key} {phase} target{target} run{run}: input={row["prompt_tokens"]} '
            f'cached={row["reused_tokens"]} prefill={row["prompt_tps"]} decode={row["decode_tps"]} '
            f'out={row["generated_tokens"]} TTFT={row["ttft_s"]} externalCPU={row["monitor"].get("mean_external_cpu_percent",-1):.2f}% accepted={row["accepted"]} {reasons}')
        if not reasons:return row
        if attempt==3:
            DATA['failures'].append({'model':key,'sweep':sweep,'phase':phase,'target':target,'reason':reasons})
            save();return row
        if phase not in ('cached','cache_seed'):
            messages=[dict(m) for m in messages]
            messages[0]['content']='[retry '+uuid.uuid4().hex+']\n'+messages[0]['content']

def run_model(sweep,definition):
    key,family,quant,preset,name=definition
    bm.ACTIVE_MODEL=key
    info=DATA['models'].setdefault(key,{'name':name,'requests':[],'loads':[]})
    loaded=None
    if any(load['sweep']==sweep for load in info['loads']):
        try:
            candidate=httpx.get(bm.BASE+'/v1/status',timeout=4,trust_env=False).json()
            cfg=json.loads((Path(os.environ['LOCALAPPDATA'])/'Strata/launch'/f'{preset.lower()}.json').read_text())
            expected={'ud':'ud-iq4_xs','iq3s':'iq3_s','qwen36':'qwen3.6'}[key]
            if candidate.get('loaded') and candidate.get('cache_max_tokens')==65536 and expected in candidate.get('model','') and str(ROOT/'models').lower().replace('\\','/') in json.dumps(cfg).lower().replace('\\\\','/').replace('\\','/'):
                loaded=candidate
                log(f'Reusing verified incomplete sweep{sweep} {key} backend after coordinator resume')
        except (httpx.HTTPError,OSError,ValueError):pass
    if loaded is None:loaded=bm.start_model(key,family,quant,preset)
    before=bm.snapshot()
    if before.get('other_loaded_models'):raise RuntimeError('Another loaded model detected')
    info['loads'].append({'sweep':sweep,'status':loaded,'before':before})
    suite=bm.Suite(bm.BASE,'',65536,1,False,log=lambda s:log(key+': '+s))
    suite.warm_up()
    quiet=monitor.quiet(initial=True)
    info['loads'][-1]['settled']=quiet
    save()
    for i,prompt in enumerate(bm.strata_bench.PROMPTS,1):
        if any(r['accepted'] and r['sweep']==sweep and r['phase']=='short' and r['run']==i for r in info['requests']):continue
        messages=[{'role':'user','content':'[fresh '+uuid.uuid4().hex+'] '+prompt}]
        measured(key,sweep,'short',0,i,messages,100)
    for target in (4096,16384,32768,60000):
        for run in range(1,DATA['method'].get('repeats_by_sweep',{}).get(str(sweep),2)+1):
            if any(r['accepted'] and r['sweep']==sweep and r['phase']=='matrix' and r['target_tokens']==target and r['run']==run for r in info['requests']):continue
            messages=suite.prompt_for(target-90,run)
            messages[0]['content']='[fresh '+uuid.uuid4().hex+']\n'+messages[0]['content']+bm.TASK
            measured(key,sweep,'matrix',target,run,messages,512)
    messages=suite.prompt_for(16384-90,'cache')
    messages[0]['content']='[fresh '+uuid.uuid4().hex+']\n'+messages[0]['content']+bm.TASK
    measured(key,sweep,'cache_seed',16384,0,messages,128)
    for run in (1,2):
        if not any(r['accepted'] and r['sweep']==sweep and r['phase']=='cached' and r['run']==run for r in info['requests']):
            measured(key,sweep,'cached',16384,run,messages,128)
    if sweep==2:
        prior=[r for r in info['requests'] if r['sweep']==sweep and r['phase']=='cached' and r['accepted']]
        if prior:
            followup=[*messages,{'role':'assistant','content':prior[-1]['raw']['text']},
                      {'role':'user','content':'Continue the tutorial with the next implementation section.'}]
            measured(key,sweep,'continuation',16384,1,followup,128)
    info['loads'][-1]['after']=bm.snapshot()
    for runtime in bm.manage.strata_status().get('running',[]):
        for source in (Path(runtime['config']),Path(runtime['config']).with_suffix('.engine.log')):
            if source.exists():shutil.copy2(source,OUT/f'sweep{sweep}-{key}-{source.name}')
    save();refresh_summary()

def main():
    log('Clean dataset: '+str(OUT))
    monitor.thread.start()
    DATA.setdefault('initial',bm.snapshot())
    gateway_pid=bm.manage.read_pid('gateway')
    if gateway_pid:
        try:
            command=' '.join(psutil.Process(gateway_pid).cmdline()).lower().replace('\\','/')
            if str(ROOT).lower().replace('\\','/') in command and 'uvicorn' in command and '8086' in command:
                subprocess.run(['taskkill.exe','/PID',str(gateway_pid),'/T','/F'],check=True,capture_output=True,creationflags=bm.manage.HIDDEN)
            else:raise RuntimeError('Gateway PID is not owned')
        except psutil.NoSuchProcess:pass
    try:
        for sweep in (1,2):
            order=MODELS if sweep==1 else list(reversed(MODELS))
            for definition in order:
                prior=DATA['models'].get(definition[0],{}).get('requests',[])
                accepted=[r for r in prior if r['sweep']==sweep and r['accepted']]
                expected_repeats=DATA['method'].get('repeats_by_sweep',{}).get(str(sweep),2)
                if all(sum(r['phase']=='matrix' and r['target_tokens']==target for r in accepted)>=expected_repeats
                       for target in (4096,16384,32768,60000)) and sum(r['phase']=='cached' for r in accepted)>=2:
                    log(f'Keeping completed sweep{sweep} block: {definition[-1]}')
                    continue
                block_allowance=900 if expected_repeats>=2 else 600
                if time.time()>DEADLINE-RESTORE_RESERVE-block_allowance:
                    raise TimeoutError('Insufficient time for another complete model block')
                log(f'Starting sweep{sweep}: {definition[-1]}')
                run_model(sweep,definition)
            if sweep not in DATA.setdefault('completed_sweeps',[]):DATA['completed_sweeps'].append(sweep)
            save()
    except Exception as exc:
        DATA['failures'].append({'orchestration':repr(exc)})
        log('Stopped measurement: '+repr(exc))
    finally:
        log('Restoring original FlashNext32K and gateway8086')
        try:
            bm.stop_backend()
            result=bm.cli(['launch','--preset','FlashNext32K','--yes','--no-tui'],'restore.log',1800)
            if result.returncode:raise RuntimeError('Restore launcher failed')
            deadline=time.time()+1200
            while time.time()<deadline:
                try:
                    status=httpx.get(bm.BASE+'/v1/status',timeout=4,trust_env=False).json()
                    if status.get('loaded') and 'ud-iq4_xs' in status.get('model',''):break
                except (httpx.HTTPError,ValueError):pass
                time.sleep(3)
            else:raise TimeoutError('Restored model did not become ready')
            bm.restart_gateway()
            for _ in range(40):
                if bm.manage.probe('http://127.0.0.1:8086/health')['ok']:break
                time.sleep(.5)
            with httpx.Client(timeout=90,trust_env=False) as client:
                reply=client.post('http://127.0.0.1:8086/v1/chat/completions',json={
                    'model':'qwen3.8-flash-next','messages':[{'role':'user','content':'Reply exactly OK.'}],
                    'max_completion_tokens':8,'reasoning_effort':'none','temperature':0},headers={'Origin':'https://browser-client.example'})
                reply.raise_for_status()
                DATA['restored']={'status':reply.status_code,'reply':reply.json()['choices'][0]['message']['content'],
                                  'cors_origin':reply.headers.get('access-control-allow-origin')}
        except Exception as exc:DATA['restore_error']=repr(exc)
        monitor.stop_event.set();monitor.thread.join(timeout=5)
        DATA['completed_utc']=datetime.now(timezone.utc).isoformat()
        save();refresh_summary()
        log('Clean benchmark finished: '+str(OUT/'summary.csv'))

if __name__=='__main__':main()
