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
        with self.assertRaisesRegex(ValueError,'another engine'):
            runner.extension_config(merged,{'model_modes':{'external':['off']}})

    def test_incompatible_sources_rejected_before_writes(self):
        a,b=self.fixture('a'),self.fixture('b','openai');b['classification']['hardware_class']='other'
        store.save(a);store.save(b)
        with patch.object(store,'backup') as backup:
            with self.assertRaisesRegex(ValueError,'Hardware'):consolidate(['a','b'],'GPU')
            backup.assert_not_called()
        self.assertEqual(store.load('a'),a);self.assertIsNotNone(store.load('b'))

    def board(self, manufacturer, model='RTX 5070 Ti', memory=16, index=0):
        return {'sensor_index':index, 'manufacturer':manufacturer, 'model':model, 'memory_gib_nominal':memory}

    def annotate_boards(self, run, cards, variant=None, source='Operator-confirmed board identity'):
        run['gpu_board_identity']={'source':source, 'gpus':copy.deepcopy(cards)}
        run['hardware']['gpu_board_identity']=copy.deepcopy(run['gpu_board_identity'])
        if variant is not None:
            run['classification']['board_variant']=variant
        return run

    def test_known_asus_and_pny_boards_are_separate_despite_same_chip_driver_and_class(self):
        a=self.annotate_boards(self.fixture('asus'),[self.board('ASUS')],'asus-prime-5070-ti-16gb')
        b=self.annotate_boards(self.fixture('pny','openai'),[self.board('PNY')],'pny-5070-ti-16gb')
        store.save(a);store.save(b)
        with patch.object(store,'backup') as backup,patch.object(store,'save') as save,patch.object(store,'delete') as delete:
            with self.assertRaisesRegex(ValueError,'board identities differ'):
                consolidate(['asus','pny'],'RTX 5070 Ti')
            backup.assert_not_called();save.assert_not_called();delete.assert_not_called()
        self.assertEqual(store.load('asus'),a);self.assertEqual(store.load('pny'),b)

    def test_strong_board_multiset_ignores_sensor_order_provenance_and_variant_aliases(self):
        cards=[self.board('PNY'),self.board('ASUS','RTX 5060 Ti',16,1)]
        a=self.annotate_boards(self.fixture('a'),cards,'pny-5070-ti-plus-asus-5060-ti-16gb')
        swapped=[self.board('asus','RTX5060TI',16.0,0),self.board('PNY Technologies','RTX5070TI',16.0,1)]
        b=self.annotate_boards(self.fixture('b','openai'),swapped,'PNY and ASUS dual GPU',source='Different confirmation wording')
        store.save(a);store.save(b)
        merged=consolidate(['a','b'],'Dual GPU')
        self.assertEqual(merged['results'],a['results']+b['results'])
        self.assertEqual(merged['source_runs'][0]['gpu_board_identity'],a['gpu_board_identity'])
        self.assertEqual(merged['source_runs'][1]['gpu_board_identity'],b['gpu_board_identity'])

    def test_known_variant_conflict_without_strong_identity_rejects_before_writes(self):
        a,b=self.fixture('a'),self.fixture('b','openai')
        a['classification']['board_variant']='asus-prime-5070-ti-16gb'
        b['classification']['board_variant']='pny-5070-ti-16gb'
        store.save(a);store.save(b)
        with patch.object(store,'backup') as backup:
            with self.assertRaisesRegex(ValueError,'board variants differ'):
                consolidate(['a','b'],'GPU')
            backup.assert_not_called()

    def test_legacy_unknown_board_identity_remains_compatible_and_rows_unchanged(self):
        a=self.fixture('legacy')
        a['classification']['board_variant']='unknown'
        b=self.annotate_boards(self.fixture('known','openai'),[self.board('PNY')],'pny-5070-ti-16gb')
        store.save(a);store.save(b)
        merged=consolidate(['legacy','known'],'GPU')
        self.assertEqual(merged['results'],a['results']+b['results'])
        self.assertEqual(merged['source_runs'][1]['gpu_board_identity'],b['gpu_board_identity'])

    def test_known_nonfirst_sources_cannot_merge_through_legacy_unknown_destination(self):
        runs=[self.fixture('legacy'),self.annotate_boards(self.fixture('asus'),[self.board('ASUS')]),
              self.annotate_boards(self.fixture('pny','openai'),[self.board('PNY')])]
        for run in runs:store.save(run)
        with patch.object(store,'backup') as backup:
            with self.assertRaisesRegex(ValueError,'board identities differ'):
                consolidate(['legacy','asus','pny'],'GPU')
            backup.assert_not_called()

    def test_board_multiset_preserves_card_multiplicity_and_nominal_memory(self):
        for cards in ([self.board('PNY'),self.board('PNY',index=1)],[self.board('PNY',memory=12)]):
            with self.subTest(cards=cards):
                a=self.annotate_boards(self.fixture('a'),[self.board('PNY')])
                b=self.annotate_boards(self.fixture('b','openai'),cards)
                store.save(a);store.save(b)
                with self.assertRaisesRegex(ValueError,'board identities differ'):
                    consolidate(['a','b'],'GPU')

    def test_conflicting_archived_or_hardware_board_evidence_is_not_silently_discarded(self):
        a=self.annotate_boards(self.fixture('a'),[self.board('PNY')])
        a['source_runs']=[{'gpu_board_identity':{'gpus':[self.board('ASUS')]}}]
        b=self.fixture('b','openai')
        store.save(a);store.save(b)
        with patch.object(store,'backup') as backup:
            with self.assertRaisesRegex(ValueError,'board identities conflict'):
                consolidate(['a','b'],'GPU')
            backup.assert_not_called()

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
