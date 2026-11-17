"""Publish measured comparison progress and portable evidence; never calls an endpoint."""
from pathlib import Path
import csv,datetime,hashlib,json,re,shutil,statistics
import common
HERE=Path(__file__).resolve().parent
BENCH=common.ROOT.parent/'gpu-bench'
ARCHIVE=BENCH/'results/studies/20261114-quality-versus-waiting'
DOC=BENCH/'docs/glm53-flash-comparison.md'
plan=json.loads((HERE/'plan.json').read_text(encoding='utf-8'))
reports=[json.loads(p.read_text(encoding='utf-8')) for p in HERE.glob('*/run.json')]
pause_path=HERE/'pause.json'
paused=json.loads(pause_path.read_text(encoding='utf-8')) if pause_path.exists() else None
lines=['## Four-model quality versus waiting time', '',
    'Updated '+datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')+'. **'+('Paused at the user\'s request; saved attempts and completed downloads are retained.' if paused else 'Comparison in progress; no overall winner is established.')+'** No overall winner is established.', '',
    'The primary question is which model supplies correct, complete answers quickly on this machine. The common frozen set has 32 ARC-Challenge, 32 GSM8K and 64 balanced MMLU-Pro questions, plus 16 logical/structured questions and 8 executable coding questions. Public128 and practical24 scores remain separate. Each condition has one attempt per question, a 2,048-token total generation cap, greedy sampling and 32,768 reserved context. Downloads must be complete or freshly acknowledged paused for timed requests.', '',
    '| Exact candidate | Requested mode | Completed public questions | Public strict correct | Practical progress / correct | Condition status |',
    '|---|---|---:|---:|---|---|']
tidy=[]
for model in plan['candidates']:
    for mode in plan['primary_modes']:
        matches=[d for d in reports if d['condition']['model']==model and d['condition']['mode_requested']==mode and d['condition']['total_output_cap']==2048]
        if not matches:
            lines.append('| '+model+' | '+mode+' | pending | pending | pending | not measured |');continue
        assert len(matches)==1,'Multiple primary conditions need explicit cohort selection.'
        d=matches[0];s=common.summary(d['results']);public=s['public128'];practical=s['practical24']
        lines.append('| '+' | '.join([model,mode,str(public['scored'])+'/128',str(public['strict_correct']) if public['scored'] else 'pending',
            f"{practical['scored']}/24 attempted; {practical['strict_correct']} correct",d['status']])+' |')
        for r in d['results']:
            g=r.get('grade') or {}
            tidy.append({'model':model,'mode':mode,'output_cap':2048,'question_id':r['id'],'cohort':r['cohort'],'category':r['category'],
                'strict_correct':g.get('strict_success'),'content_correct':g.get('content_success'),'format_passed':g.get('format_passed'),
                'first_visible_s':r.get('first_visible_content_s'),'completed_answer_s':r.get('terminal_answer_s'),
                'reasoning_tokens':r.get('reasoning_tokens'),'total_generated_tokens':r.get('total_generated_tokens'),
                'truncated':g.get('truncated'),'repetitive_answer':(g.get('answer_repetition') or {}).get('suspected'),
                **{'correct_by_'+str(t)+'s':(g.get('correct_within_s') or {}).get(str(t)) for t in (30,60,120)}})
