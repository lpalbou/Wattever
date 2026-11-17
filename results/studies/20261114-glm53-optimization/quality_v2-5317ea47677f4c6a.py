"""HTTP-only small exact-answer quality screen; never executes model-produced code.

Sixteen frozen tasks complement the speed client's five-record long retrieval.
This is a diagnostic screen, not a comprehensive intelligence benchmark.
"""
from pathlib import Path
import argparse
import hashlib
import itertools
import json
import re
import statistics
import sys
import time
import urllib.request

import client

HERE = Path(__file__).resolve().parent
ROOT = client.ROOT


def tasks():
    cases = []
    def add(name,category,question,expected):
        salt = hashlib.sha256(('glm-quality-v2-'+name).encode()).hexdigest()[:24]
        if type(expected) is int:
            shape = 'one JSON integer'
        elif isinstance(expected, str):
            shape = 'one JSON string'
        elif isinstance(expected, list):
            shape = 'one JSON array'
        elif isinstance(expected, dict):
            shape = 'one JSON object with exactly these top-level keys: '+', '.join(json.dumps(k) for k in expected)
        else:
            raise TypeError('Unsupported task answer type')
        prompt = ('Test identifier: '+salt+'.\n'+question+'\n'
                  'Response format: the entire response must be '+shape+'. '
                  'Write that value directly. Include no surrounding result/answer wrapper, markdown, or explanation.')
        cases.append(dict(name=name,category=category,prompt=prompt,expected=expected,
                          prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest()))
    add('integer-product','arithmetic','Compute 1337 multiplied by 7919.',1337*7919)
    add('modular-power','arithmetic','Compute the remainder when 17 raised to the 13th power is divided by 97.',pow(17,13,97))
    add('nested-alias','python-state',
        'What value does this Python program print? Represent it as JSON.\n'
        'a = [[1], [2]]\nb = a[:]\nb[0].append(3)\nb[1] = [9]\nprint(a)',[[1,3],[2]])
    def accumulating():
        acc=[];out=[]
        for x in [2,3,2]: acc.append(x);out.append(sum(acc))
        return out
    add('mutable-default','python-state',
        'What value does this Python program print?\n'
        'def f(x, acc=[]):\n    acc.append(x)\n    return sum(acc)\n'
        'print([f(2), f(3), f(2)])',accumulating())
    out=[]
    for x in range(4):
        for y in range(3):
            if (x+y)%2: continue
            out.append(10*x+y)
            if y==2: break
        else: out.append(-1)
    add('nested-loop-else','python-state',
        'What value does this Python program print?\n'
        'out = []\nfor x in range(4):\n    for y in range(3):\n'
        '        if (x+y)%2:\n            continue\n        out.append(10*x+y)\n'
        '        if y==2:\n            break\n    else:\n        out.append(-1)\nprint(out)',out)
    d={'a':[1],'b':[2]};alias=d['a'];d['a']=[3];alias.append(4);d['b']+=alias
    add('dictionary-alias','python-state',
        'What value does this Python program print? Represent the dictionary as JSON.\n'
        'd = {"a": [1], "b": [2]}\nalias = d["a"]\nd["a"] = [3]\n'
        'alias.append(4)\nd["b"] += alias\nprint(d)',d)
    order=[list(p) for p in itertools.permutations('ABCD') if p.index('B')==p.index('A')+1 and p.index('C')<p.index('A') and p.index('D')>p.index('B')]
    assert len(order)==1
    add('ordered-constraints','formal-constraints',
        'Place A,B,C,D in four positions from left to right, each exactly once. A is immediately before B. '
        'C is before A. D is after B. Return the unique order as a JSON array of strings.',order[0])
    truth=[list(x) for x in itertools.product([False,True],repeat=3) if x[0]==(not x[1]) and x[1]==(not x[2]) and x[2]==(x[0]==x[1])]
    assert len(truth)==1
    add('truth-liar','formal-constraints',
        'Each of A,B,C is either a truth-teller who always speaks truth or a liar who always speaks falsely. '
        'A says B is a liar. B says C is a liar. C says A and B have the same type. '
        'Return [A_is_truth_teller,B_is_truth_teller,C_is_truth_teller] as JSON booleans.',truth[0])
    solutions=[n for n in range(1,101) if n%5==3 and n%7==4 and n%3==2]
    assert len(solutions)==1
    add('modular-constraints','formal-constraints',
        'Find the unique integer from 1 through 100 inclusive whose remainder modulo 5 is 3, modulo 7 is 4, '
        'and modulo 3 is 2. Return the integer.',solutions[0])
    add('tool-search','json-tool-arguments',
        'Produce a tool-call JSON object with exactly keys name and arguments. Tool name: search_records. '
        'Arguments must be {query:"red fox",limit:3,include_archived:false,tags:["alpha","beta"]}. '
        'Use valid JSON, preserve array order and numeric/boolean types.',
        {'name':'search_records','arguments':{'query':'red fox','limit':3,'include_archived':False,'tags':['alpha','beta']}})
    add('tool-schedule','json-tool-arguments',
        'Produce a tool-call JSON object with exactly keys name and arguments. Tool name: schedule_event. '
        'Arguments: title exactly `Review "A"`; at exactly `2026-11-14T09:30:00+01:00`; '
        'attendees exactly ["a@example.test","b@example.test"]; duration_minutes integer 45; notes null.',
        {'name':'schedule_event','arguments':{'title':'Review "A"','at':'2026-11-14T09:30:00+01:00',
        'attendees':['a@example.test','b@example.test'],'duration_minutes':45,'notes':None}})
    add('stable-sort','data-transform',
        'Sort these objects by score ascending, preserving original relative order for equal scores. '
        'Return only the resulting id array: [{"id":"p","score":2},{"id":"q","score":1},'
        '{"id":"r","score":2},{"id":"s","score":1}].',['q','s','p','r'])
    add('deduplicate-order','data-transform',
        'Remove duplicate strings while retaining first-occurrence order: ["b","a","b","c","a","d","c"]. '
        'Return the resulting JSON array.',['b','a','c','d'])
    add('set-combination','data-transform',
        'Let A={1,2,4,7}, B={2,3,4,8}, C={4,8,9}. Compute(A union B) minus C. '
        'Return its elements as an ascending JSON array.',sorted(({1,2,4,7}|{2,3,4,8})-{4,8,9}))
    add('state-transactions','state-tracking',
        'A balance starts at 100. Transactions in order: add 25; subtract 40; double the balance; '
        'add a fee refund of 7; subtract 17. Return the final integer balance.',((100+25-40)*2)+7-17)
    add('nested-json-update','state-tracking',
        'Start with {"a":{"x":2,"y":5},"b":[1,3]}. Set a.x to its old value plus a.y; '
        'append the new a.x to b; delete a.y. Return the complete final JSON object.',{'a':{'x':7},'b':[1,3,7]})
    assert len(cases)==16
    return cases


