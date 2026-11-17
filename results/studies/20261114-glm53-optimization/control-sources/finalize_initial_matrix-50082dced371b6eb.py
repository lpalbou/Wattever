import json
from pathlib import Path
import subprocess
import sys
import httpx

root = Path(__file__).resolve().parent
folder = root / 'benchmark-matrix/20261030T091103Z'
path = folder / 'results.json'
data = json.loads(path.read_text())
data['environment_note'] = 'User reported some external machine use during this series; retain as exploratory evidence, superseded by the separately monitored clean rerun.'
data['method'] = ('Exploratory speed matrix; configured context 65536, temperature0, thinking off, '
                  'parallel1, vision off, MTP enabled. Three fresh requests per cell, 512 output tokens. '
                  'FlashNext IQ3_S: INT8 KV, MTP4, presence1.5 default, reserve2000MiB. '
                  'Qwen3.6 UD-Q4_K_XL: FP32 KV, MTP2, penaltiesoff, expert-vram3.0GiB including staging. '
                  'Input lengths are measured, not separate allocated contexts.')
data['models']['iq3s']['request_sampling'] = {'temperature':0,'presence_penalty':1.5,'repetition_penalty':1,'frequency_penalty':0}
with httpx.Client(timeout=90, trust_env=False) as client:
    response = client.post('http://127.0.0.1:8086/v1/chat/completions', json={
        'model':'qwen3.8-flash-next','messages':[{'role':'user','content':'Reply exactly OK.'}],
        'temperature':0,'reasoning_effort':'none','max_completion_tokens':8},headers={'Origin':'https://browser-client.example'})
    response.raise_for_status()
    data['restoration_smoke'] = {'status':response.status_code,'reply':response.json()['choices'][0]['message']['content'],
                                 'cors_origin':response.headers.get('access-control-allow-origin')}
path.write_text(json.dumps(data,indent=2))
subprocess.run([sys.executable,str(root/'aggregate_matrix.py'),str(folder)],check=True)
print('Exploratory dataset finished and endpoint8086 verified.')
