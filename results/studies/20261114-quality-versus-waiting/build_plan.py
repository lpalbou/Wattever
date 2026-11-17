"""Freeze the common tasks offline. Never contacts an endpoint or starts an engine."""
from pathlib import Path
import hashlib, json, sys
HERE=Path(__file__).resolve().parent
OLD=HERE.parent/'glm_optimization'
sys.path.insert(0,str(OLD))
from quality_v2 import tasks as exact_tasks

public=json.loads((OLD/'public-quality-plan.json').read_text(encoding='utf-8'))
tasks=[]
for job in public['jobs']:
    for item in job['tasks']:
        tasks.append(dict(item,cohort='public128',suite_id=job['suite_id'],suite_digest=job['suite_digest']))
for item in exact_tasks():
    tasks.append(dict(id='local16-'+item['name'],category=item['category'],prompt=item['prompt'],
                      cohort='practical24',scorer={'kind':'json_exact','expected':item['expected']}))

def code(name,description,cases,reference):
    prompt=('Implement this Python function: '+description+'\n'
        'Return exactly one fenced Python code block containing the function and any pure helper functions. '
        'Use Python builtins only: no imports, external libraries, file/network access, printing, or type annotations. '
        'Do not mutate the input arguments. Return values directly; do not write a command-line program.')
    tasks.append(dict(id='coding8-'+name,category='executable-coding',cohort='practical24',prompt=prompt,
        scorer={'kind':'python_function','function':name,'cases':cases,'reference':reference,
                'cpu_timeout_s':3,'process_memory_bytes':256*2**20,'execution_policy':'restricted AST and builtins; isolated Python child in a Windows Job with memory and process limits'}))

code('stable_unique','stable_unique(values): return a list retaining only the first occurrence of each integer or string, preserving order.',[
    {'args':[[]],'expected':[]},{'args':[[3,1,3,2,1]],'expected':[3,1,2]},
    {'args':[['a','b','a','A','b']],'expected':['a','b','A']},{'args':[[-1,0,-1,2]],'expected':[-1,0,2]}],
    'def stable_unique(values):\n    return list(dict.fromkeys(values))')
code('merge_intervals','merge_intervals(intervals): given integer [start,end] pairs with start <= end, return sorted merged pairs. Merge overlapping OR touching intervals. Empty input returns [].',[
    {'args':[[]],'expected':[]},{'args':[[[5,7],[1,3],[3,4],[6,9]]],'expected':[[1,4],[5,9]]},
    {'args':[[[2,2],[1,1],[1,2]]],'expected':[[1,2]]},{'args':[[[-4,-2],[0,3],[-2,1]]],'expected':[[-4,3]]}],
    'def merge_intervals(intervals):\n    out=[]\n    for a,b in sorted(intervals):\n        if out and a<=out[-1][1]: out[-1][1]=max(out[-1][1],b)\n        else: out.append([a,b])\n    return out')
code('moving_totals','moving_totals(values, window): return sums of every consecutive FULL window of positive integer length, in order. If window exceeds the list length, return [].',[
    {'args':[[1,2,3,4],2],'expected':[3,5,7]},{'args':[[],3],'expected':[]},
    {'args':[[-2,5,-1,4],3],'expected':[2,8]},{'args':[[7,-3],1],'expected':[7,-3]},{'args':[[1,2],3],'expected':[]}],
    'def moving_totals(values,window):\n    return [sum(values[i:i+window]) for i in range(len(values)-window+1)]')
code('apply_transactions','apply_transactions(start, operations): operations are [id, kind, amount] triples, with kind credit or debit and nonnegative integer amounts. Credits always apply; a debit applies only if its amount does not exceed the current balance. Return {"balance":final_integer,"declined":[ids of rejected debits in order]}.',[
    {'args':[10,[['a','debit',12],['b','credit',5],['c','debit',15]]],'expected':{'balance':0,'declined':['a']}},
    {'args':[0,[['x','debit',0],['y','debit',1],['z','credit',2]]],'expected':{'balance':2,'declined':['y']}},
    {'args':[17,[]],'expected':{'balance':17,'declined':[]}},
    {'args':[20,[['r','debit',9],['s','debit',12],['t','debit',11]]],'expected':{'balance':0,'declined':['s']}}],
    'def apply_transactions(start,operations):\n    balance=start; declined=[]\n    for id,kind,amount in operations:\n        if kind=="credit": balance+=amount\n        elif amount<=balance: balance-=amount\n        else: declined.append(id)\n    return {"balance":balance,"declined":declined}')
