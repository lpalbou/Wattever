import threading
import unittest
from unittest.mock import Mock
from wattever import runner

class WatchdogTests(unittest.TestCase):
    def test_budget_scales_with_context_and_caps(self):
        run={'results':[{'model':'m','status':'ok','concurrency':1,'target_tokens':20000,'prefill_tps':500}]}
        cfg={'per_test_timeout_s':1800}
        self.assertEqual(runner.request_deadline(run,cfg,'m',40000,1)[0],600)
        self.assertEqual(runner.request_deadline(run,cfg,'m',40000,4)[0],1800)
        self.assertEqual(runner.request_deadline({},cfg,'m',40000,1)[0],1800)

    def test_recovery_requires_unload_and_worker_exit(self):
        bench=runner.BenchRunner(); bench.cli=Mock(); bench.cli.unload_confirmed.return_value=False
        with self.assertRaises(runner.RecoveryError): bench._recover_requests('m',[],[])
        bench.cli.unload_confirmed.return_value=True
        worker=Mock(); worker.is_alive.return_value=True
        with self.assertRaises(runner.RecoveryError): bench._recover_requests('m',[worker],[])
        worker.is_alive.return_value=False
        bench._recover_requests('m',[worker],[])

if __name__ == '__main__': unittest.main()
