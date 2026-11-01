import copy
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from wattever import endpoints, runner, store
from wattever.consolidation import consolidate
from wattever.model_actions import remove_models
import test_consolidation


class ModelActionTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        for key, value in [('RUNS_DIR',temp.name+'/runs'),('TRASH_DIR',temp.name+'/trash')]:
            active=patch.object(store,key,value); active.start(); self.addCleanup(active.stop)
        fixture=test_consolidation.ConsolidationTests().fixture
        self.a,self.b=fixture('card'),fixture('external','openai')
        self.a['lms_version']='v'
        self.b['config']['endpoint_id']='route'
        for item in (self.a,self.b):store.save(item)
        self.source=consolidate(['card','external'],'GPU')
        self.connection={'provider':'openai','endpoint_base_url':'http://localhost:9999/v1',
                         'endpoint_id':'route','endpoint_inference_local':True}

    def test_remove_one_model_preserves_other_rows_and_exact_backup(self):
        original=Path(store.path_for('card')).read_bytes()
        result=remove_models('card',['external'])
        updated=store.load('card')
        self.assertEqual(updated['results'],self.a['results'])
        self.assertEqual(updated['config']['models'],['card'])
        self.assertEqual(updated['config']['provider'],'lmstudio')
        self.assertEqual(updated['remaining_tests'],0)
        self.assertEqual(result['removed_measurements'],1)
        self.assertTrue(any(p.read_bytes()==original for p in Path(store.RUNS_DIR).parent.joinpath('.history').glob('*.json')))
        self.assertEqual([r['id'] for r in store.list_runs()],['card'])
        self.assertTrue(all('external' not in s['config']['models'] for s in updated['sessions']))
        with self.assertRaisesRegex(ValueError,'last model'):remove_models('card',['card'])

    def test_invalid_or_running_removal_never_writes(self):
        with patch.object(store,'backup') as backup:
            with self.assertRaises(ValueError):remove_models('card',['unknown'])
            self.source['status']='running';store.save(self.source)
            with self.assertRaisesRegex(ValueError,'stop'):remove_models('card',['external'])
            backup.assert_not_called()

    def test_model_removal_also_removes_its_storage_comparisons(self):
        self.source['storage_comparison']={'rows':[{'model':'external'}],
                                           'alternate_results':[dict(self.b['results'][0])]}
        store.save(self.source)
        remove_models('card',['external'])
        self.assertNotIn('storage_comparison',store.load('card'))

    def storage_variants_fixture(self):
        group = next(g for g in self.source['engine_groups'] if g['config']['provider'] == 'openai')
        external_session = next(s for s in self.source['sessions'] if s['engine_group_id'] == group['id'])
        group['config']['models'].append('keep')
        self.source['config']['models'].append('keep')
        retained = dict(self.b['results'][0], model='keep', session_id='keep-primary', engine_group_id=group['id'])
        self.source['results'].append(retained)
        for session_id, model in [('keep-primary', 'keep'), ('external-alternate', 'external'), ('keep-alternate', 'keep')]:
            cfg = dict(copy.deepcopy(external_session['config']), models=[model])
            self.source['sessions'].append(dict(id=session_id, engine_group_id=group['id'], config=cfg, status='completed'))
        group['primary_session_ids'] = [external_session['id'], 'keep-primary']
        group['alternative_session_ids'] = ['external-alternate', 'keep-alternate']
        self.source['storage_comparison'] = {
            'model_ids': ['external', 'keep'],
            'rows': [{'model': 'external'}, {'model': 'keep'}],
            'baseline_results': [copy.deepcopy(self.b['results'][0]), copy.deepcopy(retained)],
            'alternate_results': [dict(self.b['results'][0], session_id='external-alternate'),
                                  dict(retained, session_id='keep-alternate')],
            'summary': {'metric': 'wall_s', 'models': [{'model': 'external', 'median_wall_speedup': 2},
                                                     {'model': 'keep', 'median_wall_speedup': 1.5}]},
            'model_files_copy': {'verified': True, 'files': 78, 'total_bytes': 210135967743,
                                 'gguf_hashes': [{'file': 'external.gguf', 'sha256': 'historical-copy-hash'}]},
        }
        store.save(self.source)
        return group

    def test_model_removal_filters_storage_summary_but_preserves_copy_evidence(self):
        self.storage_variants_fixture()
        manifest = copy.deepcopy(self.source['storage_comparison']['model_files_copy'])
        remove_models('card', ['external'])
        comparison = store.load('card')['storage_comparison']
        self.assertEqual(comparison['model_ids'], ['keep'])
        for key in ('rows', 'baseline_results', 'alternate_results'):
            self.assertEqual([row['model'] for row in comparison[key]], ['keep'])
        self.assertEqual(comparison['summary'], {'metric': 'wall_s', 'models': [{'model': 'keep', 'median_wall_speedup': 1.5}]})
        self.assertEqual(comparison['model_files_copy'], manifest)

    def test_model_removal_prunes_references_to_removed_storage_sessions(self):
        original_group = self.storage_variants_fixture()
        remove_models('card', ['external'])
        updated = store.load('card')
        group = next(g for g in updated['engine_groups'] if g['id'] == original_group['id'])
        self.assertEqual(group['primary_session_ids'], ['keep-primary'])
        self.assertEqual(group['alternative_session_ids'], ['keep-alternate'])
        retained_ids = {session['id'] for session in updated['sessions']}
        self.assertNotIn('external-alternate', retained_ids)
        self.assertTrue(set(group['primary_session_ids'] + group['alternative_session_ids']) <= retained_ids)

    def execute_selection(self, models, **options):
        selection=dict(run_id='card',connection=self.connection,model_modes={m:['off'] for m in models},
                       sizes=[512],concurrency_levels=[1],**options)
        bench=runner.BenchRunner()
        def prepare(run):run.update(classification={'hardware_class':'same'},measurement_scope={},application={'name':'Wattever','protocol_version':'2'})
        with ExitStack() as stack:
            for target,method,changes in [
                (endpoints,'resolve_identity',{'side_effect':lambda cfg,**kwargs:cfg}),
                (endpoints,'list_models',{'return_value':[{'id':m,'quantization':'IQ3_S'} for m in models]}),
                (runner.power,'gpu_info',{'return_value':self.source['gpu']}),
                (store,'host_info',{'return_value':{}}),(store,'prepare_run',{'side_effect':prepare}),
                (runner.corpus,'master_stats',{'return_value':{'sha256':'same'}}),
                (runner.threading,'Thread',{}),(store,'save_config',{})]:
                stack.enter_context(patch.object(target,method,**changes))
            plan=bench.plan_extension(selection)
            self.assertEqual(plan['total_tests'],1)
            self.assertEqual(bench.extend(selection),'card')
        return bench

    def test_external_exact_repair_stays_in_same_file_and_preserves_lm(self):
        bench=self.execute_selection(['external'],selected_cases=[['external',False,1,512,0]],rerun_existing=True)
        self.assertEqual(bench.run['results'],self.a['results'])
        self.assertEqual(bench.run['previous_attempts'][0]['model'],'external')
        self.assertEqual(bench.run['sessions'][-1]['engine_group_id'],'strata')
        self.assertEqual(len(bench.run['engine_groups']),2)
        bench._record(bench.run,dict(model='external',status='ok',target_tokens=512,concurrency=1,repeat=0,mtp=False),1)
        self.assertIsNone(bench.run['results'][-1]['mtp'])
        bench.run['status']='completed';bench._finish_session(bench.run)
        self.assertEqual(bench.run['remaining_tests'],0)
        self.assertEqual([r['id'] for r in store.list_runs()],['card'])

    def test_external_new_model_adds_to_existing_group(self):
        group=next(g for g in self.source['engine_groups'] if g['config']['provider']=='openai')
        group['config'].update(canonical_model_id='old-checkpoint',reported_mtp='on')
        store.save(self.source)
        bench=self.execute_selection(['new-model'])
        self.assertEqual(bench.run['results'],self.source['results'])
        self.assertEqual(bench.run['config']['models'],['card','external','new-model'])
        self.assertEqual(len(bench.run['engine_groups']),2)
        cfg=bench.run['sessions'][-1]['config']
        self.assertNotIn('canonical_model_id',cfg)
        self.assertNotIn('quantization',cfg.get('reproducibility',{}))
        self.assertNotIn('reported_mtp',cfg)

    def test_first_external_model_upgrades_existing_benchmark(self):
        store.save(self.a);self.source=self.a
        bench=self.execute_selection(['new-model'])
        self.assertEqual(bench.run['config']['provider'],'mixed')
        self.assertEqual(len(bench.run['engine_groups']),2)
        self.assertEqual(bench.run['results'],self.a['results'])
        self.assertTrue(all(s.get('engine_group_id') for s in bench.run['sessions']))
        group=next(g for g in bench.run['engine_groups'] if g['config']['provider']=='openai')
        self.assertFalse(group['measurement_scope']['context_control'])
        self.assertFalse(group['measurement_scope']['mtp_control'])
        self.assertEqual(group['measurement_scope'],bench.run['sessions'][-1]['measurement_scope'])
        self.assertEqual(bench.run['measurement_scope']['context_control'],'per execution session')

    def test_external_requires_fresh_matching_connection_and_keeps_secrets_private(self):
        with self.assertRaisesRegex(ValueError,'another engine'):
            runner.extension_config(self.source,{'model_modes':{'external':['off']}})
        with patch.object(endpoints,'resolve_identity',side_effect=lambda cfg,**kwargs:cfg):
            with self.assertRaisesRegex(ValueError,'differs'):
                runner.extension_config(self.source,{'connection':dict(self.connection,endpoint_id='other'),
                                                    'model_modes':{'external':['off']}})
        bench=self.execute_selection(['new-model'])
        self.assertNotIn('endpoint_base_url',str(bench.run))


if __name__=='__main__':unittest.main()
