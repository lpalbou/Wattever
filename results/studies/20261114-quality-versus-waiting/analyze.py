"""Paired analysis of saved attempts only. No network or engine operations."""
from pathlib import Path
import itertools,json,math,random,statistics

HERE=Path(__file__).resolve().parent

def distribution(values):
    values=sorted(v for v in values if v is not None)
    if not values:return None
    return {'n':len(values),'median':statistics.median(values),'min':values[0],'max':values[-1],
            **{f'p{p}':values[math.ceil(p/100*len(values))-1] for p in (90,95,99)}}

def wilson(k,n):
    if not n:return None
    z=1.959963984540054;p=k/n;den=1+z*z/n
    center=(p+z*z/(2*n))/den;half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [center-half,center+half]

def cohort(rows,expected):
    graded=[r for r in rows if 'grade' in r];n=len(rows)
    strict=sum(r['grade']['strict_success'] for r in graded)
    result={'expected':expected,'attempts':n,'graded':len(graded),'complete':n==expected,
            'errors':n-len(graded),'strict_correct':strict,
            'content_correct':sum(r['grade']['content_success'] for r in graded),
            'format_only_failures':sum(r['grade']['format_only_failure'] for r in graded),
            'content_unknown':sum(r['grade']['answer_content_passed'] is None for r in graded),
            'truncated':sum(r['grade']['truncated'] for r in graded),
            'repetitive_final':sum(r['grade']['answer_repetition']['suspected'] for r in graded),
            'repetitive_reasoning':sum(r['grade']['reasoning_repetition']['suspected'] for r in graded),
            'descriptive_wilson95':wilson(strict,n),
            'correct_completed_by_s':{str(t):sum(r['grade']['correct_within_s'][str(t)] for r in graded) for t in (30,60,120)},
            **{field:distribution(r.get(field) for r in graded) for field in
               ('first_any_output_s','first_visible_content_s','terminal_answer_s','reasoning_tokens',
                'total_generated_tokens','non_reasoning_generated_tokens')},
            'input_tokens':distribution(r.get('usage',{}).get('input_tokens') for r in graded),
            'zero_reasoning_requests':sum(r.get('reasoning_tokens')==0 for r in graded),
            'positive_reasoning_requests':sum(r.get('reasoning_tokens',0)>0 for r in graded)}
    result['categories']={c:{'n':sum(r['category']==c for r in rows),
        'strict_correct':sum(r['category']==c and r['grade']['strict_success'] for r in graded),
        'content_correct':sum(r['category']==c and r['grade']['content_success'] for r in graded)}
        for c in sorted({r['category'] for r in rows})}
    return result

def paired(a,b,field,seed=73129):
    ids=sorted(a.keys()&b.keys());diff=[];wins=losses=ties=0
    for key in ids:
        av=bool(a[key].get('grade',{}).get(field,False));bv=bool(b[key].get('grade',{}).get(field,False))
        d=int(av)-int(bv);diff.append(d);wins+=d==1;losses+=d==-1;ties+=d==0
    if not ids:return None
    rng=random.Random(seed)
    boot=sorted(sum(rng.choices(diff,k=len(diff)))/len(diff) for _ in range(10000))
    discordant=wins+losses
    p=min(1.,2*sum(math.comb(discordant,i) for i in range(min(wins,losses)+1))/2**discordant) if discordant else 1.
    return {'n':len(ids),'a_only_correct':wins,'b_only_correct':losses,'ties':ties,
            'a_minus_b_accuracy':sum(diff)/len(diff),'paired_bootstrap95':[boot[249],boot[9749]],
            'bootstrap_resamples':10000,'bootstrap_seed':seed,'mcnemar_exact_two_sided_p_unadjusted':p}

def main():
    reports=[(p.parent.name,json.loads(p.read_text(encoding='utf-8'))) for p in sorted(HERE.glob('*/run.json'))]
    result={'scope':'Frozen curated samples; descriptive uncertainty does not establish population representativeness. Paired comparisons require complete public128 and matching prompts/budget. No failed inference reruns or partial-cohort rankings.',
            'conditions':{},'paired_public128':[]}
    complete=[]
    for label,d in reports:
        c=d['condition'];rows=d['results']
        result['conditions'][label]={'condition':c,'status':d['status'],
            **{cohort_id:cohort([r for r in rows if r.get('cohort')==cohort_id],expected)
               for cohort_id,expected in [('public128',64 if c.get('cohort')=='larger-mmlu64' else 128),('practical24',24)]}}
        if c['total_output_cap']==2048 and d['status']=='completed' and result['conditions'][label]['public128']['attempts']==128:
            complete.append((label,d,{r['id']:r for r in rows if r.get('cohort')=='public128'}))
    for (la,da,a),(lb,db,b) in itertools.combinations(complete,2):
        if a.keys()!=b.keys():raise RuntimeError('Complete primary question identities differ.')
        if any(a[k]['prompt_sha256']!=b[k]['prompt_sha256'] for k in a):raise RuntimeError('Paired prompt changed.')
        result['paired_public128'].append({'a':la,'b':lb,'strict':paired(a,b,'strict_success'),
            'content':paired(a,b,'content_success'),
            'paired_answer_time_a_minus_b_s':distribution(a[k]['terminal_answer_s']-b[k]['terminal_answer_s'] for k in a
                if 'terminal_answer_s' in a[k] and 'terminal_answer_s' in b[k])})
    (HERE/'analysis.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'conditions':len(reports),'complete_primary':len(complete),'paired_comparisons':len(result['paired_public128'])}))

if __name__=='__main__':main()
