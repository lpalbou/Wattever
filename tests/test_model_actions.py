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
        bench=self.execute_selection(['new-model'])
        self.assertEqual(bench.run['results'],self.source['results'])
        self.assertEqual(bench.run['config']['models'],['card','external','new-model'])
        self.assertEqual(len(bench.run['engine_groups']),2)

    def test_first_external_model_upgrades_existing_benchmark(self):
        store.save(self.a);self.source=self.a
        bench=self.execute_selection(['new-model'])
        self.assertEqual(bench.run['config']['provider'],'mixed')
        self.assertEqual(len(bench.run['engine_groups']),2)
        self.assertEqual(bench.run['results'],self.a['results'])
        self.assertTrue(all(s.get('engine_group_id') for s in bench.run['sessions']))

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