lines += ['', 'An 8-question screen is functionality evidence only. Its attempts are retained when the rest of the same condition resumes; questions are not silently rerun. Previous XS public128 results collected with a background download are retained below as a separate accuracy cohort and are excluded from controlled answer-time comparisons.', '']
if paused:
    lines += ['### Saved results at pause', '',
        'The in-flight request completed and was saved before stopping. The benchmark client exited. Control closed its experimental panel and unloaded the model; the ownership registry is empty and port 8097 is closed. No other model was restored. The guarded experimental profile and exact configuration snapshots remain available for a future explicit resume. No inference or download will restart automatically.', '',
        '| XS / 2,048 total tokens / 32,768 context | Strict correct | Content correct | Correct completed within 30 / 60 / 120 s | Completed-answer median [range], s | p95, s | Truncated |',
        '|---|---:|---:|---|---|---:|---:|']
    for mode in ('none','low'):
        d=next((r for r in reports if r['condition']['model']=='glm-5.3-flash-maya-xs-iq1_s' and r['condition']['mode_requested']==mode),None)
        if not d:continue
        for name,s in common.summary(d['results']).items():
            if not s['scored']:continue
            t=s['completed_answer_s'];n=s['scored'];deadline=' / '.join(str(s['correct_within_s'][str(v)]) for v in (30,60,120))
            lines.append(f"| {mode}; {name}; {'partial' if n < (128 if name=='public128' else 24) else 'complete'} | {s['strict_correct']}/{n} | {s['content_correct']}/{n} | {deadline} of {n} | {t['median']:.2f} [{t['min']:.2f}-{t['max']:.2f}] | {t['p95']:.2f} | {s['truncated']} |")
    lines += ['', 'The XS Off public subset is 106/128 questions: ARC32 and GSM32 are complete, MMLU-Pro is 42/64. Strict/content scores are ARC 25/32 and 30/32; GSM 30/32 and 30/32; partial MMLU-Pro 26/42 and 31/42. Ten correct-content public answers failed the strict requested format. These partial results cannot be ranked against the earlier completed Low public128 cohort.', '',
        'S24 preparation is complete: three independently readback-verified GGUF shards, 94,701,676,224 bytes (88.20 GiB), and the official Maya 1.1.59 BF16-compatible pack with F32 expert-selection bias preserved. It has not been inference-benchmarked. All download/conversion workers have exited; a cooperative download pause marker is retained. No files were discarded.', '']
lines += [
    '### Observable timing and reasoning behavior', '',
    'Record first output of any kind, first visible final-channel text, and terminal completed-answer arrival separately. Visible analysis is not proof that a useful answer has arrived. The 30/60/120-second metric requires a correct completed answer by that deadline; truncated/incomplete responses never count. Slow tails, reasoning/output counts, repetition and category accuracy remain in each per-question record.', '',
    'Offline Chat/Responses rendering parity passed 12 cases across the pinned frontends and both Qwen templates. Live XS probes confirmed Off produces 0 provider reasoning tokens and Low can produce positive counts. Responses exposes the provider reasoning counter that Chat Completions omits. Greedy sampling is explicit; these Responses implementations do not expose a seed setting, and bitwise hardware determinism is not claimed.', '',
    '| Family/runtime | Thinking off | Enabled Low | Effective budget difference at 2,048 total tokens |',
    '|---|---|---|---|',
    '| Qwen / Strata server 0.1.53 | enable_thinking=false; template writes an empty thinking block | low prompt instruction | Default answer_room closes thinking near 1,536 tokens to leave answer room; verify active config |',
    '| GLM / Maya 1.1.59 reviewed native variant | enable_thinking=false; frontend closes the templateâ€™s thinking prefix | Low effort header | Current thinking_budget=0 allows reasoning to consume the total cap; this is not the same reasoning partition as Qwen |', '',
    'The common total output budget is identical; the backend closing policies are explicitly different. If truncation limits a conclusion, use a separately labelled complete MMLU-Pro64 cohort at 4,096 tokens rather than replacing individual failed answers.', '',
    '### Coding-grader review', '',
    'The functionality screen caught two over-restrictive grader rules. Basic type-name lookup and equivalent ordered Python interval pairs are now accepted. Exact scalar values, explicit list-container contracts, input immutability, memory/time limits and forbidden I/O/import checks remain enforced. Earlier grades and source versions are retained; saved answers were regraded without repeating inference. All candidates use the corrected policy.', '',
    '### Scope and current storage qualification', '',
    'GPU inventory/placement and RAM budgets remain Auto. The GLM 750 MiB reserve is a scoped measured tuning candidate, not a production default. Requested configuration, native applied information and actual per-GPU observations are retained separately. Long-context comparisons at approximately 20k/80k/120k will grade retrieval, combining distant evidence, contradictions and missing evidence separately from short reasoning. Expensive S24/190k grids are deferred until quality justifies them.', '',
    'The existing 500,000-reservation XS probe is saved: 489,998 actual prompt tokens, all five records correct, natural stop, 338 output tokens, 487.04 s wall for one measured request with downloading active. It demonstrates capacity, not a three-repeat throughput result or a practical latency recommendation.', '',
    'A read-only check found D: is exFAT with 1 MiB allocation units and Windows reports Full Repair Needed. The physical 990 PRO reports Healthy/OK. Detailed reliability/dirty-bit queries were denied. No repair, reformat, partition or OS change was performed. This observation is not proof of an SSD speed bottleneck. S24 checksums, independent readback verification and conversion are complete; inference remains untested.', '',
    '[Frozen protocol, question-level results and source/grade history](../results/studies/20261114-quality-versus-waiting/README.md). '
    '[Upgrade investigation](local-inference-upgrades.md) prioritizes unproven native-Linux feasibility and fully costed used RAM platforms; no extra SSD or expensive GPU is justified by assumption.', '']
