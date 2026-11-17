"""Run frozen questions against an already-loaded Control backend. HTTP only, no engine lifecycle."""
from pathlib import Path
import argparse, datetime, hashlib, json, sys, time, traceback
import common
HERE=Path(__file__).resolve().parent
ROOT=common.ROOT

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model',required=True,choices=list(common.MODELS));p.add_argument('--label',required=True)
    p.add_argument('--mode',required=True,choices=['none','low']);p.add_argument('--context',type=int,default=32768)
    p.add_argument('--resume',action='store_true');p.add_argument('--screen-only',action='store_true')
    p.add_argument('--larger-mmlu',action='store_true');p.add_argument('--probe-only',action='store_true')
    a=p.parse_args()
    if not a.label.replace('-','').replace('_','').isalnum():raise ValueError('Use a simple unique label.')
    plan_path=HERE/'plan.json';plan_raw=plan_path.read_bytes();plan=json.loads(plan_raw)
    protocol=json.loads((HERE/'protocol.json').read_text())
    plan_sha=hashlib.sha256(plan_raw).hexdigest()
    if plan_sha!=protocol['frozen_plan_sha256']:raise RuntimeError('Frozen plan identity changed.')
    cap=4096 if a.larger_mmlu else 2048
    if a.larger_mmlu and a.mode!='low':raise ValueError('Larger-budget cohort is the separately declared enabled MMLU-Pro64 study.')
    if a.context!=protocol['primary_short_context']:raise ValueError('Primary quality reservation must match the frozen protocol.')
    base,status,config=common.wait_idle(a.model,a.context)
    family=common.MODELS[a.model]
    audit=json.loads((HERE/(family+'-mode-audit.json')).read_text(encoding='utf-8'))
    if not all(r['chat_responses_render_identical'] for r in audit['parity']):raise RuntimeError('Renderer parity not confirmed.')
    selected=[t for t in plan['tasks'] if not a.larger_mmlu or t.get('suite_id')=='mmlu-pro-balanced64-chat-v1']
    screen=protocol['functionality_screen']
    order={id:i for i,id in enumerate(screen)}
    selected.sort(key=lambda t:(0,order[t['id']]) if t['id'] in order else (1,plan['tasks'].index(t)))
    out=HERE/a.label;out.mkdir(exist_ok=a.resume)
    path=out/'run.json'
    config_sha=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()
    condition={'model':a.model,'mode_requested':a.mode,'context':a.context,'total_output_cap':cap,
        'sampling':{'temperature':0,'top_p':1},'active_config_sha256':config_sha,'plan_sha256':plan_sha,
        'cohort':'larger-mmlu64' if a.larger_mmlu else 'common152','endpoint':base+'/responses'}
    if a.resume:
        report=json.loads(path.read_text(encoding='utf-8'))
        if report['condition']!=condition:raise RuntimeError('Resume condition changed; preserve prior attempts and use a new label.')
    else:
        report={'schema':'quality-wait-run-v1','condition':condition,'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'active_config':config,'loaded_before':status,'mode_audit':audit,'protocol':protocol,
            'planned_ids':[t['id'] for t in selected],'results':[],'status':'preflight',
            'client_source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'helpers_sha256':{name:hashlib.sha256((HERE/name).read_bytes()).hexdigest() for name in ('common.py','code_grade.py')},
            'notes':'One inference attempt per task/condition. Timing includes prefill and all reasoning until the completed response. Standalone public/practical cohorts remain separate.'}
        if family=='glm':report['prepared_profile']=json.loads((ROOT/'experimental-maya/prepared.json').read_text())
        common.save(path,report)
    grading_sources={name:hashlib.sha256((HERE/name).read_bytes()).hexdigest() for name in ('common.py','code_grade.py')}
    report['current_grading_source_sha256']=grading_sources
    report['grading_revision']='v3-sequence-pair-semantics'
    if a.probe_only:
        if (out/'protocol-probe.json').exists():raise RuntimeError('Preserve existing probe; no implicit inference repeat.')
        payload={'model':a.model,'input':'Connectivity probe: compute37 times41 minus19 and return only the integer.',
                 'max_output_tokens':256,'reasoning':{'effort':a.mode},'temperature':0,'top_p':1,'stream':True,'store':False}
        result=common.collect(base,payload)
        common.save(out/'protocol-probe.json',{'scope':'Protocol only; no throughput/quality score. Background downloading may be active.','result':result,'payload':payload})
        print(json.dumps({'protocol_probe':result['provider_status'],'reasoning_tokens':result['reasoning_tokens'],
                          'output_tokens':result['total_generated_tokens'],'visible':result['answer'][:150]}),flush=True)
        return
    sys.path.insert(0,str(ROOT.parent/'gpu-bench'))
    from wattever.power import PowerSampler
    from memory_telemetry import Paging
    report['download_quiescence']=common.old_client.download_quiescence()
    gpu=PowerSampler(interval_ms=500);paging=Paging(out/'paging.jsonl')
    gpu.start();paging.start();report['status']='running';common.save(path,report)
    attempted={r['id'] for r in report['results']}
    try:
        for task in selected:
            if task['id'] in attempted:continue
            if a.screen_only and task['id'] not in screen:break
            if (out/'stop.after-request').exists():report['status']='stopped_after_request';break
            base,live,current=common.wait_idle(a.model,a.context)
            if hashlib.sha256(json.dumps(current,sort_keys=True).encode()).hexdigest()!=config_sha:
                raise RuntimeError('Loaded configuration changed during the cohort.')
            quiet=common.old_client.download_quiescence()
            payload={'model':a.model,'input':task['prompt'],'max_output_tokens':cap,'reasoning':{'effort':a.mode},
                     'temperature':0,'top_p':1,'stream':True,'store':False}
            start=time.perf_counter()
            row={'id':task['id'],'category':task['category'],'cohort':task['cohort'],'suite_id':task.get('suite_id'),
                 'prompt_sha256':task['prompt_sha256'],'request_started_unix':time.time(),'download_before':quiet}
            try:
                measured=common.collect(base,payload);end=time.perf_counter()
                row.update(measured)
                row['gpu']=gpu.window(start,end);row['paging']=paging.window(start,end)
                row['budget_violation']=row['total_generated_tokens']>cap
                row['generation_headroom_verified']=row['usage']['input_tokens']+cap<=a.context
                if row['response_model']!=a.model or not row['generation_headroom_verified']:
                    raise RuntimeError('Response identity or generation headroom mismatch.')
                row['grade']=common.score(task,row)
                row['grading_source_sha256']=grading_sources
                row['grading_revision']=report['grading_revision']
                if row['budget_violation']:
                    row['grade'].update(strict_success=False,content_success=False)
                    row['grade']['correct_within_s']={str(s):False for s in (30,60,120)}
                row['download_after']=common.old_client.download_quiescence()
                common.save(out/(task['id']+'.json'),row)
                report['results'].append(row);attempted.add(task['id']);report['summary']=common.summary(report['results']);common.save(path,report)
                print(json.dumps({'id':task['id'],'done':len(report['results']),'correct':row['grade']['strict_success'],
                    'content_correct':row['grade']['content_success'],'format':row['grade']['format_passed'],
                    'reasoning_tk':row['reasoning_tokens'],'total_output_tk':row['total_generated_tokens'],
                    'first_visible_s':row['first_visible_content_s'],'final_s':row['terminal_answer_s'],
                    'status':row['provider_status']}),flush=True)
                if row['budget_violation']:raise RuntimeError('Provider exceeded the declared total generation cap; stop before any further request.')
            except Exception as exc:
                if task['id'] not in attempted:
                    row['error']={'type':type(exc).__name__,'message':str(exc)[:2000]}
                    common.save(out/(task['id']+'.json'),row);report['results'].append(row);attempted.add(task['id'])
                raise
        else:report['status']='completed'
        if report['status']=='running':report['status']='functionality_screen_saved' if a.screen_only else 'partial'
    except BaseException as exc:
        report['status']='interrupted';report['error']={'type':type(exc).__name__,'message':str(exc)[:2000]}
        raise
    finally:
        gpu.stop();paging.stop();report['summary']=common.summary(report['results'])
        report['saved_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat();common.save(path,report)
    print(json.dumps({'condition_status':report['status'],'summary':report['summary']}),flush=True)

if __name__=='__main__':main()
