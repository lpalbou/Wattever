import copy
import json
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

from wattever import bandwidth, server, store


class BandwidthApiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.gpu = {'name':'GPU', 'driver':'1', 'vram_total_mb':16000}
        self.profile = {'schema_version':1, 'protocol':'wattever-transfer-v1', 'captured_at':'2026-10-31T22:00:00Z',
                        'unit':'bytes_per_second','scope':'benchmark_client',
                        'storage_to_ram':{'status':'unavailable','reason':'fake'},
                        'ram_to_vram':{'status':'unavailable','reason':'fake'}}
        def prepare(run): run.update(classification={'hardware_class':'same'})
        for owner, key, value in ((store,'RUNS_DIR',temp.name+'/runs'),
            (server,'BENCH',SimpleNamespace(running=False)), (server,'CAP_BENCH',None),
            (server.power,'gpu_info',lambda index:copy.deepcopy(self.gpu)),
            (store,'host_info',lambda:{}), (store,'prepare_run',prepare)):
            active = patch.object(owner,key,value)
            active.start(); self.addCleanup(active.stop)
        probe = patch.object(bandwidth,'measure',return_value=copy.deepcopy(self.profile))
        self.probe = probe.start(); self.addCleanup(probe.stop)
        self.httpd = ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        threading.Thread(target=self.httpd.serve_forever,daemon=True).start()
        self.addCleanup(self.httpd.server_close); self.addCleanup(self.httpd.shutdown)
        self.base = 'http://127.0.0.1:%d' % self.httpd.server_port

    def post(self, body):
        request = urllib.request.Request(self.base+'/api/hardware/bandwidth',data=json.dumps(body).encode(),
            headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=5) as response: return json.load(response)

    def test_historical_annotation_is_backed_up_and_keeps_raw_rows(self):
        run = {'id':'saved','classification':{'hardware_class':'same'},'gpu':copy.deepcopy(self.gpu),
               'results':[{'status':'ok','prefill_tps':123}], 'sessions':[{'id':'old'}]}
        store.save(run)
        with patch.object(store,'backup') as backup:
            result = self.post({'run_id':'saved'})
            backup.assert_called_once_with('saved')
        saved = store.load('saved')
        self.assertEqual(result['transfer_bandwidth'],self.profile)
        self.assertEqual(saved['results'],run['results'])
        self.assertEqual(saved['sessions'],run['sessions'])
        self.assertEqual(saved['hardware']['transfer_bandwidth']['provenance'],'measured_later_on_matching_hardware')

    def test_rejects_busy_or_changed_hardware_before_probe(self):
        server.BENCH.running = True
        with self.assertRaises(urllib.error.HTTPError) as caught: self.post({})
        self.assertEqual(caught.exception.code,409)
        server.BENCH.running = False
        store.save({'id':'other','classification':{'hardware_class':'other'},'gpu':self.gpu})
        with self.assertRaises(urllib.error.HTTPError) as caught: self.post({'run_id':'other'})
        self.assertEqual(caught.exception.code,400)
        self.probe.assert_not_called()


if __name__ == '__main__': unittest.main()
