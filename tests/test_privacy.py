import copy
import json
import unittest

from wattever.privacy import public_projection


class PublicPrivacyTests(unittest.TestCase):
    def test_recursive_secrets_identifiers_and_network_details(self):
        original = {
            "id": "91cda523-9a77-40a5-9125-f1c8562dce11",
            "config": {"models": ["unsloth/Qwen3.6-35B-A3B-Q4_K_M"],
                       "max_tokens": 256, "OPENAI_API_KEY": "private-key",
                       "AWS_SESSION_TOKEN": "private-token", "base_url": "http://private-host:8086/v1",
                       "apiToken": "private-token", "endpoint_api_key_env": "PRIVATE_ENV", "auth": "private-opaque"},
            "extras": [{"Authorization": "Bearer private-auth", "username": "private-user",
                        "gpu_uuid": "private-uuid", "serial_number": "private-serial",
                        "environment": {"SOMETHING": "private-env"},
                        "hostname": "private-machine", "ip_address": "10.0.0.2"}],
            "strings": ["http://private-host/v1", "10.20.30.40", "[fe80::42]", "2001:db8::5",
                        "private@example.org", "C:\\data\\private.bin", "/srv/private/weights.gguf"],
            "hardware": {"cpu": {"name": "AMD Ryzen 7 5700X"}, "memory_bytes": 68719476736,
                         "pcie": {"generation": 4, "lanes": 8}},
            "results": [{"model": "org/model.gguf", "decode_tps": 28.1, "prompt_tokens": 32768,
                         "completion_tokens": 256}],
        }
        before = copy.deepcopy(original)
        public = public_projection(original)
        self.assertEqual(original, before)
        self.assertNotIn("private", json.dumps(public))
        self.assertEqual(public["id"], original["id"])
        self.assertEqual(public["config"]["models"], original["config"]["models"])
        self.assertEqual(public["results"], original["results"])
        self.assertEqual(public["hardware"], original["hardware"])
        self.assertEqual(public_projection(public), public)

    def test_diagnostics_keep_categories_but_not_text(self):
        public = public_projection({"error": "[WinError 5] C:\\secret", "status": "error",
                                    "results": [{"message": "server failed", "fail_kind": "timeout"}]})
        self.assertEqual(public["diagnostic_category"], "storage_access")
        self.assertTrue(public["results"][0]["diagnostic_redacted"])
        self.assertEqual(public["results"][0]["fail_kind"], "timeout")
        self.assertNotIn("secret", json.dumps(public))

    def test_public_citations_only_in_explicit_performance_claims(self):
        public_url = "https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/"
        public = public_projection({"performance_claims": [
            {"source": public_url}, {"source": "http://127.0.0.1/secret"},
            {"source": "https://user:secret@example.com/docs"},
            {"source": "https://example.com/docs?key=secret"},
            {"source": "https://example.com/docs\nBearer private-secret"}],
            "endpoint": public_url, "other": {"source": public_url}})
        self.assertEqual(public["performance_claims"][0]["source"], public_url)
        self.assertNotIn("secret", json.dumps(public))
        self.assertNotIn("endpoint", public)
        self.assertNotEqual(public["other"]["source"], public_url)


if __name__ == "__main__":
    unittest.main()
