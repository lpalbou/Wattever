"""Offline enum-corrected Nsight analysis; originals and measurement code untouched.

Default is a plan. --run reads completed private SQLite/original summaries and
writes NEW case/analysis-corrected sidecars. No GPU, profiler, process or network
operations occur. Do not execute while timed workloads are running.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import types

ROOT=Path(__file__).resolve().parents[1]
INV=ROOT/'untracked/communication-investigation'
SNAPSHOT=INV/'frozen-nsight_capture-pre-enum-fix.py'
MEMORY_TABLES=('ENUM_CUDA_MEM_KIND',)
COPY_TABLES=('ENUM_CUDA_MEMCPY_OPER','ENUM_CUDA_MEMCPY_KIND','ENUM_CUDA_MEMCPY_OPER_KIND')
MEMORY_ALIASES={'UNKNOWN':'unknown','PAGEABLE':'pageable','PINNED':'pinned','PAGELOCKED':'pinned',
    'DEVICE':'device','ARRAY':'array','MANAGED':'managed','DEVICESTATIC':'device_static','MANAGEDSTATIC':'managed_static'}
COPY_ALIASES={'UNKNOWN':'unknown','HTOD':'HtoD','DTOH':'DtoH','HTOA':'HtoA','ATOH':'AtoH',
    'ATOA':'AtoA','ATOD':'AtoD','DTOA':'DtoA','DTOD':'DtoD','HTOH':'HtoH','PTOP':'PtoP',
    'HOSTTODEVICE':'HtoD','DEVICETOHOST':'DtoH','DEVICETODEVICE':'DtoD','PEERTOPEER':'PtoP'}


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path,value):
    path.write_text(json.dumps(value,indent=2,allow_nan=False),encoding='utf-8')


def sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda:handle.read(8*1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def enum_map(connection,candidates,aliases):
    tables={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    table=next((name for name in candidates if name in tables),None)
    if table is None:
        return {},{'status':'unavailable','table':None,'reason':'No captured SQLite enum table; no CUPTI ordering assumed','rows':[]}
    columns={row[1].lower():row[1] for row in connection.execute('PRAGMA table_info("'+table+'")')}
    labels=[columns[k] for k in ('name','label') if k in columns]
    if 'id' not in columns or not labels:
        return {},{'status':'unavailable','table':table,'reason':'Expected id/name-or-label columns unavailable','rows':[]}
    selected=[columns['id'],*labels]
    query='SELECT '+','.join('"'+c+'"' for c in selected)+' FROM "'+table+'" ORDER BY "'+columns['id']+'"'
    mapping,rows={},[]
    for record in connection.execute(query):
        if type(record[0]) is not int or not 0<=record[0]<=1024:continue
        kind=None;symbol='unrecognized_enum_symbol'
        for raw in record[1:]:
            normalized=re.sub('[^A-Z0-9]','',str(raw).upper())
            for prefix in ('CUPTIACTIVITYMEMORYKIND','CUPTIACTIVITYMEMCPYKIND','CUDAMEMCPYKIND',
                           'CUDAMEMCPYOPER','CUDAMEMORYKIND','MEMORYKIND','MEMKIND','MEMCPY'):
                if normalized.startswith(prefix):normalized=normalized[len(prefix):];break
            if normalized in aliases:
                kind=aliases[normalized];symbol=normalized;break
        mapping[record[0]]=kind or 'unknown_from_sqlite_enum'
        rows.append({'id':record[0],'observed_symbol':symbol,'analysis_kind':mapping[record[0]]})
    return mapping,{'status':'available','table':table,'columns':selected,'rows':rows,
        'interpretation':'Captured SQLite enum is authoritative; unrecognized values remain unknown'}


def captured_enums(database):
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)) as connection:
        memory,memory_evidence=enum_map(connection,MEMORY_TABLES,MEMORY_ALIASES)
        copies,copy_evidence=enum_map(connection,COPY_TABLES,COPY_ALIASES)
    return memory,copies,{'memory':memory_evidence,'copy':copy_evidence}


def analysis_function(snapshot,memory,copies):
    """Inject enums into a private copy of PURE analysis globals, no mutation.

    The frozen source snapshot keeps the original filtering/counting/timing
    semantics reproducible even after the reusable main helper is repaired.
    NsightCapture lifecycle/measurement methods are never called or monkeypatched.
    """
    spec=importlib.util.spec_from_file_location('_frozen_nsight_offline_analysis',snapshot)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original=module.analyze_sqlite
    namespace=dict(original.__globals__,MEMORY_KINDS=memory,COPY_KINDS=copies)
    corrected=types.FunctionType(original.__code__,namespace,name='enum_corrected_analyze_sqlite',
                                 argdefs=original.__defaults__)
    corrected.__kwdefaults__=dict(original.__kwdefaults__ or {})
    return corrected


def totals(summary):
    counts=summary.get('event_counts') or {}
    durations=defaultdict(int)
    for group in summary.get('activity_groups',[]):durations[group['kind']]+=group.get('total_duration_ns',0)
    devices={str(row['trace_device']):{k:v for k,v in row.items() if k!='identity'}
             for row in summary.get('device_activity',[])}
    return {'event_counts':counts,'logical_requested_copy_bytes':sum(g.get('bytes',0)
        for g in summary.get('activity_groups',[]) if g.get('kind')=='memcpy'),
        'activity_duration_sum_ns_by_kind':dict(durations),'trace_span_ns':summary.get('trace_span_ns'),
        'per_device_activity_unions':devices}


def copy_semantics_checks(summary):
    checks=[]
    # Conservative per-direction raw payload ceiling for ANY link on this X99
    # PCIe3 platform, before packet/protocol overhead. It is not a measured rate.
    ceiling=8e9*(128/130)*16/8/1e9
    for group in summary.get('activity_groups',[]):
        if group.get('kind')!='memcpy' or group.get('name_or_direction') not in ('HtoD','DtoH'):continue
        duration=group.get('total_duration_ns',0);requested=group.get('bytes',0)
        diagnostic_ratio=requested/duration if duration>0 else None  # bytes/ns numerically equals decimal GB/s
        checks.append({'direction':group['name_or_direction'],'trace_device':group.get('trace_device'),
            'logical_requested_bytes':requested,'sum_recorded_event_duration_ns':duration,
            'requested_bytes_over_event_time_diagnostic_GB_s':diagnostic_ratio,
            'conservative_single_direction_PCIe3_x16_raw_ceiling_GB_s':ceiling,
            'above_reference_ceiling':diagnostic_ratio>ceiling if diagnostic_ratio is not None else None,
            'is_measured_PCIe_bandwidth':False,
            'interpretation':'Sanity check only. Logical requested bytes, graph/replay semantics, event timing and overlap may differ from physical bus traffic. Use independent hardware counters and synchronized wall intervals before a transport-rate claim.'})
    return checks


def root_manifest(case):
    for parent in case.parents:
        candidate=parent/'manifest-private.json'
        if candidate.exists():return read(candidate)
        if parent==ROOT:break
    raise FileNotFoundError('Capture manifest unavailable; cannot verify frozen analysis source')


def reanalyze_case(case,snapshot,analysis_name='analysis-corrected'):
    case=Path(case).resolve();snapshot=Path(snapshot).resolve()
    if not re.fullmatch('[a-f0-9]{16}',case.name):raise ValueError('Expected an owned diagnostic case directory')
    result=read(case/'result-private.json')
    if result.get('condition',{}).get('case_id')!=case.name:raise ValueError('Case identity mismatch')
    if result.get('capture_kind')!='profiling':raise ValueError('Not a separate profiled condition')
    if result.get('status') in (None,'starting','running','stopping'):raise ValueError('Case is not terminal')
    cleanup=result.get('cleanup') or []
    if not cleanup or any(row.get('exit_confirmed') is not True for row in cleanup):
        raise ValueError('Owned model/launcher exit not confirmed; offline analysis is not a lifecycle remedy')
    manifest=root_manifest(case)
    expected=(manifest.get('source_sha256') or {}).get('tools/nsight_capture.py')
    snapshot_sha=sha256(snapshot)
    if not expected or expected!=snapshot_sha:raise ValueError('Frozen helper snapshot differs from the actual capture source hash')
    sqlites=sorted(case.glob('server*/nsight/*/private/*.sqlite'))
    if not sqlites:raise FileNotFoundError('No completed private SQLite export found')
    destination=case/analysis_name
    destination.mkdir(exist_ok=False)
    index={'schema':'wattever-nsight-enum-correction-v1','case_id':case.name,
        'generated_at':datetime.now(timezone.utc).isoformat(),'status':'processing',
        'normal_aggregate_eligible':False,'original_measurement_artifacts_modified':False,
        'capture_helper_source_sha256':expected,'corrector_sha256':sha256(__file__),'traces':[]}
    write(destination/'index.json',index)
    try:
        for ordinal,database in enumerate(sqlites):
            original_path=database.parent.parent/'shareable'/database.stem/'summary.json'
            original=read(original_path)
            before=(database.stat().st_size,database.stat().st_mtime_ns)
            sqlite_sha=sha256(database)
            memory,copies,enums=captured_enums(database)
            analyze=analysis_function(snapshot,memory,copies)
            trace_dir=destination/f'trace-{ordinal:02d}'
            corrected=analyze(database,trace_dir,timeline_limit=(original.get('timeline') or {}).get('written',500000))
            original_totals,corrected_totals=totals(original),totals(corrected)
            if original_totals!=corrected_totals:raise ValueError('Counts/bytes/timing/activity changed; correction must not alter observations')
            if before!=(database.stat().st_size,database.stat().st_mtime_ns):raise ValueError('Source SQLite changed during analysis')
            provenance={'case_id':case.name,'trace_ordinal':ordinal,'source_sqlite_sha256':sqlite_sha,
                'capture_helper_source_sha256':expected,'analysis_helper_snapshot_sha256':snapshot_sha,
                'corrector_sha256':index['corrector_sha256'],'enum_provenance':enums,
                'original_summary_sha256':sha256(original_path),'invariants_verified':True,
                'observed_totals':corrected_totals,'correction_scope':'Enum-derived direction and memory-kind labels only; original trace/result/summary retained'}
            corrected['analysis_provenance']=provenance
            corrected['copy_bytes_interpretation']='Logical requested copy bytes; not measured hardware PCIe traffic'
            corrected['phase_alignment']={'status':'unavailable','trace_scope':'Whole-request collection',
                'reason':'No verified transform from exported trace timestamps to client perf_counter phases; backend durations are not a clock anchor'}
            corrected['copy_event_consistency_checks']=copy_semantics_checks(corrected)
            corrected['interpretation'].append('Recorded memcpy bytes/event durations cannot alone establish physical PCIe traffic or bandwidth; event spans overlap and graph/replay semantics may matter.')
            write(trace_dir/'summary.json',corrected);write(trace_dir/'analysis.json',provenance)
            index['traces'].append({'trace_ordinal':ordinal,'summary':f'trace-{ordinal:02d}/summary.json',
                'timeline':f'trace-{ordinal:02d}/timeline.csv','metadata':f'trace-{ordinal:02d}/analysis.json',
                'source_sqlite_sha256':sqlite_sha,'invariants_verified':True})
            write(destination/'index.json',index)
        index['status']='validated';write(destination/'index.json',index)
    except BaseException:
        index['status']='failed_incomplete';write(destination/'index.json',index);raise
    return index


def self_test():
    with closing(sqlite3.connect(':memory:')) as connection:
        connection.execute('CREATE TABLE ENUM_CUDA_MEM_KIND(id INTEGER,name TEXT,label TEXT)')
        connection.executemany('INSERT INTO ENUM_CUDA_MEM_KIND VALUES(?,?,?)',
            [(0,'PAGEABLE','Pageable'),(1,'PINNED','Pinned'),(2,'DEVICE','Device'),(7,'UNKNOWN','Unknown')])
        mapping,evidence=enum_map(connection,MEMORY_TABLES,MEMORY_ALIASES)
        assert mapping[0]=='pageable' and mapping[1]=='pinned' and mapping[2]=='device' and mapping[7]=='unknown'
        assert evidence['status']=='available'
        missing,metadata=enum_map(connection,COPY_TABLES,COPY_ALIASES)
        assert missing=={} and metadata['status']=='unavailable'
    fake={'activity_groups':[{'kind':'memcpy','name_or_direction':'HtoD','trace_device':0,
        'bytes':50000000000,'total_duration_ns':900000000}]}
    check=copy_semantics_checks(fake)[0]
    assert check['above_reference_ceiling'] and check['is_measured_PCIe_bandwidth'] is False
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);database=root/'synthetic.sqlite'
        with closing(sqlite3.connect(database)) as connection:
            connection.executescript('CREATE TABLE StringIds(id INTEGER,value TEXT);'
                'CREATE TABLE CUPTI_ACTIVITY_KIND_MEMCPY(start INTEGER,end INTEGER,deviceId INTEGER,bytes INTEGER,copyKind INTEGER,srcKind INTEGER,dstKind INTEGER);'
                'INSERT INTO CUPTI_ACTIVITY_KIND_MEMCPY VALUES(0,10,0,64,1,1,2);')
            connection.commit()
        original=analysis_function(SNAPSHOT,{1:'pageable',2:'pinned'},{1:'HtoD'})(database,root/'original')
        corrected=analysis_function(SNAPSHOT,{1:'pinned',2:'device'},{1:'HtoD'})(database,root/'corrected')
        assert totals(original)==totals(corrected)
        assert corrected['activity_groups'][0]['source_memory']=='pinned'
        assert corrected['activity_groups'][0]['destination_memory']=='device'
        unknown=analysis_function(SNAPSHOT,{},{})(database,root/'unknown-enums')
        assert totals(unknown)==totals(original)
        assert unknown['activity_groups'][0]['source_memory']=='unavailable'
        assert unknown['activity_groups'][0]['name_or_direction']=='unknown'
    print('Synthetic SQLite-enum, preserved-counts/bytes/timing and copy-semantics checks passed; no hardware/profiler operations.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--self-test',action='store_true')
    parser.add_argument('--screen',type=Path,action='append',default=[])
    parser.add_argument('--case',type=Path,action='append',default=[])
    parser.add_argument('--helper-snapshot',type=Path,default=SNAPSHOT)
    parser.add_argument('--analysis-dir-name',default='analysis-corrected')
    args=parser.parse_args()
    if args.self_test:self_test();return
    if not re.fullmatch('analysis-corrected(?:-[A-Za-z0-9_-]+)?',args.analysis_dir_name):
        parser.error('Use a new analysis-corrected[-suffix] directory; original artifacts are never overwritten')
    screens=args.screen or sorted(INV.glob('profiled-screening-*'))
    cases=set(args.case)
    for screen in screens:cases.update(p for p in (screen/'cases').glob('*') if p.is_dir())
    if not args.run:
        print(json.dumps({'mode':'plan_only','cases':[str(p) for p in sorted(cases)],'writes':False},indent=2));return
    results=[reanalyze_case(case,args.helper_snapshot,args.analysis_dir_name) for case in sorted(cases)]
    print(json.dumps({'corrected_cases':len(results),'all_validated':all(r['status']=='validated' for r in results),
        'original_artifacts_modified':False},indent=2))


if __name__=='__main__':main()
