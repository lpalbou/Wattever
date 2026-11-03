"""Offline refinement of derived closeout evidence; measured files untouched.

Uses the already sanitized full evidence cache, refreshes only final allocator
precision / terminal split-buffer restrictions, and redraws derived figures.
Original compressed evidence is retained in the ignored investigation archive.
"""
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import shutil

import report_communication_investigation as report


def csv_rows(path):
    csv.field_size_limit(32*1024**2)
    with path.open(encoding='utf-8',newline='') as handle:return list(csv.DictReader(handle))


def main():
    prefix=report.ROOT/'results/aggregates'/report.PREFIX
    raw_path=prefix.with_name(prefix.name+'-raw.json.gz')
    with gzip.open(raw_path,'rt',encoding='utf-8') as handle:raw=json.load(handle)
    provenance={p['source']:p['runtime'] for p in raw['screening_provenance']}
    sources={}
    for name in provenance:
        root=report.INV/name
        if not (root/'manifest-private.json').exists():continue
        manifest=report.read(root/'manifest-private.json')
        for case in (manifest.get('plan') or {}).get('cases',[]):sources[case['case_id']]=(root,provenance[name])
    replacements={};precision=[]
    for result in raw['screening_conditions']:
        case=result['condition'];case_id=case['case_id']
        quantized=(case.get('kv_type_k'),case.get('kv_type_v'))!=('f16','f16')
        row_failure=case.get('strategy')=='row_equal' and result.get('status')=='failed'
        if not quantized and not row_failure:continue
        root,runtime=sources[case_id]
        before=report.compact({k:v for k,v in result.items() if k not in ('actual_placement_evidence','safe_failure_evidence')})
        if quantized:
            result['actual_placement_evidence']=report.public_projection(report.actual_placement_evidence(result,root))
            evidence=report.kv_precision_evidence(result)
            if result.get('status')=='ok' and evidence['status']!='observed_cache_types_match_request':
                raise ValueError('Successful quantized-cache closeout still lacks final allocator evidence')
            precision.append({'case_id':case_id,**evidence})
        if row_failure:result['safe_failure_evidence']=report.public_projection(report.safe_failure_evidence(result,runtime,root))
        after=report.compact({k:v for k,v in result.items() if k not in ('actual_placement_evidence','safe_failure_evidence')})
        if before!=after:raise ValueError('Measurement payload changed during derived refinement')
        replacements[case_id]=report.condition_row(result,runtime)
    if not replacements:raise ValueError('No targeted closeout evidence found')
    conditions_path=prefix.with_name(prefix.name+'-conditions.csv')
    conditions=csv_rows(conditions_path)
    conditions=[replacements.get(row['case_id'],row) for row in conditions]
    report.write_csv(conditions_path,conditions)
    for suffix in ('precision-verification','failure-restrictions'):
        path=prefix.with_name(prefix.name+'-'+suffix+'.csv');rows=csv_rows(path)
        rows=[{key:replacements[row['case_id']].get(key) for key in row} if row['case_id'] in replacements else row for row in rows]
        report.write_csv(path,rows)
    raw['cache_precision_verification']=precision
    archive=report.INV/'derived-evidence-refinements'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    archive.mkdir(parents=True,exist_ok=False)
    shutil.copy2(raw_path,archive/raw_path.name)
    temporary=raw_path.with_name(raw_path.name+'.part')
    with temporary.open('wb') as binary:
        with gzip.GzipFile(filename='',mode='wb',fileobj=binary,mtime=0,compresslevel=6) as compressed:
            with io.TextIOWrapper(compressed,encoding='utf-8',newline='') as handle:
                # Existing cache is already public; newly inserted evidence is
                # separately projected above. Avoid rescanning hundreds of MB.
                json.dump(raw,handle,allow_nan=False,separators=(',',':'))
    temporary.replace(raw_path)
    doc=report.ROOT/'docs/dual-gpu-communication-and-placement.md'
    text=doc.read_text(encoding='utf-8')
    eligible=sum(row.get('metric_eligible') in (True,'True') for row in conditions if row.get('capture_kind')=='screening')
    text=re.sub(r'Eligible timing conditions: \d+',f'Eligible timing conditions: {eligible}',text,count=1)
    text=text.replace('and memory clocks are unavailable;','and memory clocks were not recorded in those original captures;')
    text='\n'.join(line.replace('specific cause unavailable','backend_split_buffers_unsupported')
        if '|row_equal|' in line else line for line in text.splitlines())+'\n'
    text+='\n## Targeted closeout evidence\n\n'
    text+='The successful Qwen27B80k closeout explicitly logs final real K(q8_0)/V(q8_0), with CPU KV352.75MiB and CUDA0 KV2469.25MiB. Its observed5.283 decode tokens/s and639.56 client prefill tokens/s remain a separate Q8 cache condition with actual80496 input and223 output tokens; they are not pooled into F16 rankings or exact-output comparisons. This is one successfully observed context, not a maximum-capacity estimate.\n\n'
    text+='The fixed256 Gemma single5070 anchor completed with hash-bound effective ignore_eos verification. The classic-row comparison failed during model loading because the pinned backend reported that CUDA0 does not support split buffers; no row-mode inference measurement exists for that condition. This is distinct from the earlier experimental tensor graph split-axis assertion.\n'
    doc.write_text(text,encoding='utf-8')
    figures=report.figures_from_saved_csv(prefix)
    print(json.dumps({'derived_cases_refined':len(replacements),'precision_cases_verified':len(precision),
        'eligible_timing_conditions':eligible,'figures_redrawn':len(figures),
        'measured_files_modified':False,'compressed_bytes':raw_path.stat().st_size,
        'generator_sha256':hashlib.sha256(Path(report.__file__).read_bytes()).hexdigest()},indent=2))


if __name__=='__main__':main()
