"""Compare both implementations with an independent occupied-time oracle."""
import json
from pathlib import Path
import random
import shutil
import subprocess
import unittest

from wattever.collective_metrics import collective_decode_phase, collective_prefill_phase


class PhaseOracleTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is required for cross-language verification')
    def test_two_hundred_overlapping_and_staggered_workloads(self):
        rng = random.Random(973119)
        rows, expected = [], []
        for _ in range(200):
            requests = []
            decoding, prefilling = set(), set()
            for _ in range(rng.randint(2, 12)):
                start, ttft, duration = rng.randint(0, 70), rng.randint(1, 30), rng.randint(1, 15)
                tokens, prompt = rng.randint(1, 300), rng.randint(10, 1000)
                requests.append(dict(status='ok', start_offset_s=start, ttft_s=ttft,
                                     decode_s=duration, decode_token_count=tokens,
                                     completion_tokens=tokens+1, prompt_tokens=prompt,
                                     wall_s=ttft+duration+rng.randint(0, 5)))
                # Integer intervals have exactly this many occupied one-second cells.
                prefilling.update(range(start, start+ttft))
                decoding.update(range(start+ttft, start+ttft+duration))
            rng.shuffle(requests)
            row = dict(status='ok', concurrency=len(requests), requests=requests,
                       wall_s=max(q['start_offset_s']+q['wall_s'] for q in requests))
            rows.append(row)
            expected.append(dict(decode=sum(q['decode_token_count'] for q in requests)/len(decoding),
                                 prefill=sum(q['prompt_tokens'] for q in requests)/len(prefilling),
                                 output=sum(q['completion_tokens'] for q in requests)/row['wall_s']))
        root = Path(__file__).resolve().parents[1]
        script = "const p=require('./web/phase-metrics.js');let s='';process.stdin.on('data',x=>s+=x);process.stdin.on('end',()=>console.log(JSON.stringify(JSON.parse(s).map(r=>({decode:p.decodePhase(r).tps,prefill:p.prefillPhase(r).tps,output:p.outputRate(r)})))));"
        result = subprocess.run(['node', '-e', script], cwd=root, input=json.dumps(rows),
                                text=True, capture_output=True, check=True, timeout=15)
        observed = json.loads(result.stdout)
        for i, (row, truth, js) in enumerate(zip(rows, expected, observed)):
            with self.subTest(workload=i):
                self.assertAlmostEqual(collective_decode_phase(row['requests'])['tps'], truth['decode'])
                self.assertAlmostEqual(collective_prefill_phase(row['requests'])['tps'], truth['prefill'])
                for metric in ('decode', 'prefill', 'output'):
                    self.assertAlmostEqual(js[metric], truth[metric])


if __name__ == '__main__':
    unittest.main()
