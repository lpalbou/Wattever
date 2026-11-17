"""HTTP-only paired GLM screen. Never starts, stops, loads or configures engines.

--prepare-only performs local tokenization and writes frozen prompts only.
Normal execution requires the selected model already loaded by Strata Control.
"""
from pathlib import Path
import argparse
import copy
from contextlib import contextmanager
import hashlib
import json
import math
import re
import statistics
import sys
import time
import urllib.request
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MODEL = 'glm-5.3-flash-maya-xs-iq1_s'
ENGINE = ROOT / 'experimental-maya/engine.log'
DEFAULT_TOKENIZER = Path('D:/StrataModels/models/GLM-5.3-Flash-Maya-XS/pack-f32-1156/tokenizer')
VENDOR = ROOT / 'experimental-maya/source-1.1.56'
SEED = 20261114
RECORDS = [{'key': chr(65+i), 'value': value, 'body_token_fraction': fraction}
           for i, (value, fraction) in enumerate(zip(
               ['amber-falcon-4317', 'silver-maple-8264', 'violet-otter-5928',
                'cobalt-willow-1746', 'ivory-badger-3682'], [.1, .3, .5, .7, .9]))]


def save(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, indent=2), encoding='utf-8')
    temp.replace(path)


def official_tokenizer(directory, vendor=VENDOR, reasoning='none'):
    # Only tokenizer/template modules: no server or lifecycle imports.
    sys.path.insert(0, str(vendor))
    sys.path.insert(0, str(vendor / 'tools'))
    from strata_tokenizer import Tokenizer
    from serve.frontend import ChatTemplate, effort_kwargs
    tok = Tokenizer.from_dir(directory)
    template_path = directory / 'chat_template.jinja'
    source = template_path.read_text(encoding='utf-8')
    efficient = vendor / 'serve/glm5_chat_template.jinja'
    if source.startswith('[gMASK]<sop>') and "reasoning_effort in ['low', 'high']" in source:
        template_path = efficient
    template = ChatTemplate(template_path)
    requested_kwargs = effort_kwargs(reasoning)

    def count(prompt):
        rendered = template.render([{'role': 'user', 'content': prompt}], **requested_kwargs)
        if requested_kwargs.get('enable_thinking') is False and rendered.endswith('<think>'):
            rendered += '</think>'
        return len(tok.encode(rendered, parse_special=True))

    identity = {'tokenizer_directory': str(directory), 'template': str(template_path),
                'files_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in directory.iterdir() if p.is_file()},
                'render_template_sha256': hashlib.sha256(template_path.read_bytes()).hexdigest(),
                'vendor_root':str(vendor),
                'frontend_sha256':hashlib.sha256((vendor/'serve/frontend.py').read_bytes()).hexdigest(),
                'tokenizer_implementation_sha256':hashlib.sha256((vendor/'tools/strata_tokenizer.py').read_bytes()).hexdigest()}
    return tok, count, identity


def record_renderer(identity, entries, reasoning='none', filename='prompt-renderers.json'):
    signature = {key:identity[key] for key in ('files_sha256','render_template_sha256','frontend_sha256','tokenizer_implementation_sha256')}
    signature['reasoning_effort'] = reasoning
    key = hashlib.sha256(json.dumps(signature,sort_keys=True).encode()).hexdigest()
    path = HERE/filename
    registry = json.loads(path.read_text()) if path.exists() else {}
    entry = registry.setdefault(key,{'identity':identity,'reasoning_effort':reasoning,'prompt_counts':{}})
    for prompt in entries:
        sha = prompt.get('sha256') or prompt['prompt_sha256']
        old = entry['prompt_counts'].get(sha)
        if old is not None and old != prompt['chat_tokens']:
            raise RuntimeError('Same renderer produced inconsistent frozen-prompt token counts')
        entry['prompt_counts'][sha] = prompt['chat_tokens']
    save(path,registry)
    return {'renderer_id':key,'render_template_sha256':identity['render_template_sha256'],
            'frontend_sha256':identity['frontend_sha256'],'reasoning_effort':reasoning,
            'tokenizer_files_digest':hashlib.sha256(json.dumps(identity['files_sha256'],sort_keys=True).encode()).hexdigest()}


