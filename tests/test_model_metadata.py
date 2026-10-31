import copy
import unittest
from unittest.mock import patch
from wattever.model_metadata import quantization_metadata, backfill_run_metadata
from wattever.identity import build_model_configuration, verify_model_configuration


class QuantizationMetadataTests(unittest.TestCase):
    def test_generic_discovery_and_saved_rows_preserve_structured_quantization(self):
        from wattever import endpoints, runner, store
        model = endpoints._model_entry({'id':'alias','quantization':{'name':'Q4_0','method':'QAT'}})
        self.assertEqual((model['quantization'],model['quantization_method']),('Q4_0','QAT'))
        bench = runner.BenchRunner()
        bench._runtime_config = {'provider':'openai','models':['alias']}
        bench._endpoint_model_catalog = {'alias':model}
        run = {'id':'generic','results':[],'config':bench._runtime_config}
        with patch.object(store,'save'):
            bench._record(run, {'model':'alias','status':'ok'},1)
        saved = run['results'][0]
        self.assertEqual((saved['quantization'],saved['quantization_method']),('Q4_0','QAT'))
        self.assertEqual(saved['model_configuration']['quantization']['source'],'endpoint_reported')
        verify_model_configuration(saved['model_configuration'])

    def test_method_is_independent_of_format_and_uses_catalog_labels(self):
        for name,method in [('Gemma 4 12B QAT','QAT'),('LFM2.5 QAD','QAD'),('Qwen 27B UD','UD')]:
            meta=quantization_metadata({'id':'alias','display_name':name,'quantization':{'name':'Q4_0'}})
            self.assertEqual((meta['quantization'],meta['quantization_method']),('Q4_0',method))
            self.assertEqual(meta['quantization_method_source'],'endpoint_reported')
        self.assertIsNone(quantization_metadata({'id':'model','quantization':'Q4_0'})['quantization_method'])
        self.assertIsNone(quantization_metadata({'id':'equation-qatapult'})['quantization_method'])
        self.assertEqual(quantization_metadata({'id':'model-qat'})['quantization_method_source'],'model_identifier')

    def test_method_changes_configuration_identity_and_legacy_remains_valid(self):
        cfg={'models':['alias']}
        plain=build_model_configuration(cfg,model='alias',effective={'quantization':'Q4_0'},require_endpoint=False)
        qat=build_model_configuration(cfg,model='alias',effective={'quantization':'Q4_0','quantization_method':'QAT'},require_endpoint=False)
        self.assertNotEqual(plain['configuration_id'],qat['configuration_id'])
        verify_model_configuration(plain);verify_model_configuration(qat)

    def test_backfill_preserves_measurements_and_does_not_overwrite_mismatched_format(self):
        identity=build_model_configuration({'models':['alias']},model='alias',effective={'quantization':'Q4_0'},require_endpoint=False)
        row={'model':'alias','quantization':'Q4_0','status':'ok','decode_tps':123.4,'model_configuration':identity,'configuration_id':identity['configuration_id']}
        other={'model':'alias','quantization':'Q8_0','status':'ok','decode_tps':70}
        run={'results':[copy.deepcopy(row),copy.deepcopy(other)]}
        backfill_run_metadata(run,[{'id':'alias','display_name':'Gemma QAT','quantization':'Q4_0'}],'now')
        self.assertEqual(run['results'][0]['decode_tps'],123.4)
        self.assertEqual(run['results'][1],other)
        self.assertEqual(run['results'][0]['quantization_method_source'],'lmstudio_catalog_backfill')
        self.assertNotEqual(run['results'][0]['configuration_id'],row['configuration_id'])
        verify_model_configuration(run['results'][0]['model_configuration'])

    def test_generic_endpoint_cannot_inherit_a_previous_local_catalog(self):
        from wattever import runner,store
        bench=runner.BenchRunner()
        bench._runtime_config={'provider':'openai','models':['alias']}
        bench._model_catalog={'alias':{'id':'alias','quantization':'Q4_0','quantization_method':'QAT'}}
        run={'id':'generic','results':[],'config':bench._runtime_config}
        with patch.object(store,'save'):
            bench._record(run,{'model':'alias','status':'ok'},1)
        self.assertIsNone(run['results'][0]['quantization'])
        self.assertIsNone(run['results'][0]['quantization_method'])
