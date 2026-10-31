import copy
import tempfile
import unittest
from unittest.mock import patch

from wattever import store


class ComparisonGroupTests(unittest.TestCase):
    def run_fixture(self, id, provider='lmstudio'):
        return {'id':id, 'status':'completed', 'classification':{'hardware_class':'machine'},
                'gpu':{'name':'GPU','driver':'1','vram_total_mb':16000},
                'config':{'provider':provider, 'endpoint_inference_local':True},
                'results':[{'model':id,'status':'ok','decode_tps':123}]}

    def test_link_preserves_engine_settings_and_measurements(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(store,'RUNS_DIR',directory), \
             patch.object(store,'backup') as backup:
            a,b = self.run_fixture('managed'), self.run_fixture('strata','openai')
            store.save(a); store.save(b)
            group = store.link_runs(['managed','strata'],'5070 Ti')
            self.assertEqual(group['run_ids'],['managed','strata'])
            for old in (a,b):
                saved = store.load(old['id'])
                self.assertEqual(saved['results'],old['results'])
                self.assertEqual(saved['config'],old['config'])
                self.assertEqual(store.summarise(saved)['comparison_group'], group)
            self.assertEqual(backup.call_count,2)

    def test_rejects_different_gpu_or_remote_inference_before_writes(self):
        for change in ('gpu','remote'):
            a,b = self.run_fixture('a'), self.run_fixture('b','openai')
            if change == 'gpu': b['gpu']['name'] = 'other GPU'
            else: b['config']['endpoint_inference_local'] = False
            with patch.object(store,'load',side_effect=[a,b]), patch.object(store,'save') as save, \
                 patch.object(store,'backup') as backup:
                with self.assertRaises(ValueError): store.link_runs(['a','b'])
                save.assert_not_called(); backup.assert_not_called()


if __name__ == '__main__':
    unittest.main()
