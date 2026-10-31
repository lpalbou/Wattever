import copy
import unittest

from wattever.validation import validate_run


class ScientificMetadataTests(unittest.TestCase):
    def setUp(self):
        self.run = {"id":"metadata-check", "created_at":"2026-10-30T12:00:00Z",
                    "status":"completed", "gpu":{}, "host":{}, "config":{}, "results":[],
                    "reproducibility":{"source":"user_reported", "settings":{"engine":"Strata"}},
                    "performance_claims":[{"precision":"INT8", "value":100, "unit":"TOPS",
                        "kind":"advertised_peak", "sparsity":"dense", "source":"Vendor data sheet",
                        "execution_path":"tensor cores", "provenance":"user_reported"}]}

    def test_reported_claims_preserve_evidence_and_precision_units(self):
        self.assertEqual(validate_run(self.run), [])
        for key, value in (("unit","TFLOP/s"), ("source",""), ("provenance","measured_by_wattever"),
                           ("value",True), ("sparsity","assumed_dense")):
            run = copy.deepcopy(self.run)
            run["performance_claims"][0][key] = value
            with self.subTest(key=key):
                self.assertTrue(validate_run(run))

    def test_reproducibility_cannot_be_labeled_detected(self):
        self.run["reproducibility"]["source"] = "detected"
        self.assertTrue(any("user_reported" in error for error in validate_run(self.run)))


if __name__ == "__main__":
    unittest.main()
