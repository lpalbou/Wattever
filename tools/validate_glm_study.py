"""Validate GLM diagnostic snapshots without model loading or network access."""
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wattever.identity import verify_model_configuration


def validate(folder):
    folder = Path(folder)
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    entries = manifest.get('figures', []) + ([manifest['storage_observation']] if 'storage_observation' in manifest else [])
    for snapshot in manifest['snapshots']:
        entries += snapshot['artifacts']
    for entry in entries:
        path = (folder / entry['file']).resolve()
        if not path.is_relative_to(folder.resolve()):
            raise ValueError('Artifact escapes its study folder')
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('Artifact checksum mismatch: ' + entry['file'])
    reference = manifest.get('native_reference')
    if reference:
        path = (folder / reference['file']).resolve()
        if not path.is_relative_to(ROOT) or hashlib.sha256(path.read_bytes()).hexdigest() != reference['sha256']:
            raise ValueError('Native reference is outside the repository or has changed')
    seen = {}
    close = lambda a,b: math.isclose(a,b,rel_tol=1e-7,abs_tol=1e-5)
    for snapshot in manifest['snapshots']:
        payload = json.loads(gzip.decompress((folder / (snapshot['name'] + '.json.gz')).read_bytes()))
        if payload['protocol'] != manifest['protocol'] or payload['source_sha256'] != snapshot['source_sha256']:
            raise ValueError('Protocol/source binding mismatch')
        run = payload['run']
        with (folder / (snapshot['name'] + '.csv')).open(encoding='utf-8', newline='') as stream:
            rows = list(csv.DictReader(stream))
        if len(rows) != len(run['results']) or len(rows) != snapshot['cases']:
            raise ValueError('Snapshot coverage mismatch')
        if sum(r['status'] == 'ok' for r in run['results']) != snapshot['successful']:
            raise ValueError('Successful coverage mismatch')
        for case, row in zip(run['results'], rows):
            key = (run['id'], case['model'], case['target_tokens'], case.get('concurrency',1), case['repeat'])
            fingerprint = hashlib.sha256(json.dumps(case,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            if key in seen and seen[key] != fingerprint:
                raise ValueError('A shared case changed between snapshots')
            seen[key] = fingerprint
            if case['repeat'] != 0 or run['config']['repeats'] != 1:
                raise ValueError('Unexpected inference repeat')
            verify_model_configuration(case['model_configuration'])
            if case['configuration_id'] != case['model_configuration']['configuration_id']:
                raise ValueError('Configuration identity mismatch')
            if row['status'] != case['status'] or int(row['target_tokens']) != case['target_tokens']:
                raise ValueError('CSV case identity mismatch')
            requests = case.get('requests') or [case]
            for request in requests:
                if request.get('status') != 'ok':
                    continue
                if not isinstance(request.get('prompt_tokens'),int) or not isinstance(request.get('completion_tokens'),int):
                    raise ValueError('Successful request lacks actual counts')
                if request.get('completion_tokens_estimated') is not False:
                    raise ValueError('Estimated output count in a controlled case')
                if not close(request['prompt_tokens']/request['ttft_s'],request['prefill_tps']):
                    raise ValueError('Client prefill/count mismatch')
                if request.get('decode_token_count') is not None and request.get('decode_s',0)>0:
                    if not close(request['decode_token_count']/request['decode_s'],request['decode_tps']):
                        raise ValueError('Client decode/count mismatch')
            if case['status'] != 'ok':
                if row['whole_group_output_tps'] or row['combined_gpu_j_per_output']:
                    raise ValueError('Incomplete group treated as successful throughput/efficiency')
                continue
            count = case.get('concurrency',1)
            if len(requests) != count or any(r.get('status') != 'ok' for r in requests):
                raise ValueError('Incomplete group marked successful')
            total = sum(r['completion_tokens'] for r in requests)
            if not close(total/case['wall_s'],float(row['whole_group_output_tps'])):
                raise ValueError('Whole-group throughput/count mismatch')
            if row['combined_gpu_j'] and not close(float(row['combined_gpu_j'])/total,float(row['combined_gpu_j_per_output'])):
                raise ValueError('Shared group energy counted inconsistently')
    print('Validated %d immutable snapshots, %d distinct recorded conditions, counts, identities and group accounting.' % (len(manifest['snapshots']),len(seen)))


if __name__ == '__main__':
    validate(ROOT / 'results/studies/20261106-glm53-flash')
