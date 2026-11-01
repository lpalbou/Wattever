import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from wattever import runner, store
from wattever.consolidation import consolidate, managed_config


class ConsolidationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        for key,value in [('RUNS_DIR',str(Path(temp.name)/'runs')),('TRASH_DIR',str(Path(temp.name)/'trash'))]:
            active=patch.object(store,key,value);active.start();self.addCleanup(active.stop)

    def fixture(self,id,provider='lmstudio'):
        cfg=dict(runner.DEFAULT_CONFIG,provider=provider,models=[id],sizes=[512],concurrency_levels=[1],repeats=1,endpoint_inference_local=True)
        if provider=='openai':cfg['reproducibility']={'engine':'Strata','quantization':'IQ3_S'}
        return {'id':id,'created_at':'2026-10-31T00:00:00Z','status':'completed','schema_version':1,
                'application':{'protocol_version':'2'},'hardware':{},'classification':{'hardware_class':'same'},
                'gpu':{'name':'GPU','driver':'1','vram_total_mb':16000},'host':{},'config':cfg,
                'provider':{'kind':provider},'corpus':{'sha256':'same'},
                'sessions':[{'id':'session-'+id,'config':copy.deepcopy(cfg),'status':'completed'}],
                'results':[{'model':id,'status':'ok','target_tokens':512,'concurrency':1,'repeat':0,
                            'mtp':None if provider=='openai' else False,'prefill_tps':123,'session_id':'session-'+id}]}

    def test_exact_rows_and_engine_settings_preserved_with_sources_archived(self):
        a,b=self.fixture('card'),self.fixture('external','openai');store.save(a);store.save(b)
        merged=consolidate(['card','external'],'RTX 5070 Ti')
        self.assertEqual(merged['results'],a['results']+b['results'])
        self.assertEqual(merged['config']['provider'],'mixed')
        self.assertEqual(len(merged['engine_groups']),2)
        self.assertEqual(len(merged['sessions']),2)
        self.assertEqual(managed_config(merged)['models'],['card'])
        self.assertEqual([r['id'] for r in store.list_runs()],['card'])
        self.assertEqual(len(list(Path(store.TRASH_DIR).glob('*.json'))),1)
        self.assertEqual(merged['source_runs'][1]['config'],b['config'])
        exported=store.to_csv([merged]);self.assertIn('engine_provider',exported);self.assertIn('openai',exported)

    def test_managed_repairs_exclude_the_external_engine(self):
        for run in [self.fixture('card'),self.fixture('external','openai')]:store.save(run)
        merged=consolidate(['card','external'],'GPU')
        cfg=runner.extension_config(merged,{'model_modes':{'card':['off']}})
        self.assertEqual(cfg['provider'],'lmstudio');self.assertEqual(cfg['models'],['card'])
        with self.assertRaisesRegex(ValueError,'external engine'):
            runner.extension_config(merged,{'model_modes':{'external':['off']}})

    def test_incompatible_sources_rejected_before_writes(self):
        a,b=self.fixture('a'),self.fixture('b','openai');b['classification']['hardware_class']='other'
        store.save(a);store.save(b)
        with patch.object(store,'backup') as backup:
            with self.assertRaisesRegex(ValueError,'Hardware'):consolidate(['a','b'],'GPU')
            backup.assert_not_called()
        self.assertEqual(store.load('a'),a);self.assertIsNotNone(store.load('b'))

    def test_managed_extension_preserves_the_other_engine_and_completion_coverage(self):
        from unittest.mock import MagicMock
        for run in [self.fixture('card'),self.fixture('external','openai')]:store.save(run)
        source=consolidate(['card','external'],'GPU')
        cfg=runner.extension_config(source,{'model_modes':{'card':['off']},'repeats':2})
        bench=runner.BenchRunner();bench.cli=MagicMock();bench.cli.version.return_value=None
        def prepare(run):run.update(classification={'hardware_class':'same'},application={'name':'Wattever','protocol_version':'2'})
        with patch.object(runner.power,'gpu_info',return_value=source['gpu']),patch.object(store,'host_info',return_value={}), \
             patch.object(store,'prepare_run',side_effect=prepare),patch.object(runner.corpus,'master_stats',return_value={'sha256':'same'}), \
             patch.object(runner.threading,'Thread'),patch.object(store,'save_config'):
            bench._start(cfg,resume_source=source,extend_in_place=True)
        self.assertEqual(bench.run['config']['provider'],'mixed')
        self.assertEqual(bench.run['results'],source['results'])
        self.assertEqual(managed_config(bench.run)['repeats'],2)
        self.assertEqual(bench.run['sessions'][-1]['engine_group_id'],'lm-studio')
        bench._record(bench.run,{'model':'card','target_tokens':512,'concurrency':1,'repeat':1,'mtp':False,'status':'ok'},2)
        bench.run['status']='completed';bench._finish_session(bench.run)
        self.assertEqual(bench.run['remaining_tests'],0)
        self.assertEqual(bench.run['status'],'completed')


if __name__=='__main__':unittest.main()
