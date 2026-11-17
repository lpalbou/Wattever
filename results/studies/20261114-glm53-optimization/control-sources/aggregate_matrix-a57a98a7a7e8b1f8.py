"""Recompute source-qualified aggregates from benchmark_matrix.py evidence."""
import csv
import json
from pathlib import Path
import statistics
import sys

folder = Path(sys.argv[1])
report = json.loads((folder / 'results.json').read_text())

def stat(values):
    values = [v for v in values if isinstance(v, (int, float))]
    if not values:
        return None
    return {'median': statistics.median(values), 'min': min(values), 'max': max(values), 'n': len(values)}

summary = {'method': report['method'], 'models': {}, 'failures': list(report.get('failures', []))}
table = []
for name, model in report['models'].items():
    output = {'status': model['status'], 'before': model['before'], 'after': model.get('after'), 'matrix': {}}
    for target, cell in model.get('matrix', {}).items():
        good = [r for r in cell['requests'] if r['ok']]
        fresh = [r for r in good if r.get('reused_tokens') is not None and r['reused_tokens'] <= 64
                 and r.get('prompt_tps_source') == 'engine']
        sustained = [r for r in good if r['generated_tokens'] >= 256 and r.get('decode_tps_source') == 'engine']
        row = {'actual_prompt_tokens': stat(r['prompt_tokens'] for r in good),
               'prefill_tps': stat(r['prompt_tps'] for r in fresh),
               'decode_tps': stat(r['decode_tps'] for r in sustained),
               'ttft_s': stat(r['ttft_s'] for r in good),
               'wall_s': stat(r['total_s'] for r in good),
               'output_tokens': stat(r['generated_tokens'] for r in good),
               'cached_tokens': stat(r['reused_tokens'] for r in good),
               'complete': len(fresh) == 3 and len(sustained) == 3,
               'drafts_offered': sum(r.get('drafts_offered') or 0 for r in good),
               'drafts_accepted': sum(r.get('drafts_accepted') or 0 for r in good)}
        output['matrix'][target] = row
        flat = {'model': name, 'target_input_tokens': target, 'complete': row['complete']}
        for metric in ('actual_prompt_tokens', 'prefill_tps', 'decode_tps', 'ttft_s', 'wall_s', 'output_tokens', 'cached_tokens'):
            for key in ('median', 'min', 'max', 'n'):
                flat[metric + '_' + key] = (row[metric] or {}).get(key)
        table.append(flat)
    output['short_chat'] = {k: v for k, v in model.get('short_chat', {}).items() if k != 'requests'}
    for r in model.get('short_chat', {}).get('requests', []):
        if not r['ok']:
            summary['failures'].append({'model': name, 'kind': 'short_chat', 'error': r.get('err')})
    cached = model.get('prefix_reuse', {}).get('summary', {}).get('requests', [])
    output['prefix_reuse'] = {metric: stat(r[field] for r in cached if r['ok']) for metric, field in
                              [('cached_tokens', 'reused_tokens'), ('ttft_s', 'ttft_s'), ('decode_tps', 'decode_tps')]}
    summary['models'][name] = output
summary['restored'] = report.get('restored', False)
(folder / 'summary.json').write_text(json.dumps(summary, indent=2))
with (folder / 'summary.csv').open('w', newline='') as f:
    if table:
        writer = csv.DictWriter(f, fieldnames=list(table[0]))
        writer.writeheader()
        writer.writerows(table)
for name, model in summary['models'].items():
    print(name, 'short chat', model['short_chat'].get('decode_tps'))
    for target, row in model['matrix'].items():
        print(target, json.dumps({k: row[k] for k in ('prefill_tps', 'decode_tps', 'ttft_s', 'complete')}))
print('Failures:', summary['failures'])
