"""External MTP intent is metadata, separate from serving behavior and API fields."""
import unittest
from unittest.mock import Mock, patch

from wattever import endpoints, runner


class ExternalMtpMetadataTests(unittest.TestCase):
    def start_fake(self, requested):
        cfg = dict(provider="openai", endpoint_base_url="http://127.0.0.1:8097/v1",
                   endpoint_inference_local=True, models=["example-model"], sizes=[512],
                   concurrency_levels=[1], requested_mtp=requested, reported_mtp="on",
                   hardware_probe_policy="defer", endpoint_reasoning_effort="none",
                   reproducibility={"engine":"Maya","engine_version":"1.1.52",
                                    "quantization":"Mixed IQ1_S + IQ2_XXS","kv_cache_dtype":"int8"})
        resolve = endpoints.resolve_identity
        thread = Mock(); thread.is_alive.return_value = False
        def prepare(run):
            run.update(application={}, hardware={}, measurement_scope={})
        with patch.object(endpoints,"resolve_identity",side_effect=lambda value: resolve(value,evidence={"service":"maya"},probe=False)), \
             patch.object(endpoints,"_opaque_route",return_value="local-maya-test"), \
             patch.object(runner.power,"gpu_info",return_value={"name":"Test GPU"}), \
             patch.object(runner.store,"host_info",return_value={}), \
             patch.object(runner.store,"new_run_id",return_value="fake-mtp-metadata"), \
             patch.object(runner.store,"prepare_run",side_effect=prepare), \
             patch.object(runner.store,"save"),patch.object(runner.store,"save_config"), \
             patch.object(runner.corpus,"master_stats",return_value={}), \
             patch.object(runner.threading,"Thread",return_value=thread):
            value=runner.BenchRunner();value.start(cfg)
        return value

    def test_start_and_record_preserve_intent_without_inventing_effective_mtp(self):
        for intent in ("on","off","unknown"):
            with self.subTest(intent=intent):
                value=self.start_fake(intent)
                self.assertEqual(value.run["config"]["requested_mtp"],intent)
                first=value.run["model_configurations"][0]["mtp"]
                self.assertEqual(first["requested"],intent)
                self.assertEqual(first["user_reported"],"on")
                self.assertEqual(first["effective"],"unknown")
                row=dict(model="example-model",target_tokens=512,concurrency=1,repeat=0,status="ok",mtp=False)
                with patch.object(runner.store,"save"):
                    value._record(value.run,row,1)
                self.assertIsNone(row["mtp"])
                self.assertEqual(row["mtp_source"],"unknown; externally managed")
                self.assertEqual(row["model_configuration"]["mtp"]["requested"],intent)
                self.assertEqual(row["model_configuration"]["mtp"]["effective"],"unknown")

    def test_annotation_never_becomes_nonstandard_inference_argument(self):
        value=runner.BenchRunner()
        cfg=dict(provider="openai",endpoint_base_url="http://127.0.0.1:8097/v1",
                 endpoint_reasoning_effort="none",requested_mtp="on",reported_mtp="on")
        with patch.object(runner.lmstudio,"measure_completion",return_value={}) as complete:
            value._measure_completion(cfg,"example-model","prompt",256)
        self.assertNotIn("requested_mtp",complete.call_args.kwargs)
        self.assertNotIn("reported_mtp",complete.call_args.kwargs)
        self.assertNotIn("mtp",complete.call_args.kwargs)

    def test_invalid_annotation_rejected_before_worker_starts(self):
        for invalid in (True,"enabled",1):
            with self.subTest(invalid=invalid),self.assertRaisesRegex(ValueError,"requested_mtp"):
                self.start_fake(invalid)


if __name__=="__main__":unittest.main()