def prepare_prompts(sizes, repeats, directory, context, cap, vendor=VENDOR):
    tok, count, identity = official_tokenizer(directory, vendor)
    index_path = HERE / 'prompts.json'
    index = json.loads(index_path.read_text()) if index_path.exists() else {'tokenizer': identity, 'entries': []}
    if index['tokenizer']['files_sha256'] != identity['files_sha256']:
        raise RuntimeError('Frozen tokenizer files changed; use a separate cohort for a different tokenizer')
    corpus = (ROOT.parent / 'gpu-bench/corpora/agentic-ai-oct2026.full.md').read_text(encoding='utf-8')
    filler = tok.encode(corpus)
    (HERE / 'prompts').mkdir(exist_ok=True)
    wanted = [(size, repeat, 'measured') for size in sizes for repeat in range(repeats)]
    wanted += [(size, 0, 'warmup') for size in sizes]
    for size, repeat, kind in wanted:
        if size < 400 or size + cap > context:
            raise ValueError('Prompt target plus generation budget exceeds context, or target below400')
        found = next((r for r in index['entries'] if (r['target'], r['repeat'], r['kind']) == (size, repeat, kind)), None)
        if found:
            prompt = (HERE / 'prompts' / found['file']).read_text(encoding='utf-8')
            if hashlib.sha256(prompt.encode()).hexdigest() != found['sha256']:
                raise RuntimeError('Frozen prompt changed')
            continue
        salt = hashlib.sha256(f'glm-opt-v1-{kind}-{size}-{repeat}'.encode()).hexdigest()[:24]
        records = [dict(r) for r in RECORDS]
        if size >= 40000:
            for record in records:
                record['value'] += '-' + hashlib.sha256((salt+record['key']).encode()).hexdigest()[:6]
        amount = max(16, size-240)
        for _ in range(10):
            ids = (filler * (amount // len(filler)+1))[:amount]
            pieces = []; offset = 0
            for record in records:
                end = int(len(ids)*record['body_token_fraction'])
                pieces.extend([tok.decode(ids[offset:end]),
                               f"\nBENCH_LEDGER {record['key']} = {record['value']}\n"])
                offset = end
            pieces.append(tok.decode(ids[offset:]))
            prompt = (salt+'\nRead the source; retain its BENCH_LEDGER records.\n'+''.join(pieces)+
                      '\nReturn a JSON object containing keys A,B,C,D,E and their exact BENCH_LEDGER values. '
                      'Return the complete JSON first, then summarize the source in about60 words. No reasoning.\n')
            actual = count(prompt)
            if abs(actual-size) <= 4:
                break
            amount = max(16, amount+size-actual)
        if abs(actual-size) > 8 or actual+cap > context:
            raise RuntimeError('Could not construct prompt within exact token/headroom bounds')
        name = f'{kind}-{size}-{repeat}.txt'
        (HERE / 'prompts' / name).write_text(prompt, encoding='utf-8')
        index['entries'].append(dict(kind=kind, target=size, repeat=repeat, file=name,
            sha256=hashlib.sha256(prompt.encode()).hexdigest(), chat_tokens=actual,
            plain_tokens=len(tok.encode(prompt)), records=records, frozen_renderer=identity))
        save(index_path, index)
    rendered = copy.deepcopy(index)
    rendered['frozen_tokenizer'] = index['tokenizer']
    rendered['tokenizer'] = identity
    for entry in rendered['entries']:
        prompt = (HERE/'prompts'/entry['file']).read_text(encoding='utf-8')
        if hashlib.sha256(prompt.encode()).hexdigest()!=entry['sha256']:
            raise RuntimeError('Frozen prompt changed')
        entry['frozen_chat_tokens'] = entry['chat_tokens']
        entry['chat_tokens'] = count(prompt)
        if (entry['target'],entry['repeat'],entry['kind']) in wanted and entry['chat_tokens']+cap>context:
            raise RuntimeError('Current renderer leaves insufficient generation headroom')
    rendered['renderer'] = record_renderer(identity,rendered['entries'])
    return rendered


def parse_log(raw):
    text = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else raw
    result = {'chunk_prefill': [], 'decode': None, 'mtp': None, 'kv': None,
              'device_stages': [], 'prompt_read': None}
    for n, ms in re.findall(r'glm prefill: (\d+) tokens in ([\d.]+) ms', text):
        result['chunk_prefill'].append({'tokens': int(n), 'ms': float(ms)})
    matches = re.findall(r'glm stat: decode ([\d.]+) ms/tok \(([\d.]+) tok/s\) \| vram hit ([\d.]+)% \| ram fetches ([\d.]+)/tok \| disk ([\d.]+) reads/tok in ([\d.]+) waits/tok, ([\d.]+) ms/tok', text)
    if matches:
        result['decode'] = dict(zip(['ms_per_token', 'tokens_per_second', 'vram_hit_percent',
            'ram_fetches_per_token', 'disk_reads_per_token', 'disk_waits_per_token', 'disk_ms_per_token'], map(float, matches[-1])))
    matches = re.findall(r'glm spec: (\d+) of (\d+) drafts accepted \(([\d.]+)%\)', text)
    if matches:
        a, n, percent = matches[-1]
        result['mtp'] = {'accepted': int(a), 'offered': int(n), 'acceptance_percent': float(percent)}
    matches = re.findall(r'glm kv: KV streaming: ([\d.]+)% of (\d+) block reads hit VRAM, ([\d.]+) MiB read from RAM', text)
    if matches:
        hit, blocks, mb = matches[-1]
        result['kv'] = dict(hit_percent=float(hit), block_reads=int(blocks), ram_read_mib=float(mb))
    matches = re.findall(r'glm vram: prompt (\d+) tokens read in ([\d.]+) s', text)
    if matches:
        n, seconds = matches[-1]
        result['prompt_read'] = {'tokens': int(n), 'seconds_rounded': float(seconds)}
    for line in text.splitlines():
        if re.search(r'glm prefill: (?:CUDA|HIP)\d+ chunk ', line):
            result['device_stages'].append(line)
    result['logical_io_interpretation'] = 'Engine disk/read counters are logical path operations; physical drive reads are separate OS counters.'
    return result


def retrieval(text, records=RECORDS):
    match = re.search(r'\{[^{}]+\}', text, re.S)
    try:
        found = json.loads(match.group()) if match else {}
    except ValueError:
        found = {}
    wanted = {r['key']: r['value'] for r in records}
    return {'passed': all(found.get(k) == v for k, v in wanted.items()),
            'matched': sum(found.get(k) == v for k, v in wanted.items()), 'expected': wanted, 'actual': found}


def summary(rows):
    result = {}
    for size in sorted({r['target'] for r in rows}):
        group = [r for r in rows if r['target'] == size]
        stats = {}
        for key in ('prompt_tokens','completion_tokens','ttft_s','prefill_tps','decode_s','decode_tps','wall_s',
                    'engine_prefill_s','engine_prefill_tps','engine_decode_s','engine_decode_tps','disk_d_read_gib'):
            values = [r[key] for r in group if r.get(key) is not None]
            stats[key] = {'median': statistics.median(values), 'min': min(values), 'max': max(values)} if values else None
        result[str(size)] = {'n': len(group), 'metrics': stats,
            'retrieval_passes': sum(r['retrieval']['passed'] for r in group),
            'retrieved_records': sum(r['retrieval']['matched'] for r in group)}
    return result


def verify_loaded_status(status, config, model, context):
    identity = status.get('model')
    if isinstance(identity, dict):
        identity = identity.get('id') or identity.get('name')
    if config.get('model_name') != model or identity != model or status.get('loaded') is not True:
        raise RuntimeError('Expected model not already loaded by Strata Control')
    pairs = dict(zip(config['args'][::2], config['args'][1::2]))
    if int(pairs.get('--max-context',0)) != context or (status.get('context') or {}).get('native') != context:
        raise RuntimeError('Loaded context differs from requested benchmark context')
    activity = status.get('activity') or {}
    if activity.get('in_flight',0) or activity.get('active_requests',0) or activity.get('waiting_requests',0) or (activity.get('queue') or {}).get('waiting',0):
        raise RuntimeError('Engine is busy; run the screen alone')


def prepared_profile_snapshot(model):
    """Copy the small prepared receipt only; never verify/read model weights."""
    path=ROOT/'experimental-maya/prepared.json'
    raw=path.read_bytes()
    profile=json.loads(raw.decode('utf-8'))
    if not isinstance(profile,dict) or profile.get('model_id')!=model:
        raise RuntimeError('Prepared profile identity differs from the benchmark model')
    return profile,{'source':str(path),'sha256':hashlib.sha256(raw).hexdigest(),
                    'scope':'Read-only prepared receipt snapshot; model/binary files are not rehashed by this client.'}


def tuning_values(pcie_frac=None, cpu_threads=None, decode_read_chunks=None):
    tune = {}
    if pcie_frac is not None:
        if isinstance(pcie_frac,bool) or not isinstance(pcie_frac,(int,float)) or not math.isfinite(pcie_frac) or not 0<=pcie_frac<=1:
            raise ValueError('pcie-frac must be finite and between 0 and 1')
        tune['pcie_frac'] = float(pcie_frac)
    if cpu_threads is not None:
        if type(cpu_threads) is not int or cpu_threads<=0:raise ValueError('cpu-threads must be a positive integer')
        tune['cpu_threads'] = cpu_threads
    if decode_read_chunks is not None:
        if type(decode_read_chunks) is not int or not 1<=decode_read_chunks<=16:raise ValueError('decode-read-chunks must be an integer from 1 through 16')
        tune['read_chunks'] = decode_read_chunks
    return tune


@contextmanager
def extra_request_payload(tune, base, model):
    """Inject experiment-local strata_tune into this process's exact chat POST.

    Wattever's transport uses endpoints.open_request/build_opener, not urlopen.
    Keep production Wattever unchanged and restore the local adapter on failure.
    """
    if not tune:
        yield
        return
    allowed=tuning_values(tune.get('pcie_frac'),tune.get('cpu_threads'),tune.get('read_chunks'))
    if allowed!=tune:raise ValueError('Unsupported request tuning key')
    address=urlsplit(base)
    if address.scheme!='http' or address.hostname not in ('127.0.0.1','localhost') or address.port!=8097 or address.path.rstrip('/')!='/v1' or address.username or address.password or address.query or address.fragment:
        raise ValueError('Request tuning is restricted to the owned loopback Maya API on port 8097')
    from wattever import endpoints
    original=endpoints.open_request
    expected=base.rstrip('/')+'/chat/completions'
    def inject(request, timeout):
        if isinstance(request,urllib.request.Request) and request.full_url==expected and request.get_method()=='POST':
            try:body=json.loads(request.data.decode('utf-8'))
            except (ValueError,AttributeError,UnicodeError):raise ValueError('Expected a JSON chat request') from None
            if not isinstance(body,dict) or body.get('model')!=model:
                raise ValueError('Request tuning model identity mismatch')
            if 'strata_tune' in body:raise ValueError('Chat request already includes strata_tune')
            body['strata_tune']=dict(tune)
            headers={k:v for k,v in request.header_items() if k.lower()!='content-length'}
            request=urllib.request.Request(request.full_url,data=json.dumps(body).encode('utf-8'),headers=headers,method='POST')
        return original(request,timeout)
    endpoints.open_request=inject
    try:yield
    finally:endpoints.open_request=original


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', required=True)
    parser.add_argument('--sizes', nargs='+', type=int, default=[512,5000,20000])
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--context', type=int, default=98304)
    parser.add_argument('--max-tokens', type=int, choices=[128,256,512,1024], default=128)
    parser.add_argument('--tokenizer', type=Path, default=DEFAULT_TOKENIZER)
    parser.add_argument('--vendor-root', type=Path, default=VENDOR)
    parser.add_argument('--model', default=MODEL)
    parser.add_argument('--pcie-frac',type=float)
    parser.add_argument('--cpu-threads',type=int)
    parser.add_argument('--decode-read-chunks',type=int)
    parser.add_argument('--base', default='http://127.0.0.1:8097/v1')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--continue-on-retrieval-failure', action='store_true',
                        help='Diagnostic matrix only: retain failed grades and continue other measured cases.')
    parser.add_argument('--skip-functional', action='store_true')
    parser.add_argument('--skip-warmup', action='store_true')
    args = parser.parse_args()
    try:tune=tuning_values(args.pcie_frac,args.cpu_threads,args.decode_read_chunks)
    except ValueError as exc:parser.error(str(exc))
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.label) or not 1 <= args.repeats <= 20:
        parser.error('Use a simple unique label and1..20 repeats')
    index = prepare_prompts(args.sizes, args.repeats, args.tokenizer, args.context, args.max_tokens,args.vendor_root)
    if args.prepare_only:
        print(json.dumps({'prepared': len(index['entries']), 'manifest': str(HERE/'prompts.json')}))
        return
    out = HERE/(args.label+'.json')
    if out.exists():
        raise FileExistsError('Preserve existing measurements; choose a new label')
    sys.path.insert(0, str(ROOT.parent/'gpu-bench'))
    sys.path.insert(0, str(ROOT/'untracked/memory_controls'))
    from wattever import lmstudio, power
    from memory_telemetry import Paging
    # GET only; an unavailable/wrong engine is a failure, never a load request.
    with urllib.request.urlopen(args.base+'/status', timeout=5) as response:
        status = json.load(response)
    config_path = ROOT/'experimental-maya/active-config.json'
    config = json.loads(config_path.read_text())
    verify_loaded_status(status,config,args.model,args.context)
    prepared,prepared_source=prepared_profile_snapshot(args.model)
    report = {'label':args.label,'model':args.model,'context':args.context,'started_unix':time.time(),
        'sampling':dict(temperature=0,top_p=1,seed=SEED,max_tokens=args.max_tokens,reasoning_effort='none'),
        'tokenizer':index['tokenizer'],'frozen_tokenizer':index['frozen_tokenizer'],
        'renderer':index['renderer'],'engine_configuration':config,'initial_status':status,
        'request_strata_tune':tune,
        'prepared_profile':prepared,'prepared_profile_snapshot':prepared_source,
        'request_tuning_scope':'Request decode read_chunks is separate from prefill environment read chunks; requested values are not proof of effective native settings.',
        'functional':[],'warmups':[],'results':[],
        'timing_notes':'Native phases use provider timings, client TTFT/wall are separate. Disk counters are systemwide; warm file reads can hit OS cache.'}
    save(out,report)
    gpu = power.PowerSampler(interval_ms=200)
    paging = Paging(HERE/(args.label+'-paging.jsonl'))
    gpu.start(); paging.start()

    def request(prompt, cap, name, expected_tokens=None):
        offset = ENGINE.stat().st_size if ENGINE.exists() else 0
        deltas = []
        with extra_request_payload(tune,args.base,args.model):
            row = lmstudio.measure_completion(args.model,prompt,cap,base=args.base,api_base=True,
                timeout=1800,temperature=0,top_p=1,seed=SEED,reasoning_effort='none',capture_output=True,
                on_progress=lambda n,t:deltas.append(time.perf_counter()),provider_name='Control-owned Maya')
        time.sleep(.15)
        raw = b''
        if ENGINE.exists():
            with ENGINE.open('rb') as stream:
                if ENGINE.stat().st_size >= offset: stream.seek(offset)
                raw = stream.read()
        (HERE/(args.label+'-'+name+'.log')).write_bytes(raw)
        row['engine_log'] = parse_log(raw)
        row['renderer'] = index['renderer']
        row['request_strata_tune']=dict(tune)
        timing = row.get('reported_timings') or {}
        row['native_timings_source'] = 'provider reported_timings' if timing else 'unavailable'
        for phase,key,count_key in [('prefill','prompt_ms','prompt_n'),('decode','predicted_ms','predicted_n')]:
            seconds = timing[key]/1000 if timing.get(key) is not None else None
            row['engine_'+phase+'_s'] = seconds
            rate_key = 'prompt_per_second' if phase=='prefill' else 'predicted_per_second'
            row['engine_'+phase+'_tps'] = timing.get(rate_key) if timing.get(rate_key) is not None else timing[count_key]/seconds if seconds and timing.get(count_key) is not None else None
        end = row['t_end']
        row['gpu'] = gpu.window(row['t_start'],end)
        row['paging'] = paging.window(row['t_start'],end)
        disk = row['paging'].get('disk_deltas',{}).get('PhysicalDrive1',{})
        row['disk_d_read_gib'] = disk['read_bytes']/2**30 if 'read_bytes' in disk else None
        gaps = [b-a for a,b in zip(deltas,deltas[1:])]
        row['stream_gaps'] = {'max_s':max(gaps,default=0),'over_1s':sum(v>1 for v in gaps),'over_5s':sum(v>5 for v in gaps)}
        report['last_request'] = row
        if row.get('response_model') != args.model: raise RuntimeError('Wrong response model')
        if row['prompt_tokens']+cap > args.context: raise RuntimeError('Actual generation headroom exhausted')
        if expected_tokens is not None and abs(row['prompt_tokens']-expected_tokens)>4:
            raise RuntimeError(f"Actual prompt count differs from frozen official chat tokenization: {row['prompt_tokens']} vs{expected_tokens}")
        if timing.get('cache_n',0)>64: raise RuntimeError('Unexpected substantial cached prompt reuse')
        return row

    try:
        if not args.skip_functional:
            cases = [('multiply','What is 17 times 23? Return only the integer.','391'),
                     ('sort','Sort 9,1,4,2 in ascending numerical order. Return only comma-separated integers.','1,2,4,9'),
                     ('code','What does this Python code print?\nprint(sum(i*i for i in range(5)))\nReturn only the integer.','30')]
            for name,prompt,wanted in cases:
                row = request(prompt,32,'functional-'+name)
                row['passed'] = re.sub(r'\s','',row['output_text']).strip('.') == wanted
                row['name'] = name; report['functional'].append(row); save(out,report)
                print(json.dumps({'functional':name,'passed':row['passed'],'answer':row['output_text']}),flush=True)
                if not row['passed']: raise RuntimeError('Functional correctness failure')
        for kind in ['warmup','measured']:
            if kind=='warmup' and args.skip_warmup: continue
            for size in args.sizes:
                for repeat in range(1 if kind=='warmup' else args.repeats):
                    entry = next(r for r in index['entries'] if (r['kind'],r['target'],r['repeat'])==(kind,size,repeat))
                    prompt = (HERE/'prompts'/entry['file']).read_text(encoding='utf-8')
                    row = request(prompt,args.max_tokens,f'{kind}-{size}-{repeat}',entry['chat_tokens'])
                    row.update(target=size,repeat=repeat,prompt_sha256=entry['sha256'],retrieval=retrieval(row['output_text'], entry.get('records',RECORDS)))
                    report['warmups' if kind=='warmup' else 'results'].append(row)
                    report['summary'] = summary(report['results']); save(out,report)
                    print(json.dumps(dict(kind=kind,target=size,repeat=repeat,prompt_tokens=row['prompt_tokens'],
                        output_tokens=row['completion_tokens'],ttft_s=row['ttft_s'],native_prefill_tps=row['engine_prefill_tps'],
                        native_decode_tps=row['engine_decode_tps'],retrieved=row['retrieval']['matched'],wall_s=row['wall_s'])),flush=True)
                    if not row['retrieval']['passed'] and not args.continue_on_retrieval_failure:
                        raise RuntimeError('Five-record retrieval failed')
    except Exception as exc:
        report['error'] = repr(exc)
        raise
    finally:
        gpu.stop(); paging.stop()
        report['finished_unix'] = time.time();save(out,report)


if __name__ == '__main__':
    main()
