"""Bounded key classification must preserve privacy for every changing value."""
import copy
import json
import random
import unittest
from unittest.mock import patch

from wattever import privacy


class PrivacyKeyCacheTests(unittest.TestCase):
    def setUp(self):
        privacy._private_metadata_key_cached.cache_clear()

    def same(self, value):
        original = copy.deepcopy(value)
        with patch.object(privacy, "_private_metadata_key", privacy._private_metadata_key_uncached):
            expected = privacy.public_projection(value)
        actual = privacy.public_projection(value)
        self.assertEqual(actual, expected)
        self.assertEqual(value, original)
        self.assertEqual(json.dumps(actual, sort_keys=True, ensure_ascii=False),
                         json.dumps(expected, sort_keys=True, ensure_ascii=False))

    def test_cached_key_never_caches_or_bypasses_changed_credential_values(self):
        self.assertEqual(privacy.public_projection({"note":"public"}), {"note":"public"})
        for value in ["Bearer opaque-secret", "Basic dTpw", "Basic Og==",
                      'Authorization: Basic malformed', '{"Authorization":"Basic invalid_secret"}',
                      "C:\\Users\\private\\weights.gguf", "10.2.3.4", "[fe80::1]",
                      "person@example.org", "sk-abcdefghijklmnop"]:
            with self.subTest(value=value):
                self.same({"note":value})
                self.assertNotEqual(privacy.public_projection({"note":value})["note"], value)
        self.assertGreater(privacy._private_metadata_key_cached.cache_info().hits, 0)
        self.assertEqual(privacy.public_projection({"note":"basic math and basic survival"}),
                         {"note":"basic math and basic survival"})

    def test_host_value_type_condition_remains_outside_key_cache(self):
        self.same({"host":{"cpu":"public CPU model"}})
        self.assertNotIn("host", privacy.public_projection({"host":"private-machine"}))
        self.same({"host":["public hardware record"]})

    def test_recursive_private_key_and_value_rules_remain_unchanged(self):
        self.same({"Authorization":"opaque", "Proxy-Authorization":"opaque", "API_TOKEN":"opaque",
                   "Basic dTpw":"secret key", "http://private.example/weights":"private key",
                   "values":["Basic Og==", "basic somatosensory", "person@example.org"],
                   "hardware":{0:{"gpu_uuid":"private", "name":"RTX5070Ti", "temperature":60}},
                   "error":"[WinError 5] C:\\private", "model":"/models/private/customer.gguf"})
        self.assertNotIn("Authorization", privacy.public_projection({"Authorization":"opaque"}))

    def test_only_exact_strings_up_to128_characters_enter_cache(self):
        self.same({"a"*128:1})
        misses = privacy._private_metadata_key_cached.cache_info().misses
        self.same({"b"*129:2})
        self.assertEqual(privacy._private_metadata_key_cached.cache_info().misses, misses)
        class Derived(str): pass
        self.same({Derived("derived-field"):3})
        self.assertEqual(privacy._private_metadata_key_cached.cache_info().misses, misses)

    def test_lru_bound_and_numeric_or_invalid_json_key_behavior(self):
        self.same({"metric-"+str(i):i for i in range(8500)})
        info = privacy._private_metadata_key_cached.cache_info()
        self.assertEqual(info.maxsize, 8192)
        self.assertLessEqual(info.currsize, 8192)
        self.same({None:1, False:2, 2.5:3})
        with self.assertRaises(TypeError): privacy.public_projection({("unsupported","key"):1})

    def test_seeded_nested_fixtures_have_identical_deep_and_serialized_outputs(self):
        rng = random.Random(73129)
        keys = ["metrics", "note", "host", "message", "stdout", "headers", "gpu_uuid", "public-field", "Authorization"]
        values = [None, False, 1, 2.3, "basic math", "Basic dTpw", "Authorization: Basic broken", "http://private-host/v1", "public text"]
        for _ in range(150):
            value = {rng.choice(keys):[rng.choice(values), {rng.choice(keys):rng.choice(values)}] for _ in range(6)}
            self.same(value)


if __name__ == "__main__": unittest.main()
