"""HTTP-only measurement and deterministic grading helpers; no lifecycle operations."""
from pathlib import Path
import decimal, hashlib, json, re, statistics, sys, time, urllib.request
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(HERE.parent/'glm_optimization'))
import client as old_client
import quality_v2
from code_grade import grade as code_grade
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))
MODELS={'qwen3.8-flash-next-iq3_s':'qwen','qwen3.8-flash-next-unsloth-ud-iq4_xs':'qwen',
        'glm-5.3-flash-maya-xs-iq1_s':'glm','glm-5.3-flash-maya-s24-iq2_xxs_s':'glm'}

def save(path,value):
    path=Path(path);tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8');tmp.replace(path)

def get(url):
    with OPENER.open(url,timeout=10) as response:return json.load(response)

def verify_idle(model,context):
    family=MODELS[model];port=8097 if family=='glm' else 8081
    base=f'http://127.0.0.1:{port}/v1'
    status=get(base+'/status')
    if status.get('service')!=('maya' if family=='glm' else 'strata') or status.get('auto_load') is not False:
        raise RuntimeError('Require a verified Control-owned endpoint with automatic loading disabled.')
    if status.get('loaded') is not True or status.get('model')!=model:
        raise RuntimeError('Exact candidate must already be loaded by Control; this client will not load it.')
    if (status.get('context') or {}).get('native')!=context:
        raise RuntimeError('Loaded reserved context differs from the declared condition.')
    if family=='glm' and (status.get('backend')!='project-maya-experimental' or status.get('model_routing')!='active-only'):
        raise RuntimeError('Maya endpoint is not active-only.')
    activity=status.get('activity') or {};queue=activity.get('queue') or {}
    if type(activity.get('in_flight')) is not int or type(queue.get('waiting')) is not int:
        raise RuntimeError('Endpoint activity is unknown.')
    if activity['in_flight'] or queue['waiting']:raise RuntimeError('Endpoint is busy; no concurrent request sent.')
    import psutil
    if psutil.virtual_memory().available<2*2**30:raise RuntimeError('Less than2GiB physical memory available; no request sent.')
    path=ROOT/('experimental-maya/active-config.json' if family=='glm' else 'desktop-runtime/model.json')
    config=json.loads(path.read_text(encoding='utf-8'))
    if config.get('model_name')!=model:raise RuntimeError('Active config identity mismatch.')
    return base,status,config

def wait_idle(model,context):
    # Allow only the prior completed response's accounting cleanup, never unknown busy work.
    last=None
    for _ in range(20):
        try:return verify_idle(model,context)
        except RuntimeError as exc:
            last=exc
            if str(exc)!='Endpoint is busy; no concurrent request sent.':raise
            time.sleep(.1)
    raise last

def collect(base,payload,clock=time.perf_counter):
    body=json.dumps(payload,ensure_ascii=False).encode();started=clock();wall_started=time.time()
    req=urllib.request.Request(base+'/responses',body,{'Content-Type':'application/json','Accept':'text/event-stream'})
    events=[];text=[];thinking=[];terminal=None;first_any=None;first_text=None;last_text=None;complete_at=None
    # No inference retries. The total token cap bounds generation; long prefill gets a generous read timeout.
    with OPENER.open(req,timeout=900) as response:
        content_type=response.headers.get('Content-Type','')
        if 'text/event-stream' not in content_type:raise RuntimeError('Expected Responses SSE, received '+content_type)
        data=[]
        for raw in response:
            line=raw.decode('utf-8').rstrip('\r\n')
            if line.startswith('data:'):data.append(line[5:].lstrip())
            elif line=='' and data:
                value=json.loads('\n'.join(data));data=[];now=clock();kind=value.get('type')
                events.append({'elapsed_s':now-started,'event':value})
                if kind in ('response.output_text.delta','response.reasoning_text.delta'):
                    delta=value.get('delta','')
                    if delta:
                        if first_any is None:first_any=now
                        if kind=='response.output_text.delta':
                            if first_text is None:first_text=now
                            text.append(delta);last_text=now
                        else:thinking.append(delta)
                if kind in ('response.completed','response.incomplete','response.failed'):
                    terminal=value.get('response') or {};complete_at=now;break
    end=clock()
    if terminal is None:raise RuntimeError('Stream ended without a terminal response event.')
    final=''.join(part.get('text','') for item in terminal.get('output',[]) if item.get('type')=='message'
                  for part in item.get('content',[]) if part.get('type')=='output_text')
    usage=terminal.get('usage') or {};total=usage.get('output_tokens');reason=(usage.get('output_tokens_details') or {}).get('reasoning_tokens')
    if type(total) is not int or type(reason) is not int or not 0<=reason<=total:
        raise RuntimeError('Provider total/reasoning token counts missing or inconsistent.')
    return {'started_unix':wall_started,'response_id':terminal.get('id'),'response_model':terminal.get('model'),
        'provider_status':terminal.get('status'),'incomplete_details':terminal.get('incomplete_details'),
        'usage':usage,'reasoning_tokens':reason,'total_generated_tokens':total,'non_reasoning_generated_tokens':total-reason,
        'token_count_note':'Provider counter. Non-reasoning generated tokens can include control markers, not only visible answer text.',
        'first_any_output_s':None if first_any is None else first_any-started,
        'first_visible_content_s':None if first_text is None else first_text-started,
        'first_visible_content_note':'First observed final-channel text; may include visible analysis and is not proof that a useful answer has arrived.',
        'last_visible_answer_s':None if last_text is None else last_text-started,
        'terminal_answer_s':complete_at-started,'transport_wall_s':end-started,
        'answer':final,'reasoning_text':''.join(thinking),'stream_text_matches_terminal':''.join(text)==final,
        'events':events,'request_payload_sha256':hashlib.sha256(body).hexdigest()}

