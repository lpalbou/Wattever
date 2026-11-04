"""Accuracy telemetry and deferred performance starts never run transfer probes."""
import copy
import unittest
from unittest.mock import Mock, patch

from wattever import bandwidth, runner, server


class HardwareProbePolicyTests(unittest.TestCase):
    def setUp(self):
        self.machine = {"gpus": [{"name": "GPU A", "sensor_index": 0}]}
        self.profile = {"captured_at": "2026-11-03T10:00:00+00:00", "source": "saved_capture",
                        "saved_capture": {"source_run_id": "prior", "measured_this_helper_session": False}}

    def test_defer_reuses_only_verified_saved_profile_with_original_timestamp(self):
        run = {"hardware": {}, "gpu": {"name": "GPU A"}, "active_session_id": "current",
               "sessions": [{"id": "current", "hardware": self.machine}]}
        original = copy.deepcopy(self.profile)
        with patch.object(bandwidth, "measure", side_effect=AssertionError("No transfer workload")), \
             patch.object(bandwidth, "saved_capture", return_value=self.profile) as saved:
            runner._attach_transfer_profile(run, {"hardware_probe_policy": "defer"})
        saved.assert_called_once_with(self.machine, run["gpu"])
        self.assertEqual(self.profile, original)
        self.assertEqual(run["hardware"]["transfer_bandwidth"]["captured_at"], original["captured_at"])
        self.assertEqual(run["hardware_probe"]["status"], "reused_saved_capture")
        self.assertFalse(run["hardware_probe"]["measured_this_session"])
        self.assertEqual(run["sessions"][0]["hardware_probe"]["captured_at"], original["captured_at"])

    def test_mismatched_saved_profile_stays_unavailable_not_silently_fresh(self):
        run = {"hardware": self.machine}
        with patch.object(bandwidth, "measure", side_effect=AssertionError("No CUDA/RAM/storage probe")), \
             patch.object(bandwidth, "saved_capture", return_value=None), \
             patch.object(bandwidth, "_latest", {"captured_at": "unverified-old-machine"}):
            self.assertIsNone(runner._attach_transfer_profile(run, {"hardware_probe_policy": "defer"}))
        self.assertNotIn("transfer_bandwidth", run["hardware"])
        self.assertNotIn("captured_at", run["hardware_probe"])
        self.assertEqual(run["hardware_probe"]["status"], "deferred")
        self.assertEqual(run["hardware_probe"]["source"], "not_measured")

    def test_default_auto_keeps_existing_measurement_behavior(self):
        run = {"hardware": self.machine}
        with patch.object(bandwidth, "measure", return_value=self.profile) as measure, \
             patch.object(bandwidth, "saved_capture", side_effect=AssertionError("Auto must still measure")):
            runner._attach_transfer_profile(run, {"gpu_index": 0})
        measure.assert_called_once_with(0, directory=None)
        self.assertEqual(runner.DEFAULT_CONFIG["hardware_probe_policy"], "auto")
        self.assertTrue(run["hardware_probe"]["measured_this_session"])

    def test_invalid_policy_rejected_before_endpoint_or_hardware_calls(self):
        value = runner.BenchRunner()
        for invalid in (None, "", "always", True, [], {}):
            with self.subTest(invalid=invalid), patch.object(runner.endpoints, "validate", side_effect=AssertionError("No endpoint calls")):
                with self.assertRaisesRegex(ValueError, "hardware_probe_policy"):
                    value.start({"hardware_probe_policy": invalid})

    def test_accuracy_start_never_probes_even_if_config_requests_auto(self):
        for saved in (None, self.profile):
            for local in (False, True):
                with self.subTest(saved=bool(saved), local=local):
                    run, cfg = {}, {"endpoint_inference_local": local, "hardware_probe_policy": "auto"}
                    sampler = Mock()
                    sampler.measurement_scope.return_value = {"gpu_count": 2}
                    with patch.object(server.hardware, "inventory", return_value=copy.deepcopy(self.machine)), \
                         patch.object(bandwidth, "measure", side_effect=AssertionError("Accuracy must never launch CUDA/RAM/storage workloads")), \
                         patch.object(bandwidth, "saved_capture", return_value=copy.deepcopy(saved)), \
                         patch.object(server.power, "PowerSampler", return_value=sampler) as factory:
                        handle = server._cap_telemetry_start(run, cfg)
                    self.assertEqual(run["hardware_probe"]["policy"], "defer")
                    self.assertFalse(run["hardware_probe"]["measured_this_session"])
                    if saved is None:
                        self.assertNotIn("transfer_bandwidth", run["measurement_client"]["hardware"])
                    else:
                        self.assertEqual(run["measurement_client"]["hardware"]["transfer_bandwidth"]["captured_at"], saved["captured_at"])
                    if local:
                        self.assertIs(handle, sampler)
                        sampler.start.assert_called_once()
                    else:
                        self.assertIsNone(handle)
                        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