text='\n'.join(lines)
(HERE/'latest-results.md').write_text(text,encoding='utf-8')
doc=DOC.read_text(encoding='utf-8')
start='<!-- quality-wait:start -->';end='<!-- quality-wait:end -->'
block=start+'\n'+text+'\n'+end+'\n\n'
if start in doc:doc=re.sub(re.escape(start)+r'.*?'+re.escape(end)+r'\s*',lambda _:block,doc,flags=re.S)
else:doc=doc.replace('## Historical AirDisk captures',block+'## Historical AirDisk captures',1)
DOC.write_text(doc,encoding='utf-8')
ARCHIVE.mkdir(parents=True,exist_ok=True)
for path in HERE.iterdir():
    if path.is_file() and path.suffix in ('.py','.json','.md'):shutil.copy2(path,ARCHIVE/path.name)
    if path.is_dir() and path.name not in ('__pycache__',) and ((path/'run.json').exists() or path.name in ('grading-sources','pause-snapshot')):
        shutil.copytree(path,ARCHIVE/path.name,dirs_exist_ok=True)
if tidy:
    with (ARCHIVE/'question-results.csv').open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(tidy[0]));writer.writeheader();writer.writerows(tidy)
readme='''# Four-model quality versus waiting time: saved interim evidence

Read [the current comparison](../../../docs/glm53-flash-comparison.md#four-model-quality-versus-waiting-time).
`plan.json` freezes public128 and practical24 question text, gold answers and coding cases;
`protocol.json` records conditions and scope. One inference attempt is retained per question/condition.
`question-results.csv` is a tidy table keyed by model, mode and question ID for paired analysis.
Each condition directory retains the request-level Responses events, provider usage, observed timestamps,
GPU/RAM telemetry and grades. Protocol probes are unscored and may have background downloading.
Earlier background-download XS accuracy results remain in the separate GLM optimization study.

`grading-sources/` and grading-review JSON files preserve the corrected execution policy and earlier grades;
no inference was repeated for those grader corrections. CPU execution uses constrained pure-function
AST/builtins and a bounded Windows Job; it is not an unrestricted arbitrary-code service.
Source copies preserve the original checkout paths and are review artifacts, not standalone engine launchers.
All real starts/unloads remain owned by Strata Control. No client in this study starts or switches an engine.

Native Linux performance and replacement-platform speed are unmeasured. No OS installation,
repartition, filesystem repair or hardware purchase has occurred.
'''
(ARCHIVE/'README.md').write_text(readme,encoding='utf-8')
manifest={'updated_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'files':{
    p.relative_to(ARCHIVE).as_posix():{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    for p in sorted(ARCHIVE.rglob('*')) if p.is_file() and p.name!='manifest.json'}}
(ARCHIVE/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({'conditions':len(reports),'question_attempts':len(tidy),'archived_files':len(manifest['files'])}))
