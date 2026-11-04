"""Keep ordinary capability prose auditable while redacting HTTP Basic secrets."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wattever import capabilities as cap, endpoints, identity
from wattever.privacy import network_detail, public_projection


class BasicPrivacyTests(unittest.TestCase):
    def test_ordinary_basic_prose_survives_recursive_projection_and_identity(self):
        phrases = ["basic math", "basic somatosensory", "basic survival", "BASIC mathematics",
                   "Basic YWJj", "Basic dXNlcg=="]
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertFalse(network_detail(phrase))
                original = {phrase: {"response": phrase}, "items": [phrase]}
                self.assertEqual(public_projection(original), original)
                self.assertEqual(identity.public_text(phrase, "model label"), phrase)
                self.assertEqual(identity.public_request_model(phrase)["value"], phrase)
                if "=" not in phrase:
                    self.assertEqual(endpoints._safe_model_text(phrase), phrase)

    def test_valid_basic_user_password_is_private_in_any_scheme_case(self):
        for text in ["Basic dTpw", "basic Og==", "BASIC dXNlcjpwYXNz", "Basic dTpw.",
                     "The request uses bAsIc dTpw credentials."]:
            with self.subTest(text=text):
                self.assertTrue(network_detail(text))
                self.assertNotEqual(public_projection(text), text)
                self.assertNotIn(text, json.dumps(public_projection({text: 1, "response": text})))
                with self.assertRaises(ValueError): identity.public_text(text, "model label")

    def test_explicit_basic_headers_remain_private_even_if_malformed_or_empty(self):
        for text in ["Authorization: Basic ???", "proxy-authorization : bAsIc not-base64!",
                     "Authorization: Basic", "Log: Authorization: Basic abc_~", "Proxy-Authorization:Basic Og==",
                     '{"Authorization": "Basic malformed_private"}',
                     "{'authorization': 'basic not-base64!'}", "Proxy-Authorization='Basic malformed'",
                     'AUTHORIZATION = "bAsIc ???"']:
            with self.subTest(text=text):
                self.assertTrue(network_detail(text))
                self.assertNotEqual(public_projection(text), text)
        for key in ["Authorization", "Proxy-Authorization", "headers", "request_headers"]:
            self.assertNotIn(key, public_projection({key: "malformed opaque auth"}))

    def test_other_network_and_secret_rules_are_unchanged(self):
        for text in ["Bearer ordinary", "bearer opaque._~+/=-", "http://private.example/path",
                     "192.168.1.2", "[fe80::123]", "2001:db8::1234", "person@example.org",
                     "sk-abcdefghijklmnop"]:
            with self.subTest(text=text):
                self.assertTrue(network_detail(text))
                self.assertNotEqual(public_projection(text), text)

    def test_capability_validation_export_import_preserves_basic_prose_and_scores(self):
        answer = "This follows from basic math, basic somatosensory processing and basic survival.\nANSWER: B"
        suite = cap.register_suite(dict(id="basic-prose-fixture-v1", name="Basic prose fixture", version="1",
            description="Privacy regression fixture", source={"kind":"original", "description":"basic math"},
            scoring_version=cap.SCORING_VERSION,
            tasks=[dict(id="basic-prose", category="privacy", prompt="Choose A or B. End with ANSWER: B.",
                        scorer={"kind":"multiple_choice", "expected":"B", "choices":["A","B"]})]), register=False)
        with tempfile.TemporaryDirectory() as directory, patch.object(cap, "RUNS_DIR", directory), \
             patch.dict(cap._SUITES, {suite["id"]:suite}), \
             patch.object(cap.lmstudio, "measure_completion", return_value={"output_text":answer,"finish_reason":"stop"}):
            runner = cap.CapabilityRunner()
            run_id = runner.start(dict(provider="openai", model="cpu-fixture", endpoint_id="cpu-fixture",
                endpoint_label="CPU fixture", endpoint_base_url="http://127.0.0.1:9/v1",
                suite_id=suite["id"], sample_size=0, repeats=1, max_tokens=256, timeout_s=2))
            runner.thread.join(timeout=5)
            self.assertFalse(runner.running)
            raw = json.loads(Path(cap._path(run_id)).read_text())
            original = copy.deepcopy(raw)
            validated = cap.validate_import(raw)
            exported = cap.export_run(validated)
            imported = cap.validate_import(json.loads(json.dumps(exported)))
            self.assertEqual(raw, original)
            for value in [validated, exported, imported, runner.export(run_id)]:
                self.assertEqual(value["results"], raw["results"])
                self.assertEqual(value["results"][0]["response"], answer)
                self.assertTrue(value["results"][0]["scoring"]["correct"])
                self.assertNotIn("audit_response_redacted", value["results"][0])


if __name__ == "__main__": unittest.main()
