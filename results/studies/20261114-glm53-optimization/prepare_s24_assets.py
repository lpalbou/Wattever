"""Verify downloaded S24 assets and convert a fresh pack; never starts an engine."""
from pathlib import Path
import datetime, hashlib, json, os, shutil, subprocess, sys
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
BASE=ROOT/'experimental-maya'
sha=lambda p:hashlib.file_digest(Path(p).open('rb'),'sha256').hexdigest()
now=lambda:datetime.datetime.now(datetime.timezone.utc).isoformat()
download=json.loads((HERE/'s24-download-receipt.json').read_text())
if download.get('status')!='complete' or len(download.get('verified',[]))!=3:
    raise SystemExit('All three S24 download checksums must complete before pack conversion.')
modeldir=Path(download['destination']);pack=modeldir/'pack-f32-1159'
receipt_path=BASE/'software-provenance/s24-pack-f32-1159.json'
if receipt_path.exists() or pack.exists():
    raise SystemExit('Existing S24 pack/receipt preserved; inspect before rerunning.')
models=[]
for item in download['files']:
    path=modeldir/item['name'];st=path.stat()
    if st.st_size!=item['bytes'] or sha(path)!=item['sha256']:
        raise SystemExit('S24 target readback verification failed: '+str(path))
    models.append({'path':str(path),'bytes':st.st_size,'mtime_ns':st.st_mtime_ns,
        'sha256':item['sha256'],'sha256_verified':True,'verified_utc':now()})
    print('Verified target readback: '+path.name,flush=True)
vendor=BASE/'source-1.1.59';converter=vendor/'tools/iq_pack.py'
from inspect_quant import inspect
metadata=inspect([m['path'] for m in models],HERE/'s24-model-metadata.json')
cmd=[str(BASE/'source/.venv/Scripts/python.exe'),str(converter),'--gguf',models[0]['path'],
     '--out',str(pack),'--compat-bf16']
receipt={'status':'converting','started_at':now(),'model_revision':download['revision'],
    'model_id':'glm-5.3-flash-maya-s24-iq2_xxs_s','quantization':'Maya-S24 IQ2_XXS_S GGUF + BF16-compat',
    'command':cmd,'converter_sha256':sha(converter),'source_revision':'027992cb1f09829488e650e466233ca351d3d6ef',
    'output':str(pack),'engine_executed':False,'control_manifests_modified':False,'model_files':models,
    'model_header_summary':{k:metadata[k] for k in ('parameters','total_tensor_bytes','expert_bytes','bytes_by_dtype')}}
receipt_path.write_text(json.dumps(receipt,indent=2),encoding='utf-8')
env=dict(os.environ,MAYA_GGUF_PY=str(BASE/'source/third_party/llama.cpp/gguf-py'))
with (HERE/'s24-conversion.log').open('w',encoding='utf-8') as log:
    result=subprocess.run(cmd,env=env,stdout=log,stderr=subprocess.STDOUT)
receipt['returncode']=result.returncode
if result.returncode:
    receipt['status']='failed';receipt_path.write_text(json.dumps(receipt,indent=2));raise SystemExit(result.returncode)
seed=vendor/'data/expert-profile-glm.bin'
if sha(seed)!='53b03a13482b6fedc599d149be2d69f88c38d9edc4611a740da46314ab1dc5a7':
    raise SystemExit('Official expert-profile seed identity mismatch.')
shutil.copy2(seed,pack/'expert-profile-seed.bin')
files=[]
for path in sorted(pack.rglob('*')):
    if not path.is_file():continue
    st=path.stat();relative=path.relative_to(pack);digest=sha(path)
    item={'path':str(path),'relative':relative.as_posix(),
        'bytes':st.st_size,'mtime_ns':st.st_mtime_ns,'sha256':digest,'sha256_verified':True}
    xs=Path('D:/StrataModels/models/GLM-5.3-Flash-Maya-XS/pack-f32-1156')/relative
    if xs.exists():
        item['xs_reference_sha256']=sha(xs)
        item['xs_byte_identical']=item['xs_reference_sha256']==digest
    files.append(item)
receipt.update(status='completed',finished_at=now(),files=files)
receipt_path.write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'status':'completed','files':len(files),'receipt':str(receipt_path)}),flush=True)