def strict_score(text, expected):
    value=text.strip()
    # One enclosing code fence is a formatting wrapper; prose is not discarded.
    if value.startswith('```') and value.endswith('```'):
        value=re.sub(r'^```(?:json)?\s*','',value)[:-3].strip()
    try: actual=json.loads(value)
    except ValueError: return {'passed':False,'actual':None,'parse_error':True}
    # JSON booleans and numbers are distinct; Python's True==1 is insufficient.
    def same(a,b):
        if type(a) is not type(b): return False
        if isinstance(a,dict): return a.keys()==b.keys() and all(same(a[k],b[k]) for k in b)
        if isinstance(a,list): return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
        return a==b
    return {'passed':same(actual,expected),'actual':actual,'parse_error':False}


def score(text, expected):
    result = strict_score(text, expected)
    result['answer_content_passed'] = result['passed']
    result['format_passed'] = result['passed']
    result['failure_kind'] = None if result['passed'] else 'answer_or_format'
    actual = result.get('actual')
    if not result['passed'] and isinstance(actual, dict) and len(actual)==1:
        key = next(iter(actual))
        if key in ('result','answer','output'):
            semantic = strict_score(json.dumps(actual[key]), expected)
            if semantic['passed']:
                result['answer_content_passed'] = True
                result['failure_kind'] = 'format_only_wrapper'
    if not result['passed'] and not result.get('parse_error') and not result['answer_content_passed']:
        result['failure_kind'] = 'wrong_answer'
    return result


