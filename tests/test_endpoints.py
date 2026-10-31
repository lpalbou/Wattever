import copy
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock, patch

from wattever import endpoints, lmstudio, runner, server, store


class EndpointIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        requests = self.requests
        self.model_payload={"object":"list","data":[{"id":"org/model"}]}
        model_payload=self.model_payload
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                requests.append((self.path, self.headers.get("Authorization"), None))
                if self.path == "/redirect/models":
                    self.send_response(302)
                    self.send_header("Location", "/private-target")
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(model_payload).encode())
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((self.path, self.headers.get("Authorization"), body))
                self.send_response(200)
                self.end_headers()
                prompt = body["messages"][0]["content"]
                if prompt == "capture":
                    chunks=[{"model":"provider/model","choices":[{"delta":{"reasoning_content":"hidden reasoning"}}]},
                            {"choices":[{"delta":{"content":"a"*500}}]},
                            {"choices":[{"delta":{"content":"b"*500}}]},
                            {"choices":[{"delta":{},"finish_reason":"stop"}]},
                            {"usage":{"prompt_tokens":10,"completion_tokens":1002}}]
                    try:
                        for chunk in chunks:self.wfile.write(("data: "+json.dumps(chunk)+"\n\n").encode())
                        self.wfile.write(b'data: [DONE]\n\n')
                    except (ConnectionResetError,ConnectionAbortedError,BrokenPipeError):pass  # Expected when the capture bound aborts the response.
                    return
                if prompt in ("hidden unknown", "hidden reported", "streamed reasoning", "role only"):
                    chunks = []
                    if prompt == "role only":
                        chunks.append({"choices":[{"delta":{"role":"assistant","content":""}}]})
                    elif prompt == "streamed reasoning":
                        chunks.append({"choices":[{"delta":{"reasoning_content":"thinking"}}]})
                    else:
                        chunks.append({"choices":[{"delta":{}}]})
                    chunks.append({"choices":[{"delta":{"content":"O"}}]})
                    chunks.append({"choices":[{"delta":{"content":"K"}}]})
                    usage = {"prompt_tokens":10,"completion_tokens":27}
                    if prompt in ("hidden reported", "streamed reasoning"):
                        usage["completion_tokens_details"] = {"reasoning_tokens":20}
                    chunks.append({"usage":usage})
                    for chunk in chunks:
                        self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                    self.wfile.write(b'data: [DONE]\n\n')
                    return
                if prompt == "multiline":
                    self.wfile.write(b': keepalive\n\ndata: {"choices": [\ndata: {"delta":{"content":"hello"}}]}\n\n')
                    self.wfile.write(b'data: {"usage": {\ndata: "prompt_tokens": 10, "completion_tokens": 4}}\n\n')
                    self.wfile.write(b'data: [DONE]\n\n')
                    return
                if prompt in ("bad count", "bad timing"):
                    self.wfile.write(b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n')
                    payload = {"usage":{"completion_tokens":True}} if prompt == "bad count" else {"timings":{"prompt_ms":float("nan")}}
                    self.wfile.write(("data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n").encode())
                    return
                if prompt == "error":
                    self.wfile.write(b'data:{"error":{"message":"private http://secret.example/v1 Bearer super-secret"}}\n\n')
                    return
                self.wfile.write(b'data:{"choices":[{"delta":{"content":"hello world"}}]}\n\n')
                if prompt != "no usage":
                    self.wfile.write(b'data:{"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":4,"prompt_tokens_details":{"cached_tokens":3}},"timings":{"prompt_per_second":123,"private":"ignore"}}\n\n')
                self.wfile.write(b'data:[DONE]\n\n')
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.cfg = dict(runner.DEFAULT_CONFIG, provider="openai",
                        endpoint_base_url="http://127.0.0.1:%d/v1" % self.httpd.server_port,
                        endpoint_api_key_env="WATTEVER_TEST_KEY", models=["org/model"])
        patcher = patch.dict(os.environ, {"WATTEVER_TEST_KEY":"super-secret"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_discovery_and_authenticated_stream_have_correct_openai_routes(self):
        self.assertEqual(endpoints.list_models(dict(self.cfg))[0]["id"], "org/model")
        bench = runner.BenchRunner()
        value = bench._measure_completion(self.cfg, "org/model", "test", 4, disable_thinking=True)
        self.assertEqual([r[0] for r in self.requests], ["/v1/models", "/v1/chat/completions"])
        self.assertTrue(all(r[1] == "Bearer super-secret" for r in self.requests))
        self.assertNotIn("reasoning_effort", self.requests[-1][2])
        self.assertEqual(value["completion_tokens"], 4)
        self.assertEqual(value["cached_prompt_tokens"], 3)
        self.assertEqual(value["reported_timings"], {"prompt_per_second":123})
        self.assertIn("client_ttft", value["metric_sources"]["prefill_tps"])
        self.assertIsNone(value["decode_tps"])
        self.assertIn("only one observed output delta", value["timing_scope"]["decode_token_scope"])

    def test_model_discovery_preserves_explicit_quant_context_architecture_and_loaded_state(self):
        self.model_payload["data"]=[
            {"id":"flash-ud-iq4_xs","quantization":"q8_0","meta":{"quantization":"BF16","n_ctx":32768},
             "max_context_length":262144,"arch":"qwen3next","status":{"value":"loaded"}},
            {"id":"another-model","quantization":{"name":"unsafe-object"},"meta":{"quantization":"iq3_s"},"architecture":"qwen3moe"},
            {"id":"details-model","details":{"quantization":"UD-Q4_K_XL","context_length":65536}},
            {"id":"loaded-only","meta":{"n_ctx":32768},"architecture":{"input_modalities":["text"]}},
        ]
        models=endpoints.list_models(dict(self.cfg))
        self.assertEqual(models[0]["quantization"],"Q8_0","Explicit quantization wins over identifier inference")
        self.assertEqual(models[0]["quantization_source"],"endpoint_reported")
        self.assertEqual(models[0]["state"],"loaded")
        self.assertEqual(models[0]["max_context_length"],262144)
        self.assertEqual(models[0]["loaded_context_length"],32768)
        self.assertEqual(models[0]["arch"],"qwen3next")
        self.assertEqual(models[1]["quantization"],"IQ3_S")
        self.assertEqual(models[1]["architecture"],"qwen3moe")
        self.assertEqual(models[2]["quantization"],"UD-Q4_K_XL")
        self.assertEqual(models[2]["max_context_length"],65536)
        self.assertEqual(models[3]["loaded_context_length"],32768)
        self.assertEqual(models[3]["max_context_length"],32768)
        self.assertIsNone(models[3]["architecture"],"A modality object is not an architecture name")
        self.assertEqual([request[0] for request in self.requests],["/v1/models"])

    def test_model_identifier_quantization_is_known_suffix_only_with_explicit_source(self):
        expected={"qwen3.8-flash-next-unsloth-ud-iq4_xs":"UD-IQ4_XS","qwen-iq3_s":"IQ3_S",
                  "org/model-ud-q4_k_xl.gguf":"UD-Q4_K_XL","model-q4-k-m":"Q4_K_M",
                  "model-mxfp4":"MXFP4","model-bf16":"BF16","model-fp16":"FP16",
                  "model-Q5_K_M-00001-of-00003.gguf":"Q5_K_M","qwen3.8-27b@iq4_xs":"IQ4_XS"}
        unknown=["flash","model-Q4","model-IQ3_S-2026","mxfp4-architecture","modelq4_k_m","model-q4_k_m_extra"]
        self.model_payload["data"]=[{"id":name} for name in list(expected)+unknown]
        models=endpoints.list_models(dict(self.cfg))
        for model in models:
            with self.subTest(model=model["id"]):
                if model["id"] in expected:
                    self.assertEqual(model["quantization"],expected[model["id"]])
                    self.assertEqual(model["quantization_source"],"model_identifier")
                else:
                    self.assertIsNone(model["quantization"])
                    self.assertEqual(model["quantization_source"],"unknown")
        self.assertEqual([request[0] for request in self.requests],["/v1/models"])

    def test_unsafe_discovery_metadata_and_invalid_contexts_are_ignored(self):
        invalid=[True,False,0,-1,32768.5,"32768",float("nan"),float("inf"),100000001,10**500]
        unsafe=["http://private.example/quant",r"C:\private\quant","Bearer fake-credential","q4\nsecret","x"*81]
        self.model_payload["data"]=[{"id":"unknown-%d" % index,"max_context_length":value,
            "meta":{"n_ctx":value},"quantization":unsafe[index%len(unsafe)],"arch":{"name":"not-a-string"}}
            for index,value in enumerate(invalid)]
        self.model_payload["data"].append({"id":"safe-float","max_context_length":32768.0})
        models=endpoints.list_models(dict(self.cfg))
        for model in models[:-1]:
            self.assertIsNone(model["quantization"])
            self.assertEqual(model["quantization_source"],"unknown")
            self.assertIsNone(model["max_context_length"])
            self.assertIsNone(model["loaded_context_length"])
            self.assertIsNone(model["arch"])
        self.assertEqual(models[-1]["max_context_length"],32768)
        self.assertIs(type(models[-1]["max_context_length"]),int)
        self.assertNotIn("fake-credential",json.dumps(models))
        self.assertEqual([request[0] for request in self.requests],["/v1/models"])

    def test_reasoning_effort_omitted_by_default_and_sent_only_when_explicit(self):
        bench = runner.BenchRunner()
        bench._measure_completion(self.cfg, "org/model", "test", 4)
        self.assertNotIn("reasoning_effort", self.requests[-1][2])
        value = bench._measure_completion(dict(self.cfg, endpoint_reasoning_effort="none"), "org/model", "test", 4)
        self.assertEqual(self.requests[-1][2]["reasoning_effort"], "none")
        self.assertEqual(value["timing_scope"]["requested_reasoning_effort"], "none")
        self.assertIn("unknown", value["timing_scope"]["reasoning_effort_effective"])
        with self.assertRaises(ValueError):
            endpoints.validate(dict(self.cfg, endpoint_reasoning_effort="turbo"))

    def test_bounded_visible_output_capture_and_official_reasoning_token_field(self):
        bench=runner.BenchRunner()
        value=lmstudio.measure_completion("org/model","capture",16,base=self.cfg["endpoint_base_url"],api_base=True,
                                         capture_output=True,temperature=None,token_limit_field="max_completion_tokens")
        self.assertEqual(value["output_text"],"a"*500+"b"*500)
        self.assertEqual(value["response_model"],"provider/model")
        self.assertNotIn("temperature",self.requests[-1][2])
        self.assertNotIn("seed",self.requests[-1][2])
        self.assertNotIn("top_p",self.requests[-1][2])
        self.assertNotIn("max_tokens",self.requests[-1][2])
        self.assertEqual(self.requests[-1][2]["max_completion_tokens"],16)
        default=bench._measure_completion(self.cfg,"org/model","capture",16)
        self.assertNotIn("output_text",default)
        with self.assertRaisesRegex(RuntimeError,"capture limit"):
            lmstudio.measure_completion("org/model","capture",16,base=self.cfg["endpoint_base_url"],api_base=True,capture_output=True,output_limit_chars=10)

    def test_missing_usage_is_unknown_not_stream_chunk_tokens(self):
        value = runner.BenchRunner()._measure_completion(self.cfg, "org/model", "no usage", 4)
        self.assertIsNone(value["completion_tokens"])
        self.assertIsNone(value["decode_tps"])
        self.assertIsNone(value["prefill_tps"])
        self.assertEqual(value["deltas"], 1)

    def test_multiline_sse_json_and_invalid_provider_measurements(self):
        bench = runner.BenchRunner()
        value = bench._measure_completion(self.cfg, "org/model", "multiline", 4)
        self.assertEqual(value["completion_tokens"], 4)
        self.assertEqual(value["sample_text"], "hello")
        for prompt in ("bad count", "bad timing"):
            with self.subTest(prompt=prompt), self.assertRaisesRegex(RuntimeError, "Invalid"):
                bench._measure_completion(self.cfg, "org/model", prompt, 4)

    def test_decode_scope_excludes_hidden_reasoning_and_rejects_unknown_numerator(self):
        bench = runner.BenchRunner()
        unknown = bench._measure_completion(self.cfg, "org/model", "hidden unknown", 32)
        self.assertEqual(unknown["completion_tokens"], 27)
        self.assertIsNone(unknown["decode_token_count"])
        self.assertIsNone(unknown["decode_tps"])
        self.assertEqual(unknown["timing_scope"]["first_output_kind"], "content")
        self.assertEqual(unknown["timing_scope"]["empty_deltas_before_output"], 1)
        reported = bench._measure_completion(self.cfg, "org/model", "hidden reported", 32)
        self.assertEqual(reported["reasoning_tokens"], 20)
        self.assertEqual(reported["decode_token_count"], 6)
        self.assertIn("hidden reasoning excluded", reported["timing_scope"]["decode_token_scope"])
        streamed = bench._measure_completion(self.cfg, "org/model", "streamed reasoning", 32)
        self.assertEqual(streamed["decode_token_count"], 26)
        self.assertTrue(streamed["timing_scope"]["reasoning_streamed"])
        self.assertEqual(streamed["timing_scope"]["first_output_kind"], "reasoning_content")
        role = bench._measure_completion(self.cfg, "org/model", "role only", 32)
        self.assertEqual(role["decode_token_count"], 26)
        self.assertEqual(role["timing_scope"]["empty_deltas_before_output"], 0)
        self.assertIn("potential hidden reasoning", role["timing_scope"]["decode_token_scope"])

    def test_error_redaction_and_redirect_refusal(self):
        with self.assertRaises(RuntimeError) as caught:
            runner.BenchRunner()._measure_completion(self.cfg, "org/model", "error", 4)
        self.assertNotIn("secret.example", str(caught.exception))
        self.assertNotIn("super-secret", str(caught.exception))
        cfg = dict(self.cfg, endpoint_base_url=self.cfg["endpoint_base_url"].replace("/v1", "/redirect"))
        with self.assertRaisesRegex(ValueError, "manually"):
            endpoints.list_models(cfg)
        self.assertNotIn("/private-target", [r[0] for r in self.requests])


class EndpointPolicyTests(unittest.TestCase):
    def test_base_normalization_and_rejected_credentials(self):
        self.assertEqual(endpoints.normalize_base("http://localhost:8086/"), "http://localhost:8086/v1")
        self.assertEqual(endpoints.normalize_base("http://localhost:8086/custom/v1/"), "http://localhost:8086/custom/v1")
        for value in ("file:///tmp/model", "http://user:pass@localhost/v1", "http://host/v1?key=secret", "http://host/v1/chat/completions"):
            with self.subTest(value=value), self.assertRaises(ValueError): endpoints.normalize_base(value)

    def test_external_controls_and_local_power_require_explicit_assertion(self):
        cfg = dict(runner.DEFAULT_CONFIG, provider="openai", endpoint_base_url="http://localhost:8086/v1")
        source = {"config":cfg,"hardware":{"cpu":{"name":"clientCPU"}},"host":{},"gpu":{"name":"clientGPU"},"measurement_scope":{},"classification":{}}
        run = copy.deepcopy(source)
        endpoints.describe_run(run, cfg)
        self.assertEqual(run["measurement_client"]["gpu"]["name"], "clientGPU")
        self.assertEqual(run["hardware"]["gpus"], [])
        self.assertIsNone(run["measurement_scope"]["power_gpu_sensor_index"])
        cfg["endpoint_inference_local"] = True
        endpoints.describe_run(source, cfg)
        self.assertEqual(source["hardware"]["cpu"]["name"], "clientCPU")
        self.assertEqual(source["measurement_scope"]["inference_local_source"], "user_reported")

    def test_run_start_never_calls_lms_or_persists_connection(self):
        bench = runner.BenchRunner()
        cli = MagicMock()
        bench.cli = cli
        cfg = dict(runner.DEFAULT_CONFIG, provider="openai", models=["manual/model"], sizes=[16],
                   warmup=False, endpoint_base_url="http://localhost:8086/v1", endpoint_api_key_env="",
                   surprise={"secret":"do-not-save"})
        saved = []
        def prepare(run):
            run.update(application={},hardware={},measurement_scope={},classification={})
        with patch.object(store, "prepare_run", side_effect=prepare), patch.object(store, "save", side_effect=lambda r:saved.append(copy.deepcopy(r))), patch.object(store, "save_config") as save_cfg, patch.object(store, "host_info", return_value={}), patch.object(runner.power, "gpu_info", return_value={}), patch.object(endpoints, "_probe_identity", return_value={}), patch.object(threading.Thread, "start"):
            bench.start(cfg)
        self.assertEqual(cli.mock_calls, [])
        text = json.dumps(saved) + json.dumps(save_cfg.call_args.args)
        self.assertNotIn("8086", text)
        self.assertNotIn("endpoint_api_key_env", text)
        self.assertNotIn("do-not-save", text)
        self.assertIsInstance(bench.cli, endpoints.ExternalControl)
        row = {"mtp":False,"context_length":100,"reasoning_disabled":True}
        with patch.object(store, "save"):
            bench._record(bench.run, row, 1)
        self.assertIsNone(row["mtp"])
        self.assertIsNone(row["context_length"])
        self.assertIsNone(row["reasoning_disabled"])

    def test_metadata_validation_requires_units_and_provenance(self):
        claim = {"precision":"INT4","value":10,"unit":"TOPS","kind":"estimate","sparsity":"unknown","source":"vendor datasheet","execution_path":"tensor cores"}
        cfg = {"performance_claims":[claim]}
        endpoints.validate(cfg)
        self.assertEqual(cfg["performance_claims"][0]["provenance"], "user_reported")
        for field, value in (("unit","TFLOP/s"),("source",""),("precision","FP64")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                endpoints.validate({"performance_claims":[dict(claim, **{field:value})]})
        with self.assertRaises(ValueError): endpoints.validate({"endpoint_label":[]})


if __name__ == "__main__": unittest.main()
