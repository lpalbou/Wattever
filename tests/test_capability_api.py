import copy
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

from wattever import capabilities, server


class CapabilityApiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        for owner, key, value in ((capabilities, "RUNS_DIR", temp.name),
                                  (server, "_other_helper_running", lambda port:False),
                                  (server, "CAP_BENCH", capabilities.CapabilityRunner()),
                                  (server, "BENCH", SimpleNamespace(running=False))):
            active = patch.object(owner, key, value)
            active.start()
            self.addCleanup(active.stop)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_port

    def request(self, path, body=None):
        request = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(), headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)

    def test_suite_discovery_advertises_runtime_quality_policy_defaults_and_limits(self):
        payload=self.request("/api/capabilities/suites")
        self.assertTrue(any(suite["id"]=="wattever-core-v1" for suite in payload["suites"]))
        self.assertEqual(payload["runner"],{
            "completion_policy":"finished-stream-visible-answer-v2",
            "defaults":{"max_tokens":32768,"timeout_s":0},
            "limits":{"max_tokens":{"min":1,"max":131072},"timeout_s":{"min":0,"max":86400}},
        })
        with patch.dict(capabilities.DEFAULT_CONFIG,{"max_tokens":4096,"timeout_s":600}):
            runtime=self.request("/api/capabilities/suites")
        self.assertEqual(runtime["runner"]["defaults"],{"max_tokens":4096,"timeout_s":600},
                         "Discovery must describe the loaded runner rather than cached frontend defaults")

    def test_http_run_export_import_regrades_and_preserves_identity(self):
        answers = {task["prompt"]: task["scorer"] for task in capabilities.CORE_TASKS}
        def measure(model, prompt, *args, **kwargs):
            rule = answers[prompt]
            value = json.dumps(rule["expected"]) if rule["kind"] == "json_exact" else str(rule["expected"])
            return {"output_text":value, "finish_reason":"stop"}
        with patch.object(capabilities.lmstudio, "measure_completion", side_effect=measure):
            started = self.request("/api/capabilities/start", {"model":"fixture-model", "provider":"openai",
                "endpoint_base_url":"http://localhost:9/v1", "endpoint_id":"test-route", "endpoint_label":"Fixture",
                "sample_size":3, "repeats":2, "seed":13})
            for _ in range(100):
                if not self.request("/api/capabilities/status")["running"]: break
                time.sleep(.01)
        run = self.request("/api/capabilities/runs/" + started["run_id"] + "/download")
        self.assertEqual(run["status"], "completed")
        self.assertEqual(len(run["results"]), 6)
        self.assertEqual(run["model_configuration"]["endpoint"]["id"], "test-route")
        capabilities.validate_import(run)
        bundle = self.request("/api/capabilities/export.json")
        self.assertEqual(len(bundle["capability_runs"]), 1)
        capabilities.validate_import(bundle["capability_runs"][0])
        imported = copy.deepcopy(run)
        imported["id"] = "cap-shared-copy"
        result = self.request("/api/capabilities/import", imported)
        self.assertIn("cap-shared-copy", result["imported"])

    def test_modes_cannot_overlap_and_lifecycle_does_not_unload_models(self):
        with patch.object(server.BENCH, "running", True):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request("/api/capabilities/start", {})
            self.assertEqual(error.exception.code, 409)
            error.exception.read();error.exception.close()
        with patch.object(server.CAP_BENCH, "thread", SimpleNamespace(is_alive=lambda:True)):
            for route in ("/api/bench/start", "/api/bench/resume", "/api/lmstudio/unload"):
                with self.subTest(route=route):
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        self.request(route, {})
                    self.assertEqual(error.exception.code, 409)
                    error.exception.read();error.exception.close()

    def test_quality_defaults_and_empty_completed_streams_survive_http_export_import(self):
        for finish in ("length","stop"):
            with self.subTest(finish_reason=finish):
                captured={"scope":"visible content only","visible_content_observed":False,"visible_answer_present":False,
                          "reasoning_observed":False,"observable_output_present":False,"stream_complete":True}
                value={"output_text":"","finish_reason":finish,"prompt_tokens":32,"completion_tokens":1024,"reasoning_tokens":1024,
                       "wall_s":8.5,"ttft_s":None,"timing_scope":{"boundary":"no observable output"},"output_capture":captured}
                with patch.object(capabilities.lmstudio,"measure_completion",return_value=value) as request:
                    started=self.request("/api/capabilities/start",{"model":"fixture-model","provider":"openai",
                        "endpoint_base_url":"http://localhost:9/v1","endpoint_id":"test-route","endpoint_label":"Fixture","sample_size":1})
                    server.CAP_BENCH.thread.join(timeout=2)
                    self.assertFalse(server.CAP_BENCH.running)
                    self.assertEqual(request.call_args.args[2],32768)
                    self.assertIsNone(request.call_args.kwargs["timeout"])
                run=self.request("/api/capabilities/runs/"+started["run_id"]+"/download")
                row=run["results"][0]
                self.assertEqual(run["status"],"completed")
                self.assertEqual(run["summary"]["errors"],0)
                self.assertEqual(run["summary"]["scored"],1)
                self.assertEqual(row["response"],"")
                self.assertEqual(row["finish_reason"],finish)
                self.assertEqual(row["usage"]["reasoning_tokens"],1024)
                self.assertEqual(row["observed_output"],captured)
                self.assertEqual(row["timing"]["wall_s"],8.5)
                self.assertEqual(run["protocol"]["timeout_s"],0)
                self.assertEqual(run["protocol"]["completion_policy"],capabilities.COMPLETION_POLICY)
                self.assertEqual(row["scoring"]["truncated"],finish=="length")
                imported=copy.deepcopy(run);imported["id"]="cap-empty-import-"+finish
                result=self.request("/api/capabilities/import",imported)
                self.assertIn(imported["id"],result["imported"])

    def test_stream_transport_after_long_reasoning_and_hidden_token_limit_reaches_audit(self):
        answers={task["prompt"]:task["scorer"] for task in capabilities.CORE_TASKS}
        seen=[]
        class Endpoint(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append(body)
                budget=body.get("max_tokens",body.get("max_completion_tokens"))
                limit=budget<512
                expected=answers[body["messages"][0]["content"]]
                answer=json.dumps(expected["expected"]) if expected["kind"]=="json_exact" else str(expected["expected"])
                chunks=[{"choices":[{"delta":{} if limit else {"reasoning_content":"thinking "*512}}]}]
                if not limit:chunks.append({"choices":[{"delta":{"content":answer}}]})
                chunks.append({"choices":[{"delta":{},"finish_reason":"length" if limit else "stop"}],
                    "usage":{"prompt_tokens":40,"completion_tokens":256 if limit else 513,
                             "completion_tokens_details":{"reasoning_tokens":256 if limit else 512}}})
                self.send_response(200);self.end_headers()
                self.wfile.write(("".join("data: "+json.dumps(chunk)+"\n\n" for chunk in chunks)+"data: [DONE]\n\n").encode())
        endpoint=ThreadingHTTPServer(("127.0.0.1",0),Endpoint)
        threading.Thread(target=endpoint.serve_forever,daemon=True).start()
        self.addCleanup(endpoint.server_close);self.addCleanup(endpoint.shutdown)
        for budget in (None,256):
            cfg={"model":"fixture-model","provider":"openai","endpoint_base_url":"http://127.0.0.1:%d/v1" % endpoint.server_port,
                 "endpoint_id":"fixture-route","endpoint_label":"Fixture","sample_size":1}
            if budget is not None:cfg["max_tokens"]=budget
            started=self.request("/api/capabilities/start",cfg)
            server.CAP_BENCH.thread.join(timeout=3)
            self.assertFalse(server.CAP_BENCH.running)
            run=self.request("/api/capabilities/runs/"+started["run_id"]+"/download")
            row=run["results"][0]
            self.assertEqual(row["status"],"ok")
            self.assertEqual(run["summary"]["errors"],0)
            self.assertTrue(row["observed_output"]["stream_complete"])
            if budget is None:
                self.assertEqual(seen[-1]["max_tokens"],32768)
                self.assertEqual(row["usage"]["reasoning_tokens"],512)
                self.assertTrue(row["scoring"]["correct"])
                self.assertTrue(row["observed_output"]["visible_answer_present"])
                self.assertNotIn("thinking",row["response"])
            else:
                self.assertEqual(row["response"],"")
                self.assertEqual(row["finish_reason"],"length")
                self.assertEqual(row["usage"]["reasoning_tokens"],256)
                self.assertEqual(run["summary"]["truncated"],1)
                self.assertEqual(run["summary"]["scored"],1)
                self.assertIsNone(row["timing"]["ttft_s"])
            capabilities.validate_import(run)

    def test_preview_respects_active_default_helper(self):
        with patch.object(server, "_other_helper_running", return_value=True):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request("/api/capabilities/start", {})
            self.assertEqual(error.exception.code, 409)
            self.assertIn("8713", error.exception.read().decode())


if __name__ == "__main__": unittest.main()
