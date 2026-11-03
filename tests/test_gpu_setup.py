import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
import unittest
from unittest.mock import patch

from wattever import gpu_setup


class GpuSetupTests(unittest.TestCase):
    def test_http_route_stages_only_when_idle(self):
        from wattever import server
        with tempfile.TemporaryDirectory() as directory, patch.object(gpu_setup,'control_dir',return_value=Path(directory)), \
             patch.object(gpu_setup.power,'list_gpus',return_value=[{'index':0},{'index':1}]), \
             patch.object(server,'BENCH',SimpleNamespace(running=False)), patch.object(server,'CAP_BENCH',None):
            httpd=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
            try:
                def post():
                    request=urllib.request.Request('http://127.0.0.1:%d/api/hardware/gpu-setup'%httpd.server_port,
                        data=b'{"mode":"auto"}',headers={'Content-Type':'application/json'})
                    with urllib.request.urlopen(request,timeout=3) as response:return json.load(response)
                self.assertFalse(post()['model_started'])
                server.BENCH.running=True
                with self.assertRaises(urllib.error.HTTPError) as caught:post()
                self.assertEqual(caught.exception.code,409)
            finally:
                httpd.shutdown();httpd.server_close();thread.join(3)
                self.assertFalse(thread.is_alive())

    def test_staging_only_writes_a_choice_and_preserves_other_desktop_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            settings=root/'desktop-settings.json';settings.write_text('{"reasoning_effort":"none"}')
            with patch.object(gpu_setup,'control_dir',return_value=root), patch.object(gpu_setup.power,'list_gpus',return_value=[{'index':0},{'index':1}]):
                result=gpu_setup.stage({'mode':'single','gpu_index':1})
                self.assertFalse(result['model_started']);self.assertFalse(result['model_switched'])
                self.assertEqual(gpu_setup.status()['strata']['choice']['gpu_index'],1)
                self.assertEqual(json.loads(settings.read_text()),{'reasoning_effort':'none'})
                with self.assertRaises(ValueError):gpu_setup.stage({'mode':'single','gpu_index':2})
                self.assertEqual(gpu_setup.status()['strata']['choice']['gpu_index'],1)

    def test_unsupported_integration_and_unknown_keys_never_write(self):
        with patch.object(gpu_setup,'control_dir',return_value=None):
            self.assertFalse(gpu_setup.status()['strata']['available'])
            with self.assertRaises(ValueError):gpu_setup.stage({'mode':'auto'})
        for value in ({'mode':'auto','command':'load'}, {'mode':'single','gpu_index':True}, {'mode':'all'}, {'mode':'auto','schema_version':2}):
            with self.assertRaises(ValueError):gpu_setup.validate(value)

    def test_per_card_transfer_validation_retains_failures_but_rejects_duplicate_devices(self):
        from wattever.validation import validate_transfer_bandwidth
        profile={'schema_version':1,'protocol':'wattever-transfer-v1','unit':'bytes_per_second','scope':'benchmark_client',
                 'captured_at':'2026-11-02T16:00:00Z','storage_to_ram':{'status':'unavailable','reason':'not tested'},
                 'ram_to_vram':{'status':'unavailable','reason':'busy'},'ram_to_vram_devices':[
                    {'gpu_sensor_index':0,'gpu_name':'GPU 0','status':'ok','bytes_per_second':10,'samples':[{'bytes':100,'elapsed_s':10}],
                     'method':'fake','statistic':'median'},
                    {'gpu_sensor_index':1,'gpu_name':'GPU 1','status':'unavailable','reason':'busy'}]}
        self.assertEqual(validate_transfer_bandwidth(profile),[])
        profile['ram_to_vram_devices'][1]['gpu_sensor_index']=0
        self.assertTrue(any('unique' in error for error in validate_transfer_bandwidth(profile)))
        profile['ram_to_vram_devices'][1]['gpu_sensor_index']=1
        profile['ram_to_vram_devices'][0]['bytes_per_second']=999
        self.assertTrue(any('median' in error for error in validate_transfer_bandwidth(profile)))