def measure_with_reasoning(lmstudio, *args, **kwargs):
    """Tap this process's SSE iterator while the standard client measures timing.

    Captures only explicitly streamed reasoning; no inference of unreported text.
    The standalone screen makes one sequential request at a time.
    """
    original = lmstudio._stream_payloads
    reasoning = []
    def tap(response, cancel):
        for payload in original(response, cancel):
            if payload != '[DONE]':
                try:
                    data = json.loads(payload)
                    for choice in data.get('choices') or []:
                        delta = choice.get('delta') or {}
                        value = delta.get('reasoning_content') or delta.get('reasoning')
                        if isinstance(value, str): reasoning.append(value)
                except (ValueError, TypeError, AttributeError):
                    pass  # The standard measurement parser reports malformed SSE.
            yield payload
    lmstudio._stream_payloads = tap
    try:
        row = lmstudio.measure_completion(*args, **kwargs)
    finally:
        lmstudio._stream_payloads = original
    row['reasoning_text'] = ''.join(reasoning)
    row['reasoning_capture'] = {'scope':'explicit streamed reasoning deltas only',
        'server_reasoning_tokens':row.get('reasoning_tokens'),
        'unstreamed_reasoning_text':'unavailable'}
    row['final_answer_text'] = row.get('output_text','')
    row['final_answer_present'] = bool(row['final_answer_text'].strip())
    row['generation_budget_exhausted'] = row.get('finish_reason') == 'length'
    return row


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--label',required=True);p.add_argument('--model',default=client.MODEL)
    p.add_argument('--base',default='http://127.0.0.1:8097/v1')
    p.add_argument('--context',type=int,default=98304)
    p.add_argument('--max-tokens',type=int,choices=[128,256,512,1024,2048,4096,8192],default=512)
    p.add_argument('--reasoning',choices=['none','auto','low','medium','high','xhigh','max'],default='none')
    p.add_argument('--pcie-frac',type=float);p.add_argument('--cpu-threads',type=int)
    p.add_argument('--decode-read-chunks',type=int)
    p.add_argument('--tokenizer',type=Path,default=client.DEFAULT_TOKENIZER)
    p.add_argument('--vendor-root',type=Path,default=client.VENDOR)
    p.add_argument('--prepare-only',action='store_true');args=p.parse_args()
    try:tune=client.tuning_values(args.pcie_frac,args.cpu_threads,args.decode_read_chunks)
    except ValueError as exc:p.error(str(exc))
    if not re.fullmatch(r'[A-Za-z0-9_-]+',args.label):p.error('Use a simple unique label')
    _,count,identity=client.official_tokenizer(args.tokenizer,args.vendor_root)
    cases=tasks()
    for case in cases:
        case['chat_tokens']=count(case['prompt'])
        if case['chat_tokens']+args.max_tokens>args.context:raise ValueError('Insufficient generation headroom')
    frozen=HERE/'quality-tasks-v2.json'
    if frozen.exists():
        saved=json.loads(frozen.read_text())
        def text_and_answers(rows):
            return [{k:v for k,v in row.items() if k!='chat_tokens'} for row in rows]
        if text_and_answers(saved['tasks'])!=text_and_answers(cases) or saved['tokenizer']['files_sha256']!=identity['files_sha256']:
            raise RuntimeError('Frozen quality texts/answers/tokenizer files changed; preserve existing cohort')
    else:
        saved={'suite':'glm-local-exact16-v2','tokenizer':identity,'tasks':cases}
        client.save(frozen,saved)
    # Preserve frozen none-mode texts/answers/counts. Reasoning changes only the
    # rendered assistant prefix, so size the actual requested template afresh.
    _,requested_count,identity=client.official_tokenizer(args.tokenizer,args.vendor_root,args.reasoning)
    for case,original in zip(cases,saved['tasks']):
        case['frozen_none_chat_tokens']=original['chat_tokens']
        case['chat_tokens']=requested_count(case['prompt'])
        if case['chat_tokens']+args.max_tokens>args.context:raise ValueError('Insufficient generation headroom for requested reasoning mode')
    renderer=client.record_renderer(identity,cases,args.reasoning,'quality-renderers-v2.json')
    if args.prepare_only:
        print(json.dumps({'prepared':len(cases),'suite':str(frozen),'max_prompt_tokens':max(c['chat_tokens'] for c in cases)}));return
    target=HERE/(args.label+'-quality-v2.json')
    if target.exists():raise FileExistsError('Preserve previous measurements; choose a new label')
    sys.path.insert(0,str(ROOT.parent/'gpu-bench'));sys.path.insert(0,str(ROOT/'untracked/memory_controls'))
    from wattever import lmstudio,power
    from memory_telemetry import Paging
    with urllib.request.urlopen(args.base+'/status',timeout=5) as response:status=json.load(response)
    config=json.loads((ROOT/'experimental-maya/active-config.json').read_text())
    client.verify_loaded_status(status,config,args.model,args.context)
    prepared,prepared_source=client.prepared_profile_snapshot(args.model)
    report={'suite':'glm-local-exact16-v2','label':args.label,'model':args.model,'started_unix':time.time(),
        'sampling':dict(temperature=0,top_p=1,seed=client.SEED,max_tokens=args.max_tokens,reasoning_effort=args.reasoning),
        'tokenizer':identity,'frozen_tokenizer':saved['tokenizer'],'renderer':renderer,
        'engine_configuration':config,'initial_status':status,'results':[],
        'request_strata_tune':tune,
        'timing_scope_note':'Accuracy only. Raw timings are diagnostics; background download or other storage work may be active, so these rates are not a controlled throughput comparison.',
        'prepared_profile':prepared,'prepared_profile_snapshot':prepared_source,
        'request_tuning_scope':'Request decode read_chunks does not change prefill environment read chunks; these are requested values.',
        'scope':'Sixteen local exact-answer diagnostic tasks; not a comprehensive capability benchmark. Five-record long retrieval is measured separately by client.py.'}
    client.save(target,report)
    gpu=power.PowerSampler(interval_ms=200);paging=Paging(HERE/(args.label+'-quality-v2-paging.jsonl'))
    gpu.start();paging.start()
    try:
        for case in cases:
            offset=client.ENGINE.stat().st_size if client.ENGINE.exists() else 0
            with client.extra_request_payload(tune,args.base,args.model):
                row=measure_with_reasoning(lmstudio,args.model,case['prompt'],args.max_tokens,base=args.base,api_base=True,
                    timeout=1800,temperature=0,top_p=1,seed=client.SEED,reasoning_effort=args.reasoning,capture_output=True,
                    provider_name='Control-owned Maya quality')
            time.sleep(.15);raw=b''
            if client.ENGINE.exists():
                with client.ENGINE.open('rb') as stream:
                    if client.ENGINE.stat().st_size>=offset:stream.seek(offset)
                    raw=stream.read()
            (HERE/(args.label+'-quality-v2-'+case['name']+'.log')).write_bytes(raw)
            row.update(task=case,score=score(row['output_text'],case['expected']),engine_log=client.parse_log(raw))
            row['renderer']=renderer
            row['request_strata_tune']=dict(tune)
            if row['generation_budget_exhausted'] and not row['final_answer_present']:
                row['score']['failure_kind']='budget_exhausted_no_final_answer'
            timing=row.get('reported_timings') or {}
            for phase,key,rate in [('prefill','prompt_ms','prompt_per_second'),('decode','predicted_ms','predicted_per_second')]:
                row['engine_'+phase+'_s']=timing[key]/1000 if key in timing else None
                row['engine_'+phase+'_tps']=timing.get(rate)
            row['gpu']=gpu.window(row['t_start'],row['t_end']);row['paging']=paging.window(row['t_start'],row['t_end'])
            disk=row['paging'].get('disk_deltas',{}).get('PhysicalDrive1',{})
            row['disk_d_read_gib']=disk['read_bytes']/2**30 if 'read_bytes' in disk else None
            row['protocol_valid']=(row.get('response_model')==args.model and abs(row['prompt_tokens']-case['chat_tokens'])<=4
                and row['prompt_tokens']+args.max_tokens<=args.context and timing.get('cache_n',0)<=64)
            report['results'].append(row)
            passed=sum(r['score']['passed'] and r['protocol_valid'] for r in report['results'])
            rates={k:[r[k] for r in report['results'] if r.get(k) is not None] for k in ['engine_prefill_tps','engine_decode_tps']}
            report['summary']={'passed':passed,'attempted':len(report['results']),'total':len(cases),
                'answer_content_passed':sum(r['score']['answer_content_passed'] and r['protocol_valid'] for r in report['results']),
                'format_only_failures':sum(r['score']['failure_kind']=='format_only_wrapper' for r in report['results']),
                'generation_budget_exhausted':sum(r['generation_budget_exhausted'] for r in report['results']),
                'budget_exhausted_no_final_answer':sum(r['score']['failure_kind']=='budget_exhausted_no_final_answer' for r in report['results']),
                'native_rate_medians':{k:statistics.median(v) if v else None for k,v in rates.items()},
                'by_category':{category:{'passed':sum(r['score']['passed'] and r['protocol_valid'] for r in report['results'] if r['task']['category']==category),
                'attempted':sum(r['task']['category']==category for r in report['results'])} for category in sorted({c['category'] for c in cases})}}
            client.save(target,report)
            print(json.dumps({'task':case['name'],'passed':row['score']['passed'],'protocol_valid':row['protocol_valid'],
                'answer_content_passed':row['score']['answer_content_passed'],'failure_kind':row['score']['failure_kind'],
                'score_so_far':f'{passed}/{len(report["results"])}','native_prefill_tps':row['engine_prefill_tps'],
                'native_decode_tps':row['engine_decode_tps'],'finish_reason':row['finish_reason'],
                'reasoning_tokens':row.get('reasoning_tokens'),'generation_budget_exhausted':row['generation_budget_exhausted'],
                'answer':row['output_text']}),flush=True)
            if not row['protocol_valid']:raise RuntimeError('Model/tokenization/headroom/cache protocol mismatch')
    except Exception as exc:report['error']=repr(exc);raise
    finally:
        gpu.stop();paging.stop();report['finished_unix']=time.time();client.save(target,report)
    print(json.dumps({'final':report['summary']}),flush=True)


if __name__=='__main__':main()
