"""Completed audit streams retain empty final answers without retaining reasoning."""
import io
import itertools
import json
import unittest
import urllib.error
from unittest.mock import patch

from wattever import lmstudio


def choice(delta=None, finish_reason=None):
    return {"choices": [{"delta": delta or {}, "finish_reason": finish_reason}]}


def usage(completion_tokens, reasoning_tokens, **extra):
    return {"usage": {"prompt_tokens": 12, "completion_tokens": completion_tokens,
                      "completion_tokens_details": {"reasoning_tokens": reasoning_tokens},
                      "prompt_tokens_details": {"cached_tokens": 3}}, **extra}


class StreamCaptureTests(unittest.TestCase):
    def measure(self, chunks, done=True, **kwargs):
        payload = b"".join(("data: " + json.dumps(chunk) + "\n\n").encode() for chunk in chunks)
        if done:
            payload += b"data: [DONE]\n\n"
        clock = itertools.count(100)
        api_base = kwargs.pop("api_base", True)
        transport = "wattever.endpoints.open_request" if api_base else "wattever.lmstudio.urllib.request.urlopen"
        with patch(transport, return_value=io.BytesIO(payload)) as request, \
             patch.object(lmstudio.time, "perf_counter", side_effect=lambda: float(next(clock))):
            value = lmstudio.measure_completion("served/model", "Question", kwargs.pop("max_tokens", 256),
                                                base="http://fixture.invalid/v1" if api_base else "http://fixture.invalid", api_base=api_base,
                                                capture_output=kwargs.pop("capture_output", True), **kwargs)
            return value, request.call_args

    def test_speculative_counters_preserve_actual_zero_or_activity_without_inference(self):
        for offered, accepted in ((0, 0), (37, 21)):
            with self.subTest(offered=offered, accepted=accepted):
                value, _ = self.measure([choice({"content": "answer"}), choice(finish_reason="stop"),
                                        usage(4, 0, timings={"draft_n": offered, "draft_n_accepted": accepted})])
                self.assertEqual(value["reported_timings"], {"draft_n": offered, "draft_n_accepted": accepted})
        value, _ = self.measure([choice({"content": "answer"}), choice(finish_reason="stop"), usage(4, 0)])
        self.assertNotIn("draft_n", value["reported_timings"])
        self.assertNotIn("draft_n_accepted", value["reported_timings"])

    def test_invalid_speculative_counters_are_not_recast_as_zero(self):
        for field in ("draft_n", "draft_n_accepted"):
            for bad in (-1, True, 1.5, float("nan"), float("inf"), "3"):
                with self.subTest(field=field, value=bad), self.assertRaises(RuntimeError):
                    self.measure([choice({"content": "answer"}), choice(finish_reason="stop"),
                                  usage(4, 0, timings={field: bad})])
        with self.assertRaisesRegex(RuntimeError, "more speculative"):
            self.measure([choice({"content": "answer"}), choice(finish_reason="stop"),
                          usage(4, 0, timings={"draft_n": 3, "draft_n_accepted": 4})])

    def test_hidden_reasoning_output_limit_preserves_terminal_evidence_without_timings(self):
        chunks = [choice({"role": "assistant", "content": ""}), choice(),
                  choice(finish_reason="length"),
                  usage(256, 256, model="reported/model", timings={"predicted_n": 256, "predicted_ms": 900})]
        value, request = self.measure(chunks, timeout=None)
        self.assertIsNone(request.args[1])
        self.assertEqual(value["output_text"], "")
        self.assertEqual(value["finish_reason"], "length")
        self.assertEqual(value["completion_tokens"], 256)
        self.assertEqual(value["reasoning_tokens"], 256)
        self.assertEqual(value["cached_prompt_tokens"], 3)
        self.assertEqual(value["response_model"], "reported/model")
        self.assertEqual(value["reported_timings"], {"predicted_n": 256, "predicted_ms": 900})
        self.assertGreater(value["wall_s"], 0)
        for field in ("t_first_token", "ttft_s", "decode_s", "prefill_tps", "decode_tps", "decode_token_count"):
            self.assertIsNone(value[field], field)
        self.assertIn("no observable output", value["metric_sources"]["ttft_s"])
        self.assertFalse(value["output_capture"]["visible_answer_present"])
        self.assertFalse(value["output_capture"]["observable_output_present"])
        self.assertTrue(value["output_capture"]["stream_complete"])
        self.assertFalse(value["completion_tokens_estimated"])

    def test_streamed_reasoning_only_is_never_a_visible_answer(self):
        secret = "private scratch reasoning that is not the final answer"
        value, _ = self.measure([choice({"reasoning_content": secret}),
                                 choice({"reasoning_content": " more private reasoning"}),
                                 choice(finish_reason="length"), usage(256, 256)])
        self.assertEqual(value["output_text"], "")
        self.assertEqual(value["sample_text"], "")
        self.assertNotIn(secret, json.dumps(value))
        self.assertEqual(value["finish_reason"], "length")
        self.assertEqual(value["completion_tokens"], 256)
        self.assertTrue(value["output_capture"]["reasoning_observed"])
        self.assertFalse(value["output_capture"]["visible_content_observed"])
        self.assertFalse(value["output_capture"]["visible_answer_present"])
        self.assertTrue(value["output_capture"]["observable_output_present"])
        self.assertEqual(value["timing_scope"]["first_output_kind"], "reasoning_content")
        self.assertIsNotNone(value["ttft_s"])

    def test_blank_stop_retains_zero_usage_and_missing_observation(self):
        value, _ = self.measure([choice({"role": "assistant", "content": ""}),
                                 choice(finish_reason="stop"), usage(0, 0)])
        self.assertEqual(value["finish_reason"], "stop")
        self.assertEqual(value["output_text"], "")
        self.assertEqual(value["completion_tokens"], 0)
        self.assertEqual(value["reasoning_tokens"], 0)
        self.assertFalse(value["completion_tokens_estimated"])
        self.assertIsNone(value["ttft_s"])
        self.assertIsNone(value["decode_tps"])

    def test_missing_capture_usage_remains_unknown_even_on_legacy_transport(self):
        for text in (False, True):
            chunks = [choice({"content": "5"}), choice({"content": "12"})] if text else []
            chunks.append(choice(finish_reason="stop"))
            with self.subTest(text=text):
                value, _ = self.measure(chunks, api_base=False)
                self.assertIsNone(value["completion_tokens"])
                self.assertIsNone(value["decode_tps"])
                self.assertEqual(value["metric_sources"]["completion_tokens"], "unavailable")

    def test_whitespace_content_is_retained_but_not_a_nonblank_answer(self):
        value, _ = self.measure([choice({"content": " \n"}), choice(finish_reason="stop"), usage(1, 0)])
        self.assertEqual(value["output_text"], " \n")
        self.assertTrue(value["output_capture"]["visible_content_observed"])
        self.assertFalse(value["output_capture"]["visible_answer_present"])
        self.assertIsNotNone(value["ttft_s"])

    def test_final_answer_after_more_than_256_reasoning_tokens_is_final_content_only(self):
        secret = "scratch work " * 512
        value, request = self.measure([choice({"reasoning_content": secret}),
                                      choice({"content": "5"}), choice({"content": "12"}),
                                      choice(finish_reason="stop"), usage(515, 512)],
                                     max_tokens=1024, output_limit_chars=3, timeout=None)
        body = json.loads(request.args[0].data)
        self.assertEqual(body["max_tokens"], 1024)
        self.assertEqual(value["output_text"], "512")
        self.assertEqual(value["sample_text"], "512")
        self.assertNotIn("scratch work", json.dumps(value))
        self.assertEqual(value["reasoning_tokens"], 512)
        self.assertEqual(value["finish_reason"], "stop")
        self.assertTrue(value["output_capture"]["visible_answer_present"])
        self.assertTrue(value["output_capture"]["reasoning_observed"])
        self.assertEqual(value["output_capture"]["scope"], "visible content only; streamed reasoning excluded")

    def test_default_speed_path_still_rejects_no_observable_text(self):
        with self.assertRaisesRegex(RuntimeError, "no observable content or reasoning text"):
            self.measure([choice(finish_reason="length"), usage(256, 256)], capture_output=False)

    def test_done_only_is_not_a_completed_audit_response(self):
        with self.assertRaisesRegex(RuntimeError, "terminal choice finish_reason"):
            self.measure([])

    def test_usage_only_without_finish_reason_is_not_a_completed_audit_response(self):
        with self.assertRaisesRegex(RuntimeError, "terminal choice finish_reason"):
            self.measure([usage(256, 256)])

    def test_reasoning_only_without_finish_reason_is_not_a_completed_audit_response(self):
        with self.assertRaisesRegex(RuntimeError, "terminal choice finish_reason"):
            self.measure([choice({"reasoning_content": "private reasoning"}), usage(256, 256)])

    def test_visible_answer_without_finish_reason_is_not_a_completed_audit_response(self):
        with self.assertRaisesRegex(RuntimeError, "terminal choice finish_reason"):
            self.measure([choice({"content": "512"}), usage(1, 0)])

    def test_capture_rejects_nonstring_or_blank_terminal_finish_reason(self):
        for value in (True, False, 0, [], {}, "", " "):
            with self.subTest(finish_reason=value), self.assertRaisesRegex(RuntimeError, "Invalid terminal choice finish_reason"):
                self.measure([choice({"content": "512"}, finish_reason=value), usage(1, 0)])

    def test_default_speed_terminal_validation_is_unchanged(self):
        value, _ = self.measure([choice({"content": "512"}), usage(1, 0)], capture_output=False)
        self.assertIsNone(value["finish_reason"])
        self.assertEqual(value["completion_tokens"], 1)

    def test_missing_done_remains_a_transport_failure_with_or_without_observed_reasoning(self):
        for reasoning in (False, True):
            chunks = [choice({"reasoning_content": "reasoning"})] if reasoning else []
            chunks.extend([choice(finish_reason="length"), usage(256, 256)])
            with self.subTest(reasoning=reasoning), self.assertRaisesRegex(RuntimeError, r"before \[DONE\]"):
                self.measure(chunks, done=False)

    def test_completed_blank_stream_still_validates_usage(self):
        for counts in (usage(True, 0), usage(1, 2)):
            with self.subTest(counts=counts), self.assertRaises(RuntimeError):
                self.measure([choice(finish_reason="stop"), counts])

    def test_stream_errors_and_invalid_json_are_not_empty_completed_answers(self):
        with self.assertRaisesRegex(RuntimeError, "stream error"):
            self.measure([{"error": {"message": "fixture error"}}])
        with patch("wattever.endpoints.open_request", return_value=io.BytesIO(b"data: not-json\n\ndata: [DONE]\n\n")):
            with self.assertRaisesRegex(RuntimeError, "Invalid JSON"):
                lmstudio.measure_completion("model", "prompt", 256, api_base=True, capture_output=True)

    def test_http_error_remains_an_error(self):
        failure = urllib.error.HTTPError("http://fixture.invalid/v1", 400, "Bad request", {}, io.BytesIO(b"bad parameter"))
        with patch("wattever.endpoints.open_request", side_effect=failure), self.assertRaisesRegex(RuntimeError, "HTTP 400"):
            lmstudio.measure_completion("model", "prompt", 256, api_base=True, capture_output=True)


if __name__ == "__main__":
    unittest.main()
