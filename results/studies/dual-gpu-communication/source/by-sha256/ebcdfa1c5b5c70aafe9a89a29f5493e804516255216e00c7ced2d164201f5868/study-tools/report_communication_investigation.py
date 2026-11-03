"""Generate a separate engineering diagnostic report from SAVED evidence only.

No engine, HTTP, CUDA, profiler or telemetry operations occur here. Default is
plan-only. Use --generate after all measurement/cleanup stages finish. Historical
datasets and the preceding 180-case report are read-only inputs.
"""
from __future__ import annotations

import argparse
import bisect
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import gzip
import io
import json
import math
from pathlib import Path
import re
import statistics
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
INV = ROOT / "untracked/communication-investigation"
PRIOR = ROOT / "untracked/dual-gpu-5960-experiment"
sys.path.insert(0, str(ROOT))
from wattever.privacy import public_projection

PREFIX = "dual-gpu-communication-and-placement"
INTEL = "https://www.intel.com/content/www/us/en/products/sku/82930/intel-core-i75960x-processor-extreme-edition-20m-cache-up-to-3-50-ghz/specifications.html"
EVGA = "https://www.evga.com/support/manuals/files/131-HE-E095_Part1_v2.pdf"
LLAMA = "https://github.com/ggml-org/llama.cpp/blob/3f8af15a7/docs/multi-gpu.md"
GEMMA_SOURCE = "https://github.com/ggml-org/llama.cpp/blob/3f8af15a7/src/models/gemma4.cpp"
INVENTORIES={'lfm25':'lfm25-gguf.json','qwen27':'qwen27q3-gguf.json',
             'qwen35':'qwen35q4-gguf.json','gemmae2b':'gemmae2b-gguf.json'}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def median(values):
    values = [v for v in values if finite(v)]
    return statistics.median(values) if values else None


def fmt(value, digits=2):
    return f"{value:,.{digits}f}" if finite(value) else "unavailable"


