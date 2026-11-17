"""HTTP-only pinned public accuracy jobs; cannot start, stop or load engines."""
from pathlib import Path
import argparse, hashlib, json, sys, time, urllib.request, urllib.error
import psutil
import client

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
BENCH=ROOT.parent/'gpu-bench'
PLAN=BENCH/'untracked/glm-tests-20261106/capability-plan'
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(route, data=None, base='http://127.0.0.1:8713/api'):
    body=None if data is None else json.dumps(data).encode()
    req=urllib.request.Request(base+route,data=body,headers={'Content-Type':'application/json'})
    for attempt in range(4):
        try:
            with opener.open(req,timeout=15) as response:return json.load(response)
        except urllib.error.HTTPError as exc:
            if data is not None or exc.code not in (500,502,503,504) or attempt==3:raise
            print(json.dumps({'metadata_read_retry':route,'http_status':exc.code,'attempt':attempt+1}),flush=True)
            time.sleep(.25*2**attempt)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--label',required=True);p.add_argument('--model',default=client.MODEL)
    p.add_argument('--context',type=int,required=True)
    p.add_argument('--suites',nargs='+',choices=['arc32','gsm32','mmlu64'],default=['arc32','gsm32','mmlu64'])
    p.add_argument('--max-tokens',type=int,default=2048)
    p.add_argument('--resume',action='store_true',help='Resume metadata collection and remaining suites; never rerun completed owned inference.')
    a=p.parse_args()
    if not a.label.replace('-','').replace('_','').isalnum():raise ValueError('Use a simple cohort label.')
    out=HERE/a.label;out.mkdir(exist_ok=a.resume)
    active=json.loads((ROOT/'experimental-maya/active-config.json').read_text())
    loaded=api('/status',base='http://127.0.0.1:8097/v1')
    client.verify_loaded_status(loaded,active,a.model,a.context)
    version=api('/version')
    if version.get('features',{}).get('capability_start_transfer_probes') is not False:
        raise RuntimeError('Capability jobs must not run transfer probes beside the model.')
    client.save(out/'loaded-before.json',loaded);client.save(out/'active-config.json',active)
    client.save(out/'helper-version.json',version)
    client.save(out/'prepared-profile.json',json.loads((ROOT/'experimental-maya/prepared.json').read_text()))
    proof=json.loads((PLAN/'selection-proof.json').read_text())
    summaries=[]
    for suite in a.suites:
        count=64 if suite=='mmlu64' else 32
        base_suite=suite.replace('32','64')
        cfg=json.loads((PLAN/(base_suite+'.config.json')).read_text())
        before=api('/status')
        if before.get('running') or before.get('capability_running') or api('/capabilities/status').get('running'):
            raise RuntimeError('Another Wattever job is active; preserve it.')
        current=api('/status',base='http://127.0.0.1:8097/v1')
        client.verify_loaded_status(current,active,a.model,a.context)
        cfg.update(model=a.model,sample_size=0 if count==64 else count,max_tokens=a.max_tokens,
            timeout_s=0,endpoint_reasoning_effort='low',reported_reasoning='Low requested; captured provider output',
            label=a.label+' '+suite+' Low',
            notes='Accuracy-only study. One fresh request per item, one attempt, no implicit retry; same frozen task selection across quantizations. Total output cap includes reasoning. Background S24 download may be active: no inference speed/energy comparison is claimed. Model is already loaded exclusively by Strata Control.')
        cfg['reproducibility'].update(engine='Project Maya experimental',
            engine_version='1.1.59+wddm-cap-buffered-direct',context_tokens=a.context,
            model_revision=active['control_condition']['model_revision'],quantization=active['control_condition']['quantization'])
        owned=out/(suite+'-owned-run.json')
        if a.resume and owned.exists():
            if json.loads((out/(suite+'-requested-config.json')).read_text())!=cfg:
                raise RuntimeError('Resume configuration changed; preserve the original attempt.')
            run_id=json.loads(owned.read_text())['run_id']
            existing=api('/capabilities/runs/'+run_id)
            if existing.get('status')!='completed':
                raise RuntimeError('Existing owned job is not complete; preserve it without an inference retry.')
            print(json.dumps({'resume_completed_metadata':suite,'run_id':run_id}),flush=True)
        else:
            client.save(out/(suite+'-requested-config.json'),cfg)
            run_id=api('/capabilities/start',cfg)['run_id']
            client.save(owned,{'run_id':run_id})
            print(json.dumps({'started':suite,'run_id':run_id}),flush=True)
        last=-1
        try:
            while True:
                state=api('/capabilities/status')
                if state.get('run_id')!=run_id:raise RuntimeError('Capability ownership changed; preserve the other job.')
                done=state.get('live',{}).get('done',0)
                if done!=last:
                    last=done
                    raw=api('/capabilities/runs/'+run_id)
                    client.save(out/(suite+'-raw.json'),raw)
                    summary=raw.get('summary') or {}
                    print(json.dumps({'suite':suite,'done':done,
                        **{k:summary.get(k) for k in ('planned','correct','incorrect','errors','truncated','percent')}}),flush=True)
                if not state.get('running'):
                    raw=api('/capabilities/runs/'+run_id)
                    client.save(out/(suite+'-raw.json'),raw)
                    if raw.get('status')!='completed' or len(raw.get('results',[]))!=count:
                        raise RuntimeError('Public quality job incomplete; preserve every attempt.')
                    reference=next(x for x in proof['jobs'] if x['name']==base_suite)
                    if raw['suite_digest']!=reference['suite_digest']:
                        raise RuntimeError('Pinned suite digest differs from the prepared reference.')
                    if not set(raw['protocol']['selected_item_ids']).issubset(reference['selected_item_ids']):
                        raise RuntimeError('Public selection is not nested in the frozen 64-item reference.')
                    client.save(out/(suite+'-export.json'),api('/capabilities/runs/'+run_id+'/download'))
                    summaries.append({'suite':suite,'run_id':run_id,'summary':raw['summary'],
                        'evaluation_key':raw['evaluation_key'],'suite_digest':raw['suite_digest'],
                        'selected_item_ids':raw['protocol']['selected_item_ids']})
                    break
                if psutil.virtual_memory().available<1.5*2**30:
                    raise RuntimeError('Measured available RAM fell below the experiment stop threshold.')
                serving=api('/status',base='http://127.0.0.1:8097/v1')
                if serving.get('loaded') is not True or serving.get('model')!=a.model or serving.get('recorded_condition')!=active['control_condition']:
                    raise RuntimeError('Loaded model condition changed during the public quality job.')
                activity=serving.get('activity') or {}
                if activity.get('in_flight',0)>1 or (activity.get('queue') or {}).get('waiting',0)>0:
                    raise RuntimeError('Another client is competing with the accuracy job.')
                time.sleep(3)
        except BaseException:
            state=api('/capabilities/status')
            if state.get('running') and state.get('run_id')==run_id:
                client.save(out/(suite+'-cancel.json'),api('/capabilities/cancel',{'expected_run_id':run_id}))
            raise
        client.save(out/'summary.json',{'model':a.model,'context':a.context,'reasoning':'low',
            'max_tokens':a.max_tokens,'scope':'accuracy only; background download allowed; no speed comparison',
            'jobs':summaries})
    print('All public accuracy jobs completed; no model lifecycle operations performed.',flush=True)


if __name__=='__main__':main()
