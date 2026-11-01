import copy
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

from wattever import hardware, server, store
from test_memory_topology import snapshot


class MemoryTopologyApiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.current = snapshot()
        self.current['hardware']['memory']['channel_topology'] = {
            'status':'inferred', 'source':'SMBIOS Type 17', 'method':'explicit_firmware_channel_slot_labels',
            'channel_count':4, 'populated_channel_count':4, 'active_channel_count':None,
            'captured_at':'2026-11-01T12:00:00Z'}
        self.current['hardware']['memory']['channel_count'] = 4
        for owner, key, value in ((store,'RUNS_DIR',temp.name+'/runs'),
            (server,'BENCH',SimpleNamespace(running=False)), (server,'CAP_BENCH',None),
            (server.power,'gpu_info',lambda index:copy.deepcopy(self.current['gpu']))):
            active = patch.object(owner,key,value)
            active.start(); self.addCleanup(active.stop)
        active = patch.object(hardware,'inventory',side_effect=lambda:copy.deepcopy(self.current['hardware']))
        self.probe=active.start(); self.addCleanup(active.stop)
        self.httpd=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        threading.Thread(target=self.httpd.serve_forever,daemon=True).start()
        self.addCleanup(self.httpd.server_close); self.addCleanup(self.httpd.shutdown)
        self.base='http://127.0.0.1:%d' % self.httpd.server_port

    def post(self,body):
        request=urllib.request.Request(self.base+'/api/hardware/memory',data=json.dumps(body).encode(),
            headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=5) as response: return json.load(response)

    def test_capture_and_later_annotation_preserve_inference_data(self):
        self.assertEqual(self.post({})['memory']['channel_count'],4)
        run=dict(snapshot(),id='saved',results=[{'status':'ok'}],sessions=[{'id':'old'}],
                 hardware_follow_ups=[{'kind':'memory_topology','status':'pending'}])
        store.save(run)
        with patch.object(store,'backup') as backup:
            self.post({'run_id':'saved'})
            backup.assert_called_once_with('saved')
        saved=store.load('saved')
        self.assertEqual(saved['results'],run['results'])
        self.assertEqual(saved['sessions'],run['sessions'])
        self.assertFalse(saved['hardware']['memory']['channel_topology']['historical_inference_conditions_verified'])
        self.assertEqual(saved['hardware_follow_ups'][0]['status'],'completed')

    def test_busy_missing_mismatched_and_unavailable_do_not_modify_saved_run(self):
        run=dict(snapshot(),id='saved',results=[])
        store.save(run)
        for body,expected,change in [({},409,'busy'),({'run_id':'missing'},404,None),
                ({'run_id':'saved'},400,'gpu'),({'run_id':'saved'},400,'unavailable')]:
            server.BENCH.running=change=='busy'
            self.current=snapshot()
            self.current['hardware']['memory']['channel_topology']={'status':'unavailable','channel_count':None}
            if change=='gpu': self.current['gpu']['name']='Other GPU'
            with self.assertRaises(urllib.error.HTTPError) as caught: self.post(body)
            self.assertEqual(caught.exception.code,expected)
            self.assertEqual(store.load('saved'),run)


if __name__=='__main__': unittest.main()