code('topo_order','topo_order(nodes, edges): nodes are unique strings; each [a,b] directed edge requires a before b, with both endpoints in nodes. Return the lexicographically smallest topological node order, or None if a cycle exists. Ignore duplicate edges.',[
    {'args':[['c','a','b'],[['a','c'],['b','c']]],'expected':['a','b','c']},
    {'args':[['a','b'],[['a','b'],['b','a']]],'expected':None},
    {'args':[['a','b','c'],[['a','b'],['a','b']]],'expected':['a','b','c']},
    {'args':[[],[]],'expected':[]},{'args':[['x'],[['x','x']]],'expected':None}],
    'def topo_order(nodes,edges):\n    remaining=set(nodes); edges=set(tuple(e) for e in edges); out=[]\n    while remaining:\n        ready=sorted(n for n in remaining if not any(b==n and a in remaining for a,b in edges))\n        if not ready: return None\n        n=ready[0];out.append(n);remaining.remove(n)\n    return out')
code('rle_encode','rle_encode(text): run-length encode consecutive Unicode characters as a list of [character, positive_count] pairs. Empty text returns [].',[
    {'args':[''],'expected':[]},{'args':['aaabbcca'],'expected':[['a',3],['b',2],['c',2],['a',1]]},
    {'args':['éé🙂🙂é'],'expected':[['é',2],['🙂',2],['é',1]]},{'args':['ab'],'expected':[['a',1],['b',1]]}],
    'def rle_encode(text):\n    out=[]\n    for c in text:\n        if out and out[-1][0]==c: out[-1][1]+=1\n        else: out.append([c,1])\n    return out')
code('deep_get','deep_get(value, path, default): traverse nested dictionaries using string keys and lists using nonnegative integer indices. Return default if a step is missing, out of range or incompatible with its container. An empty path returns value.',[
    {'args':[{'a':[{'b':7}]},['a',0,'b'],'missing'],'expected':7},
    {'args':[{'a':[1]},['a',2],None],'expected':None},
    {'args':[[5],[-1],'x'],'expected':'x'},{'args':[{'a':2},['a','b'],99],'expected':99},
    {'args':[{'a':2},[],None],'expected':{'a':2}},{'args':[[3],['0'],'x'],'expected':'x'}],
    'def deep_get(value,path,default):\n    for p in path:\n        if isinstance(value,dict) and isinstance(p,str) and p in value: value=value[p]\n        elif isinstance(value,list) and isinstance(p,int) and 0<=p<len(value): value=value[p]\n        else: return default\n    return value')
code('allocate_cents','allocate_cents(total, weights): allocate nonnegative integer total proportionally to nonnegative integer weights, whose sum is positive. Use integer largest remainders: floor every exact share, then distribute leftover cents to largest fractional remainders; ties go to lower index. Return the integer allocation list; avoid floating-point rounding.',[
    {'args':[10,[1,1,1]],'expected':[4,3,3]},{'args':[7,[0,1,1]],'expected':[0,4,3]},
    {'args':[0,[1,2]],'expected':[0,0]},{'args':[1000000003,[1,1,1]],'expected':[333333335,333333334,333333334]},
    {'args':[11,[1,2,3]],'expected':[2,4,5]}],
    'def allocate_cents(total,weights):\n    s=sum(weights); out=[total*w//s for w in weights]\n    order=sorted(range(len(weights)),key=lambda i:(-(total*weights[i]%s),i))\n    for i in order[:total-sum(out)]: out[i]+=1\n    return out')

assert len(tasks)==152 and len({t['id'] for t in tasks})==152
for item in tasks:item['prompt_sha256']=hashlib.sha256(item['prompt'].encode()).hexdigest()
plan={'schema':'quality-wait-common-v1','candidates':['qwen3.8-flash-next-iq3_s','qwen3.8-flash-next-unsloth-ud-iq4_xs',
    'glm-5.3-flash-maya-xs-iq1_s','glm-5.3-flash-maya-s24-iq2_xxs_s'],
    'primary_modes':['none','low'],'mode_note':'Off and family-specific Low enabled; template/backend mappings must be verified, not assumed equivalent.',
    'primary_total_generation_tokens':2048,'sampling':{'temperature':0,'top_p':1},
    'seed_policy':'Responses support is audited; no unsupported seed field is silently sent. Greedy sampling is requested, not bitwise hardware determinism.',
    'timing_policy':'Downloads complete or freshly acknowledged paused for every measured request; no competing GPU workload.',
    'time_deadlines_s':[30,60,120],'deadline_scoring':'Only correct, completed, non-truncated final answers received within the deadline count.',
    'larger_budget_policy':'If truncation limits conclusions, run a separate complete frozen MMLU-Pro64 cohort at4096 for relevant stable enabled candidates. Never merge selected reruns into2048 scores.',
    'tasks':tasks}
path=HERE/'plan.json'
raw=(json.dumps(plan,indent=2,ensure_ascii=False)+'\n').encode()
if path.exists() and path.read_bytes()!=raw:raise SystemExit('Frozen plan already exists and differs; preserve it and version changes explicitly.')
path.write_bytes(raw)
print(json.dumps({'tasks':len(tasks),'public128':128,'practical24':24,'sha256':hashlib.sha256(raw).hexdigest()}))
