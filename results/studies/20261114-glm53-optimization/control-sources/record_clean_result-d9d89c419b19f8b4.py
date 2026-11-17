import json
from pathlib import Path
import manage

root=Path(__file__).resolve().parent
folder=root/'benchmark-clean/20261030T100949Z'
summary=json.loads((folder/'final-summary.json').read_text())
assert len(summary['models'])==3
assert all(cell['decode_tps']['n']>=2 and cell['prompt_tps']['n']>=2 for model in summary['models'].values() for cell in model['matrix'].values())
assert not summary['failures'],summary['failures']
assert not summary.get('restore_verification_error'),summary.get('restore_verification_error')
check=summary['independent_restore_check']
assert check['http_status']==200 and check['context']==32768 and check['reply'].strip()=='OK' and check['cors_origin']=='*',check
for filename in ('verification.json','experiment.json'):
    path=root/filename
    record=json.loads(path.read_text())
    record['latest_clean_benchmark']={'summary':str(folder/'final-summary.json'),'csv':str(folder/'final-summary.csv'),
                                    'accepted_fresh_runs':summary['accepted_fresh_matrix_requests'],
                                    'samples_per_cell':'2-3','complete_two_sweeps':summary['complete_two_sweeps'],
                                    'endpoint_restored':check}
    path.write_text(json.dumps(record,indent=2))
owned=manage.owned_backend(manage.strata_status())
if not owned:raise RuntimeError('Restored preset ownership not verified')
logs=manage.collect_logs(owned)
manage.save_state('ready',clean_benchmark=str(folder/'final-summary.json'),endpoint='http://127.0.0.1:8086/v1',
                  verification='verification.json',resources=manage.resources(),engine_logs=logs)
print(json.dumps({'restore':check,'excluded':summary['exclusion_counts'],
                  'external_cpu_means':summary['outside_cpu_percent_across_fresh_runs'],
                  'independent_matrix_loads_confirmed':summary['independent_matrix_loads_confirmed'],
                  'unexpected_failures':summary['failures']},indent=2))
