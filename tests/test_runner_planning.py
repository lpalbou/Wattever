import copy
import unittest
from unittest.mock import MagicMock, patch
from contextlib import ExitStack

from wattever import runner


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(runner.DEFAULT_CONFIG, models=['a', 'b'], sizes=[512, 5000],
                        concurrency_levels=[1, 2], warmup=False)

    def test_shared_pool_scales_with_concurrency_and_margin(self):
        single, total = runner.context_budget(80000, self.cfg, 4)
        self.assertEqual(single, 84768)
        self.assertEqual(total, single * 4)
        self.assertGreater(single, 81312 + self.cfg['max_tokens'])
        self.assertEqual(runner.context_budget(80000, self.cfg, 2, 82000), (82000, 164000))
        self.assertEqual(runner.context_budget(80000, self.cfg, 1, 80000), (None, None))

    def test_checkpoint_keys_include_repeat_mode_and_concurrency(self):
        cfg = dict(self.cfg, repeats=2, mtp_modes=['off', 'on'])
        base = dict(model='a', target_tokens=512, concurrency=2, mtp=True)
        run = {'results': [dict(base, repeat=0, status='ok'), dict(base, repeat=1, status='cancelled'),
                           dict(base, model='other', status='ok')]}
        self.assertEqual(runner.completed_cases(run, cfg), {('a', True, 2, 512, 0)})
        run['results'] = [dict(base, status='failed')]
        self.assertEqual(len(runner.completed_cases(run, cfg)), 2)

    def schedule(self, fixed=False, rows=None):
        r = runner.BenchRunner()
        r.cli = MagicMock()
        r.cli.server_running.return_value = True
        cfg = dict(self.cfg, context_policy='fixed' if fixed else 'per_test')
        run = dict(id='mock', gpu={}, results=copy.deepcopy(rows or []))
        sampler = MagicMock(source='mock', error=None)
        sampler.measure_idle.return_value = {}
        r._model_max_context = MagicMock(return_value=100000)
        loads, tests = [], []
        def load(model, needed, cfg, mtp, parallel):
            loads.append((model, needed, parallel))
            return True, {'load_s': 1}, 'max'
        def one(run, cfg, model, size, rep, reps, detail, offload, needed, vram, cpt, mtp, concurrency):
            if rows and model == 'a' and size == 5000 and concurrency == 1:
                self.assertEqual(cpt, 4.7)
            tests.append((model, concurrency, size))
            return dict(model=model, target_tokens=size, concurrency=concurrency, mtp=mtp,
                        repeat=rep, status='ok', _cpt=4.5)
        r._load_with_fallback = load
        r._one_test = one
        with patch.object(runner.power, 'PowerSampler', return_value=sampler), patch.object(runner.store, 'save'):
            r._run(run, cfg)
        self.assertEqual(run['status'], 'completed', run.get('error'))
        self.assertEqual(r.live['done'], 8)
        return loads, tests, run

    def test_every_model_finishes_before_next_model(self):
        loads, tests, _ = self.schedule()
        self.assertEqual([x[0] for x in tests], ['a'] * 4 + ['b'] * 4)
        self.assertEqual(len(loads), 8)
        self.assertEqual(loads[2][1], runner.context_budget(512, self.cfg, 2)[1])

    def test_fixed_allocation_loads_once_per_model(self):
        loads, tests, run = self.schedule(fixed=True)
        self.assertEqual(len(loads), 2)
        self.assertTrue(all(x[2] == 2 for x in loads))
        self.assertEqual([x['load_reused'] for x in run['results']], [False, True, True, True] * 2)

    def test_resume_skips_completed_work(self):
        row = dict(model='a', target_tokens=512, concurrency=1, mtp=False,
                   repeat=0, status='ok', chars_per_token_after=4.7)
        _, tests, run = self.schedule(rows=[row])
        self.assertEqual(len(tests), 7)
        self.assertNotIn(('a', 1, 512), tests)
        self.assertEqual(run['results'][0], row)

    def test_resume_preserves_source_and_starts_new_file(self):
        cfg = dict(self.cfg)
        source = dict(id='original', status='cancelled', label='My run', config=cfg,
                      classification={'hardware_class': 'same'}, gpu={'name': 'mock'},
                      corpus={'sha256': 'same'}, lms_version='v',
                      results=[dict(model='a', target_tokens=512, repeat=0, status='ok'),
                               dict(model='a', target_tokens=5000, repeat=0, status='cancelled')])
        original = copy.deepcopy(source)
        r = runner.BenchRunner()
        r.cli = MagicMock()
        r.cli.version.return_value = 'v'
        def prepare(run):
            run.update(classification={'hardware_class': 'same'}, application={'version': 'test'})
        with ExitStack() as stack:
            stack.enter_context(patch.object(runner.power, 'gpu_info', return_value={'name': 'mock'}))
            stack.enter_context(patch.object(runner.store, 'host_info', return_value={}))
            stack.enter_context(patch.object(runner.store, 'prepare_run', side_effect=prepare))
            stack.enter_context(patch.object(runner.store, 'new_run_id', return_value='new'))
            stack.enter_context(patch.object(runner.corpus, 'master_stats', return_value={'sha256': 'same'}))
            save = stack.enter_context(patch.object(runner.store, 'save'))
            stack.enter_context(patch.object(runner.store, 'save_config'))
            stack.enter_context(patch.object(runner.threading, 'Thread'))
            self.assertEqual(r._start(cfg, resume_source=source), 'new')
            self.assertEqual(source, original)
            self.assertEqual(r.run['resumed_from'], 'original')
            self.assertEqual(len(r.run['results']), 1)
            self.assertEqual(save.call_args.args[0]['id'], 'new')
            r.thread = None
            source['config'] = dict(cfg, context_planner_version=0)
            with self.assertRaisesRegex(ValueError, 'older context planner'):
                r._start(source['config'], resume_source=source)
            source['config'] = cfg
            source['gpu']['driver'] = 'changed'
            with self.assertRaisesRegex(ValueError, 'GPU or driver'):
                r._start(cfg, resume_source=source)
            source['gpu'] = {'name': 'mock'}
            source['corpus']['sha256'] = 'changed'
            with self.assertRaisesRegex(ValueError, 'corpus differs'):
                r._start(cfg, resume_source=source)
            source['corpus']['sha256'] = 'same'
            source['classification']['hardware_class'] = 'different'
            with self.assertRaisesRegex(ValueError, 'Hardware differs'):
                r._start(cfg, resume_source=source)


if __name__ == '__main__':
    unittest.main()