def compact(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def cell(value):
    return str(value if value is not None else "unreported").replace("|", "/").replace("\n", " ")


def table(headers, rows):
    return ["|" + "|".join(headers) + "|", "|" + "|".join("---" for _ in headers) + "|"] + [
        "|" + "|".join(cell(v) for v in row) + "|" for row in rows]


def write_csv(path, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row)) or ["availability"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        out = csv.DictWriter(handle, fieldnames=fields)
        out.writeheader()
        out.writerows(rows)


def write_compressed_public_json(path,value):
    """Deterministic gzip header, no oversized public uncompressed Git blob."""
    with path.open('wb') as binary:
        with gzip.GzipFile(filename='',mode='wb',fileobj=binary,compresslevel=6,mtime=0) as compressed:
            with io.TextIOWrapper(compressed,encoding='utf-8',newline='') as stream:
                json.dump(public_projection(value),stream,allow_nan=False,separators=(',',':'))


def figures_from_saved_csv(prefix):
    csv.field_size_limit(32*1024**2)
    numeric=set(RATIO_METRICS)|{'clients','target_tokens','transfer_bytes','median_joint_payload_GB_s','median_joint_host_us'}
    booleans={'metric_eligible','validation_passed'}
    def rows(path):
        with path.open(encoding='utf-8',newline='') as handle:values=list(csv.DictReader(handle))
        for row in values:
            for key,value in list(row.items()):
                if value=='':row[key]=None
                elif key in booleans:
                    if value not in ('True','False'):raise ValueError('Invalid saved figure eligibility flag')
                    row[key]=value=='True'
                elif key in numeric:
                    parsed=float(value)
                    if not math.isfinite(parsed):raise ValueError('Invalid saved figure number')
                    row[key]=int(parsed) if key in ('clients','target_tokens','transfer_bytes') else parsed
        return values
    micro=rows(prefix.with_name(prefix.name+'-micro-summary.csv'))
    conditions=rows(prefix.with_name(prefix.name+'-conditions.csv'))
    return charts(prefix,micro,conditions)


def historical_integrity():
    snapshot=read(INV/'historical-files.json')
    records=snapshot.get('files')
    if not isinstance(records,list) or not records:raise ValueError('Historical immutable-file snapshot unavailable')
    permissions_path=INV/'authorized-dataset-archives.json'
    permissions=read(permissions_path) if permissions_path.exists() else {}
    archives={}
    for record in permissions.get('archives',[]):
        key=record.get('original_path')
        if key in archives:raise ValueError('Duplicate authorized historical relocation')
        archives[key]=record
    def digest(path):
        value=hashlib.sha256()
        with path.open('rb') as handle:
            for chunk in iter(lambda:handle.read(8*1024*1024),b''):value.update(chunk)
        return value.hexdigest()
    verified=[]
    for record in records:
        relative=record.get('path');expected=record.get('sha256')
        if not isinstance(relative,str) or not re.fullmatch('[a-f0-9]{64}',str(expected)):
            raise ValueError('Invalid immutable historical-file record')
        original=(ROOT/relative).resolve()
        if not original.is_relative_to((ROOT/'results/runs').resolve()):
            raise ValueError('Historical snapshot path outside the explicitly guarded run directory')
        if record.get('role')=='current_LM_source_to_be_preserved_in_native_consolidation':
            final=read(PRIOR/'finalization.json')
            if final.get('partial') or final.get('run_id')!=original.stem or final.get('cases')!=180:
                raise ValueError('Current source consolidation is not the completed authorized180-case cohort')
            source=read(PRIOR/(original.stem+'.json'));current=read(original)
            source_rows=source.get('results') or [];current_rows=current.get('results') or []
            if len(source_rows)!=144 or len(current_rows)!=180:
                raise ValueError('Current LM source/consolidated case counts changed')
            def key(row):return row['model'],row.get('concurrency',1),row['target_tokens'],row.get('repeat',0)
            source_map={key(row):compact(row) for row in source_rows}
            current_map={key(row):compact(row) for row in current_rows}
            if len(source_map)!=144 or len(current_map)!=180 or any(current_map.get(k)!=v for k,v in source_map.items()):
                raise ValueError('An original144-case LM measurement row changed during consolidation')
            verified.append({'file_name':original.name,'verification_location':'authorized_current_source_consolidation',
                'immutable_measurement_rows_verified':144,'consolidated_rows':180,
                'original_file_sha256_not_reasserted_after_authorized_consolidation':expected})
            continue
        if record.get('role')!='historical_immutable':raise ValueError('Unknown historical snapshot role')
        location='original';path=original
        if not path.exists():
            relocation=archives.get(relative)
            if (not relocation or not permissions.get('authorization') or relocation.get('bytes_unchanged') is not True
                or relocation.get('sha256')!=expected):
                raise ValueError('Missing historical file without an explicit identical-SHA authorized relocation: '+original.name)
            path=(ROOT/relocation['archive_path']).resolve()
            if not path.is_relative_to((ROOT/'results/archives/superseded-setup-trials').resolve()):
                raise ValueError('Historical archive destination outside the explicitly authorized directory')
            location='explicit_authorized_archive'
        if not path.is_file() or path.stat().st_size!=record.get('size') or digest(path)!=expected:
            raise ValueError('Immutable historical-file bytes changed or remain missing: '+original.name)
        verified.append({'file_name':original.name,'sha256':expected,'verification_location':location})
    canonical=permissions.get('canonical_run_ids') or []
    visible={path.stem for path in (ROOT/'results/runs').glob('*.json')}
    catalog={'declared_setup_count':permissions.get('visible_setup_count'),
        'canonical_files_present':bool(canonical) and set(canonical)<=visible,
        'exact_visible_catalog_matches_ledger':bool(canonical) and visible==set(canonical),
        'visible_json_count_at_generation':len(visible)}
    return {'verified':True,'checked_files':len(verified),'files':verified,'canonical_catalog':catalog,
        'policy':'Only explicit user-authorized relocations preserve identity; original SHA256 and size must still match'}


def public_operational_qualification(source):
    if source is None:return None
    text=compact(source).lower()
    lookup=('lookup' in text or 'log scan' in text) and 'read' in text
    # Full internal/operator provenance remains untracked. Only the measurement
    # qualification belongs in public reports; publication-repair stories do not.
    return {'schema':'wattever-background-qualification-v1','background_read_recorded':lookup,
        'exact_utc_interval_known':False,'specific_overlapping_case_known':False,
        'effect_size_measured':False,'repeated_inference_for_this_event':False,
        'qualified_strategy_scope':'Gemma independent-instance phase' if lookup else None,
        'full_source_provenance_retained_untracked':True}


def micro_input(path):
    """Load probe plus optional gated-wrapper siblings without changing sources."""
    path=Path(path)
    data=read(path)
    if path.name=='transfers.json':
        siblings={name:read(path.parent/name) for name in ('telemetry.json','manifest.json') if (path.parent/name).exists()}
        if siblings or path.parent.name.startswith(('communication-full-','communication-smoke-')):
            data=dict(data,wrapper_capture={'scope':'Gated run_communication_capture.py diagnostic',**siblings})
    return data


def micro_status(data):
    status=data.get('status')
    telemetry=(data.get('wrapper_capture') or {}).get('telemetry.json')
    if status=='completed' and data.get('wrapper_capture') and telemetry is None:
        return 'wrapper_telemetry_pending_or_unavailable'
    if status=='completed' and telemetry and (telemetry.get('failure') or telemetry.get('cleanup_errors')):
        return 'wrapper_failed_or_cleanup_incomplete'
    metadata=((telemetry or {}).get('diagnostic') or {}).get('metadata') or {}
    if status=='completed' and (metadata.get('cleanup_errors') or metadata.get('status') in ('stop_unconfirmed','cleanup_incomplete')):
        return 'wrapper_counter_cleanup_incomplete'
    return status


def payload_samples(captures):
    """Compute every published rate from its actual joint interval and bytes."""
    rows, summaries, peers = [], [], []
    for number, (path, data) in enumerate(captures):
        sizes = {m.get("transfer_bytes") for m in data.get("measurements", [])}
        scope = "full" if len(sizes) >= 10 and not path.parent.name.startswith('communication-smoke-') else "smoke_or_subset"
        for peer in (data.get("inventory") or {}).get("peer_capability", []):
            peers.append(dict(capture=number, **peer))
        for measurement in data.get("measurements", []):
            values = [];event_rates=defaultdict(list)
            for index, sample in enumerate(measurement.get("samples", [])):
                elapsed, payload = sample.get("elapsed_s"), sample.get("payload_bytes")
                route_bytes = sum(r.get("payload_bytes", 0) for r in sample.get("routes", []))
                valid = finite(elapsed) and elapsed > 0 and finite(payload) and payload > 0 and payload == route_bytes
                rate = payload / elapsed if valid else None
                row = dict(capture=number, capture_scope=scope, capture_status=micro_status(data), case=measurement.get("case"),
                    mode=measurement.get("timing_mode"), transfer_bytes=measurement.get("transfer_bytes"),
                    internal_sample=index, joint_payload_bytes=payload, joint_elapsed_s=elapsed,
                    measured_joint_payload_GB_s=rate/1e9 if rate else None,
                    bytes_and_joint_time_valid=valid, destination_validation_passed=measurement.get("validation_passed"),
                    validation_scope=measurement.get("validation_scope"),
                    worker_intervals_overlap=sample.get("host_worker_intervals_overlap"),
                    timing_scope=sample.get("timing_scope"), routes_json=compact(sample.get("routes", [])))
                references=[]
                for route in sample.get('routes',[]):
                    for field in ('HtoD_gpu_event_ms','DtoH_gpu_event_ms','DtoD_gpu_event_ms'):
                        milliseconds=route.get(field);payload=route.get('payload_bytes')
                        if not finite(milliseconds) or milliseconds<=0 or not finite(payload) or payload<=0:continue
                        rate=payload/(milliseconds/1000)/1e9
                        event_device=route.get('destination_gpu') if field.startswith('HtoD') else route.get('source_gpu')
                        reference={'event_field':field,'event_device':event_device,'payload_bytes':payload,
                            'cuda_event_ms':milliseconds,'cuda_event_payload_GB_s':rate,
                            'scope':'Individual route/stage CUDA-event reference, never measured simultaneous host-wall throughput'}
                        references.append(reference)
                        if valid and measurement.get('validation_passed') is True:event_rates[(field,event_device)].append(rate)
                row['cuda_event_references_json']=compact(references)
                rows.append(row)
                if valid and measurement.get("validation_passed") is True:
                    values.append(row)
            summaries.append(dict(capture=number, capture_scope=scope, capture_status=micro_status(data), case=measurement.get("case"),
                mode=measurement.get("timing_mode"), transfer_bytes=measurement.get("transfer_bytes"),
                internal_samples=len(measurement.get("samples", [])), valid_samples=len(values),
                median_joint_payload_GB_s=median([r["measured_joint_payload_GB_s"] for r in values]),
                median_joint_host_us=median([r["joint_elapsed_s"]*1e6 for r in values]),
                transport=sorted({r.get("transport", "unreported") for s in measurement.get("samples", []) for r in s.get("routes", [])}),
                validation_passed=measurement.get("validation_passed"), copies_per_route=measurement.get("copies_per_route")))
            summaries[-1]['cuda_event_references_json']=compact([{'event_field':field,'event_device':device,
                'median_cuda_event_payload_GB_s':median(rates)} for (field,device),rates in sorted(event_rates.items(),key=lambda item:(item[0][0],str(item[0][1])))])
    return rows, summaries, peers


def vram_copy_reference_rows(micro_summary):
    rows=[]
    for device in (0,1):
        path=PRIOR/f'gpu-compute-{device}.json'
        if path.exists():
            for measurement in read(path).get('measurements',[]):
                if measurement.get('kind')!='vram_device_to_device_copy' or measurement.get('bytes_per_copy')!=256*2**20:continue
                rows.append({'capture':'historical_compute_probe','gpu_index':device,'bytes_per_copy':measurement['bytes_per_copy'],
                    'copies_per_sample':measurement.get('calls_per_sample'),'internal_samples':len(measurement.get('sample_ms',[])),
                    'CUDA_event_payload_GB_s':measurement.get('median_payload_GB_s'),
                    'joint_host_wall_payload_GB_s':None,'nominal_read_plus_write_GB_s':measurement.get('median_read_plus_write_GB_s'),
                    'rate_definition':'Payload=size*copies/CUDA-event interval; nominal read-plus-write separately2x',
                    'validation_passed':(measurement.get('validation') or {}).get('passed')})
        for measurement in micro_summary:
            if measurement.get('capture_scope')!='full' or measurement.get('capture_status')!='completed' or measurement.get('case')!='DtoD'+str(device) or measurement.get('transfer_bytes')!=256*2**20:continue
            references=json.loads(measurement['cuda_event_references_json'])
            rate=next((r['median_cuda_event_payload_GB_s'] for r in references if r['event_field']=='DtoD_gpu_event_ms'),None)
            rows.append({'capture':'communication_'+str(measurement['capture'])+'_'+measurement['mode'],'gpu_index':device,
                'bytes_per_copy':measurement['transfer_bytes'],'copies_per_sample':compact(measurement['copies_per_route']),
                'internal_samples':measurement['internal_samples'],'CUDA_event_payload_GB_s':rate,
                'joint_host_wall_payload_GB_s':measurement['median_joint_payload_GB_s'],'nominal_read_plus_write_GB_s':None,
                'rate_definition':'Joint host wall and route CUDA event are separate clocks/scopes; actual copy count retained',
                'validation_passed':measurement['validation_passed']})
    return rows


def topology_rows(data):
    nodes = data.get("nodes") or {}
    rows = []
    for node in nodes.values():
        if node.get("class") not in ("Display", "SCSIAdapter"):
            continue
        prop = node.get("properties") or {}
        parent = nodes.get(prop.get("DEVPKEY_Device_Parent"), {})
        pp = parent.get("properties") or {}
        rows.append({"device": node.get("name"), "pnp_generation_snapshot": prop.get("DEVPKEY_PciDevice_CurrentLinkSpeed"),
            "pnp_width_snapshot": prop.get("DEVPKEY_PciDevice_CurrentLinkWidth"),
            "parent_bridge_location": pp.get("DEVPKEY_Device_LocationInfo"),
            "physical_motherboard_slot": "unverified", "scope": "PnP snapshot, not under-load throughput"})
    return rows


def failure_patterns(text):
    """Publish constants/small allowlisted values, NEVER original private text."""
    findings=[]
    def add(code,detail,**values):
        if not any(f['code']==code for f in findings):
            findings.append(dict(code=code,detail=detail,evidence='owned_terminal_engine_log_or_error_text',**values))
    arch=re.search(r"SPLIT_MODE_TENSOR.{0,100}(?:not implemented|not supported).{0,60}architecture\s*['\"]?([a-z0-9_]{1,48})",text,re.I)
    if arch:
        add('tensor_architecture_not_implemented','Pinned backend rejected tensor splitting for the reported architecture',
            architecture=arch[1].lower() if arch[1].lower() in ('gemma4','qwen35','qwen35moe','lfm2moe','qwen3next','qwen4exp','deepseek41') else 'unrecognized_architecture')
    if re.search(r'GGML_ASSERT\s*\(\s*src_ss\[0\]\.axis\s*!=\s*GGML_BACKEND_SPLIT_AXIS_0\s*\)\s*failed',text):
        add('tensor_split_axis_assertion','Backend rejected the source tensor split axis during graph execution',
            assertion='src_ss[0].axis != GGML_BACKEND_SPLIT_AXIS_0')
    if re.search(r'device\s+CUDA\d+\s+does\s+not\s+support\s+split\s+buffers',text,re.I):
        add('backend_split_buffers_unsupported','Pinned CUDA backend rejected split weight buffers during model loading; no measured row-split request completed')
    if re.search(r'(?:n_head_kv|KV heads|key.value heads).{0,180}(?:divisib|%.*(?:device|n_device)|must be.*multiple)',text,re.I):
        add('tensor_kv_head_divisibility','Backend reported a KV-head/device divisibility restriction')
    if re.search(r'(?:divisib|must be.*multiple).{0,160}(?:n_head_kv|KV heads|key.value heads)',text,re.I):
        add('tensor_kv_head_divisibility','Backend reported a KV-head/device divisibility restriction')
    if re.search(r'(?:SPLIT_MODE_TENSOR|split.mode.tensor).{0,100}requires.{0,50}flash',text,re.I):
        add('tensor_requires_flash_attention','Backend requires flash attention for tensor mode')
    if re.search(r'(?:SPLIT_MODE_TENSOR|tensor).{0,120}(?:KV cache quantization|quantized KV).{0,100}(?:not implemented|not supported)',text,re.I):
        add('tensor_quantized_kv_unsupported','Backend rejected quantized KV with tensor mode')
    if re.search(r'(?:out of memory|failed to allocate|not enough memory|CUDA_ERROR_OUT_OF_MEMORY)',text,re.I):
        pool=('pinned_host' if re.search(r'(?:cudaHostAlloc|cuMemHostAlloc|pinned host).{0,120}(?:fail|out of memory)',text,re.I)
              else 'device' if re.search(r'(?:cudaMalloc|cuMemAlloc|CUDA\d.*buffer).{0,120}(?:fail|out of memory)',text,re.I)
              else 'unresolved')
        add('allocation_out_of_memory','An allocation failed; an unspecified pool is not asserted to be VRAM',allocation_pool=pool)
    flag=re.search(r'(?:unknown|unrecognized|invalid|unsupported)\s+(?:command.line\s+)?(?:argument|option|flag)\s*:?\s*[\'\"]?(--[a-z][a-z0-9-]{0,63})(?=\s|[\'\"]|$)',text,re.I)
    if flag:
        allowed={'--kv-unified-per-slot','--kv-unified','--cache-ram','--no-cache-idle-slots','--spec-type',
                 '--reasoning','--no-cache-prompt','--no-context-shift','--split-mode','--tensor-split',
                 '--flash-attn','--cache-type-k','--cache-type-v','--fit','--fit-target','--offline'}
        add('backend_cli_option_unsupported','Backend rejected an explicit command-line option',
            option=flag[1].lower() if flag[1].lower() in allowed else 'unrecognized_option')
    if re.search(r'(?:prompt|requested context|n_ctx).{0,100}(?:exceeds|exceeded|overflow|too long|larger than|greater than)|(?:exceeds|larger than|greater than).{0,60}(?:allocated|available|max(?:imum)?|configured).{0,40}(?:context|n_ctx)|(?:context window|n_ctx).{0,100}(?:exceeded|overflow|too small|insufficient)',text,re.I):
        add('context_capacity_rejected','Backend or request validation reported a context-capacity limit')
    if re.search(r'(?:timed out|TimeoutError|deadline exceeded|read timeout)',text,re.I):
        add('request_or_startup_timeout','A startup/socket/future-wait timeout was reported; this is not a hard group deadline or proof of deadlock')
    return findings[:8]


def safe_failure_evidence(result,runtime,root):
    case=result['condition'];status=result.get('status')
    if status in ('starting','running','stopping',None):
        return {'availability':'unavailable_nonterminal','findings':[]}
    findings=[]
    for request in result.get('requests',[]):
        validity=request.get('measurement_validity') or {}
        for field,code,detail in (
            ('fixed_context_overflow','actual_request_context_overflow','Actual prompt plus output budget exceeds the verified slot context'),
            ('positive_cache_reuse_observed','cached_prefill_contamination','Positive reported prompt-cache reuse disqualifies a cold-prefill comparison')):
            if validity.get(field) is True:
                findings.append({'code':code,'detail':detail,'evidence':'public_request_measurement_validity'})
    evidence={'availability':'public_validity_only' if findings else 'unavailable','findings':findings}
    if status=='ok':return evidence
    model=case.get('model');metadata={}
    inventory_path=INV/INVENTORIES[model] if model in INVENTORIES else None
    if inventory_path and inventory_path.exists():
        inventory=read(inventory_path);meta=inventory.get('metadata') or {}
        identity=(runtime.get('model_files') or {}).get(model) or {}
        checked=(identity.get('header_sha256')==inventory.get('header_sha256') and
                 identity.get('file_bytes')==inventory.get('file_bytes') and bool(identity.get('header_sha256')))
        architecture=meta.get('general.architecture')
        if checked and architecture in ('lfm2moe','qwen35','qwen35moe','gemma4'):
            heads=meta.get(architecture+'.attention.head_count_kv')
            values=heads if isinstance(heads,list) else [heads]
            values=sorted({v for v in values[:256] if type(v) is int and 0<=v<=65536})
            metadata={'architecture':architecture,'kv_heads_metadata':values,'metadata_header_identity_checked':True}
    evidence['metadata']=metadata
    if case.get('strategy')=='tensor_equal':
        reference=['Tensor mode requires supported architecture, flash attention and nonquantized KV; automatic fitting is disabled']
        if metadata.get('architecture')=='lfm2moe':
            reference.append('Pinned multi-GPU documentation lists LFM2-MoE as unsupported for tensor mode; this is a source restriction, not observed rejection evidence')
        if any(h>0 and h%2 for h in metadata.get('kv_heads_metadata',[])):
            reference.append('Metadata contains KV-head counts indivisible by the two requested tensor devices; whether the backend rejects or replicates is determined by observed logs')
        evidence['reference_restrictions']=reference
    case_id=case.get('case_id','')
    if not re.fullmatch(r'[a-f0-9]{16}',case_id):return evidence
    directory=root/'cases'/case_id
    private_path=directory/'result-private.json'
    if not private_path.exists():return evidence
    private=read(private_path)
    if private.get('condition')!=case or private.get('status')!=status:
        evidence['availability']='unavailable_private_public_identity_mismatch';return evidence
    if any(item.get('exit_confirmed') is not True for item in private.get('cleanup',[])):
        evidence['availability']='unavailable_cleanup_not_confirmed';return evidence
    texts=[str(private.get('error') or '')]
    for request in private.get('requests',[]):
        texts.extend(str(request.get(k) or '') for k in ('error','fail_kind','message','reason'))
    terminal_error_text='\n'.join(texts)
    terminal_codes={f['code'] for f in failure_patterns(terminal_error_text)}
    for path in sorted(directory.glob('server*/server.log'))[:2]:
        # Bounded tail only; raw bytes/lines, paths, URLs, PIDs and stack frames
        # are never returned by this function or copied into the public report.
        with path.open('rb') as handle:
            handle.seek(max(0,path.stat().st_size-2*1024*1024))
            texts.append(handle.read().decode('utf-8','replace'))
    observed=failure_patterns('\n'.join(texts))
    for finding in observed:
        direct=finding['code'] in terminal_codes or finding['code']=='tensor_split_axis_assertion'
        direct=direct or (status=='allocation_failed' and finding['code']=='allocation_out_of_memory')
        direct=direct or (finding['code']=='backend_split_buffers_unsupported' and status=='failed'
            and not private.get('actual_context_verified') and not private.get('requests'))
        finding['condition_failure_supported']=direct
        finding['evidence_scope']='terminal_error_or_fatal_assertion' if direct else 'coexisting_engine_log_observation'
    if observed:
        evidence['availability']='observed_terminal_private_evidence'
        evidence['findings']=(findings+observed)[:10]
        evidence['interpretation']='Allowlisted messages observed in terminal engine/error evidence; metadata alone does not establish failure cause and nonfatal warning messages may coexist'
    return evidence


def failed_capture_diagnostics(result,root):
    if result.get('status') in ('ok','starting','running','stopping',None):return None
    case_id=result['condition'].get('case_id','')
    if not re.fullmatch('[a-f0-9]{16}',case_id):return None
    directory=root/'cases'/case_id
    private_path=directory/'result-private.json'
    if not private_path.exists():return None
    private=read(private_path)
    if private.get('condition')!=result['condition'] or private.get('status')!=result.get('status'):return None
    if any(c.get('exit_confirmed') is not True for c in private.get('cleanup',[])):return None
    def safe(value):
        if isinstance(value,list):return [safe(item) for item in value]
        if isinstance(value,dict):return {k:safe(v) for k,v in value.items()
            if re.sub('[^a-z0-9]','',str(k).lower()) not in
            ('pid','pids','ownedpids','processid','luid','uuid','gpuuuid','pcibusid','pcibusaddress')}
        return value
    data={'scope':'Whole diagnostic sampler interval, not a reconstructed request/group window',
        'aligned_request_group_energy_available':False,'group_rates_or_energy_per_token_derived':False}
    for name,file_name in (('power','power-raw.json'),('diagnostic','diagnostic-telemetry.json')):
        path=directory/file_name
        if not path.exists():continue
        payload=read(path)
        summary=dict(payload.get('summary') or {})
        summary.pop('samples',None)
        data[name+'_summary']=safe(public_projection(summary))
        data[name+'_samples']=safe(public_projection(payload.get('history') or []))
    return data if 'power_summary' in data or 'diagnostic_summary' in data else None


def trace_observation_totals(summary):
    durations=defaultdict(int)
    for row in summary.get('activity_groups',[]):durations[row['kind']]+=row.get('total_duration_ns',0)
    return {'counts':summary.get('event_counts',{}),'logical_copy_bytes':sum(row.get('bytes',0)
        for row in summary.get('activity_groups',[]) if row.get('kind')=='memcpy'),
        'duration_sums_by_kind':dict(durations),'trace_span_ns':summary.get('trace_span_ns'),
        'device_activity':summary.get('device_activity',[])}


def corrected_trace_sidecars(result,runtime,root):
    case=result['condition']
    if not re.fullmatch('[a-f0-9]{16}',case.get('case_id','')):return []
    directory=root/'cases'/case['case_id']
    expected=(runtime.get('source_sha256') or {}).get('tools/nsight_capture.py')
    candidates=[]
    for path in directory.glob('analysis-corrected*/index.json'):
        index=read(path)
        if index.get('status')!='validated' or index.get('case_id')!=case['case_id']:continue
        if not expected or index.get('capture_helper_source_sha256')!=expected:continue
        candidates.append((index.get('generated_at',''),path,index))
    if not candidates:return []
    _,path,index=max(candidates,key=lambda entry:entry[0])
    summaries=[]
    for trace in index.get('traces',[]):
        relative=Path(trace.get('summary',''))
        if relative.is_absolute() or '..' in relative.parts:raise ValueError('Unsafe corrected-analysis summary reference')
        summary=read(path.parent/relative)
        provenance=summary.get('analysis_provenance') or {}
        if (trace.get('invariants_verified') is not True or provenance.get('invariants_verified') is not True
            or provenance.get('source_sqlite_sha256')!=trace.get('source_sqlite_sha256')):
            raise ValueError('Corrected trace invariant/provenance check failed')
        summaries.append(summary)
    def original_summaries(value):
        if isinstance(value,list):
            for item in value:yield from original_summaries(item)
        elif isinstance(value,dict) and 'event_counts' in value:yield value
    originals=list(original_summaries(result.get('trace_exports',[])))
    if len(originals)!=len(summaries) or any(trace_observation_totals(a)!=trace_observation_totals(b)
            for a,b in zip(originals,summaries)):
        raise ValueError('Corrected sidecar observations differ from archived public trace summaries')
    return summaries


def output_stop_evidence(result,root):
    case=result['condition']
    policy=case.get('output_stop_policy','natural_eos')
    evidence={'requested_policy':policy,'verification_source':'not_required_for_natural_eos',
        'actual_output_count_verified':None,'actual_server_ignore_eos_verified':None,
        'eligible_fixed_output_comparison':False,'comparison_eligible':policy=='natural_eos'}
    if policy=='natural_eos':return evidence
    evidence['verification_source']='unavailable_or_unverified_fixed_output_sidecar'
    if policy!='ignore_eos_requested_256':return evidence
    case_id=case.get('case_id','')
    if not re.fullmatch('[a-f0-9]{16}',case_id):return evidence
    directory=root/'cases'/case_id
    result_path=directory/'result-private.json';sidecar=directory/'fixed-output-verification.json'
    if not result_path.exists() or not sidecar.exists():return evidence
    raw=result_path.read_bytes();private=json.loads(raw);proof=read(sidecar)
    if (proof.get('schema')!='fixed-output-verification-v1'
        or proof.get('result_sha256')!=hashlib.sha256(raw).hexdigest()
        or private.get('condition')!=case or private.get('status')!=result.get('status')
        or public_projection(private.get('requests'))!=result.get('requests')
        or not private.get('cleanup') or any(c.get('exit_confirmed') is not True for c in private['cleanup'])):
        return evidence
    requests=result.get('requests') or []
    count_ok=(len(requests)==case.get('clients') and all(r.get('status')=='ok'
        and type(r.get('completion_tokens')) is int and r['completion_tokens']==256
        and r.get('completion_tokens_estimated') is False for r in requests))
    servers=[]
    for path in sorted(directory.glob('server*/actual-server-private.json')):
        if not re.fullmatch(r'server\d+',path.parent.name):return evidence
        metadata_raw=path.read_bytes();metadata=json.loads(metadata_raw)
        value=(((metadata.get('props') or {}).get('default_generation_settings') or {}).get('params') or {}).get('ignore_eos')
        servers.append({'source':path.parent.name+'/'+path.name,
            'sha256':hashlib.sha256(metadata_raw).hexdigest(),
            'props_ignore_eos':value if type(value) is bool else None,
            'slot_ignore_eos':[s.get('params',{}).get('ignore_eos') for s in metadata.get('slots',[])]})
    props_ok=(len(servers)==(2 if case.get('strategy')=='independent' else 1)
        and all(s['props_ignore_eos'] is True for s in servers))
    verified=(private.get('status')=='ok' and count_ok and props_ok)
    if (proof.get('server_evidence')!=servers or proof.get('actual_output_count_verified') is not count_ok
        or proof.get('actual_server_ignore_eos_verified') is not props_ok
        or proof.get('eligible_fixed_output_comparison') is not verified
        or proof.get('expected_output_tokens_per_request')!=256):return evidence
    evidence.update(verification_source='hash_bound_terminal_private_result_and_effective_props',
        result_sha256=proof['result_sha256'],actual_output_count_verified=count_ok,
        actual_server_ignore_eos_verified=props_ok,server_evidence=servers,
        eligible_fixed_output_comparison=verified,comparison_eligible=verified)
    return evidence


def parse_pipeline_lines(log):
    """Allowlisted startup observations, not a prediction from split flags."""
    observations=[]
    for number,line in enumerate(log.splitlines(),1):
        match=re.search(r'pipeline\s+parallelism\s+(enabled|disabled)(?:\s*\(\s*n_copies\s*=\s*(\d+)\s*\))?',line,re.I)
        if match:
            observations.append({'line_number':number,'enabled':match[1].lower()=='enabled',
                'n_copies':int(match[2]) if match[2] else None,
                'observed_fragment':'pipeline parallelism '+match[1].lower()+(' (n_copies='+match[2]+')' if match[2] else '')})
        elif re.search(r'llama_context|pipeline',line,re.I):
            copies=re.search(r'\bn_copies\s*=\s*(\d+)\b',line)
            if copies:observations.append({'line_number':number,'enabled':None,'n_copies':int(copies[1]),
                'observed_fragment':'n_copies='+copies[1]})
        if len(observations)>=32:break
    states={row['enabled'] for row in observations if type(row['enabled']) is bool}
    return {'pipeline_parallelism_enabled':next(iter(states)) if len(states)==1 else None,
        'state':'mixed_observed' if len(states)>1 else 'observed' if states else 'unavailable',
        'n_copies_values':sorted({row['n_copies'] for row in observations if row['n_copies'] is not None}),
        'observations':observations,'qualification':'A logged pipeline/copy-count setting is not proof of concurrent GPU execution or phase-specific benefit'}


def pipeline_evidence(result,root):
    evidence={'availability':'unavailable','servers':[],
        'scope':'First2MiB of each terminal owned server startup log; no live scans, no assumed setting from requested split'}
    case_id=result['condition'].get('case_id','')
    if not re.fullmatch('[a-f0-9]{16}',case_id):return evidence
    directory=root/'cases'/case_id;private_path=directory/'result-private.json'
    if result.get('status') in (None,'starting','running','stopping') or not private_path.exists():return evidence
    private=read(private_path)
    if (private.get('condition')!=result['condition'] or private.get('status')!=result.get('status')
        or not private.get('cleanup') or any(c.get('exit_confirmed') is not True for c in private['cleanup'])):return evidence
    for path in sorted(directory.glob('server*/server.log')):
        if not re.fullmatch(r'server\d+',path.parent.name):continue
        with path.open('rb') as handle:prefix=handle.read(2*1024**2)
        parsed=parse_pipeline_lines(prefix.decode('utf-8',errors='replace'))
        evidence['servers'].append(dict(parsed,server=path.parent.name,observed_prefix_bytes=len(prefix),
            bounded_prefix_sha256=hashlib.sha256(prefix).hexdigest(),
            full_log_bytes=path.stat().st_size,prefix_truncated=path.stat().st_size>len(prefix)))
    if evidence['servers']:evidence['availability']='terminal_bounded_startup_evidence'
    return evidence


def parse_actual_placement(log):
    """Separate fit dry passes from the last real weight/KV allocations.

    Allocation log sizes are logged allocations, not a physical-RAM accounting
    system. Offloaded layer counts do not establish that CPU expert weights are
    absent. Unknown tensor roles/compute placement remain unknown.
    """
    lines=log.splitlines()
    boundaries=[i for i,line in enumerate(lines) if re.search(r'llama_model_loader:\s+loaded\s+(?:meta\s*data|metadata)',line,re.I)]
    starts=boundaries or [0]
    allocation=re.compile(r'\b([A-Za-z0-9_-]+)\s+(model|KV|compute)\s+buffer\s+size\s*=\s*([\d.]+)\s*(MiB|GiB|KiB|bytes)\b',re.I)
    def buffers(start,end):
        rows=[]
        for i in range(start,end):
            match=allocation.search(lines[i])
            if not match:continue
            size=float(match[3])*{'mib':2**20,'gib':2**30,'kib':2**10,'bytes':1}[match[4].lower()]
            if not math.isfinite(size) or size<0:continue
            rows.append({'line_number':i+1,'backend':match[1],'kind':match[2].lower(),
                'logged_bytes':size,'positive_allocation':size>0,
                'observed_fragment':match[0]})
        return rows
    passes=[]
    for index,start in enumerate(starts):
        end=starts[index+1] if index+1<len(starts) else len(lines)
        rows=buffers(start,end)
        passes.append({'start':start,'end':end,'buffers':rows,
            'has_positive_model':any(r['kind']=='model' and r['positive_allocation'] for r in rows)})
    candidates=[row for row in passes if row['has_positive_model']]
    if not candidates:return {'availability':'no_positive_model_allocation_in_bounded_log',
        'metadata_pass_count':len(boundaries),'dry_or_unresolved_pass_count':len(passes),
        'all_weights_gpu_resident_verified':None,'actual_kv_locality_verified':None}
    selected=candidates[-1];start,end=selected['start'],selected['end']
    loads=[i for i in range(start,end) if re.search(r'load_tensors:\s+loading\s+model\s+tensors',lines[i],re.I)]
    if loads:
        epochs=[(at,loads[index+1] if index+1<len(loads) else end) for index,at in enumerate(loads)]
        real=[(at,stop) for at,stop in epochs if any(r['kind']=='model' and r['positive_allocation'] for r in buffers(at,stop))]
        if real:start,end=real[-1]
    rows=buffers(start,end)
    model_rows=[row for row in rows if row['kind']=='model' and row['positive_allocation']]
    totals=defaultdict(float)
    for row in model_rows:totals[row['backend']]+=row['logged_bytes']
    cpu_models=[row for row in model_rows if re.search(r'CPU|Host',row['backend'],re.I)]
    policy=[]
    layer_map={}
    offload=[]
    first_context=next((i for i in range(start,end) if re.search(r'llama_context:\s+constructing\s+llama_context',lines[i],re.I)),end)
    for i in range(start,end):
        line=lines[i]
        assigned=re.search(r'layer\s+(\d+)\s+assigned to device\s+(CUDA\d+|CPU)\b',line,re.I)
        model_scope=(i<first_context and not re.search(r'kv_cache|llama_memory',line,re.I))
        if assigned and model_scope:layer_map[int(assigned[1])]=assigned[2]
        offloaded=re.search(r'offloaded\s+(\d+)/(\d+)\s+layers',line,re.I)
        if offloaded:offload.append({'offloaded_layers':int(offloaded[1]),'total_layers':int(offloaded[2])})
        if model_scope and re.search(r'CPU|Host',line,re.I):
            tensor=re.search(r'\b(?:blk\.\d+\.)?(?:ffn_(?:down|up|gate)_exps|token_embd|output)(?:\.weight)?\b',line)
            if tensor and re.search(r'override|buffer|assign|device|tensor',line,re.I):
                policy.append({'line_number':i+1,'tensor_name':tensor[0],
                    'role':'expert' if '_exps' in tensor[0] else 'embedding_or_output',
                    'host_backend_mentioned':True,'interpretation':'Logged tensor/backend policy, not proof of the executing processor'})
    contexts=[i for i in range(start,end) if re.search(r'llama_context:\s+constructing\s+llama_context',lines[i],re.I)]
    kv_start=start;kv_context_isolated=False
    if contexts:
        epochs=[(at,contexts[index+1] if index+1<len(contexts) else end) for index,at in enumerate(contexts)]
        real=[(at,stop) for at,stop in epochs if any(r['kind']=='kv' and r['positive_allocation'] for r in buffers(at,stop))]
        if real:kv_start,kv_end=real[-1];kv_context_isolated=True
        else:kv_end=end
    else:kv_end=end
    kv_rows=[row for row in buffers(kv_start,kv_end) if row['kind']=='kv' and row['positive_allocation']]
    kv_types={'k':set(),'v':set()}
    for line in lines[kv_start:kv_end] if kv_context_isolated else []:
        if 'kv_cache' not in line.lower():continue
        for axis in ('k','v'):
            kv_types[axis].update(m.lower() for m in re.findall(r'\b'+axis+r'\s*\(\s*(f16|f32|bf16|q8_0|q4_0|q4_1|q5_0|q5_1|iq4_nl)\s*\)',line,re.I))
    return {'availability':'final_positive_model_load_observed' if boundaries else 'positive_allocations_observed_pass_boundary_unavailable',
        'metadata_pass_count':len(boundaries),'selected_pass_start_line':selected['start']+1,
        'selected_real_load_start_line':start+1,'selected_real_load_end_line':end,
        'preceding_passes_excluded':len([p for p in passes if p['start']<selected['start']]),
        'zero_model_allocation_rows_excluded':sum(r['kind']=='model' and not r['positive_allocation'] for p in passes for r in p['buffers']),
        'model_buffers':model_rows,'logged_model_buffer_bytes_sum_by_backend':dict(totals),
        'host_model_buffer_present':bool(cpu_models),
        'all_weights_gpu_resident_verified':False if cpu_models else None,
        'host_tensor_policy_observations':policy[:128],
        'host_expert_policy_observed':any(p['role']=='expert' for p in policy),
        'layer_device_assignments_final_load':layer_map,'reported_layer_offload_final_load':offload[-1] if offload else None,
        'kv_buffers_positive':kv_rows,'kv_context_epoch_isolated':kv_context_isolated,
        'kv_cache_types_final_real_context':{k:sorted(v) for k,v in kv_types.items()},
        'pipeline_final_real_context':parse_pipeline_lines('\n'.join(lines[kv_start:kv_end])) if kv_context_isolated else
            {'state':'unavailable','pipeline_parallelism_enabled':None,'n_copies_values':[],
             'observations':[],'qualification':'Final real KV context epoch not isolated; startup dry-fit pipeline messages do not prove the active setting'},
        'actual_kv_locality_verified':None,
        'qualification':'Logged allocation sums are not independently measured physical RAM or live VRAM residency. CPU/host model buffers disprove all-weights-in-VRAM claims even when every layer is reported offloaded. Embedding/output tensors and expert policies are distinct; missing explicit expert names do not identify the CPU-buffer role. Zero-size dry allocations and earlier fit passes are excluded; per-layer KV location remains unverified.'}


def actual_placement_evidence(result,root):
    evidence={'availability':'unavailable','servers':[],
        'scope':'Bounded first16MiB terminal owned startup logs; isolated final positive model-load and KV-context epochs'}
    case_id=result['condition'].get('case_id','')
    if not re.fullmatch('[a-f0-9]{16}',case_id):return evidence
    directory=root/'cases'/case_id;private_path=directory/'result-private.json'
    if result.get('status') in (None,'starting','running','stopping') or not private_path.exists():return evidence
    private=read(private_path)
    if (private.get('condition')!=result['condition'] or private.get('status')!=result.get('status')
        or not private.get('cleanup') or any(c.get('exit_confirmed') is not True for c in private['cleanup'])):return evidence
    for path in sorted(directory.glob('server*/server.log')):
        if not re.fullmatch(r'server\d+',path.parent.name):continue
        with path.open('rb') as handle:prefix=handle.read(16*1024**2)
        parsed=parse_actual_placement(prefix.decode('utf-8',errors='replace'))
        evidence['servers'].append(dict(parsed,server=path.parent.name,observed_prefix_bytes=len(prefix),
            bounded_prefix_sha256=hashlib.sha256(prefix).hexdigest(),
            full_log_bytes=path.stat().st_size,prefix_truncated=path.stat().st_size>len(prefix)))
    if evidence['servers']:evidence['availability']='terminal_bounded_final_real_load_evidence'
    return evidence


def placement_scope(result):
    case=result['condition'];strategy=case.get('strategy','')
    mode=(case.get('strategy_definition') or {}).get('split_mode')
    if mode is None:mode=('row' if strategy.startswith('row_') else 'tensor' if strategy.startswith('tensor_')
        else 'layer' if strategy.startswith('layer_') or strategy=='gemma_kv_local' else 'none')
    placements=result.get('placement') or []
    actual_servers=(result.get('actual_placement_evidence') or {}).get('servers') or []
    final_buffers=[r for server in actual_servers for r in server.get('model_buffers',[])]
    lines=[r.get('observed_fragment','') for r in final_buffers]
    nonsharded=any(re.search(r'\bCUDA\d+\s+model\s+buffer\s+size',line,re.I) for line in lines)
    split_buffer=any(re.search(r'CUDA\d+[_-]Split|(?:split|sharded)\s+model\s+buffer',line,re.I) for line in lines)
    supported=(mode in ('none','layer') and strategy!='independent' and nonsharded and not split_buffer
        and len(actual_servers)==1 and actual_servers[0].get('availability')=='final_positive_model_load_observed')
    mapped=(actual_servers[0].get('layer_device_assignments_final_load') or {}) if actual_servers else {}
    assigned=[mapped.get(i,mapped.get(str(i))) for i in range(13,35)]
    colocated=all(value is not None for value in assigned) and len(set(assigned))==1
    index=re.fullmatch(r'CUDA(\d+)',assigned[0] or '') if colocated else None
    mappings=result.get('visible_to_physical') or []
    mapping=mappings[0] if len(mappings)==1 and isinstance(mappings[0],dict) else {}
    physical=mapping.get(int(index[1]),mapping.get(str(int(index[1])))) if index else None
    final_colocation=(physical==0 if colocated and type(physical) is int else False
        if all(value is not None for value in assigned) and not colocated else None)
    return {'requested_split_mode':mode,
        'layer_assignment_scope':'Logical backend/main-device assignment; whole weight-layer physical colocation unverified'
            if mode in ('row','tensor') else 'Layer/device assignment; weight colocation requires nonsharded model-buffer evidence',
        'nonsharded_cuda_model_buffer_observed':nonsharded,'split_model_buffer_observed':split_buffer,
        'final_real_load_isolated':supported,'final_layer_assignment_colocation_observed':colocated,
        'host_model_buffers_present':any(server.get('host_model_buffer_present') is True for server in actual_servers),
        'gemma_weight_colocation_on_5070':final_colocation if supported else None,
        'qualification':'Shared-KV locality remains unknown; independent instances need a separate per-instance map. Row/tensor layer assignment does not reveal weight shards, split axes/fractions or physical KV placement'}


def profile_placement_comparisons(results,runtimes):
    """Compare observed allocations without importing profile timings to ranks."""
    rows=[]
    normal=[r for r in results if r.get('capture_kind')=='screening' and r.get('status')=='ok']
    keys=('model','strategy','target_tokens','clients','context_per_slot','kv_type_k','kv_type_v',
        'flash_attn','flash_attention','kv_layout','cpu_threads','threads','reasoning','mtp')
    def signature(result):
        case=result['condition'];runtime=runtimes.get(case['case_id'],{})
        return {'case':{k:case.get(k) for k in keys},
            'model_identity':(runtime.get('model_files') or {}).get(case.get('model')),
            'executable_sha256':runtime.get('executable_sha256'),
            'source_sha256':{k:v for k,v in (runtime.get('source_sha256') or {}).items() if k!='tools/nsight_capture.py'},
            'gpu_mapping':runtime.get('gpu_mapping'),'cuda_overrides':runtime.get('cuda_overrides'),
            'host_hardware':runtime.get('host_hardware'),
            'visible_to_physical':result.get('visible_to_physical')}
    def allocation_shape(result):
        servers=(result.get('actual_placement_evidence') or {}).get('servers') or []
        if not servers or any(s.get('availability')!='final_positive_model_load_observed' or s.get('prefix_truncated') for s in servers):return None
        shaped=[]
        for server in servers:
            kv=defaultdict(float)
            for row in server.get('kv_buffers_positive',[]):kv[row['backend']]+=row['logged_bytes']
            shaped.append({'server':server.get('server'),
                'logged_model_buffer_bytes_by_backend':server.get('logged_model_buffer_bytes_sum_by_backend') or {},
                'positive_kv_buffer_bytes_by_backend':dict(kv),
                'kv_context_epoch_isolated':server.get('kv_context_epoch_isolated'),
                'final_weight_layer_backend_assignments':{str(k):v for k,v in (server.get('layer_device_assignments_final_load') or {}).items()},
                'host_expert_policy_observed':server.get('host_expert_policy_observed')})
        return shaped
    for profile in results:
        if profile.get('capture_kind')!='profiling':continue
        case=profile['condition'];identity=signature(profile)
        matches=[r for r in normal if identity==signature(r)] if identity['model_identity'] and identity['executable_sha256'] else []
        if not matches:
            rows.append({'profile_case_id':case['case_id'],'model':case.get('model'),'strategy':case.get('strategy'),
                'status':'matching_unprofiled_allocation_evidence_unavailable','timings_normalized':False})
        for baseline in matches:
            a,b=allocation_shape(profile),allocation_shape(baseline)
            comparable=a is not None and b is not None
            rows.append({'profile_case_id':case['case_id'],'unprofiled_case_id':baseline['condition']['case_id'],
                'model':case.get('model'),'strategy':case.get('strategy'),'target_tokens':case.get('target_tokens'),
                'clients':case.get('clients'),'status':'observed_allocation_comparison' if comparable else 'final_real_allocation_evidence_incomplete',
                'observed_allocation_difference':a!=b if comparable else None,
                'profile_allocation_shape_json':compact(a),'unprofiled_allocation_shape_json':compact(b),
                'output_stop_policy_differs':case.get('output_stop_policy','natural_eos')!=baseline['condition'].get('output_stop_policy','natural_eos'),
                'requested_output_budget_differs':case.get('max_tokens')!=baseline['condition'].get('max_tokens'),
                'timings_normalized':False,
                'qualification':'Nsight injection before model loading can consume VRAM and alter automatic fitting. Equal logged aggregate buffers do not prove identical tensor placement; output/sampling-policy differences remain qualified. Profile timings never enter ordinary rankings.'})
    return rows


def screen_inputs(paths):
    results, planned, runtime_by_case, provenance = {}, {}, {}, []
    for path in paths:
        data = read(path)
        root = path.parent
        manifest_path = root / "manifest-private.json"
        manifest = read(manifest_path) if manifest_path.exists() else {}
        plan = manifest.get("plan") or {}
        kind = data.get("capture_kind", plan.get("capture_kind"))
        runtime = data.get("runtime") or {}
        for condition in plan.get("cases", []):
            if condition.get("capture_kind") == "screening":
                planned[condition["case_id"]] = condition
        for result in data.get("conditions", []):
            case_id = result["condition"]["case_id"]
            existing=results.get(case_id)
            if existing and compact({k:v for k,v in existing.items() if k not in ('safe_failure_evidence','corrected_trace_analyses','failed_capture_diagnostics','output_stop_evidence','pipeline_evidence','actual_placement_evidence')}) != compact(result):
                raise ValueError("Conflicting captures for caseID " + case_id + "; do not select the fastest repeat")
            if existing and compact(runtime_by_case[case_id])!=compact(runtime):
                raise ValueError('Conflicting capture provenance for duplicate caseID '+case_id)
            evidence=safe_failure_evidence(result,runtime,root)
            if existing and (existing.get('safe_failure_evidence') or {}).get('availability')=='observed_terminal_private_evidence' and evidence.get('availability')!='observed_terminal_private_evidence':
                evidence=existing['safe_failure_evidence']
            result=dict(result,safe_failure_evidence=evidence,
                actual_placement_evidence=actual_placement_evidence(result,root),
                pipeline_evidence=pipeline_evidence(result,root),
                output_stop_evidence=output_stop_evidence(result,root),
                corrected_trace_analyses=corrected_trace_sidecars(result,runtime,root)
                    if result.get('capture_kind')=='profiling' else [],
                failed_capture_diagnostics=failed_capture_diagnostics(result,root))
            results[case_id], runtime_by_case[case_id] = result, runtime
        progress_path = root / "progress.json"
        progress = read(progress_path) if progress_path.exists() else {}
        provenance.append({"source": root.name, "capture_kind": kind,
            "recorded_conditions": len(data.get("conditions", [])),
            "planned_conditions": plan.get("case_count", progress.get("planned_conditions")),
            "finished_conditions": progress.get("completed_conditions"),
            "runtime": runtime})
    return list(results.values()), planned, runtime_by_case, provenance


def sustained_reference_rows(captures):
    """Separate protocol control; never overwrite the original micro curves."""
    rows=[]
    for path,data in captures:
        if data.get('protocol')!='sustained-local-vram-reference-v1':
            raise ValueError('Unknown sustained local-copy diagnostic protocol')
        for card in data.get('cards',[]):
            events,hosts=[],[]
            samples=card.get('samples') or []
            valid=bool(samples)
            copies=card.get('copies_per_sample')
            size=card.get('transfer_bytes')
            for sample in samples:
                routes=sample.get('routes') or []
                route=routes[0] if len(routes)==1 else {}
                duration=route.get('DtoD_gpu_event_ms')
                payload=sample.get('payload_bytes')
                elapsed=sample.get('elapsed_s')
                consistent=(type(copies) is int and 0<copies<=128 and type(size) is int and size==256*1024**2
                    and type(payload) is int and payload==size*copies
                    and route.get('source_gpu')==card.get('gpu_index')
                    and route.get('destination_gpu')==card.get('gpu_index')
                    and route.get('transport')=='DtoD' and route.get('copies')==copies
                    and route.get('payload_bytes')==payload
                    and finite(duration) and duration>0 and finite(elapsed) and elapsed>0)
                valid=valid and consistent
                if consistent:
                    events.append(payload/(duration/1000)/1e9)
                    hosts.append(payload/elapsed/1e9)
            completed=(data.get('status')=='completed' and data.get('cleanup_confirmed') is True
                and card.get('status')=='completed' and (card.get('validation') or {}).get('passed') is True
                and valid and len(samples)==card.get('requested_internal_samples')==15)
            clocks=card.get('clocks') or {}
            row={'capture':path.parent.name,'protocol':data.get('protocol'),
                'capture_status':data.get('status'),'card_status':card.get('status'),
                'gpu_index':card.get('gpu_index'),'transfer_bytes':size,
                'copies_per_sample':copies,'internal_samples':len(samples),
                'target_gpu_event_ms':card.get('target_gpu_event_ms'),
                'sample_denominators_verified':valid,'protocol_control_eligible':completed,
                'cuda_event_payload_GB_s':median(events) if completed else None,
                'synchronized_host_payload_GB_s':median(hosts) if completed else None,
                'sample_cuda_event_payload_GB_s_json':compact(events),
                'sample_host_payload_GB_s_json':compact(hosts),
                'validation_passed':(card.get('validation') or {}).get('passed'),
                'cleanup_confirmed':data.get('cleanup_confirmed'),
                'source_pattern':'Deterministic random64KiB block repeated; historical reference used independent full-random initialization',
                'timing_scope':'Sequential cards; CUDA event batch and synchronized host wall rates are separate payload references',
                'clock_causality_established':False,
                'power_scope':'Both installed boards, including idle other board; warmup/calibration/validation included, not transfer-only energy'}
            for field in ('sm_clock_mhz','memory_clock_mhz'):
                for statistic in ('valid_samples','min','max','median'):
                    row[field+'_'+statistic]=(clocks.get(field) or {}).get(statistic)
            rows.append(row)
    return rows


def energy_window_evidence(window, require_inventory=True):
    """Respect sampler confidence and independently verify retained time coverage."""
    cards=window.get('gpus') or []
    reasons=[]
    if window.get('gpu_power_complete') is not True:reasons.append('incomplete_power_samples')
    if require_inventory and window.get('inventory_complete') is not True:reasons.append('incomplete_inventory')
    if window.get('low_confidence',True):reasons.append('sampler_low_confidence')
    if window.get('raw_retention_truncated') is True:reasons.append('raw_retention_truncated')
    if len(cards)!=2 or {g.get('index') for g in cards}!={0,1}:reasons.append('two_unique_cards_not_verified')
    for name,item in [('global',window)]+[(str(g.get('index')),g) for g in cards]:
        coverage=item.get('gpu_sampled_coverage_fraction')
        if not finite(coverage) or not .8<=coverage<=1.00000001:reasons.append(name+'_coverage_below_80pct_or_unavailable')
        if item.get('gpu_power_complete') is not True:reasons.append(name+'_incomplete_power')
        if item.get('low_confidence',True):reasons.append(name+'_low_confidence')
        if item.get('raw_retention_truncated') is True:reasons.append(name+'_raw_retention_truncated')
    return {'eligible':not reasons,'minimum_sampled_coverage_fraction':.8,
        'exclusions':sorted(set(reasons)),'global_coverage_fraction':window.get('gpu_sampled_coverage_fraction'),
        'per_card_coverage':{str(g.get('index')):g.get('gpu_sampled_coverage_fraction') for g in cards},
        'raw_retention_truncated':window.get('raw_retention_truncated'),
        'interpretation':'Sampler energy extrapolates time-weighted sampled power to the requested interval; accepted coverage is at least 80%, not proof of uninterrupted physical measurement'}


def condition_row(result, runtime):
    case, group = result["condition"], result.get("group") or {}
    requests = result.get("requests") or []
    power = group.get("power") or {}
    precision=kv_precision_evidence(result)
    placement=placement_scope(result)
    stop_policy=case.get('output_stop_policy','natural_eos')
    stop_evidence=result.get('output_stop_evidence') or {}
    stop_eligible=(stop_policy=='natural_eos' or (stop_policy=='ignore_eos_requested_256'
        and stop_evidence.get('verification_source')=='hash_bound_terminal_private_result_and_effective_props'
        and stop_evidence.get('eligible_fixed_output_comparison') is True
        and all(type(r.get('completion_tokens')) is int and r['completion_tokens']==256
            and r.get('completion_tokens_estimated') is False for r in requests)))
    counts_exact = len(requests) == case["clients"] and all(
        type(r.get("prompt_tokens")) is int and r['prompt_tokens']>0 and type(r.get("completion_tokens")) is int and r['completion_tokens']>0
        and not r.get("completion_tokens_estimated", True) for r in requests)
    elapsed=group.get('t_end',0)-group.get('t_start',0) if all(finite(group.get(k)) for k in ('t_start','t_end')) else None
    elapsed_verified=finite(elapsed) and elapsed>0 and finite(group.get('wall_s')) and math.isclose(group['wall_s'],elapsed,rel_tol=1e-8,abs_tol=1e-7)
    output_total=sum(r['completion_tokens'] for r in requests) if counts_exact else None
    cache_ok = all(not (r.get("measurement_validity") or {}).get("positive_cache_reuse_observed", False) for r in requests)
    timing_eligible = (result.get("capture_kind") == "screening" and result.get("status") == "ok"
        and result.get("measured_repeats") == 1 and result.get("actual_context_verified") is True
        and counts_exact and cache_ok and elapsed_verified and all(r.get('status')=='ok' for r in requests))
    # Nonbaseline cache precision is a quality/capacity treatment. Do not label
    # ignored or unverified requests as a measured q8/q4 condition.
    eligible=timing_eligible and precision['comparison_eligible'] and stop_eligible
    energy_evidence=energy_window_evidence(power)
    energy_ok = eligible and group.get("all_card_energy_available") is True and energy_evidence['eligible']
    cards = power.get("gpus") or []
    energy_sum_verified = (finite(power.get("gpu_energy_j")) and power['gpu_energy_j']>=0 and {g.get("index") for g in cards}=={0,1}
        and all(finite(g.get("gpu_energy_j")) and g['gpu_energy_j']>=0 for g in cards)
        and math.isclose(power["gpu_energy_j"],sum(g["gpu_energy_j"] for g in cards),rel_tol=1e-8,abs_tol=1e-6))
    energy_ok = energy_ok and energy_sum_verified and power.get('gpu_power_complete') is True and power.get('inventory_complete') is True
    decode_phase=(group.get('single_request_phases') or {}).get('decode') or {}
    decode_cards=decode_phase.get('gpus') or []
    decode_sum=(finite(decode_phase.get('gpu_energy_j')) and decode_phase['gpu_energy_j']>=0 and {g.get('index') for g in decode_cards}=={0,1}
        and all(finite(g.get('gpu_energy_j')) and g['gpu_energy_j']>=0 for g in decode_cards)
        and math.isclose(decode_phase['gpu_energy_j'],sum(g['gpu_energy_j'] for g in decode_cards),rel_tol=1e-8,abs_tol=1e-6))
    decode_energy_evidence=energy_window_evidence(decode_phase)
    decode_energy_ok=energy_ok and decode_energy_evidence['eligible'] and decode_sum
    decode_tokens=requests[0].get('decode_token_count') if len(requests)==1 else None
    decode_energy_ok=decode_energy_ok and type(decode_tokens) is int and 0<decode_tokens<=output_total
    row = {k:case.get(k) for k in ("case_id", "model", "strategy", "target_tokens", "clients", "context_per_slot",
        "max_tokens", "reasoning", "mtp", "kv_type_k", "kv_type_v", "endpoint_transport")}
    row.update(capture_kind=result.get("capture_kind"), status=result.get("status"),
        whole_case_elapsed_s=case_elapsed(result),observed_group_wall_s=elapsed if elapsed_verified else None,
        measured_repeats=result.get("measured_repeats"), metric_eligible=eligible,
        timing_observation_eligible=timing_eligible,kv_precision_status=precision['status'],
        output_stop_policy=stop_policy,output_stop_policy_verified=stop_eligible,
        output_stop_evidence_json=compact(stop_evidence),
        group_elapsed_denominator_verified=elapsed_verified,
        observed_kv_type_k=precision['observed_k'],observed_kv_type_v=precision['observed_v'],
        kv_precision_evidence_json=compact(precision),
        observed_client_prefill_tps=median([r.get('prefill_tps') for r in requests]) if timing_eligible else None,
        observed_decode_tps=median([r.get('decode_tps') for r in requests]) if timing_eligible else None,
        exact_usage_verified=counts_exact, cache_clean_or_configured=cache_ok,
        model_name=(result.get("model") or {}).get("name"), quant=(result.get("model") or {}).get("quant"),
        prompt_counts_json=compact([r.get("prompt_tokens") for r in requests]),
        output_counts_json=compact([r.get("completion_tokens") for r in requests]),
        prompt_hashes_json=compact([p.get("prompt_sha256") for p in result.get("prompts", [])]),
        prefill_tps=median([r.get("prefill_tps") for r in requests]) if eligible else None,
        decode_tps=median([r.get("decode_tps") for r in requests]) if eligible else None,
        ttft_s=median([r.get("ttft_s") for r in requests]) if eligible else None,
        wall_s=elapsed if eligible else None,
        aggregate_output_tps=output_total/elapsed if eligible else None,
        gpu_energy_j=power.get("gpu_energy_j") if energy_ok else None,
        gpu_energy_per_output_token_including_prefill_j=power['gpu_energy_j']/output_total if energy_ok else None,
        single_request_decode_gpu_energy_per_token_j=decode_phase['gpu_energy_j']/decode_tokens if decode_energy_ok else None,
        energy_eligible=energy_ok, combined_energy_sum_verified=energy_sum_verified,
        energy_window_evidence_json=compact(energy_evidence),
        decode_energy_window_evidence_json=compact(decode_energy_evidence),
        power_raw_retention_truncated=power.get('raw_retention_truncated'),
        power_sampled_coverage_fraction=power.get('gpu_sampled_coverage_fraction'),
        physical_installed_gpu_indices_json='[0,1]',
        execution_gpu_scope=('5070 Ti execution; 5060 Ti installed and idle' if case.get('strategy')=='single5070'
            else '5060 Ti execution; 5070 Ti installed and idle' if case.get('strategy')=='single5060'
            else 'Both installed GPUs; actual execution/placement recorded separately'),
        gpu_energy_scope='Both physically installed boards, including an idle nonparticipating board',
        power_low_confidence=power.get("low_confidence"),
        global_vram_peak_mb=power.get("vram_mb_max"), whole_machine_cpu_util_fraction=power.get("cpu_util_avg"),
        placement_json=compact(result.get("placement", [])), backend_timings_json=compact(result.get("backend_timings", [])),
        failure_evidence_availability=(result.get('safe_failure_evidence') or {}).get('availability','unavailable'),
        failure_reason_codes=','.join(f.get('code','unavailable') for f in (result.get('safe_failure_evidence') or {}).get('findings',[])
            if f.get('condition_failure_supported',f.get('evidence')=='public_request_measurement_validity')),
        failure_constraints_json=compact(result.get('safe_failure_evidence') or {}),
        placement_interpretation_json=compact(placement),
        actual_placement_evidence_json=compact(result.get('actual_placement_evidence') or {}),
        core_reported_cpu_offload_json=compact([p.get('cpu_offload') for p in result.get('placement',[])]),
        core_reported_offloaded_layers_json=compact([p.get('offloaded_layers') for p in result.get('placement',[])]),
        pipeline_evidence_json=compact(result.get('pipeline_evidence') or {}),
        gemma_weight_colocation_on_5070=placement['gemma_weight_colocation_on_5070'],
        actual_kv_locality_verified=None)
    # Reviewed configuration adapters register extra placement strategies while
    # retaining the fingerprinted measurement/client/power implementation. Their
    # definitions and own hashes remain explicit treatment/provenance fields.
    row['strategy_definition_json']=compact(case.get('strategy_definition') or {})
    row['configuration_wrapper']=case.get('configuration_wrapper')
    row['configuration_wrapper_sha256']=case.get('configuration_wrapper_sha256')
    row['configuration_stack_json']=compact(case.get('configuration_stack') or [])
    row['filesystem_cache_state_verified']=False
    row['cpu_mmap_weights_prefaulted_verified']=False
    row['host_weight_residency_qualification']='Eight-token per-engine warmup does not establish all CPU-mapped layers or rare experts prefaulted; filesystem-cache equality is unknown'
    row['kv_precision_treatment_json']=compact(case.get('kv_precision_treatment') or {})
    omitted={'strategy','case_id','capture_kind','measured_repeats'}
    if str(case.get('configuration_wrapper','')).replace('\\','/') in ('untracked/run_row_screen.py','untracked/run_kv_screen.py'):
        omitted.update(('strategy_definition','configuration_wrapper','configuration_wrapper_sha256'))
    if str(case.get('configuration_wrapper','')).replace('\\','/')=='untracked/run_fixed_output_screen.py':
        # Row adapter entries describe the intended placement treatment. The
        # fixed wrapper SHA, stop policy, sampling and output contract remain in
        # config, preventing natural/fixed or changed-wrapper pooling.
        omitted.update(('strategy_definition','configuration_stack'))
    config = {k:v for k,v in case.items() if k not in omitted}
    config.update(quant=row["quant"], model_identity=(runtime.get("model_files") or {}).get(case["model"]),
                  executable_sha256=runtime.get("executable_sha256"), cuda_overrides=runtime.get("cuda_overrides"),
                  source_sha256=runtime.get('source_sha256'),gpu_inventory=runtime.get('gpu_mapping'),
                  host_hardware=runtime.get("host_hardware"))
    row["matched_condition_signature"] = hashlib.sha256(compact(config).encode()).hexdigest()
    row["chart_series_signature"] = hashlib.sha256(compact({k:v for k,v in config.items()
        if k not in ("target_tokens","context_per_slot")}).encode()).hexdigest()[:8]
    return row


def case_elapsed(result):
    try:
        start=datetime.fromisoformat(str(result['started_at']).replace('Z','+00:00'))
        end=datetime.fromisoformat(str(result['finished_at']).replace('Z','+00:00'))
        seconds=(end-start).total_seconds()
        return seconds if start.tzinfo and end.tzinfo and seconds>=0 else None
    except (KeyError,ValueError,TypeError):return None


def kv_precision_evidence(result):
    case=result['condition'];observed={'k':set(),'v':set()}
    kinds=r'(f16|f32|bf16|q8_0|q4_0|q4_1|q5_0|q5_1|iq4_nl)'
    final=(result.get('actual_placement_evidence') or {}).get('servers') or []
    final_types=bool(final) and all(s.get('kv_context_epoch_isolated') is True
        and all((s.get('kv_cache_types_final_real_context') or {}).get(a) for a in ('k','v')) for s in final)
    if final_types:
        for server in final:
            for axis in ('k','v'):observed[axis].update(server['kv_cache_types_final_real_context'][axis])
    for placement in [] if final_types else result.get('placement',[]):
        for line in placement.get('raw_lines',[]):
            # Allocator/cache diagnostics only, never a command's requested flag
            # or a weight-tensor dtype. No arbitrary line is copied to outputs.
            if not re.search(r'llama_(?:kv_cache|memory)|KV\s+(?:cache|buffer)',line,re.I):continue
            if re.search(r'recurrent|\bssm\b|mamba',line,re.I) and 'kv_cache' not in line.lower():continue
            for axis in ('k','v'):
                matches=re.findall(r'\btype_'+axis+r'\s*=\s*[\'\"]?'+kinds+r'\b',line,re.I)
                matches+=re.findall(r'\b'+axis+r'\s*\(\s*'+kinds+r'\s*\)',line,re.I)
                observed[axis].update(m.lower() for m in matches)
    requested=(case.get('kv_type_k'),case.get('kv_type_v'))
    actual=tuple(next(iter(observed[a])) if len(observed[a])==1 else None for a in ('k','v'))
    mixed=any(len(observed[a])>1 for a in ('k','v'))
    if mixed:status='mixed_reported_cache_types'
    elif all(actual):status='observed_cache_types_match_request' if actual==requested else 'observed_cache_types_conflict_with_request'
    else:status='requested_cache_types_actual_unverified'
    baseline=requested==('f16','f16')
    eligible=status=='observed_cache_types_match_request' or (baseline and status=='requested_cache_types_actual_unverified')
    return {'status':status,'requested_k':requested[0],'requested_v':requested[1],
        'observed_k':actual[0],'observed_v':actual[1],'reported_k_values':sorted(observed['k']),
        'reported_v_values':sorted(observed['v']),'comparison_eligible':eligible,
        'scope':'Final real KV-context allocator diagnostics' if final_types else 'Explicit cache allocator diagnostics; GGUF weight dtype is not KV-cache evidence',
        'qualification':'Baseline F16 request may remain qualified when cache diagnostics are unavailable; nonbaseline precision requires explicit matching cache evidence'}


RATIO_METRICS = ("prefill_tps", "decode_tps", "ttft_s", "wall_s", "aggregate_output_tps",
                 "gpu_energy_per_output_token_including_prefill_j", "single_request_decode_gpu_energy_per_token_j")


def pair_strategies(rows, baseline_strategy='single5070'):
    pairs = []
    baselines = defaultdict(list)
    for row in rows:
        if row["capture_kind"] == "screening" and row["strategy"] == baseline_strategy:
            baselines[row["matched_condition_signature"]].append(row)
    for row in rows:
        if row["capture_kind"] != "screening" or row["strategy"] == baseline_strategy:
            continue
        candidates = baselines[row["matched_condition_signature"]]
        before = candidates[0] if len(candidates) == 1 else None
        reasons = []
        if before is None: reasons.append("no_unique_matched_"+baseline_strategy+"_baseline")
        elif not before["metric_eligible"] or not row["metric_eligible"]: reasons.append("failed_unverified_or_censored_condition")
        elif row["prompt_hashes_json"] != before["prompt_hashes_json"] or row["prompt_counts_json"] != before["prompt_counts_json"]:
            reasons.append("actual_prompt_hash_or_count_mismatch")
        pair = {k:row.get(k) for k in ("case_id", "model", "quant", "strategy", "target_tokens", "clients",'kv_type_k','kv_type_v','kv_precision_status','output_stop_policy')}
        pair['condition_group']=row.get('chart_series_signature')
        pair.update(baseline_case_id=before["case_id"] if before else None,
                    baseline_strategy=baseline_strategy,
                    base_status=before["status"] if before else "absent", status=row["status"],
                    configuration_exclusions=compact(reasons))
        pair['actual_output_counts_match']=bool(before and row['output_counts_json']==before['output_counts_json'])
        pair['actual_output_counts_json']=row.get('output_counts_json')
        pair['baseline_output_counts_json']=before.get('output_counts_json') if before else None
        for metric in RATIO_METRICS:
            why = list(reasons)
            if "energy" in metric and (not row["energy_eligible"] or not before or not before["energy_eligible"]):
                why.append("incomplete_or_low_confidence_all_card_energy")
            a,b = row.get(metric), before.get(metric) if before else None
            if not finite(a) or not finite(b) or b <= 0:why.append("metric_unavailable")
            # Preserve explicitly descriptive same-budget comparisons without
            # weakening the strict actual-output-matched ratio columns.
            pair[metric+'_descriptive_same_budget_ratio']=a/b if not why else None
            pair[metric+'_descriptive_qualification']=('Actual output counts differ; early EOS is allowed and no forced fixed-output policy was used'
                if before and not pair['actual_output_counts_match'] and metric not in ('prefill_tps','ttft_s') else None)
            if metric not in ('prefill_tps','ttft_s') and before and not pair['actual_output_counts_match']:
                why.append("actual_output_count_mismatch")
            pair[metric+"_ratio"] = a/b if not why else None
            pair[metric+"_exclusions"] = compact(why)
        pairs.append(pair)
    return pairs


def telemetry_rows(results):
    rows = []
    for result in results:
        case, group = result["condition"], result.get("group") or {}
        telemetry = group.get("telemetry") or {}
        powers = {p['index']:p for p in (group.get("power") or {}).get("gpus", []) if type(p.get('index')) is int}
        gpus = {g['gpu_index']:g for g in telemetry.get("gpus", []) if type(g.get('gpu_index')) is int}
        for index in sorted(set(powers) | set(gpus)):
            row = {k:case.get(k) for k in ("case_id", "model", "strategy", "target_tokens", "clients")}
            row.update(capture_kind=result.get("capture_kind"), gpu_index=index,
                counter_samples=telemetry.get("sample_count"), telemetry_dropped=telemetry.get("retention_dropped_samples"))
            for field in ("gpu_energy_j", "gpu_w_avg", "gpu_w_max", "gpu_util_avg", "vram_mb_max", "temp_c_max", "clock_mhz_avg", "gpu_sampled_coverage_fraction"):
                row[field] = powers.get(index, {}).get(field)
            for field, stats in (gpus.get(index, {}).get("metrics") or {}).items():
                for statistic in ("valid_samples", "min", "max", "mean"):
                    row[field+"_"+statistic] = stats.get(statistic)
            links = sorted({(g.get("pcie_current_generation"),g.get("pcie_current_width"))
                for sample in telemetry.get("samples", []) for g in sample.get("gpus", [])
                if g.get("gpu_index")==index and finite(g.get("pcie_current_generation")) and finite(g.get("pcie_current_width"))})
            row["under_workload_negotiated_link_pairs_json"] = compact(links)
            row["process_metrics_json"] = compact([{k:v for k,v in p.items() if k != "pid"} for p in telemetry.get("processes", [])])
            row["system_paging_json"] = compact(telemetry.get("paging", {}))
            rows.append(row)
    return rows


def failed_sensor_rows(results):
    rows=[]
    for result in results:
        evidence=result.get('failed_capture_diagnostics')
        if not evidence:continue
        power=evidence.get('power_summary') or {};diagnostic=evidence.get('diagnostic_summary') or {}
        devices={g['index']:g for g in power.get('gpus',[]) if type(g.get('index')) is int}
        counters={g['gpu_index']:g for g in diagnostic.get('gpus',[]) if type(g.get('gpu_index')) is int}
        for index in sorted(set(devices)|set(counters)):
            rows.append({**{k:result['condition'].get(k) for k in ('case_id','model','strategy','target_tokens','clients')},
                'outcome':result.get('status'),'gpu_index':index,'scope':evidence['scope'],
                'aligned_group_energy_available':False,'whole_capture_power_metrics_json':compact(devices.get(index,{}).get('metrics',{})),
                'whole_capture_counter_metrics_json':compact(counters.get(index,{}).get('metrics',{})),
                'whole_capture_paging_json':compact(diagnostic.get('paging',{})),
                'power_summary_json':compact({k:v for k,v in power.items() if k not in ('gpus','samples')}),
                'rate_or_energy_per_token_derived':False})
    return rows


def client_phase_rows(results):
    rows=[]
    for result in results:
        if result.get('capture_kind')!='profiling' or len(result.get('requests',[]))!=1:continue
        request=result['requests'][0]
        start,first,end=(request.get(key) for key in ('t_start','t_first_token','t_end'))
        if not all(finite(v) for v in (start,first,end)) or not start<=first<=end:continue
        for sample in (result.get('group',{}).get('telemetry') or {}).get('samples',[]):
            timestamp=sample.get('t_perf')
            if not finite(timestamp):continue
            phase='before_request' if timestamp<start else 'client_prefill_TTFT' if timestamp<first else 'client_decode' if timestamp<=end else 'after_request'
            processes=[{'owned_process_ordinal':i,'cpu_percent_machine':p.get('cpu_percent_machine')}
                for i,p in enumerate(sample.get('processes',[]))]
            for card in sample.get('gpus',[]):
                row={k:result['condition'].get(k) for k in ('case_id','model','strategy','target_tokens','clients')}
                row.update(relative_client_time_s=timestamp-start,client_phase=phase,client_TTFT_s=first-start,
                    client_wall_s=end-start,gpu_index=card.get('gpu_index'),owned_process_cpu_json=compact(processes),
                    clock_alignment='Client request and DiagnosticTelemetry share host perf_counter; Nsight clock alignment unavailable')
                for field in ('pcie_rx_kb_s','pcie_tx_kb_s','dedicated_bytes','shared_bytes'):row[field]=card.get(field)
                rows.append(row)
    return rows


def client_phase_charts(prefix,rows):
    if not rows:return []
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    outputs=[]
    for case in sorted({r['case_id'] for r in rows}):
        samples=[r for r in rows if r['case_id']==case];first=samples[0]
        fig,axes=plt.subplots(3,1,figsize=(11,8),sharex=True,layout='constrained')
        for axis in axes:
            axis.axvspan(0,first['client_TTFT_s'],color='#69b4cd',alpha=.12,label='Client TTFT window')
            axis.axvspan(first['client_TTFT_s'],first['client_wall_s'],color='#70b98a',alpha=.12,label='Client decode window')
            axis.axvline(first['client_TTFT_s'],color='#555',linestyle='--',linewidth=1);axis.grid(alpha=.15)
        for device in sorted({r['gpu_index'] for r in samples if type(r.get('gpu_index')) is int}):
            data=[r for r in samples if r['gpu_index']==device]
            for field in ('pcie_rx_kb_s','pcie_tx_kb_s'):
                valid=[r for r in data if finite(r.get(field))]
                if valid:axes[0].plot([r['relative_client_time_s'] for r in valid],[r[field] for r in valid],label=f'GPU{device} {field}')
            for field in ('dedicated_bytes','shared_bytes'):
                valid=[r for r in data if finite(r.get(field))]
                if valid:axes[1].plot([r['relative_client_time_s'] for r in valid],[r[field]/2**30 for r in valid],label=f'GPU{device} {field}')
        unique={r['relative_client_time_s']:r for r in samples}
        processes=defaultdict(list)
        for timestamp,row in sorted(unique.items()):
            for process in json.loads(row['owned_process_cpu_json']):
                if finite(process.get('cpu_percent_machine')):processes[process['owned_process_ordinal']].append((timestamp,process['cpu_percent_machine']))
        for ordinal,data in sorted(processes.items()):axes[2].plot([p[0] for p in data],[p[1] for p in data],label=f'Owned model process {ordinal}')
        for axis in axes:
            if axis.lines and len(axis.lines)>1:axis.legend(fontsize=7)
            else:axis.text(.5,.5,'Counter unavailable',ha='center',va='center',transform=axis.transAxes)
        axes[0].set_ylabel('NVML raw KB/s');axes[1].set_ylabel('WDDM allocation GiB');axes[2].set_ylabel('CPU % of machine')
        axes[2].set_xlabel('Seconds from client request start')
        fig.suptitle(first['model']+' / '+first['strategy']+' — client-boundary telemetry (profiled)\nNo Nsight event alignment; sampling windows can straddle phase boundaries')
        path=prefix.with_name(prefix.name+'-client-phase-'+case+'.png');fig.savefig(path,dpi=170,bbox_inches='tight');plt.close(fig);outputs.append(path)
    return outputs


def micro_telemetry_rows(captures):
    rows=[]
    for capture,(_,data) in enumerate(captures):
        wrapper=data.get('wrapper_capture') or {}
        telemetry=wrapper.get('telemetry.json') or {}
        diagnostic=telemetry.get('diagnostic') or {}
        power=telemetry.get('measured_power_window') or {}
        powers={g['index']:g for g in power.get('gpus',[]) if type(g.get('index')) is int}
        gpus={g['gpu_index']:g for g in diagnostic.get('gpus',[]) if type(g.get('gpu_index')) is int}
        windows=[(sample['host_start_ns']/1e9,sample['host_end_ns']/1e9)
            for measurement in data.get('measurements',[]) for sample in measurement.get('samples',[])
            if finite(sample.get('host_start_ns')) and finite(sample.get('host_end_ns'))]
        merged=[]
        for a,b in sorted(windows):
            if merged and a<=merged[-1][1]:merged[-1]=(merged[-1][0],max(merged[-1][1],b))
            else:merged.append((a,b))
        starts=[a for a,_ in merged]
        for index in sorted(set(powers)|set(gpus)):
            row={'capture':capture,'capture_status':micro_status(data),'gpu_index':index,
                'power_scope':telemetry.get('power_scope','unavailable'),
                'diagnostic_sample_count':diagnostic.get('sample_count'),
                'wrapper_counter_availability':'available' if diagnostic else 'unavailable'}
            for field in ('gpu_energy_j','gpu_w_avg','gpu_w_max','gpu_util_avg','vram_mb_max','temp_c_max','clock_mhz_avg','gpu_sampled_coverage_fraction'):
                row[field]=powers.get(index,{}).get(field)
            for field,stats in (gpus.get(index,{}).get('metrics') or {}).items():
                for statistic in ('valid_samples','min','max','mean'):row[field+'_'+statistic]=stats.get(statistic)
            all_pairs=set();timed_pairs=set();sample_count=0
            for sample in diagnostic.get('samples',[]):
                timestamp=sample.get('t_perf')
                position=bisect.bisect_right(starts,timestamp)-1 if finite(timestamp) else -1
                timed=position>=0 and timestamp<=merged[position][1]
                for card in sample.get('gpus',[]):
                    if card.get('gpu_index')!=index:continue
                    pair=(card.get('pcie_current_generation'),card.get('pcie_current_width'))
                    if not all(finite(v) for v in pair):continue
                    all_pairs.add(pair)
                    if timed:timed_pairs.add(pair);sample_count+=1
            row['diagnostic_interval_link_pairs_json']=compact(sorted(all_pairs))
            row['inside_actual_timed_copy_link_pairs_json']=compact(sorted(timed_pairs))
            row['inside_actual_timed_copy_link_samples']=sample_count
            rows.append(row)
        if not diagnostic:
            rows.append({'capture':capture,'capture_status':micro_status(data),'wrapper_counter_availability':'unavailable',
                         'power_scope':'Flat/manual probe or missing wrapper telemetry; no extra counter values invented'})
    return rows


def host_pin_rows(captures):
    rows=[]
    for capture,(_,data) in enumerate(captures):
        protocol=data.get('protocol') or {}
        pooled=data.get('capture_kind')=='diagnostic_pooled_host_allocation'
        cleanup=data.get('cleanup') or {}
        final_releases=(cleanup.get('host_cleanup') or {}).get('releases') or []
        allocated=[a for a in data.get('attempts',[]) if (a.get('allocation') or {}).get('success') is True]
        offsets=[]
        if pooled and allocated:
            for offset in (0,-1,1):
                matches=[[r for r in final_releases if type(a.get('chunk_index')) is int
                    and r.get('allocation_index')==a['chunk_index']+offset and r.get('size_bytes')==a.get('size_bytes')]
                    for a in allocated]
                if all(len(items)==1 for items in matches) and len({items[0]['allocation_index'] for items in matches})==len(allocated):
                    offsets.append(offset)
        release_offset=offsets[0] if len(offsets)==1 else None
        for attempt in data.get('attempts',[]):
            before=attempt.get('memory_before') or {}
            allocation=attempt.get('allocation') or {}
            release=(next((r for r in final_releases if release_offset is not None and type(attempt.get('chunk_index')) is int
                and r.get('allocation_index')==attempt['chunk_index']+release_offset and r.get('size_bytes')==attempt.get('size_bytes')), {})
                if pooled else attempt.get('release') or {})
            rows.append({'capture':capture,'capture_kind':data.get('capture_kind'),'capture_status':data.get('status'),
                'allocation_test_type':'pooled_concurrent_chunks' if pooled else 'sequential_single_allocation',
                'chunk_index':attempt.get('chunk_index'),
                'gpu_index':(data.get('physical_gpu') or {}).get('index'),
                'gpu_name':(data.get('physical_gpu') or {}).get('name'),
                'driver_version':(data.get('physical_gpu') or {}).get('driver_version'),
                'api':protocol.get('api'),'flags':protocol.get('flags'),
                'context_scope':protocol.get('context') or (data.get('driver') or {}).get('context'),
                'driver_visible_device_count':(data.get('driver') or {}).get('visible_device_count'),
                'size_bytes':attempt.get('size_bytes'),'status':attempt.get('status'),
                'allocation_code':allocation.get('code'),'allocation_name':allocation.get('name'),
                'allocation_success':allocation.get('success'),'allocation_wall_s':attempt.get('allocation_wall_s'),
                'physical_available_bytes_before':before.get('physical_available_bytes'),
                'commit_headroom_bytes_before':before.get('commit_headroom_bytes'),
                'headroom_gate_allowed':(attempt.get('gate') or {}).get('allowed'),
                'held_bytes_before_step':attempt.get('held_bytes_before'),
                'held_bytes_after_step':attempt.get('held_bytes_after'),
                'held_allocation_count_after_step':attempt.get('held_allocation_count_after'),
                'ownership_certain_after_step':attempt.get('ownership_certain'),
                'held_bytes_before_final_cleanup':data.get('held_bytes_before_cleanup'),
                'sequence_outcome':data.get('sequence_outcome'),
                'release_confirmed':release.get('release_confirmed'),
                'release_required':allocation.get('success') is True if pooled else release.get('required'),
                'release_code':(release.get('release') or {}).get('code'),
                'release_mapping_index_offset':release_offset if pooled else None,
                'release_mapping_evidence':'index offset and requested size verified across all held chunks' if pooled and release_offset is not None else 'per-attempt release' if not pooled else 'per-chunk mapping unavailable; inspect global cleanup separately',
                'release_wall_s':release.get('release_wall_s'),
                'held_confirmed_bytes_after_release':release.get('held_confirmed_bytes_after'),
                'global_host_release_confirmed':cleanup.get('host_release_confirmed') if pooled else (cleanup.get('host_cleanup') or {}).get('release_confirmed'),
                'context_destroyed':cleanup.get('context_destroyed'),
                'context_cleanup_confirmed':cleanup.get('confirmed'),
                'remaining_host_allocations':cleanup.get('remaining_host_allocations'),
                'unreleased_requested_bytes':cleanup.get('unreleased_requested_bytes'),
                'buffers_touched':protocol.get('host_buffers_touched'),'copies_or_kernels':protocol.get('copies_or_kernels'),
                'native_comparison':protocol.get('native_comparison'),
                'inference_repeat_count':None,'normal_aggregate_eligible':False})
    return rows


def trace_rows(results):
    rows = []
    def summaries(item):
        if isinstance(item,list):
            for child in item:yield from summaries(child)
        elif isinstance(item,dict):
            yield item
    for result in results:
        if result.get("capture_kind") != "profiling":continue
        case = result["condition"]
        corrected=result.get('corrected_trace_analyses') or []
        for index, summary in enumerate(summaries(corrected or result.get("trace_exports", []))):
            copies = [g for g in summary.get("activity_groups", []) if g.get("kind")=="memcpy"]
            waits = [g for g in summary.get("activity_groups", []) if g.get("synchronization_or_wait_api")]
            rows.append({**{k:case.get(k) for k in ("case_id","model","strategy","target_tokens","clients")},
                "trace_index":index, "status":summary.get("status", "unavailable"),
                "profile_overhead_quantified":False, "normal_aggregate_eligible":False,
                "trace_span_ns":summary.get("trace_span_ns"), "event_counts_json":compact(summary.get("event_counts", {})),
                'analysis_source':'enum_corrected_sidecar' if corrected else 'archived_memory_labels_unverified',
                'memory_enum_status':(((summary.get('analysis_provenance') or {}).get('enum_provenance') or {}).get('memory') or {}).get('status','unverified'),
                'trace_phase_scope':'whole request collection; no verified transform from exported trace clock to client phase clock',
                "logical_requested_copy_bytes":sum(g.get("bytes",0) for g in copies) if 'memcpy' in summary.get('event_counts',{}) else None,
                'copy_event_consistency_checks_json':compact(summary.get('copy_event_consistency_checks',[])),
                "wait_api_duration_sum_ns":sum(g.get("total_duration_ns",0) for g in waits)
                    if any(k in summary.get('event_counts',{}) for k in ('runtime','driver')) else None,
                "device_activity_json":compact(summary.get("device_activity", [])),
                "unavailable_json":compact(summary.get("unavailable", ["Trace export unavailable; raw reason remains private"]))})
    return rows


def backend_rows(results):
    rows=[]
    for result in results:
        for server,observation in enumerate(result.get('backend_timings',[])):
            parsed=observation.get('parsed') or [{}]
            for timing in parsed:
                rows.append({**{k:result['condition'].get(k) for k in ('case_id','model','strategy','target_tokens','clients')},
                    'capture_kind':result.get('capture_kind'),'server_ordinal':server,
                    'phase':timing.get('phase'),'backend_elapsed_ms':timing.get('elapsed_ms'),
                    'backend_count':timing.get('count'),'backend_tokens_per_second':timing.get('tokens_per_second'),
                    'request_assignment':observation.get('request_assignment','unassigned'),
                    'raw_timing_lines_json':compact(observation.get('raw_lines',[]))})
    return rows


def charts(prefix, micro, rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    figures = []
    models_public={'gemmae2b':'Gemma4 E2B','lfm25':'LFM2.5-24B-A2B',
        'qwen27':'Qwen3.8-27B','qwen35':'Qwen3.6-35B-A3B'}
    strategy_public={'single5070':'5070 Ti only','single5060':'5060 Ti only',
        'layer_equal':'Layer50/50','layer_favor5070':'Layer67/33',
        'gemma_kv_local':'Reverse layer33/67','tensor_equal':'Tensor50/50',
        'independent':'One instance / GPU','row_equal':'Row50/50','row_favor5070':'Row67/33'}
    strategy_order=list(strategy_public)
    palette=plt.get_cmap('tab10')
    strategy_colors={name:palette(i%10) for i,name in enumerate(strategy_order)}
    route_public={'HtoD0':'RAM →5070 Ti','HtoD1':'RAM →5060 Ti',
        'DtoH0':'5070 Ti →RAM','DtoH1':'5060 Ti →RAM',
        'DtoD0':'5070 Ti local VRAM','DtoD1':'5060 Ti local VRAM',
        'cross_0_to_1':'5070 →5060 (host staged)','cross_1_to_0':'5060 →5070 (host staged)',
        'simultaneous_HtoD_equal':'RAM →both, equal bytes',
        'simultaneous_HtoD_weighted_2_1':'RAM →both,2:1 bytes',
        'simultaneous_DtoH_equal':'Both →RAM, equal bytes',
        'bidirectional_cross':'Both cross-card directions (host staged)',
        'mixed_HtoD0_DtoH1':'RAM →5070 +5060 →RAM',
        'mixed_DtoH0_HtoD1':'5070 →RAM +RAM →5060'}
    full = [r for r in micro if r["capture_scope"]=="full" and r["capture_status"]=="completed" and r["validation_passed"]]
    if full:
        fig, axes = plt.subplots(1,2,figsize=(15,7.7))
        route_keys=sorted({(r['capture'],r['case']) for r in full})
        route_colors={key:plt.get_cmap('tab20')(i%20) for i,key in enumerate(route_keys)}
        legend={}
        for axis,mode,metric,label in ((axes[0],"batched_throughput","median_joint_payload_GB_s","Joint payload GB/s"),
                                      (axes[1],"synchronized_group_latency","median_joint_host_us","Joint host latency µs")):
            for capture,name in sorted({(r["capture"],r["case"]) for r in full}):
                data = sorted([r for r in full if r["capture"]==capture and r["case"]==name and r["mode"]==mode and finite(r[metric])],key=lambda r:r["transfer_bytes"])
                label_name=route_public.get(name,name)+(f" capture{capture}" if len({r['capture'] for r in full})>1 else "")
                style=('--' if name.startswith('DtoH') else ':' if name.startswith('DtoD') else '-.'
                    if name.startswith('cross_') or name=='bidirectional_cross' else (0,(5,1))
                    if name.startswith('simultaneous_') else (0,(3,1,1,1)) if name.startswith('mixed_') else '-')
                if data:
                    line,=axis.plot([r["transfer_bytes"] for r in data],[r[metric] for r in data],
                        marker='.',linestyle=style,color=route_colors[(capture,name)],label=label_name)
                    legend[label_name]=line
            axis.set_xscale("log");axis.set_yscale("log");axis.set_xlabel("Bytes per copy / route");axis.set_ylabel(label);axis.grid(alpha=.2)
        axes[0].set_title('Measured simultaneous payload / synchronized wall time')
        axes[1].set_title('Barrier release to all synchronized workers complete')
        fig.suptitle('CUDA communication paths · synchronized transfer measurements',fontsize=15,y=.98)
        fig.legend(list(legend.values()),list(legend),loc='lower center',ncol=3,fontsize=8,
            bbox_to_anchor=(.5,.015),frameon=False)
        fig.subplots_adjust(bottom=.30,top=.88,wspace=.25)
        path = prefix.with_name(prefix.name+"-transfers.png")
        fig.savefig(path,dpi=170,bbox_inches="tight");plt.close(fig);figures.append(path)
    eligible = [r for r in rows if r["metric_eligible"] and r["capture_kind"]=="screening"]
    metrics = [("prefill_tps",1,"Client prompt tokens / TTFT (tokens/s)"),
        ("decode_tps",1,"Single-request decode tokens/s"), ("ttft_s",1,"Client TTFT seconds"),
        ("aggregate_output_tps",2,"Two-client output tokens / group wall seconds"),
        ("gpu_energy_per_output_token_including_prefill_j",2,"Both cards: GPU joules / output token, including prefill"),
        ("single_request_decode_gpu_energy_per_token_j",1,"Both cards: decode GPU joules / token")]
    models = sorted({r["model"] for r in eligible})
    for metric,clients,label in metrics:
        if not any(r["clients"]==clients and finite(r.get(metric)) for r in eligible):continue
        panel_rows=max(1,math.ceil(len(models)/2))
        fig,axes=plt.subplots(panel_rows,2,figsize=(15,max(5.6,4.1*panel_rows+1.5)),squeeze=False)
        legend={}
        metric_rows=[r for r in eligible if r['clients']==clients and finite(r.get(metric))]
        caches={(str(r.get('kv_type_k')),str(r.get('kv_type_v'))) for r in metric_rows}
        stops={r.get('output_stop_policy','natural_eos') for r in metric_rows}
        for axis,model in zip(axes.flat,models):
            series={(r["strategy"],r["chart_series_signature"]) for r in eligible if r["model"]==model and r["clients"]==clients}
            for strategy,signature in sorted(series):
                values=sorted([r for r in eligible if r["model"]==model and r["strategy"]==strategy and r["chart_series_signature"]==signature and r["clients"]==clients and finite(r.get(metric))],key=lambda r:r["target_tokens"])
                cache=(str(values[0].get('kv_type_k'))+'/'+str(values[0].get('kv_type_v'))) if values else 'unreported'
                fixed=bool(values and values[0].get('output_stop_policy')=='ignore_eos_requested_256')
                quantized=bool(values and (values[0].get('kv_type_k'),values[0].get('kv_type_v'))!=('f16','f16'))
                label_name=strategy_public.get(strategy,strategy)
                if len(caches)>1:label_name+=' · KV '+cache
                if len(stops)>1:label_name+=' · '+('fixed256' if fixed else 'natural EOS')
                peers=[s for s in series if s[0]==strategy]
                if len(peers)>len(caches)*len(stops):label_name+=' · cfg'+str(signature)
                if values:
                    line,=axis.plot([r["target_tokens"] for r in values],[r[metric] for r in values],
                        marker='^' if fixed else 'o',linestyle='-.' if fixed and quantized else '--' if fixed else ':' if quantized else '-',
                        color=strategy_colors.get(strategy,'#666666'),label=label_name)
                    legend[label_name]=line
            quants=sorted({str(r.get('quant')) for r in metric_rows if r['model']==model})
            axis.set_title(models_public.get(model,model)+' · '+', '.join(quants),fontsize=12)
            axis.set_xlabel("Prompt target tokens");axis.set_ylabel(label);axis.set_xscale("log");axis.grid(alpha=.2)
            ticks=sorted({512,20000,80000}|{r['target_tokens'] for r in metric_rows if r['model']==model})
            axis.set_xticks(ticks,labels=[str(n) if n<1000 else f'{n/1000:g}k' for n in ticks])
            axis.minorticks_off();axis.set_ylim(bottom=0)
        for axis in list(axes.flat)[len(models):]:axis.set_visible(False)
        cache_caption=('Requested K/V '+ '/'.join(next(iter(caches))) if len(caches)==1 else 'K/V precision distinguishes separate curves')
        eos_caption=('fixed256; EOS suppressed' if stops=={'ignore_eos_requested_256'} else 'natural EOS; variable actual output counts'
            if stops=={'natural_eos'} else 'Natural EOS and fixed256 are separate curves')
        subtitle=('Both boards installed; energy includes the idle other board' if 'energy' in metric
            else 'Both boards installed; single-GPU strategies use one card')
        fig.suptitle(label+' · '+str(clients)+' client'+('s' if clients!=1 else ''),fontsize=15,y=.985)
        fig.text(.5,.945,cache_caption+' · '+eos_caption+'\n'+subtitle+' · One repeat; split fractions are requested',ha='center',va='top',fontsize=9)
        legend_rows=max(1,math.ceil(len(legend)/4))
        fig.legend(list(legend.values()),list(legend),loc='lower center',ncol=min(4,max(1,len(legend))),
            fontsize=9,bbox_to_anchor=(.5,.01),frameon=False)
        fig.subplots_adjust(bottom=.08+.028*legend_rows,top=.85,hspace=.40,wspace=.29)
        path=prefix.with_name(prefix.name+"-"+metric+".png")
        fig.savefig(path,dpi=170,bbox_inches="tight");plt.close(fig);figures.append(path)
    return figures


def recommendation_rows(rows):
    groups=defaultdict(list)
    for row in rows:
        if row["metric_eligible"] and row["capture_kind"]=="screening":
            groups[(row["model"],row["target_tokens"],row["clients"],row["matched_condition_signature"])].append(row)
    out=[]
    for (model,target,clients,signature),cases in sorted(groups.items()):
        for goal,metric,maximize in (("lowest_request_wall_time","wall_s",False),
            ("highest_concurrent_throughput","aggregate_output_tps",True),
            ("lowest_GPU_energy_per_output_token_including_prefill","gpu_energy_per_output_token_including_prefill_j",False)):
            if (goal=="lowest_request_wall_time" and clients!=1) or (goal=="highest_concurrent_throughput" and clients!=2):continue
            candidates=[r for r in cases if finite(r.get(metric))]
            # Recommendations require identical actual prompt hashes/counts, not target labels alone.
            vectors={(r["prompt_hashes_json"],r["prompt_counts_json"]) for r in candidates}
            if len(candidates)<2 or len(vectors)!=1:continue
            if goal!='highest_concurrent_throughput' and len({r['output_counts_json'] for r in candidates})!=1:continue
            output_vectors={r['output_counts_json'] for r in candidates}
            winner=(max if maximize else min)(candidates,key=lambda r:r[metric])
            out.append(dict(model=model,target_tokens=target,clients=clients,goal=goal,
                quant=winner.get('quant'),kv_type_k=winner.get('kv_type_k'),kv_type_v=winner.get('kv_type_v'),
                kv_precision_status=winner.get('kv_precision_status'),condition_group=winner.get('chart_series_signature'),
                output_stop_policy=winner.get('output_stop_policy'),
                recommended_strategy=winner["strategy"],metric=metric,value=winner[metric],
                actual_output_counts_json=winner['output_counts_json'],
                comparison_output_counts_json=compact(sorted(output_vectors)),
                actual_output_counts_match=len(output_vectors)==1,
                comparison_scope=('Exact actual output counts matched' if len(output_vectors)==1
                    else 'Descriptive actual-work throughput under a common output budget; early-EOS lengths differ'),
                compared_strategies=len(candidates),scope="best observed point; one repeat, no significance claim"))
    return out


def historical_rows():
    path=ROOT/'results/aggregates/5070-plus-5060-all-configurations.csv'
    if not path.exists():return []
    models={'lfm2.5-24b-a2b','qwen3.8-27b@q3_k_xl','qwen/qwen3.8-27b',
        'qwen/qwen3.6-35b-a3b','qwen3.6-35b-a3b-ud-q4_k_xl','google/gemma-4-e2b'}
    rows=[]
    with path.open(encoding='utf-8',newline='') as handle:
        for row in csv.DictReader(handle):
            if row.get('model') not in models or row.get('concurrency')!='1' or row.get('target_tokens') not in ('20000','80000'):continue
            rows.append({k:row.get(k) for k in ('configuration_label','run_id','model','quantization','engine','engine_version',
                'target_tokens','concurrency','repeat','status','prefill_tps','decode_tps','ttft_s',
                'single_client_j_per_token_decode','kv_cache_dtype','mtp_requested','reasoning_requested','gpu_telemetry_scope')})
    return rows


def generate(args):
    prior_manifest=read(PRIOR/"finalization.json")
    if prior_manifest.get("partial") or prior_manifest.get("cases")!=180:
        raise ValueError("Finish and publish the original 180-case cohort before new report generation")
    prior_report=read(PRIOR/'report-summary.json')
    if prior_report.get('partial') or prior_report.get('cases')!=180:
        raise ValueError('The original full report must be published before diagnostic report generation')
    captures=[(p,micro_input(p)) for p in args.micro]
    host_pin_captures=[(p,read(p)) for p in args.host_pin]
    sustained_captures=[(p,read(p)) for p in args.sustained_vram]
    operational_events=public_operational_qualification(read(args.operational_events)) if args.operational_events.exists() else None
    historical_checks=historical_integrity()
    results,planned,runtimes,provenance=screen_inputs(args.screen)
    rows=[condition_row(result,runtimes.get(result["condition"]["case_id"],{})) for result in results]
    seen={r["case_id"] for r in rows if r["capture_kind"]=="screening"}
    expected={(model,strategy,target,clients) for model in ('lfm25','qwen27','qwen35','gemmae2b')
        for strategy in ('single5070','single5060','layer_equal','layer_favor5070','tensor_equal','independent','gemma_kv_local')
        if strategy!='gemma_kv_local' or model=='gemmae2b' for target in (512,20000,80000) for clients in (1,2)}
    mandatory_planned={case_id for case_id,case in planned.items()
        if (case.get('model'),case.get('strategy'),case.get('target_tokens'),case.get('clients')) in expected
        and (case.get('kv_type_k'),case.get('kv_type_v'))==('f16','f16')
        and case.get('output_stop_policy','natural_eos')=='natural_eos'}
    pending=(set(planned)-seen)&mandatory_planned
    optional_deferred=(set(planned)-seen)-mandatory_planned
    observed={(r['model'],r['strategy'],r['target_tokens'],r['clients']) for r in rows
        if r['capture_kind']=='screening' and (r.get('kv_type_k'),r.get('kv_type_v'))==('f16','f16')
        and r.get('output_stop_policy','natural_eos')=='natural_eos'}
    semantic_missing=expected-observed
    full_micro=any(micro_status(d)=='completed' and not p.parent.name.startswith('communication-smoke-')
        and len({m.get('transfer_bytes') for m in d.get('measurements',[])})>=10 for p,d in captures)
    # A preserved failed smoke/earlier attempt is not pending work once the full
    # validated capture and requested terminal inference matrix exist.
    incomplete = bool(pending or semantic_missing or not full_micro)
    if incomplete and not args.allow_partial:raise ValueError("Diagnostic coverage incomplete; inspect inputs or explicitly --allow-partial")
    prefix=ROOT/"results/aggregates"/args.prefix
    report=ROOT/"docs/dual-gpu-communication-and-placement.md"
    if not args.overwrite and (report.exists() or any(prefix.parent.glob(prefix.name+"-*"))):
        raise FileExistsError("Diagnostic report exists; choose --prefix or explicitly --overwrite for these diagnostic artifacts")
    micro_rows,micro_summary,peers=payload_samples(captures);micro_telemetry=micro_telemetry_rows(captures)
    copy_references=vram_copy_reference_rows(micro_summary)
    sustained_references=sustained_reference_rows(sustained_captures)
    host_pins=host_pin_rows(host_pin_captures)
    pairs=pair_strategies(rows);telemetry=telemetry_rows(results);profiles=trace_rows(results);backend=backend_rows(results)
    failed_sensors=failed_sensor_rows(results)
    profile_allocation_comparisons=profile_placement_comparisons(results,runtimes)
    phases=client_phase_rows(results)
    precision_rows=[{k:r.get(k) for k in ('case_id','model','quant','strategy','target_tokens','clients','status',
        'kv_type_k','kv_type_v','observed_kv_type_k','observed_kv_type_v','kv_precision_status','kv_precision_evidence_json',
        'metric_eligible','observed_client_prefill_tps','observed_decode_tps')} for r in rows]
    stop_rows=[{k:r.get(k) for k in ('case_id','model','quant','strategy','target_tokens','clients','status',
        'output_stop_policy','output_stop_policy_verified','output_stop_evidence_json','output_counts_json',
        'metric_eligible','timing_observation_eligible','observed_client_prefill_tps','observed_decode_tps')} for r in rows]
    pipeline_rows=[]
    for result in results:
        case=result['condition'];observed=result.get('pipeline_evidence') or {}
        for server in observed.get('servers',[]):
            actual=next((s for s in (result.get('actual_placement_evidence') or {}).get('servers',[])
                if s.get('server')==server.get('server')),None)
            final_pipeline=(actual or {}).get('pipeline_final_real_context') or {}
            display=dict(server)
            display['startup_observations_may_include_dry_fit_json']=compact(server.get('observations') or [])
            display.update(state=final_pipeline.get('state','unavailable'),
                pipeline_parallelism_enabled=final_pipeline.get('pipeline_parallelism_enabled'),
                n_copies_values=final_pipeline.get('n_copies_values',[]),
                observations=final_pipeline.get('observations',[]),
                pipeline_evidence_basis='Final real positive KV context' if (actual or {}).get('kv_context_epoch_isolated')
                    else 'Active context not isolated; dry-fit startup observations retained separately')
            pipeline_rows.append({**{k:case.get(k) for k in ('case_id','model','strategy','target_tokens','clients','output_stop_policy')},
                'capture_kind':result.get('capture_kind'),'result_status':result.get('status'),
                'availability':observed.get('availability'),**display})
    for row in pipeline_rows:
        row['n_copies_values_json']=compact(row.pop('n_copies_values',[]))
        row['observations_json']=compact(row.pop('observations',[]))
    actual_placement_rows=[]
    for result in results:
        for server in (result.get('actual_placement_evidence') or {}).get('servers',[]):
            actual_placement_rows.append({**{k:result['condition'].get(k) for k in ('case_id','model','strategy','target_tokens','clients')},
                'capture_kind':result.get('capture_kind'),'status':result.get('status'),
                **{k:server.get(k) for k in ('server','availability','metadata_pass_count','preceding_passes_excluded',
                    'zero_model_allocation_rows_excluded','host_model_buffer_present','host_expert_policy_observed',
                    'all_weights_gpu_resident_verified','kv_context_epoch_isolated','prefix_truncated','bounded_prefix_sha256')},
                'logged_model_buffer_bytes_sum_by_backend_json':compact(server.get('logged_model_buffer_bytes_sum_by_backend') or {}),
                'reported_layer_offload_final_load_json':compact(server.get('reported_layer_offload_final_load')),
                'positive_kv_buffer_allocations_json':compact(server.get('kv_buffers_positive') or []),
                'host_tensor_policy_json':compact(server.get('host_tensor_policy_observations') or []),
                'final_layer_assignments_json':compact(server.get('layer_device_assignments_final_load') or {})})
    gemma_pairs=[p for p in pair_strategies([r for r in rows if r['model']=='gemmae2b'],'layer_equal')
                 if p['strategy']=='gemma_kv_local']
    gemma_favor_pairs=[p for p in pair_strategies([r for r in rows if r['model']=='gemmae2b'],'layer_favor5070')
                       if p['strategy']=='gemma_kv_local']
    topology_source=read(args.topology) if args.topology.exists() else {}
    topology=topology_rows(topology_source)
    choices=recommendation_rows(rows);legacy=historical_rows()
    restrictions=[{k:row.get(k) for k in ('case_id','model','quant','strategy','target_tokens','clients','capture_kind','status',
        'whole_case_elapsed_s','observed_group_wall_s','failure_evidence_availability','failure_reason_codes','failure_constraints_json')} for row in rows if row.get('status')!='ok']
    prefix.parent.mkdir(parents=True,exist_ok=True)
    outputs={}
    for name,data in (("micro-samples",micro_rows),("micro-summary",micro_summary),("micro-telemetry",micro_telemetry),
                      ('vram-copy-definitions',copy_references),('client-phase-timeline',phases),
                      ('sustained-vram-control',sustained_references),
                      ("host-pin",host_pins),("conditions",rows),
                      ("strategy-ratios",pairs),("gemma-reverse-vs-equal",gemma_pairs),
                      ('gemma-reverse-vs-forward-favor',gemma_favor_pairs),
                      ("telemetry",telemetry),('failed-attempt-telemetry',failed_sensors),('precision-verification',precision_rows),
                      ('output-stop-verification',stop_rows),
                      ('pipeline-startup',pipeline_rows),
                      ('actual-placement',actual_placement_rows),
                      ('profile-placement-comparison',profile_allocation_comparisons),
                      ("profiles",profiles),("backend-timings",backend),
                      ("failure-restrictions",restrictions),("recommendations",choices),("historical-context",legacy)):
        path=prefix.with_name(prefix.name+"-"+name+".csv");write_csv(path,data);outputs[name]=path
    raw_path=prefix.with_name(prefix.name+"-raw.json.gz")
    operations_path=prefix.with_name(prefix.name+'-operational-events.json')
    operations_path.write_text(json.dumps(operational_events,indent=2,allow_nan=False),encoding='utf-8')
    raw={"schema":"wattever-communication-engineering-report-v1","generated_at":datetime.now(timezone.utc).isoformat(),
        "normal_benchmark_aggregate":False,"coverage_partial":bool(incomplete),"pending_case_ids":sorted(pending),
        'optional_unattempted_deferred_case_ids':sorted(optional_deferred),
        "unobserved_representative_conditions":sorted(semantic_missing),"full_transfer_capture_available":full_micro,
        "captures":[dict(capture_index=i,**data) for i,(_,data) in enumerate(captures)],
        "host_pin_captures":[dict(capture_index=i,**data) for i,(_,data) in enumerate(host_pin_captures)],
        "host_pin_attempts":host_pins,'vram_copy_reference_definitions':copy_references,'client_phase_timeline':phases,
        'sustained_vram_control_captures':[dict(capture_index=i,**data) for i,(_,data) in enumerate(sustained_captures)],
        'sustained_vram_control_summary':sustained_references,
        'operational_events':operational_events,
        'historical_integrity':historical_checks,
        'archived_nsight_memory_label_warning':'Archived memory label strings are not authoritative. Captured SQLite enum tables govern memory/direction interpretation; event counts, logical requested bytes and timing do not depend on label strings.',
        "screening_provenance":provenance,"screening_conditions":results,"topology_facts":topology,"micro_telemetry":micro_telemetry,
        "strategy_ratios":pairs,"gemma_reverse_vs_equal":gemma_pairs,
        'pipeline_startup_observations':pipeline_rows,
        'actual_placement_observations':actual_placement_rows,
        'profile_placement_comparisons':profile_allocation_comparisons,
        'gemma_reverse_vs_forward_favor':gemma_favor_pairs,
        "recommendations":choices,"profiles":profiles,"backend_timings":backend,"historical_context":legacy}
    write_compressed_public_json(raw_path,raw)
    legacy_raw=prefix.with_name(prefix.name+'-raw.json')
    if legacy_raw.exists():
        legacy_archive=INV/'legacy-derived-uncompressed';legacy_archive.mkdir(exist_ok=True)
        archive=legacy_archive/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-'+legacy_raw.name)
        legacy_raw.rename(archive)
    figures=charts(prefix,micro_summary,rows)+client_phase_charts(prefix,phases)
    link=lambda p:"../results/aggregates/"+p.name
    normal=[r for r in rows if r["capture_kind"]=="screening"]
    lines=["# Dual-GPU communication and placement investigation","",
        "**"+("Partial diagnostic capture" if incomplete else "Completed supplied diagnostic matrices")+".** These captures are separate from normal Wattever benchmark aggregates. Existing historical measurements remain unchanged.","",
        f"Recorded conditions: {len(normal)} unprofiled screening and {sum(r['capture_kind']=='profiling' for r in rows)} profiled. Screening outcomes: {compact(dict(Counter(r['status'] for r in normal)))}. Eligible timing conditions: {sum(r['metric_eligible'] for r in normal)}. Required but absent case IDs: {len(pending)}; unobserved required representative model/strategy/prompt/client combinations: {len(semantic_missing)}. Optional unattempted configurations deferred under the user time limit: {len(optional_deferred)}. Full transfer capture available: {full_micro}. Each measured inference condition has one repeat; internal CUDA-copy samples are not inference repeats.","",
        'The required150-condition screen is complete. Broad classic-row24 and fixed-output48 follow-up matrices were deferred under the user time limit; only the individually recorded closeout probes were attempted. Unattempted optional plan entries are deferred research, not pending attempted measurements or silently fabricated results.','',
        f"[Raw shareable evidence (gzip JSON)]({link(raw_path)}) · [All conditions]({link(outputs['conditions'])}) · [Matched strategy ratios]({link(outputs['strategy-ratios'])}) · [Per-card telemetry]({link(outputs['telemetry'])}) · [Original 180-case comparison](5070-plus-5060-comparison.md)","",
        "## Communication topology and peer capability","",
        "Operator identities are PNY RTX5070 Ti16GB and ASUS RTX5060 Ti16GB; NVIDIA names and per-capture mappings verify logical ordering where provided. PnP endpoint/parent relationships are observed topology, while physical PE-slot labels remain unverified. Firmware generic Slot1–7 entries conflict with this three-slot board and are not used as slot proof.",""]
    lines+=table(["Observed endpoint","PnP Gen snapshot","Width snapshot","Parent bridge location","Physical slot"],
        [[r['device'],r['pnp_generation_snapshot'],r['pnp_width_snapshot'],r['parent_bridge_location'],r['physical_motherboard_slot']] for r in topology])
    lines += ["",f"The [EVGA X99 Micro2 manual]({EVGA}) documents x16/x16/x8 allocation with a40-lane processor, and the M.2/PE3 sharing rule. [Intel specifies40 PCIe3 lanes and four memory channels for5960X]({INTEL}). These specifications do not prove which physical slots are occupied. Distinct observed root bridges do not establish absence of contention in host memory/controller paths; no active GPU lane loss is inferred from NVMe presence alone.","",
        "Negotiated link pairs during workload windows are retained in the telemetry CSV. Snapshot generation/width is not bandwidth; maxima of independent samples are not combined into an invented link state.",""]
    lines+=table(["Capture","Accessing CUDA device","Peer allocation device","Can access peer"],
        [[p['capture'],p.get('accessor_device'),p.get('peer_allocation_device'),p.get('can_access_peer')] for p in peers])
    lines += ["","Unsupported peer access explicitly uses portable-pinned host staging. Peer-enabled CUDA API paths remain transport-unverified unless independently traced; capability or a peer-copy event alone is not labeled direct P2P.","",
        "## Transfer measurements","",f"[Raw internal samples]({link(outputs['micro-samples'])}) · [Transfer summaries]({link(outputs['micro-summary'])})","",
        "All rates below are medians of actual joint payload/elapsed samples. Simultaneous throughput is measured over a shared barrier-to-completion interval, never the sum of individual rates. Host latency includes Python dispatch, context/event handling and synchronization. Staged cross-device routes synchronize DtoH before HtoD; there is no within-route double-buffer pipeline. Host pages are CUDA-pinned with OS first-touch placement; CPU affinity/NUMA are not pinned. Full destination validation occurs after the last measured batch with prior poisoning, not independently after every internal sample. These short Windows/WDDM captures are not pure PCIe latency or peak VRAM bandwidth. Memory clocks were not captured, so no clock-state explanation is established. Requested mode names do not imply multiple copies: the actual copies-per-route field governs, including one copy at256MiB under the64MiB batch budget.",""]
    lines+=table(['Capture','Status','Distinct copy sizes','Recorded measurements','Cleanup errors'],
        [[i,micro_status(d),len({m.get('transfer_bytes') for m in d.get('measurements',[])}),len(d.get('measurements',[])),
          len((d.get('cleanup') or {}).get('errors',[]))] for i,(_,d) in enumerate(captures)])
    lines+=['']
    lines += [f"[Communication-capture telemetry]({link(outputs['micro-telemetry'])}) comes from the gated wrapper's sibling telemetry.json. Negotiated link pairs are retained both across the diagnostic interval and, where sampler timestamps overlap, inside actual timed-copy windows. A missing timed-window sample is unavailable evidence, not an idle link. Wrapper board energy covers allocations, warmups, validation and checkpoint I/O; it is not isolated transfer energy.",'']
    lines+=table(['Capture','GPU','Link pairs inside timed copies','Timed-window samples','Whole diagnostic GPU J','Peak °C'],
        [[r.get('capture'),r.get('gpu_index'),r.get('inside_actual_timed_copy_link_pairs_json'),r.get('inside_actual_timed_copy_link_samples'),fmt(r.get('gpu_energy_j')),fmt(r.get('temp_c_max'))] for r in micro_telemetry])
    lines+=['']
    largest=max((r['transfer_bytes'] for r in micro_summary if r['capture_scope']=='full'),default=None)
    selected=[r for r in micro_summary if r['capture_scope']=='full' and r['capture_status']=='completed' and r['transfer_bytes']==largest and r['mode']=='batched_throughput']
    lines+=table(["Largest transfer / route bytes","Case","Copies / route","Measured joint payload GB/s","Joint host µs","Validated","Transport"],
        [[r['transfer_bytes'],r['case'],compact(r['copies_per_route']),fmt(r['median_joint_payload_GB_s']),fmt(r['median_joint_host_us']),r['validation_passed'],compact(r['transport'])] for r in selected])
    lines += ['',f"[Local-copy reference definitions]({link(outputs['vram-copy-definitions'])}) reconcile the older and new256MiB measurements. Both use payload bytes, not a2× arithmetic mismatch; nominal read-plus-write is a separately named quantity. The older probe calibrates longer CUDA-event batches (110/58copies in the retained captures); the new largest-size modes each use one copy and report a separately timed host-wall rate. Compare like timing scopes before discussing differences.",'']
    lines += table(['Capture / mode','GPU','Copies / sample','Internal samples','CUDA-event payload GB/s','Joint host-wall payload GB/s','Nominal read+write GB/s'],
        [[r['capture'],r['gpu_index'],r['copies_per_sample'],r['internal_samples'],fmt(r['CUDA_event_payload_GB_s']),
          fmt(r['joint_host_wall_payload_GB_s']),fmt(r['nominal_read_plus_write_GB_s'])] for r in copy_references])
    lines += ['', 'The5070 latency-mode CUDA-event reference agrees approximately with the old375GB/s payload result, while other new event references are lower. Host overhead alone cannot explain a lower GPU-event reference. Conditions, sustained state, boost behavior and protocol are not isolated, and memory clocks are unavailable; this is not evidence of a physical VRAM-bandwidth regression.','']
    lines += ['',f"[Separate sustained local-copy protocol control]({link(outputs['sustained-vram-control'])}) is optional and never replaces or pools with the original280-copy capture or historical compute probe. It requests sequential cards,256MiB payloads,5 warmups,15 internally timed batches calibrated toward80ms (bounded at128 copies), CUDA-event and synchronized host-wall timing, full final-buffer validation and sparse50ms SM/memory-clock observations. Its repeated random64KiB source block differs from the historical independent full-random pattern. Event and host rates count payload once; neither is a physical DRAM-controller traffic counter. Clock observations do not establish a boost/state cause.",'']
    lines += table(['Capture','GPU','Status','Eligible control','Copies / batch','Batches','CUDA-event payload GB/s','Host-wall payload GB/s','SM clock median MHz (N)','Memory clock median MHz (N)','Cleanup'],
        [[r['capture'],r['gpu_index'],r['card_status'],r['protocol_control_eligible'],r['copies_per_sample'],r['internal_samples'],
          fmt(r['cuda_event_payload_GB_s']),fmt(r['synchronized_host_payload_GB_s']),
          fmt(r['sm_clock_mhz_median'])+' ('+str(r['sm_clock_mhz_valid_samples'])+')',
          fmt(r['memory_clock_mhz_median'])+' ('+str(r['memory_clock_mhz_valid_samples'])+')',r['cleanup_confirmed']]
         for r in sustained_references])
    if not sustained_references:lines += ['','No sustained-copy protocol-control capture was supplied; the original diagnostic samples remain the available evidence.','']
    lines += ["","Smoke/subset captures remain in raw evidence and CSV, and are excluded from the full-transfer summary graph/table. Local VRAM copies are payload rates; physical read-plus-write traffic is different.","",
        "## Host-pinned allocation diagnostic","",f"[Allocation attempts]({link(outputs['host-pin'])}) retain requested sizes, exact Driver API outcomes, physical/commit headroom, gate decisions, release and context-cleanup evidence. These allocation-only samples are separate from copy samples and inference repeats.",""]
    lines+=table(['Capture / test type','Chunk','Requested GiB','Allocation result / code','Allocation seconds','Available RAM GiB','Commit headroom GiB','Gate','Final release','Context cleanup'],
        [[str(r['capture'])+' / '+r['allocation_test_type'],r['chunk_index'],fmt(r['size_bytes']/2**30 if finite(r['size_bytes']) else None),str(r['allocation_name'])+' / '+str(r['allocation_code']),
          fmt(r['allocation_wall_s'],6),fmt(r['physical_available_bytes_before']/2**30 if finite(r['physical_available_bytes_before']) else None),
          fmt(r['commit_headroom_bytes_before']/2**30 if finite(r['commit_headroom_bytes_before']) else None),r['headroom_gate_allowed'],
          r['release_confirmed'] if r['release_required'] else 'not required',r['context_cleanup_confirmed']] for r in host_pins])
    lines += ['']+table(['Capture','Sequence outcome','Held concurrently before cleanup GiB','Final release order','All host allocations released','Remaining allocations','Context destroyed'],
        [[index,data.get('sequence_outcome','sequential allocations, each released before next'),
          fmt(data.get('held_bytes_before_cleanup')/2**30 if finite(data.get('held_bytes_before_cleanup')) else None),
          ((data.get('cleanup') or {}).get('host_cleanup') or {}).get('release_order','per-attempt release'),
          (data.get('cleanup') or {}).get('host_release_confirmed',((data.get('cleanup') or {}).get('host_cleanup') or {}).get('release_confirmed')),
          (data.get('cleanup') or {}).get('remaining_host_allocations'),(data.get('cleanup') or {}).get('context_destroyed')]
         for index,(_,data) in enumerate(host_pin_captures)])
    single_failures=sorted({r['size_bytes'] for r in host_pins if r['allocation_test_type']=='sequential_single_allocation'
        and r['allocation_success'] is False and r['headroom_gate_allowed'] is True and finite(r['size_bytes'])})
    successful_pools=[data for _,data in host_pin_captures if data.get('capture_kind')=='diagnostic_pooled_host_allocation'
        and data.get('sequence_outcome')=='all_chunks_held_concurrently' and (data.get('cleanup') or {}).get('confirmed') is True]
    if single_failures and successful_pools:
        lines += ['', 'In the supplied fresh-context captures, single allocations of '+', '.join(fmt(n/2**30)+' GiB' for n in single_failures)+
            ' failed despite passing the headroom gate. A pooled sequence held '+', '.join(fmt(d.get('held_bytes_before_cleanup')/2**30)
                for d in successful_pools if finite(d.get('held_bytes_before_cleanup')))+
            ' GiB concurrently and completed verified cleanup. This demonstrates a working chunked allocation path; simple total-host-RAM exhaustion alone does not account for this contrast.','']
    if not host_pins:lines+=['','No supplied host-allocation capture is available.']
    lines += ['', 'Pooled attempt status "held" describes ownership during the probe, before final cleanup. Per-chunk releases and the final global cleanup are reported separately above; a held-during-probe status is not an unreleased allocation. Capture-level held-byte totals are repeated as metadata in attempt rows and must not be added. If concurrent8+8+2.75GiB allocations succeed while a single16/18.75GiB allocation fails, chunking demonstrably helps in that fresh-context experiment and simple total-host-RAM exhaustion does not explain that contrast by itself. The native Runtime allocation in a loaded process remains a different API/context/state condition.','',
        'A fresh CUDA Driver context and cuMemHostAlloc flags0 are different API/context/state conditions from the native engine\'s Runtime allocation in a loaded process, even though flags0 correspond to the default allocation policy. Allocation success at a smaller size and CUDA out-of-memory at a larger size, despite OS headroom, measure those specific requests only. Buffers were not touched and no copies/kernels were run in this diagnostic. Exact failure cause and applicability to the native Qwen35 pageable-host condition remain unresolved; this does not prove that adding the second GPU caused the failure. Do not infer actual paging or a physical-RAM limit from that error alone.','',
        "## Controlled execution strategies","",
        "The baseline standalone matrix uses explicit IPv4 loopback, F16 requested K/V, reasoning off, MTP off, common weights, per-slot context and sampling. Optional cache-precision interventions are separate conditions, identified by requested and observed K/V types; the baseline F16 note does not apply to a q8 request. A nonbaseline precision is not claimed verified from a weight GGUF or an ignored flag: matching cache allocator evidence is required for its ratios/rankings. Unverified conditions retain actual timing observations in separate CSV columns/raw evidence. CPU offload, actual layer placement and native tensor restrictions remain recorded outcomes. Automatic fitting may choose different weight placement; no tensor-mode fallback is silently substituted. Independent instances use one logical model instance per card; file-backed GGUF pages may be shared by the OS, so this does not establish twice the physical host RAM. One-client independent runs still pay the second loaded card's idle draw. Do not sum process RSS or per-card shared allocations as physical RAM; OS total-minus-available is the global physical-memory boundary. Tensor restrictions belong to the actual pinned architecture/build, not a generic promise of support.",""]
    outcomes=Counter((r['model'],r['strategy'],r['status']) for r in normal)
    lines += ['',f"[Final-load allocation evidence]({link(outputs['actual-placement'])}) isolates the last model-loader pass with positive real model buffers, excluding earlier dry fit passes and zero-size allocations. KV allocations are read from the last positive KV context when context markers establish it. Unavailable pass/context boundaries and startup-prefix truncation remain explicit; no dry CPU KV/layer assignment is silently combined with a final GPU allocation.",'',
        'An offloaded41/41 layer message does not establish that all weights reside in GPU memory: tensor/expert overrides can retain nonzero CPU_Mapped model allocations. The original offload flags remain reported metadata, alongside final positive CPU/host/GPU model-buffer sizes. CPU embeddings/output tensors and explicitly named expert policies are identified separately; a host buffer with no tensor-role evidence is not labeled an expert by its size. Logged allocation sums are not independent physical-RAM totals, active compute placement, or a per-layer KV map.','']
    lines += ['', 'The frozen runner warms each engine with only8 generated tokens. This does not establish that every CPU-offloaded memory-mapped GGUF page or rarely used MoE expert was prefaulted, nor that filesystem-cache state was equal across conditions. GPU startup loading and an8-token warmup do not verify full host-weight residency. First-query file-backed demand paging can cause page reads without RAM exhaustion or pagefile swapping. Per-phase reads, process working set and faults provide qualified context only; global counters include other processes, and no SSD bottleneck is claimed without attributed I/O evidence. Filesystem/host-page state is separate from token-prefix cache reuse: cache_prompt=false and a zero reused-token count do not prove a cold or equal OS file cache.','']
    lines += ['', 'In this new strategy matrix, single5070 and single5060 mean execution on one GPU while the other card remains physically installed and idle. Their all-card energy includes that idle board. Historical physically single-card installations have a different energy boundary and remain separate; these labels never mean the other board was removed.','']
    lines+=table(["Model","Strategy","Outcome","Conditions"],[[*key,count] for key,count in sorted(outcomes.items())])
    lines+=['',f"[Cache precision verification]({link(outputs['precision-verification'])}) distinguishes requested flags from observed cache allocator types. Quantized-cache conditions without explicit matching evidence remain outside precision-specific comparisons; their observed transport timings remain available.",'']
    if any(r['output_stop_policy']!='natural_eos' for r in rows):
        lines += ['',f"[Output-stop verification]({link(outputs['output-stop-verification'])}) keeps the optional fixed256 condition separate from natural EOS. The fixed wrapper requests --ignore-eos and256 output tokens; comparison eligibility additionally requires exact256 nonestimated API counts for every request and every effective server props.default_generation_settings.params.ignore_eos=true. The verification sidecar is accepted only after rechecking its saved-result SHA256, exact public request projection, every server-metadata SHA256/props value and owned-process cleanup. Missing or conflicting proof leaves timing observations available but excludes the fixed-output claim and its rankings. Natural-EOS screening scores may select new configurations, never substitute as fixed-output measurements.",'']
    lines += ['',f"[Structured failure restrictions]({link(outputs['failure-restrictions'])}) retain observed architecture, split-axis, KV/flash, allocation and context rejection categories where an allowlisted message establishes them. Private result identity/status and owned-process cleanup are checked before reading bounded terminal logs. Raw error strings, stack frames, local paths, URLs, IDs and credentials are never copied. Metadata KV-head counts and documented restrictions remain separate from an observed failure cause.",'']
    lines += table(['Model','Strategy','Prompt','Clients','Outcome','Observed safe reason codes','Evidence availability','KV-head metadata'],
        [[r['model'],r['strategy'],r['target_tokens'],r['clients'],r['status'],r['failure_reason_codes'] or 'specific cause unavailable',
          r['failure_evidence_availability'],compact((json.loads(r['failure_constraints_json']).get('metadata') or {}).get('kv_heads_metadata'))]
         for r in restrictions])
    lines += ['','Timeouts are censored outcomes, not zero performance, capacity-OOM evidence or a deadlock diagnosis. In the frozen standalone runner, --timeout900 applies to urllib socket operations; future waits/cancellation and executor joins do not impose a hard900-second group deadline. Actual measured group wall time is retained when available. Whole-case UTC elapsed time includes idle checks, loading, tokenization/warmup, requests, cancellation, cleanup and profiling exports where applicable; it is separately named and never substituted for missing inference time.','']
    lines += table(['Model','Strategy','Prompt','Clients','Outcome','Whole case elapsed seconds','Recorded group wall seconds'],
        [[r['model'],r['strategy'],r['target_tokens'],r['clients'],r['status'],fmt(r['whole_case_elapsed_s']),fmt(r['observed_group_wall_s'])] for r in restrictions])
    lines += ['', 'vLLM comparison: '+('no configured Linux environment was established (WSL registry absent in the retained inventory); no OS, driver or reboot changes were made.'
        if (topology_source.get('linux') or {}).get('lxss_registry_present') is False else 'Linux/runtime availability is not established by the supplied topology inventory.')+
        ' Native Windows standalone llama.cpp reuses the GGUF files. [Official vLLM GGUF documentation](https://docs.vllm.ai/en/latest/features/quantization/gguf/) identifies experimental support through its separate GGUF plugin; compatibility/performance for these quants was not measured here.','']
    definitions={r['strategy_definition_json'] for r in rows if r.get('configuration_wrapper')}
    if definitions:
        lines += ['','Classic `row` is a separate placement strategy from experimental `tensor`: the row adapter requests a weight partition across both cards while keeping KV/intermediates on the designated main GPU; experimental tensor mode shards KV and performs cross-device reductions. These are requested policies, not proof of actual placement. Wrapper SHA256/strategy definitions remain in the conditions/raw evidence; unchanged core measurement/client/power source fingerprints are still required for matched strategy comparisons.','']
        lines += table(['Configuration adapter','Adapter SHA256','Requested strategy definition'],
            sorted({(r['configuration_wrapper'],r['configuration_wrapper_sha256'],r['strategy_definition_json']) for r in rows if r.get('configuration_wrapper')}))
    lines += ["",f"[Pinned llama.cpp CUDA documentation]({LLAMA}). Per-model unsupported cases are retained above and in the conditions CSV. Failed/censored runs have no fabricated zero throughput.","",
        "Matched ratios require identical software/weight-header identity, requested parameters, context, prompt hashes and actual input counts. Full-file hashes are reported when available; a header/tensor-directory hash is not proof of every weight byte. Strict decode, whole-group throughput/elapsed and energy ratios additionally match actual output counts; TTFT/prefill comparisons do not require matching future output length. Early EOS is allowed: a common output budget does not force identical completion counts. Separate descriptive same-budget ratio columns retain valid actual-work comparisons when counts differ, with both counts and the length qualification; they are not pooled into strict ratios. Absolute tokens/s remains available for every verified successful condition. Requested KV flags and observed raw placement logs remain separate evidence; a split fraction is not an actual weight/KV map.",""]
    lines+=table(['Capture','Observed software version','Executable SHA256','Source SHA256 map','CUDA overrides','Weight identity scope'],
        [[p['source'],compact((p['runtime'].get('actual_version') or {})),p['runtime'].get('executable_sha256'),
          compact(p['runtime'].get('source_sha256',{})),
          compact(p['runtime'].get('cuda_overrides',{})),'Full hash when supplied, otherwise bounded GGUF header/tensor directory'] for p in provenance])
    weights={compact((model,identity)): (model,identity) for p in provenance
        for model,identity in (p['runtime'].get('model_files') or {}).items()}
    lines += ['']+table(['Model','GGUF filename','Weight file GiB','Header/directory SHA256','Full-file SHA256'],
        [[model,identity.get('file_name'),fmt(identity.get('file_bytes')/2**30 if finite(identity.get('file_bytes')) else None),
          identity.get('header_sha256'),identity.get('full_file_sha256')] for model,identity in weights.values()])
    lines += ['',f"[Backend timing attachment]({link(outputs['backend-timings'])}) preserves prompt-evaluation/decode durations, counts, rates and raw timing lines by owned server. These rows remain unassigned to requests unless the original slot/task evidence establishes attribution; no row-order matching is invented.",'']
    ratio_groups=defaultdict(list)
    for pair in pairs:ratio_groups[(pair['model'],pair['quant'],str(pair['kv_type_k'])+'/'+str(pair['kv_type_v']),pair['output_stop_policy'],pair['strategy'],pair['clients'],pair['condition_group'])].append(pair)
    lines+=table(["Model","Weight quant","Requested K/V","Output stop","Strategy","Clients","Configuration group","Prefill ratio (N)","Decode ratio (N)","Aggregate ratio (N)","GPU energy/token ratio (N)"],
        [[*key,*[f"{fmt(median([r[m+'_ratio'] for r in cells]),3)} ({sum(finite(r[m+'_ratio']) for r in cells)})"
            for m in ('prefill_tps','decode_tps','aggregate_output_tps','gpu_energy_per_output_token_including_prefill_j')]]
            for key,cells in sorted(ratio_groups.items())])
    lines += ["","Ratios are strategy/single5070 within the new controlled protocol. They do not pool native Strata, LM Studio, smoke or profiled measurements. Backend prompt-evaluation durations are retained separately and may be unassigned to requests; they are not substituted for client TTFT.","",
        '## Why prefill can improve while decode slows','',
        f"[Bounded startup pipeline evidence]({link(outputs['pipeline-startup'])}) retains startup enabled/disabled messages and n_copies, but reported active settings require the independently isolated final real positive KV-context epoch. Earlier fit/dry-run pipeline messages are retained separately and do not establish the active setting. The split request alone never establishes that pipelining was enabled. Missing messages, unisolated contexts, truncated startup coverage and mixed states remain explicit; a logged pipeline setting alone does not establish overlapping GPU kernels or a phase-specific speedup.",'']
    lines += table(['Model','Strategy','Prompt','Clients','Server','Capture type','Pipeline state','Enabled','n_copies','Evidence basis','Startup prefix truncated'],
        [[r.get('model'),r.get('strategy'),r.get('target_tokens'),r.get('clients'),r.get('server'),r.get('capture_kind'),
          r.get('state'),r.get('pipeline_parallelism_enabled'),r.get('n_copies_values_json'),r.get('pipeline_evidence_basis'),r.get('prefix_truncated')]
         for r in pipeline_rows])
    lines += ['',
        'A plausible execution explanation is that prefill has many prompt tokens and microbatches available for pipelining, while a single autoregressive decode stream must complete each token through its dependent layers before the next token. Actual pipeline startup messages and recorded GPU overlap support only the observed parts of that explanation; whole-request traces without a verified phase clock cannot isolate prefill concurrency. Two clients are a separate workload and can change scheduling.',
        'Heterogeneous execution can also slow decode without PCIe being the dominant cost: serial layer work assigned to the slower card can lengthen the critical path, through its weight/KV reads and kernels. As an illustrative calculation only, half the weights scanned at bandwidth B and half at B/2 would take1.5 times a full scan at B, ignoring every other cost. This is not a measured model DRAM rate or a prediction from the local-copy reference. Requested split fractions need not equal actual weight bytes or computation. Measured transfer/wait evidence, actual placement, CPU/offload and quantized-KV conditions are considered separately; PCIe capability alone does not establish causality.','',
        "## Gemma E2B: measured regression and current hypothesis","",
        "The preceding report independently confirmed severe20k/80k decode regressions in native server timing logs; the localhost fallback affects TTFT and cannot by itself explain post-first-token decode rates. Existing low dedicated VRAM and high clocks did not establish a paging diagnosis.","",
        f"Pinned [Gemma source]({GEMMA_SOURCE}) and the inspected GGUF identify35 layers with20 shared-KV tail layers, using zero-based source layers13 and14. A split that separates those sources from tail15–34 could cause repeated context-dependent cross-device reads/copies. Reverse CUDA device order1,0 with fractions1,2 is a proposed placement experiment, not proof of causality. Weight-colocation flags are accepted only for unsplit/layer modes with nonsharded CUDA model-buffer evidence; row/tensor main-device assignments do not establish whole weight-layer colocation. Actual KV allocation/reuse and transfer/wait causality require KV logs and traces.",""]
    lines+=table(["Strategy","Prompt","Clients","Decode tokens/s","Weight13–34 colocated on5070","Actual KV locality verified"],
        [[r['strategy'],r['target_tokens'],r['clients'],fmt(r['decode_tps']),r['gemma_weight_colocation_on_5070'],r['actual_kv_locality_verified']]
         for r in normal if r['model']=='gemmae2b'])
    lines += ['',f"[Direct reverse-placement/equal-split comparison]({link(outputs['gemma-reverse-vs-equal'])}) uses the same strict input/config matching; ratios below are reverse/equal rather than reverse/singleGPU.",'']
    lines += table(['Prompt','Clients','Decode ratio','TTFT ratio','GPU energy/output token ratio'],
        [[r['target_tokens'],r['clients'],fmt(r['decode_tps_ratio'],3),fmt(r['ttft_s_ratio'],3),
          fmt(r['gpu_energy_per_output_token_including_prefill_j_ratio'],3)] for r in gemma_pairs])
    lines += ['',f"[Reverse versus forward favor5070]({link(outputs['gemma-reverse-vs-forward-favor'])}) is an additional intervention comparison: both request roughly two-thirds of weight allocation on the faster card, with reversed device/layer order. Actual layer boundaries, layer weights and compute costs are not identical, so this improves the control for requested allocation fraction without isolating KV traffic as the sole cause. Weight-source/tail colocation is observed where logged; KV allocation/reuse mapping inferred from source code or aggregate buffers remains an inference until an explicit per-layer KV map establishes it.",'']
    lines += table(['Prompt','Clients','Reverse/forward-favor decode ratio','Reverse/forward-favor TTFT ratio'],
        [[r['target_tokens'],r['clients'],fmt(r['decode_tps_ratio'],3),fmt(r['ttft_s_ratio'],3)] for r in gemma_favor_pairs])
    lines += ["","## Profiling and counter availability","",f"[Sanitized trace summaries]({link(outputs['profiles'])})","",
        "Nsight captures exclude model loading by starting collection after readiness/warmup, but injection is present before collection. Graph-node tracing and diagnostic logging/sampling add unquantified overhead. Profiled points are excluded from timing/energy rankings. CPU stack/context-switch capture, WDDM event tracing and GPU hardware counters are not enabled by the helper; no privilege/driver/global-trace changes were made. Missing trace/counter evidence remains unavailable, never measured zero.",""]
    lines += ['',f"[Profile/unprofiled allocation comparison]({link(outputs['profile-placement-comparison'])}) checks matching model-file/runtime identities, GPU mapping, requested strategy/context and K/V precision using the separately parsed final real allocations. Nsight injection before model loading can consume VRAM and perturb --fit decisions, including CPU expert placement. A trace therefore does not establish the unprofiled run's weight/KV placement. Differences are flagged without normalizing timing; missing or truncated allocation evidence remains unavailable. Output-stop/budget differences are identified and any placement comparison across them remains qualified. Even equal aggregate buffer sizes do not prove identical tensor placement.",'']
    lines+=table(["Model","Strategy","Prompt","Trace status / analysis","GPU event counts","Logical requested copy bytes","Sum wait API ns","Event-time sanity flags"],
        [[r['model'],r['strategy'],r['target_tokens'],r['status']+' / '+r['analysis_source'],r['event_counts_json'],
          r['logical_requested_copy_bytes'],r['wait_api_duration_sum_ns'],
          sum(c.get('above_reference_ceiling') is True for c in json.loads(r['copy_event_consistency_checks_json']))
             if json.loads(r['copy_event_consistency_checks_json']) else 'unavailable'] for r in profiles])
    lines += ['', 'Memory-kind and copy-direction labels follow each captured SQLite export\'s enum tables. Validated sidecars must retain capture-source/SQLite provenance and unchanged counts, logical bytes and timing; originals remain archived. Legacy archived label strings are unverified and are not used as evidence of placement. Missing enum tables produce unknown labels rather than an assumed ordering.','',
        'Trace memcpy bytes are logical requested-copy sizes, not independently measured physical PCIe traffic. Requested bytes divided by summed event durations can exceed the platform\'s physical PCIe ceiling because graph/replay semantics, event timing and overlap differ from hardware bus accounting. The event-time sanity flags identify such inconsistencies; they are not throughput measurements. CPU wait/API and copy durations overlap and cannot be added as elapsed bus time or idle time. Actual phase NVML KB/s samples, when available, remain separate evidence.','']
    lines += [f"[Client-boundary telemetry timeline]({link(outputs['client-phase-timeline'])}) uses the recorded request start/first-output/end and sampler perf_counter timestamps. It can assign NVML/WDDM/CPU observations to client TTFT and decode windows, with counter sampling windows possibly straddling a boundary. This is not a native kernel-prefill definition. The Nsight traces remain whole-request collections: no verified clock transform to those client phases is available, and no per-phase kernel/copy causality is claimed from an inferred offset or a backend duration. Corresponding phase figures contain no guessed Nsight overlay.",'']
    if not profiles:lines += ["","No supplied sanitized profile capture is available; kernel/communication causality remains unresolved."]
    lines += ["","Trace-local GPU activity unions avoid double counting overlapping kernel/copy intervals. API-duration sums can overlap and are not elapsed idle time. Gaps mean no recorded GPU activity, not proven hardware idle. NVML PCIe counters retain raw driver KB/s and sampling windows; they are not integrated transfer bytes. WDDM shared allocation is not proof of spill; global paging includes unrelated applications, process faults include soft/hard faults, and commit charge is not actual pagefile occupancy.","",
        "Both board powers, energy, utilization, VRAM, clocks and temperatures are retained per condition/card. Group energy is integrated once over the common overlapping-job window. Energy rankings require complete inventory and power on both unique cards, no low-confidence or truncated-retention flag, and at least80% sampled temporal coverage globally and per card. The sampler extrapolates time-weighted sampled power to window edges; accepted energy is not proof of continuous physical measurement. Coverage and eligibility exclusions remain explicit in conditions CSV. Decode-phase energy uses its own verified interval and coverage. Do not add per-request energies or independent VRAM peaks; the combined VRAM peak is the maximum simultaneous sum. CPU/wall-system energy is not measured. Per-process CPU/memory, global paging, collection overhead and counter availability remain in the telemetry attachment/raw evidence.","",
        f"[Failed-attempt sensors]({link(outputs['failed-attempt-telemetry'])}) retain whole-sampler-interval diagnostics when a timeout/failure leaves the request group empty. This is not a reconstructed aligned group window: no throughput, group-energy/token or capacity-OOM conclusion is fabricated from a timeout. Raw sensor samples remain in the separate failed-capture evidence.","",
        "## Recommendations within tested conditions","",
        "The following are best observed points among matched, successful, unprofiled configurations. One repeat cannot establish statistical significance, stability across ambient conditions, or model intelligence. Recommendations remain specific to model, context, client count and measured GPU-board boundary.",""]
    lines+=table(["Model / weight quant","Requested K/V","Output stop","Prompt","Clients","Objective","Observed strategy","Metric / value","Candidates","Comparison scope"],
        [[r['model']+' / '+str(r['quant']),str(r['kv_type_k'])+'/'+str(r['kv_type_v']),r['output_stop_policy'],r['target_tokens'],r['clients'],r['goal'],r['recommended_strategy'],r['metric']+' / '+fmt(r['value']),r['compared_strategies'],r['comparison_scope']] for r in choices])
    capacity=defaultdict(list)
    for row in normal:
        if row['metric_eligible']:capacity[(row['model'],row['quant'],str(row['kv_type_k'])+'/'+str(row['kv_type_v']),row['output_stop_policy'],row['clients'],row['chart_series_signature'])].append(row)
    lines += ["","Largest usable context means the largest successfully observed target, not an extrapolated maximum:",""]
    lines+=table(["Model","Weight quant","Requested K/V","Output stop","Clients","Configuration group","Largest successful prompt target","Successful strategies at that target"],
        [[*key,max(r['target_tokens'] for r in values),', '.join(sorted({r['strategy'] for r in values if r['target_tokens']==max(q['target_tokens'] for q in values)}))] for key,values in sorted(capacity.items())])
    lines += ["","## Historical comparisons and connection boundary","",
        "See the [180-case dual-card comparison](5070-plus-5060-comparison.md), [CPU comparison](5960x-cpu-comparison.md), and [single-card hardware follow-up](5070-ti-hardware-follow-up.md). They retain original weights, KV/MTP/reasoning flags, endpoint paths, power boundaries and timestamps. New controlled F16-KV/MTP-off standalone results are a separate condition; native Strata variants and LM Studio variants are not merged into its ratios.","",
        "The retained inference-free localhost diagnostic found a roughly two-second IPv6 fallback versus direct IPv4. New controlled requests explicitly use127.0.0.1 with proxy bypass. Historical client TTFT/prefill rates are unchanged; no guessed delay is subtracted. Native prompt-evaluation timing remains a separate boundary. Decode regressions supported by native post-first-token timing require another explanation.","",
        f"[Retained historical context rows]({link(outputs['historical-context'])}) are copied read-only from the existing aggregate CSV. The20k/80k single-client observations below are context, not matched new-strategy ratios. Different quants/engines, CPU configurations, repeats and energy boundaries remain identified.",""]
    lines+=table(['Historical hardware','Model / quant','Engine','Prompt','Repeat','Status','Client prefill t/s','Decode t/s','Decode GPU J/token'],
        [[r['configuration_label'],r['model']+' / '+str(r['quantization']),r['engine'],r['target_tokens'],r['repeat'],r['status'],r['prefill_tps'],r['decode_tps'],r['single_client_j_per_token_decode']] for r in legacy])
    lines += ["","## Reproduction and evidence limits","","```powershell",
        '$benchPython = "C:\\tests\\flash-next\\.venv\\Scripts\\python.exe"',
        '& $benchPython untracked/run_communication_capture.py --smoke',
        '& $benchPython untracked/run_communication_capture.py',
        '& $benchPython tools/standalone_screen.py --run --models gemmae2b --sizes 512 20000 80000 --clients 1 2 --stop-after 1 --output untracked/communication-investigation/screening-gemma',
        '# Inspect the first newly measured condition, then retain the full plan and resume once:',
        '& $benchPython tools/standalone_screen.py --run --models gemmae2b --sizes 512 20000 80000 --clients 1 2 --resume --output untracked/communication-investigation/screening-gemma',
        '& $benchPython tools/standalone_screen.py --run --models lfm25 qwen27 qwen35 --sizes 512 20000 80000 --clients 1 2 --output untracked/communication-investigation/screening-remaining',
        "# Profiling adds --profile --nsys <pinned-x64-nsys.exe> and uses a NEW diagnostic directory.",
        '# Reporting uses default Python with matplotlib/numpy; the FLASH venv owns GPU measurements.',
        'python untracked/report_communication_investigation.py --generate',"```","",
        "Commands describe the protocol, not blanket permission to interrupt active services. Before GPU work the harness requires prior cohort completion, fresh Strata state and no loaded LM Studio/standalone model; native Strata lifecycle remains owned by Strata Control. Bind only verified-free ports and stop only verified owned processes. Exact condition parameters, actual executable versions/hash, model/header identity and software provenance are in raw evidence.","",
        'The archived study runners encode this Windows machine\'s guarded layout and model inventory; they are research sources, not a claim of a universally portable launcher. Other machines can use stock llama.cpp with the recorded parameters and Wattever\'s ordinary OpenAI-compatible endpoint workflow, recording their own actual placement and hardware metadata.','',
        "**Measured:** validated copy samples, saved successful inference timing/usage and available sensors. **Inferred:** documented lane-allocation implications and the possible Gemma shared-KV cross-split mechanism. **Unresolved unless supplied traces/logs establish it:** physical slot assignment, direct peer transport, per-layer KV placement, paging attribution, and whether transfer/waiting causally accounts for the decode loss. CPU/memory/PCIe capacity alone is not a causal diagnosis.",""]
    lines += ['','## Background activity and uncertainty','',f"[Measurement qualification]({link(operations_path)}) retains nonconfidential background-activity information. Historical inputs are verified against their original size/SHA256; any relocated input must match its explicit authorized mapping and the same original checksum.",'']
    if operational_events and operational_events['background_read_recorded']:
        lines += ['A brief background read was recorded during the independent-instance phase; exact timing and the specific overlapping case are unknown. No effect size was established and no retrospective timing correction was applied. Per-case telemetry is retained. Small differences remain descriptive observations under a one-repeat design, without a statistical-significance claim.','']
    for figure in figures:lines += ["!["+figure.stem+"]("+link(figure)+")",""]
    report.write_text("\n".join(lines)+"\n",encoding="utf-8")
    return {"report":str(report.relative_to(ROOT)),"partial":bool(incomplete),"screening_conditions":len(normal),
        "profiled_conditions":len(rows)-len(normal),"micro_samples":len(micro_rows),"figures":len(figures),"historical_datasets_modified":False}


def self_test():
    """Tiny synthetic schema/math checks only; no file/GPU/profiler operations."""
    capture={'status':'completed','inventory':{},'measurements':[{'case':'joint','transfer_bytes':64,
        'timing_mode':'batched_throughput','validation_passed':True,'samples':[{'elapsed_s':2.0,
        'payload_bytes':128,'routes':[{'payload_bytes':64},{'payload_bytes':64}]}]}]}
    samples,summary,_=payload_samples([(Path('synthetic'),capture)])
    assert samples[0]['measured_joint_payload_GB_s']==64/1e9
    assert summary[0]['median_joint_host_us']==2e6
    capture['measurements'][0]['samples'][0]['payload_bytes']=256
    samples,summary,_=payload_samples([(Path('synthetic'),capture)])
    assert samples[0]['measured_joint_payload_GB_s'] is None and summary[0]['valid_samples']==0
    base=dict(case_id='a',model='synthetic',quant='Q4',strategy='single5070',target_tokens=512,clients=1,
        capture_kind='screening',matched_condition_signature='same',metric_eligible=True,status='ok',
        prompt_hashes_json='same',prompt_counts_json='[512]',output_counts_json='[256]',energy_eligible=True)
    for metric in RATIO_METRICS:base[metric]=2.0
    other=dict(base,case_id='b',strategy='layer_equal',decode_tps=1.0,prefill_tps=4.0)
    pair=pair_strategies([base,other])[0]
    assert pair['decode_tps_ratio']==.5 and pair['prefill_tps_ratio']==2
    other['output_counts_json']='[255]'
    pair=pair_strategies([base,other])[0]
    assert pair['decode_tps_ratio'] is None and pair['decode_tps_descriptive_same_budget_ratio']==.5
    assert pair['prefill_tps_ratio']==2 and pair['actual_output_counts_match'] is False
    other['output_counts_json']='[256]'
    other['prompt_counts_json']='[520]'
    assert pair_strategies([base,other])[0]['decode_tps_ratio'] is None
    other.update(prompt_counts_json='[512]',capture_kind='profiling')
    assert not pair_strategies([base,other])
    wrapper={'status':'completed','measurements':[{'samples':[{'host_start_ns':1000000000,'host_end_ns':2000000000}]}],
        'wrapper_capture':{'telemetry.json':{'diagnostic':{'gpus':[{'gpu_index':0,'metrics':{}}],
            'samples':[{'t_perf':1.5,'gpus':[{'gpu_index':0,'pcie_current_generation':3,'pcie_current_width':16}]},
                       {'t_perf':3.0,'gpus':[{'gpu_index':0,'pcie_current_generation':1,'pcie_current_width':16}]}]}}}}
    counter=micro_telemetry_rows([(Path('synthetic'),wrapper)])[0]
    assert counter['inside_actual_timed_copy_link_pairs_json']=='[[3,16]]'
    assert counter['inside_actual_timed_copy_link_samples']==1
    assert micro_status({'status':'completed','wrapper_capture':{'manifest.json':{}}})=='wrapper_telemetry_pending_or_unavailable'
    private=r'C:\Users\SUPERSECRET\server.cpp:9 GGML_ASSERT(src_ss[0].axis != GGML_BACKEND_SPLIT_AXIS_0) failed'
    safe=failure_patterns(private+'\nunknown argument --api-key-SUPERSECRET\nn_head_kv = 1\nhttps://private.invalid/SUPERSECRET')
    assert any(f['code']=='tensor_split_axis_assertion' for f in safe)
    assert not any(f['code']=='tensor_kv_head_divisibility' for f in safe)
    assert 'SUPERSECRET' not in compact(safe) and 'Users' not in compact(safe) and 'https:' not in compact(safe)
    pipeline=parse_pipeline_lines(r'C:\Users\SUPERSECRET\context.cpp: llama_context: pipeline parallelism enabled (n_copies=4)')
    assert pipeline['pipeline_parallelism_enabled'] is True and pipeline['n_copies_values']==[4]
    assert 'SUPERSECRET' not in compact(pipeline)
    assert parse_pipeline_lines('no explicit setting')['state']=='unavailable'
    assert parse_pipeline_lines('pipeline parallelism enabled\npipeline parallelism disabled')['state']=='mixed_observed'
    real_log=('llama_model_loader: loaded meta data dry\nload_tensors: loading model tensors\n'
        'load_tensors: layer 13 assigned to device CPU\nload_tensors: CPU_Mapped model buffer size =0MiB\n'
        'load_tensors: CUDA0 model buffer size =0MiB\n'
        'llama_model_loader: loaded meta data final\nload_tensors: loading model tensors\n'
        +'\n'.join('load_tensors: layer '+str(i)+' assigned to device CUDA0' for i in range(13,35))
        +'\nload_tensors: offloaded 41/41 layers\nload_tensors: tensor blk.0.ffn_up_exps.weight assigned buffer CPU_Mapped\n'
        'load_tensors: CPU_Mapped model buffer size =144MiB\nload_tensors: CPU_Mapped model buffer size =210MiB\n'
        'load_tensors: CUDA0 model buffer size =11462.95MiB\n'
        'llama_context: constructing llama_context dry\nllama_kv_cache: layer 13 assigned to device CPU\nllama_kv_cache: CPU KV buffer size =0MiB\n'
        'llama_context: constructing llama_context final\nllama_kv_cache: CUDA0 KV buffer size =1660MiB\n')
    actual=parse_actual_placement(real_log)
    assert actual['metadata_pass_count']==2 and actual['preceding_passes_excluded']==1
    assert actual['all_weights_gpu_resident_verified'] is False and actual['host_expert_policy_observed']
    assert actual['logged_model_buffer_bytes_sum_by_backend']['CPU_Mapped']==354*2**20
    assert actual['layer_device_assignments_final_load'][13]=='CUDA0'
    assert actual['kv_context_epoch_isolated'] and len(actual['kv_buffers_positive'])==1
    assert actual['kv_buffers_positive'][0]['backend']=='CUDA0'
    placement={'condition':{'strategy':'layer_equal'},'placement':[{'raw_lines':['CUDA0 model buffer size =100MiB'],
        'gemma_weight_colocation_on_5070':True}],'visible_to_physical':[{0:0,1:1}],
        'actual_placement_evidence':{'servers':[actual]}}
    assert placement_scope(placement)['gemma_weight_colocation_on_5070'] is True
    placement['condition']['strategy']='row_equal'
    assert placement_scope(placement)['gemma_weight_colocation_on_5070'] is None
    placement['condition']['strategy']='tensor_equal'
    assert placement_scope(placement)['gemma_weight_colocation_on_5070'] is None
    baseline={'condition':{'case_id':'1'*16,'model':'gemmae2b','strategy':'single5070'},
        'status':'ok','capture_kind':'screening','actual_placement_evidence':{'servers':[actual]}}
    profiled=json.loads(compact(baseline));profiled['condition']['case_id']='2'*16;profiled['capture_kind']='profiling'
    runtime={'model_files':{'gemmae2b':{'header_sha256':'synthetic'}},'executable_sha256':'synthetic'}
    runtimes={'1'*16:runtime,'2'*16:runtime}
    comparison=profile_placement_comparisons([baseline,profiled],runtimes)[0]
    assert comparison['observed_allocation_difference'] is False and comparison['timings_normalized'] is False
    profiled['actual_placement_evidence']['servers'][0]['logged_model_buffer_bytes_sum_by_backend']['CPU_Mapped']+=1024
    assert profile_placement_comparisons([baseline,profiled],runtimes)[0]['observed_allocation_difference'] is True
    case={'case_id':'a'*16,'model':'synthetic','strategy':'single5070','target_tokens':512,'clients':1,
        'context_per_slot':1024,'max_tokens':256,'kv_type_k':'f16','kv_type_v':'f16'}
    request={'status':'ok','prompt_tokens':512,'completion_tokens':256,'completion_tokens_estimated':False,
        'decode_token_count':255,'prefill_tps':1024.0,'decode_tps':170.0,'ttft_s':.5,'measurement_validity':{}}
    complete={'gpu_power_complete':True,'low_confidence':False,'gpu_sampled_coverage_fraction':.95,
        'raw_retention_truncated':False}
    power=dict(complete,gpu_energy_j=20.0,inventory_complete=True,
        gpus=[dict(complete,index=0,gpu_energy_j=12.0),dict(complete,index=1,gpu_energy_j=8.0)])
    decode=dict(complete,gpu_energy_j=15.0,inventory_complete=True,
        gpus=[dict(complete,index=0,gpu_energy_j=9.0),dict(complete,index=1,gpu_energy_j=6.0)])
    result={'condition':case,'capture_kind':'screening','status':'ok','measured_repeats':1,'actual_context_verified':True,
        'model':{'name':'Synthetic','quant':'Q4_0'},'requests':[request],
        'group':{'t_start':10.0,'t_end':12.0,'wall_s':2.0,'aggregate_output_tps':999999,
            'all_card_energy_available':True,'power':power,'single_request_phases':{'decode':decode}}}
    row=condition_row(result,{})
    assert row['aggregate_output_tps']==128.0 and row['gpu_energy_per_output_token_including_prefill_j']==20/256
    assert row['single_request_decode_gpu_energy_per_token_j']==15/255
    assert not energy_window_evidence(dict(power,raw_retention_truncated=True))['eligible']
    assert not energy_window_evidence(dict(power,gpu_sampled_coverage_fraction=.79))['eligible']
    size=256*1024**2
    copy_sample={'payload_bytes':size*100,'elapsed_s':.09,'routes':[{
        'source_gpu':0,'destination_gpu':0,'transport':'DtoD','copies':100,
        'payload_bytes':size*100,'DtoD_gpu_event_ms':80.0}]}
    sustained={'protocol':'sustained-local-vram-reference-v1','status':'completed','cleanup_confirmed':True,
        'cards':[{'gpu_index':0,'transfer_bytes':size,'copies_per_sample':100,'samples':[copy_sample]*15,
            'requested_internal_samples':15,'status':'completed','validation':{'passed':True}}]}
    control=sustained_reference_rows([(Path('synthetic/result.json'),sustained)])[0]
    assert control['protocol_control_eligible'] and math.isclose(control['cuda_event_payload_GB_s'],size*100/.08/1e9)
    assert control['memory_clock_mhz_valid_samples'] is None and not control['clock_causality_established']
    sustained['cleanup_confirmed']=False
    assert not sustained_reference_rows([(Path('synthetic/result.json'),sustained)])[0]['protocol_control_eligible']
    altered=json.loads(compact(result));altered['group']['wall_s']=9.0
    assert condition_row(altered,{})['metric_eligible'] is False
    altered=json.loads(compact(result));altered['condition'].update(kv_type_k='q8_0',kv_type_v='q8_0')
    assert condition_row(altered,{})['metric_eligible'] is False
    altered['placement']=[{'raw_lines':["llama_kv_cache: type_k = 'q8_0', type_v = 'q8_0'"]}]
    assert condition_row(altered,{})['metric_eligible'] is True
    fixed=json.loads(compact(result));fixed['condition']['output_stop_policy']='ignore_eos_requested_256'
    assert condition_row(fixed,{})['metric_eligible'] is False
    assert condition_row(fixed,{})['chart_series_signature']!=condition_row(result,{})['chart_series_signature']
    fixed['cleanup']=[{'exit_confirmed':True}]
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder);case_dir=root/'cases'/fixed['condition']['case_id'];server=case_dir/'server0'
        server.mkdir(parents=True)
        result_raw=compact(fixed).encode();(case_dir/'result-private.json').write_bytes(result_raw)
        props_raw=compact({'props':{'default_generation_settings':{'params':{'ignore_eos':True}}},'slots':[]}).encode()
        (server/'actual-server-private.json').write_bytes(props_raw)
        proof={'schema':'fixed-output-verification-v1','result_sha256':hashlib.sha256(result_raw).hexdigest(),
            'actual_output_count_verified':True,'actual_server_ignore_eos_verified':True,
            'expected_output_tokens_per_request':256,'eligible_fixed_output_comparison':True,
            'server_evidence':[{'source':'server0/actual-server-private.json',
                'sha256':hashlib.sha256(props_raw).hexdigest(),'props_ignore_eos':True,'slot_ignore_eos':[]}]}
        (case_dir/'fixed-output-verification.json').write_text(compact(proof),encoding='utf-8')
        public=public_projection(fixed);public['output_stop_evidence']=output_stop_evidence(public,root)
        assert condition_row(public,{})['metric_eligible'] is True
        (server/'actual-server-private.json').write_text('{}',encoding='utf-8')
        assert output_stop_evidence(public,root)['comparison_eligible'] is False
    print('Synthetic joint-rate, usage/energy denominators, precision, phase/matching and sanitization checks passed; no hardware touched.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate",action="store_true")
    parser.add_argument('--figures-only',action='store_true',help='Redraw transfer/inference figures from existing CSVs; no captured JSON/log scans or numeric rewrites')
    parser.add_argument("--self-test",action="store_true",help="Synthetic CPU-only math/schema checks; no capture or report generation")
    parser.add_argument("--micro",type=Path,action="append",default=[])
    parser.add_argument('--host-pin',type=Path,action='append',default=[])
    parser.add_argument('--sustained-vram',type=Path,action='append',default=[])
    parser.add_argument('--operational-events',type=Path,default=INV/'operational-events.json')
    parser.add_argument("--screen",type=Path,action="append",default=[])
    parser.add_argument("--topology",type=Path,default=INV/"topology-private.json")
    parser.add_argument("--prefix",default=PREFIX)
    parser.add_argument("--allow-partial",action="store_true")
    parser.add_argument("--overwrite",action="store_true")
    args=parser.parse_args()
    if args.self_test:self_test();return
    if not re.fullmatch(r"[A-Za-z0-9_-]+",args.prefix):parser.error("--prefix must be a simple diagnostic artifact basename")
    if args.figures_only:
        if args.generate:parser.error('--figures-only and --generate are separate operations')
        prefix=ROOT/'results/aggregates'/args.prefix
        print(json.dumps({'mode':'figures_only','figures':[str(path.relative_to(ROOT)) for path in figures_from_saved_csv(prefix)],
            'numeric_data_modified':False},indent=2));return
    if not args.screen:
        args.screen=sorted(INV.glob('*/screening-public.json'))
    if not args.micro:
        args.micro=sorted(set(INV.glob('communication-full-*/transfers.json'))
            |set(INV.glob('communication-smoke-*/transfers.json'))|set(INV.glob("communication-*.json")))
    if not args.host_pin:args.host_pin=sorted(INV.glob('host-pin-*.json'))
    if not args.sustained_vram:args.sustained_vram=sorted(INV.glob('sustained-local-vram-*/result.json'))
    if not args.generate:
        print(json.dumps({"mode":"plan_only","micro_inputs":[str(p) for p in args.micro],"host_pin_inputs":[str(p) for p in args.host_pin],"sustained_vram_inputs":[str(p) for p in args.sustained_vram],"screen_inputs":[str(p) for p in args.screen],"writes":False},indent=2));return
    print(json.dumps(generate(args),indent=2))


if __name__=="__main__":main()
