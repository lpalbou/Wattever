import copy
import json
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from wattever import runner, store


class ExtensionTests(unittest.TestCase):
    def source(self, legacy=False):
        cfg = dict(runner.DEFAULT_CONFIG, models=['old'], sizes=[512, 10000], concurrency_levels=[1, 2])
        if legacy:
            cfg.pop('context_planner_version')
            cfg.pop('context_safety_fraction')
        return dict(id='original', created_at='2026-10-27T00:00:00Z', label='My GPU', notes='Keep me',
                    status='cancelled', config=cfg, gpu={'name': 'GPU', 'driver': '1', 'vram_total_mb': 16000},
                    classification={'hardware_class': 'same'}, application={'version': 'old'},
                    corpus={'sha256': 'same'}, host={}, lms_version='v', results=[
                        dict(model='old', target_tokens=512, repeat=0, concurrency=1, status='ok', prefill_tps=100),
                        dict(model='old', target_tokens=512, repeat=0, concurrency=2, status='failed'),
                        dict(model='old', target_tokens=10000, repeat=0, concurrency=2, status='skipped')])

    def selection(self, **changes):
        return dict(run_id='original', model_modes={'old': ['on'], 'new': ['off']},
                    sizes=[512,10000], concurrency_levels=[1,2], retry_failed=False, **changes)

    def test_per_model_modes_preview_excludes_unrequested_techniques(self):
        source = self.source()
        cfg = runner.extension_config(source, self.selection())
        cases = runner.planned_cases(cfg)
        self.assertEqual(len(cases), 8)
        self.assertNotIn(('new', True, 1, 512, 0), cases)
        self.assertNotIn(('old', False, 1, 512, 0), cases)
        plan = runner.extension_plan(source, cfg)
        self.assertEqual((plan['new_tests'], plan['retry_tests'], plan['total_tests']), (8, 0, 8))

    def test_retry_preview_keeps_successes_and_includes_skipped(self):
        source = self.source()
        cfg = runner.extension_config(source, dict(self.selection(), model_modes={'old': ['off']}))
        self.assertEqual(runner.extension_plan(source, cfg)['total_tests'], 1)
        plan = runner.extension_plan(source, cfg, retry_failed=True)
        self.assertEqual((plan['existing'], plan['retry_tests'], plan['new_tests']), (1,2,1))

    def test_rerun_replaces_only_selected_successes_and_archives_measurement(self):
        source = self.source()
        original = copy.deepcopy(source)
        selection = dict(self.selection(),model_modes={'old':['off']},sizes=[512],concurrency_levels=[1])
        cfg = runner.extension_config(source,selection)
        preview = runner.extension_plan(source,cfg,rerun_existing=True)
        self.assertEqual((preview['new_tests'],preview['retry_tests'],preview['rerun_tests'],preview['total_tests']), (0,0,1,1))
        r = runner.BenchRunner()
        r.cli = MagicMock()
        r.cli.version.return_value = 'v'
        def prepare(run):
            run.update(classification={'hardware_class':'same'},application={'version':'new'})
        with ExitStack() as stack:
            stack.enter_context(patch.object(runner.power,'gpu_info',return_value=source['gpu']))
            stack.enter_context(patch.object(store,'host_info',return_value={}))
            stack.enter_context(patch.object(store,'prepare_run',side_effect=prepare))
            stack.enter_context(patch.object(runner.corpus,'master_stats',return_value={'sha256':'same'}))
            stack.enter_context(patch.object(runner.threading,'Thread'))
            stack.enter_context(patch.object(store,'save'))
            stack.enter_context(patch.object(store,'save_config'))
            backup = stack.enter_context(patch.object(store,'backup'))
            self.assertEqual(r._start(cfg,resume_source=source,extend_in_place=True,rerun_existing=True),'original')
            self.assertEqual(source,original)
            self.assertEqual(len(r.run['results']),2)
            self.assertTrue(all(row['concurrency']==2 for row in r.run['results']))
            self.assertEqual(r.run['previous_attempts'][0]['prefill_tps'],100)
            self.assertEqual(r.run['previous_attempts'][0]['archive_reason'],'rerun')
            self.assertEqual(r.live['done'],0)
            self.assertEqual(r.live['total'],1)
            self.assertEqual(runner.completed_cases(r.run,cfg),set())
            backup.assert_called_once_with('original')

    def test_invalid_selections_rejected(self):
        for changes in ({'model_modes': {}}, {'model_modes': {'a': ['bad']}}, {'sizes': []},
                        {'sizes': ['10']}, {'concurrency_levels': [101]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                runner.extension_config(self.source(), dict(self.selection(), **changes))

    def test_same_file_extension_backs_up_and_preserves_success_and_provenance(self):
        source = self.source(legacy=True)
        source['status'] = 'running'  # A hard-interrupted file retains this on disk.
        original = copy.deepcopy(source)
        cfg = runner.extension_config(source, dict(self.selection(), model_modes={'old':['off'], 'new':['off']}))
        r = runner.BenchRunner()
        r.cli = MagicMock()
        r.cli.version.return_value = 'v'
        def prepare(run):
            run.update(classification={'hardware_class': 'same'}, application={'version': 'new'})
        with TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch.object(store, 'RUNS_DIR', str(Path(directory)/'runs')))
            stack.enter_context(patch.object(store, 'CONFIG_PATH', str(Path(directory)/'config.json')))
            stack.enter_context(patch.object(runner.power, 'gpu_info', return_value=source['gpu']))
            stack.enter_context(patch.object(store, 'host_info', return_value={}))
            stack.enter_context(patch.object(store, 'prepare_run', side_effect=prepare))
            stack.enter_context(patch.object(runner.corpus, 'master_stats', return_value={'sha256': 'same'}))
            stack.enter_context(patch.object(runner.threading, 'Thread'))
            store.save(source)
            raw = Path(store.path_for(source['id'])).read_bytes()
            self.assertEqual(r._start(cfg, resume_source=source, extend_in_place=True, retry_failed=True), 'original')
            saved = store.load('original')
            self.assertEqual(source, original)
            self.assertEqual(saved['label'], 'My GPU')
            self.assertEqual(saved['created_at'], original['created_at'])
            self.assertEqual(saved['notes'], 'Keep me')
            self.assertEqual(len(saved['results']), 1)
            self.assertEqual(saved['results'][0]['prefill_tps'], 100)
            self.assertEqual(saved['results'][0]['session_id'], 'initial')
            self.assertEqual(len(saved['previous_attempts']), 2)
            self.assertEqual(saved['sessions'][0]['config'], original['config'])
            self.assertEqual(saved['sessions'][0]['status'], 'interrupted')
            self.assertEqual(saved['sessions'][1]['config']['context_planner_version'], 1)
            self.assertEqual(saved['config']['models'], ['old','new'])
            self.assertEqual(saved['application']['version'], 'old')
            backups = list((Path(directory)/'.history').glob('*.json'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), raw)
            self.assertEqual(len(list(Path(store.RUNS_DIR).glob('*.json'))), 1)
            self.assertEqual(r.live['done'], 1)
            self.assertEqual(r.live['total'], 8)
            sampler = MagicMock(source='mock')
            sampler.measure_idle.return_value = {}
            r._warmup = MagicMock()
            r._model_max_context = MagicMock(return_value=100000)
            r._load_with_fallback = MagicMock(return_value=(True, {}, 'max'))
            measured = []
            def measure(run, cfg, model, size, rep, reps, detail, offload, needed, vram, cpt, mtp, concurrency):
                measured.append((model,mtp,concurrency,size,rep))
                return dict(model=model,target_tokens=size,repeat=rep,mtp=mtp,concurrency=concurrency,status='ok')
            r._one_test = measure
            with patch.object(runner.power,'PowerSampler',return_value=sampler):
                r._run(r.run,cfg)
            self.assertEqual(len(measured),7)
            self.assertNotIn(('old',False,1,512,0), measured)
            self.assertEqual([case[0] for case in measured],['old']*3+['new']*4)
            self.assertEqual(store.load('original')['status'],'completed')
            self.assertEqual(len(store.load('original')['results']),8)
            self.assertEqual(store.load('original')['remaining_tests'],0)
            self.assertIn('context_planner_version', store.to_csv([store.load('original')]))

    def test_legacy_failure_does_not_block_corrected_new_work(self):
        source = self.source(legacy=True)
        cfg = runner.extension_config(source, dict(self.selection(), model_modes={'old':['off']},
                                                  sizes=[512,10000,20000]))
        cfg['warmup'] = False
        r = runner.BenchRunner()
        r.cli = MagicMock()
        r._model_max_context = MagicMock(return_value=100000)
        r._load_with_fallback = MagicMock(return_value=(True, {}, 'max'))
        calls = []
        def measure(run, cfg, model, size, rep, reps, detail, offload, needed, vram, cpt, mtp, concurrency):
            calls.append((size,concurrency))
            return dict(model=model,target_tokens=size,repeat=rep,mtp=mtp,concurrency=concurrency,status='ok')
        r._one_test = measure
        run = copy.deepcopy(source)
        run.update(active_session_id='new',sessions=[],status='running')
        for row in run['results']:
            row['session_id'] = 'initial'
        sampler = MagicMock(source='mock')
        sampler.measure_idle.return_value = {}
        with patch.object(runner.power,'PowerSampler',return_value=sampler), patch.object(store,'save'):
            r._run(run,cfg)
        self.assertEqual(run['status'],'completed',run.get('error'))
        self.assertEqual(calls,[(10000,1),(20000,1),(20000,2)])
        self.assertEqual(r.live['done'],6)
        self.assertTrue(all(row['session_id']=='new' for row in run['results'][3:]))

    def test_busy_extension_cannot_modify_source(self):
        r = runner.BenchRunner()
        r.thread = MagicMock()
        r.thread.is_alive.return_value = True
        with patch.object(store, 'save') as save, patch.object(store, 'backup') as backup:
            with self.assertRaisesRegex(RuntimeError, 'running benchmark'):
                r.extend(self.selection())
            save.assert_not_called()
            backup.assert_not_called()

    def test_session_completion_retains_incomplete_original_work(self):
        source = self.source()
        new_cfg = runner.extension_config(source, dict(self.selection(), model_modes={'new':['on']}, sizes=[512], concurrency_levels=[1]))
        source.update(active_session_id='new', status='completed', finished_at='now',
                      config=runner.merged_config(source['config'], new_cfg),
                      sessions=[{'id':'initial','config': self.source()['config']}, {'id':'new','config':new_cfg}])
        source['results'].append(dict(model='new',mtp=True,concurrency=1,target_tokens=512,repeat=0,status='ok'))
        runner.BenchRunner()._finish_session(source)
        self.assertEqual(source['status'], 'interrupted')
        self.assertEqual(source['remaining_tests'], 1)
        self.assertEqual(source['sessions'][1]['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