def repetitions(text):
    words=text.split();grams=[tuple(words[i:i+8]) for i in range(max(0,len(words)-7))]
    duplicate=1-len(set(grams))/len(grams) if grams else 0
    lines=[line.strip() for line in text.splitlines() if len(line.strip())>=12]
    longest=0;last=None;count=0
    for line in lines:
        count=count+1 if line==last else 1;last=line;longest=max(longest,count)
    return {'words':len(words),'duplicate_8gram_fraction':duplicate,'longest_identical_line_run':longest,
            'suspected':len(words)>=80 and duplicate>.3 or longest>=3}

def score(task,row):
    text=row['answer'].strip();rule=task['scorer'];kind=rule['kind'];content=False;format_ok=False;details={}
    if kind=='python_function':
        details=code_grade(text,rule);content=details['content_passed'];format_ok=details['format_passed']
    elif kind=='json_exact':
        details=quality_v2.score(text,rule['expected']);content=details['answer_content_passed']
        if not content:
            # Recover a complete terminal JSON value for content-only grading; never a nested substring.
            candidates=[text]
            candidates.extend(text[i+1:].strip() for i,char in enumerate(text) if char=='\n')
            for candidate in candidates:
                if quality_v2.score(candidate,rule['expected'])['answer_content_passed']:
                    content=True;details['content_recovery']='terminal JSON value after visible prose';break
        # The original local16 prompt explicitly forbids fences and wrappers.
        try:
            value=json.loads(text);format_ok=type(value) is type(rule['expected'])
            if isinstance(value,dict):format_ok=format_ok and value.keys()==rule['expected'].keys()
        except ValueError:format_ok=False
    elif kind=='multiple_choice':
        expected=str(rule['expected'])
        strict=re.search(r'(?:^|\n)ANSWER:\s*([A-Z])\s*$',text)
        format_ok=bool(strict)
        relaxed=strict or re.search(r'(?:answer(?:\s+is)?|option|choice)\s*[:=]?\s*\(?([A-J])\)?[.\s]*$',text,re.I)
        actual=relaxed.group(1).upper() if relaxed else text if re.fullmatch(r'[A-J]',text) else None
        content=actual==expected;details={'normalized_answer':actual,'expected':expected,'content_rule':'explicit final option, or standalone letter'}
    elif kind=='numeric_final':
        strict=re.search(r'(?:^|\n)####\s*([-+\d.,]+)\s*$',text);format_ok=bool(strict)
        relaxed=strict or re.search(r'([-+]?(?:\d[\d,]*)(?:\.\d+)?)\s*[.!]?\s*$',text)
        actual=relaxed.group(1).replace(',','') if relaxed else None
        try:content=actual is not None and decimal.Decimal(actual)==decimal.Decimal(str(rule['expected']).replace(',',''))
        except decimal.DecimalException:content=False
        details={'normalized_answer':actual,'expected':rule['expected'],'content_rule':'final numeric expression; strict format separately requires####'}
    else:raise ValueError('Unknown frozen scoring rule '+kind)
    complete=row['provider_status']=='completed' and bool(text)
    repeated=repetitions(text)
    return {'answer_content_passed':None if content is None else bool(content),'format_passed':bool(format_ok),'completed_final_answer':complete,
        'strict_success':bool(content and format_ok and complete),'content_success':bool(content and complete),
        'format_only_failure':bool(content and not format_ok and complete),'truncated':row['provider_status']=='incomplete',
        'answer_repetition':repeated,'reasoning_repetition':repetitions(row.get('reasoning_text','')),'details':details,
        'correct_within_s':{str(s):bool(content and format_ok and complete and row['terminal_answer_s']<=s) for s in (30,60,120)}}

def summary(rows):
    groups={}
    for cohort in ('public128','practical24'):
        rs=[r for r in rows if r.get('cohort')==cohort];scored=[r for r in rs if 'grade' in r]
        times=sorted(r['terminal_answer_s'] for r in scored)
        groups[cohort]={'attempts':len(rs),'scored':len(scored),'errors':len(rs)-len(scored),
            'strict_correct':sum(r['grade']['strict_success'] for r in scored),'content_correct':sum(r['grade']['content_success'] for r in scored),
            'format_only_failures':sum(r['grade']['format_only_failure'] for r in scored),
            'answer_content_unknown':sum(r['grade']['answer_content_passed'] is None for r in scored),
            'execution_policy_unsupported':sum((r['grade'].get('details') or {}).get('failure')=='execution_policy_unsupported' for r in scored),
            'truncated':sum(r['grade']['truncated'] for r in scored),'repetitive_answers':sum(r['grade']['answer_repetition']['suspected'] for r in scored),
            'correct_within_s':{str(s):sum(r['grade']['correct_within_s'][str(s)] for r in scored) for s in (30,60,120)},
            'completed_answer_s':{'median':statistics.median(times),'min':min(times),'max':max(times),
                'p90':times[max(0,__import__('math').ceil(.90*len(times))-1)],'p95':times[max(0,__import__('math').ceil(.95*len(times))-1)]} if times else None,
            'categories':{category:{'n':len([r for r in rs if r['category']==category]),
                'strict_correct':sum(r['grade']['strict_success'] for r in scored if r['category']==category),
                'content_correct':sum(r['grade']['content_success'] for r in scored if r['category']==category)} for category in sorted({r['category'] for r in rs})}}
    return groups
