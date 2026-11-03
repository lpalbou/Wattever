"""Deferred cache equivalence tests. Run after applying the performance patch."""
import re
import unittest
from unittest.mock import patch

from wattever import privacy


class PrivateKeyCacheTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(privacy, "_private_key_cached"), "Apply deferred performance patch before these tests")
        privacy._private_key_cached.cache_clear()

    def tearDown(self):
        if hasattr(privacy, "_private_key_cached"):
            privacy._private_key_cached.cache_clear()

    def test_equivalence_for_policy_keys_and_public_metadata(self):
        keys = list(privacy.PRIVATE_KEYS) + ["PUBLIC_TOKEN", "nested_apiKey", "gpu_uuid", "metric", "configuration_id", "decode_tps"]
        keys += ["public_field_%d" % index for index in range(1000)]
        for key in keys:
            with self.subTest(key=key):
                original = privacy._private_key_uncached(key)
                cached = privacy.private_key(key)
                self.assertEqual(bool(cached), bool(original))
                self.assertEqual(type(cached), type(original))
                if isinstance(original, re.Match):
                    self.assertEqual(cached.group(), original.group())
                    self.assertEqual(cached.string, original.string)

    def test_repeated_short_key_runs_classifier_once(self):
        original = privacy._private_key_uncached
        with patch.object(privacy, "_private_key_uncached", wraps=original) as uncached:
            values = [privacy.private_key("PUBLIC_TOKEN") for _ in range(10)]
        self.assertEqual(uncached.call_count, 1)
        self.assertTrue(all(values))
        self.assertEqual(privacy._private_key_cached.cache_info().hits, 9)

    def test_cache_size_is_bounded_and_oldest_key_evicted(self):
        maximum = privacy._private_key_cached.cache_info().maxsize
        self.assertEqual(maximum, 8192)
        for index in range(maximum + 1):
            privacy.private_key("field_%d" % index)
        info = privacy._private_key_cached.cache_info()
        self.assertEqual(info.currsize, maximum)
        privacy.private_key("field_0")
        self.assertEqual(privacy._private_key_cached.cache_info().misses, info.misses + 1)

    def test_boundary_and_long_keys_bypass_cache(self):
        short = "x" * 128
        long = "x" * 129 + "_TOKEN"
        original = privacy._private_key_uncached
        with patch.object(privacy, "_private_key_uncached", wraps=original) as uncached:
            privacy.private_key(short)
            privacy.private_key(short)
            privacy.private_key(long)
            privacy.private_key(long)
        self.assertEqual(uncached.call_count, 3)
        self.assertEqual(privacy._private_key_cached.cache_info().currsize, 1)
        self.assertEqual(bool(privacy.private_key(long)), bool(original(long)))

    def test_nonstrings_keep_original_exception_semantics_without_cache_entry(self):
        for key in (None, 42, [], {}):
            with self.subTest(type=type(key).__name__):
                with self.assertRaises(Exception) as original:
                    privacy._private_key_uncached(key)
                with self.assertRaises(type(original.exception)):
                    privacy.private_key(key)
        self.assertEqual(privacy._private_key_cached.cache_info().currsize, 0)

    def test_unhashable_string_subclass_follows_uncached_path(self):
        class UnhashableString(str):
            __hash__ = None
        key = UnhashableString("API_KEY")
        self.assertEqual(bool(privacy.private_key(key)), bool(privacy._private_key_uncached(key)))
        self.assertEqual(privacy._private_key_cached.cache_info().currsize, 0)

    def test_public_projection_matches_uncached_policy_with_nested_repeated_keys(self):
        value = [{"metric": index / 8, "endpoint_base_url": "http://127.0.0.1:8086/v1",
                  "nested": {"API_KEY": "fixture-secret", "count": index}} for index in range(20)]
        with patch.object(privacy, "private_key", privacy._private_key_uncached):
            expected = privacy.public_projection(value)
        actual = privacy.public_projection(value)
        self.assertEqual(actual, expected)
        self.assertEqual([row["metric"] for row in actual], [index / 8 for index in range(20)])


if __name__ == "__main__":
    unittest.main()
