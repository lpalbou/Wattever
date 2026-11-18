"""A benchmark on an external endpoint survives a model the endpoint cannot serve.

Every test drives the real run loop against a scriptable local HTTP server; no
model, engine or power sensor is involved.
"""
import json
import threading
import unittest
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tempfile import TemporaryDirectory
from unittest.mock import patch

from wattever import endpoints, lmstudio, runner, store


UNSERVABLE = ("refuse", 409, "Model 'drafter' failed to load: Model type qwen3_5_mtp not supported.")


class Endpoint:
    """An OpenAI-compatible server whose answer to each request is scripted.

    ``script(model, kind, nth)`` receives the model, whether the request is a
    measured ``test`` or a short ``check`` (warmup or availability check), and
    how many requests of that kind the model has had. It returns ``"ok"``,
    ``("refuse", status, text)`` or ``"vanish"``.
    """

    def __init__(self, script, models=()):
        self.requests, self.counts, self.lock = [], {}, threading.Lock()
        owner = self
        catalog = {"object": "list", "data": [{"id": model} for model in models]}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(catalog).encode())

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                model = body["model"]
                kind = "check" if body["messages"][-1]["content"] == runner.WARMUP_PROMPT else "test"
                with owner.lock:
                    nth = owner.counts[(model, kind)] = owner.counts.get((model, kind), 0) + 1
                    owner.requests.append((model, kind))
                answer = script(model, kind, nth)
                if answer == "vanish":
                    # The whole server goes away before this request is answered.
                    owner.stop()
                    self.close_connection = True
                    return
                if answer != "ok":
                    _, status, text = answer
                    self.send_response(status)
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": {"message": text}}).encode())
                    return
                self.send_response(200)
                self.end_headers()
                for chunk in ({"choices": [{"delta": {"content": "hello"}}]},
                              {"choices": [{"delta": {"content": " world"}}]},
                              {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 2}}):
                    self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d/v1" % self.httpd.server_port
        self.stopped = False
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()

    def stop(self):
        with self.lock:
            if self.stopped:
                return
            self.stopped = True
        self.httpd.shutdown()
        self.httpd.server_close()

    def sent(self, model, kind=None):
        return sum(1 for item in self.requests if item[0] == model and kind in (None, item[1]))


class EndpointBenchmarkCase(unittest.TestCase):
    """Runs benchmarks in a private results folder against a scripted endpoint."""

    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)

        def prepare(run):
            run.update(application={"name": "Wattever", "protocol_version": "2"}, hardware={},
                       measurement_scope={}, classification={"hardware_class": "same"},
                       corpus=dict(run["corpus"], sha256="fixture"))

        with ExitStack() as stack:
            for target, name, value in [(store, "RUNS_DIR", folder.name + "/runs"),
                                        (store, "TRASH_DIR", folder.name + "/trash"),
                                        (store, "CONFIG_PATH", folder.name + "/config.json"),
                                        (runner, "ENDPOINT_GRACE_S", 0.4), (runner, "ENDPOINT_RETRY_S", 0.05)]:
                stack.enter_context(patch.object(target, name, value))
            for target, name, changes in [(store, "prepare_run", {"side_effect": prepare}),
                                          (store, "host_info", {"return_value": {}}),
                                          (runner.power, "gpu_info", {"return_value": {"name": "Fixture GPU"}}),
                                          (endpoints, "resolve_identity", {"side_effect": lambda cfg, **kwargs: cfg})]:
                stack.enter_context(patch.object(target, name, **changes))
            self.addCleanup(stack.pop_all().close)

    def serve(self, script, models=()):
        endpoint = Endpoint(script, models)
        self.addCleanup(endpoint.stop)
        return endpoint

    def connection(self, endpoint):
        return {"provider": "openai", "endpoint_base_url": endpoint.url, "endpoint_id": "route",
                "endpoint_api_key": "typed-secret-key"}

    def benchmark(self, endpoint, models, **changes):
        cfg = dict(self.connection(endpoint), models=models, sizes=[16, 32], concurrency_levels=[1, 2],
                   max_tokens=8, per_test_timeout_s=20, hardware_probe_policy="defer")
        cfg.update(changes)
        bench = runner.BenchRunner()
        bench.start(cfg)
        return self.finish(bench)

    def finish(self, bench):
        bench.thread.join(timeout=30)
        self.assertFalse(bench.thread.is_alive(), "the benchmark did not finish")
        return store.load(bench.run["id"])

    def outcomes(self, run, model):
        return [(row["target_tokens"], row["concurrency"], row["status"], row.get("fail_kind"))
                for row in run["results"] if row["model"] == model]


class UnservableModelTests(EndpointBenchmarkCase):
    def test_a_model_the_endpoint_cannot_serve_is_skipped_and_the_others_still_run(self):
        endpoint = self.serve(lambda model, kind, nth: UNSERVABLE if model == "drafter" else "ok")
        run = self.benchmark(endpoint, ["first", "drafter", "last"])
        self.assertEqual(run["status"], "completed")
        self.assertNotIn("error", run)
        self.assertEqual(run["remaining_tests"], 0)
        every_test = [(16, 1, "ok", None), (32, 1, "ok", None), (16, 2, "ok", None), (32, 2, "ok", None)]
        self.assertEqual(self.outcomes(run, "first"), every_test)
        # The model listed after the unservable one is measured in full.
        self.assertEqual(self.outcomes(run, "last"), every_test)
        self.assertEqual(self.outcomes(run, "drafter"), [
            (16, 1, "failed", runner.FAIL_UNAVAILABLE), (32, 1, "skipped", runner.FAIL_SKIPPED),
            (16, 2, "skipped", runner.FAIL_SKIPPED), (32, 2, "skipped", runner.FAIL_SKIPPED)])
        failed, skipped = [row for row in run["results"] if row["model"] == "drafter"][:2]
        # The server's own explanation is kept, and so is how the verdict was reached.
        self.assertIn("HTTP 409", failed["message"])
        self.assertIn("qwen3_5_mtp not supported", failed["message"])
        self.assertEqual(failed["endpoint_check"], {"verdict": "model_unavailable", "http_status": 409})
        self.assertIn("could not serve this model", skipped["message"])
        # One measured attempt and one check settle it; no further request is wasted on the model.
        self.assertEqual(endpoint.sent("drafter", "test"), 1)
        self.assertEqual(endpoint.sent("drafter", "check"), 2)

    def test_one_failed_test_does_not_condemn_a_model_that_still_answers(self):
        def script(model, kind, nth):
            return ("refuse", 500, "internal error") if (kind, nth) == ("test", 2) else "ok"
        endpoint = self.serve(script)
        run = self.benchmark(endpoint, ["flaky"], sizes=[16, 32, 48], concurrency_levels=[1])
        self.assertEqual(run["status"], "completed")
        self.assertEqual(self.outcomes(run, "flaky"), [
            (16, 1, "ok", None), (32, 1, "failed", runner.FAIL_ERROR), (48, 1, "ok", None)])
        self.assertNotIn("endpoint_check", run["results"][1])

    def test_a_failed_group_of_simultaneous_requests_is_judged_the_same_way(self):
        endpoint = self.serve(lambda model, kind, nth: UNSERVABLE if model == "drafter" else "ok")
        run = self.benchmark(endpoint, ["drafter", "last"], sizes=[16], concurrency_levels=[2], warmup=False)
        self.assertEqual(self.outcomes(run, "drafter"), [(16, 2, "failed", runner.FAIL_UNAVAILABLE)])
        self.assertEqual(self.outcomes(run, "last"), [(16, 2, "ok", None)])
        self.assertEqual(run["status"], "completed")

    def test_later_repeats_of_an_unservable_model_are_skipped(self):
        endpoint = self.serve(lambda model, kind, nth: UNSERVABLE)
        run = self.benchmark(endpoint, ["drafter"], sizes=[16], concurrency_levels=[1], repeats=3)
        self.assertEqual([(row["repeat"], row["status"]) for row in run["results"]],
                         [(0, "failed"), (1, "skipped"), (2, "skipped")])
        self.assertEqual(run["status"], "completed")

    def test_the_typed_key_never_reaches_the_saved_benchmark(self):
        endpoint = self.serve(lambda model, kind, nth: ("refuse", 409, "rejected typed-secret-key at " + endpoint.url))
        run = self.benchmark(endpoint, ["drafter"], sizes=[16], concurrency_levels=[1])
        text = json.dumps(run)
        self.assertNotIn("typed-secret-key", text)
        self.assertNotIn(endpoint.url, text)


class LostEndpointTests(EndpointBenchmarkCase):
    def test_an_endpoint_that_disappears_stops_the_run_and_keeps_the_rest_for_later(self):
        endpoint = self.serve(lambda model, kind, nth: "vanish" if (model, kind) == ("second", "test") else "ok")
        run = self.benchmark(endpoint, ["first", "second", "third"], sizes=[16], concurrency_levels=[1])
        self.assertEqual(run["status"], "error")
        self.assertIn("stopped responding", run["error"])
        self.assertIn("Add / rerun tests", run["error"])
        self.assertEqual(self.outcomes(run, "first"), [(16, 1, "ok", None)])
        self.assertEqual(self.outcomes(run, "second"), [(16, 1, "failed", runner.FAIL_ENDPOINT)])
        # Nothing is recorded against a model that never got its turn.
        self.assertEqual(self.outcomes(run, "third"), [])
        self.assertEqual(run["remaining_tests"], 1)

    def test_an_endpoint_that_comes_back_lets_the_benchmark_go_on(self):
        endpoint = self.serve(lambda model, kind, nth: ("refuse", 503, "restarting") if (kind, nth) == ("test", 1) else "ok")
        answers = iter([False, False, True])
        with patch.object(endpoints, "reachable", side_effect=lambda cfg, timeout=5: next(answers, True)):
            run = self.benchmark(endpoint, ["model"], sizes=[16, 32], concurrency_levels=[1])
        self.assertEqual(run["status"], "completed")
        self.assertEqual(self.outcomes(run, "model"), [(16, 1, "failed", runner.FAIL_ERROR), (32, 1, "ok", None)])

    def test_a_rejected_key_stops_the_run_instead_of_failing_every_model(self):
        endpoint = self.serve(lambda model, kind, nth: ("refuse", 401, "invalid api key"))
        run = self.benchmark(endpoint, ["first", "second"], sizes=[16], concurrency_levels=[1])
        self.assertEqual(run["status"], "error")
        self.assertIn("rejected the API key", run["error"])
        self.assertEqual(self.outcomes(run, "first"), [(16, 1, "failed", runner.FAIL_ERROR)])
        self.assertEqual(self.outcomes(run, "second"), [])


class ContinuationTests(EndpointBenchmarkCase):
    def test_a_benchmark_stopped_by_the_old_behaviour_continues_past_the_unservable_model(self):
        """The file an earlier version left behind: one failed test, the rest never run."""
        endpoint = self.serve(lambda model, kind, nth: "ok")
        run = self.benchmark(endpoint, ["first"])
        self.assertEqual(run["status"], "completed")
        endpoint.stop()
        run["config"]["models"] = ["first", "drafter", "last"]
        run["sessions"][0]["config"]["models"] = ["first", "drafter", "last"]
        run["results"].append(dict(run["results"][0], model="drafter", status="failed", fail_kind=runner.FAIL_ERROR,
                                   message="HTTP 409 from external endpoint: not supported"))
        run.update(status="error", error="Could not verify model unload after a request error.", remaining_tests=7)
        store.save(run)

        # Like the real server, this one lists the model it then fails to load.
        endpoint = self.serve(lambda model, kind, nth: UNSERVABLE if model == "drafter" else "ok",
                              models=["first", "drafter", "last"])
        bench = runner.BenchRunner()
        selection = {"run_id": run["id"], "connection": self.connection(endpoint),
                     "model_modes": {model: ["off"] for model in ("first", "drafter", "last")},
                     "sizes": [16, 32], "concurrency_levels": [1, 2]}
        self.assertEqual(bench.plan_extension(selection)["total_tests"], 7)
        self.assertEqual(bench.extend(selection), run["id"])
        continued = self.finish(bench)
        self.assertEqual(continued["status"], "completed")
        self.assertNotIn("error", continued)
        self.assertEqual(continued["remaining_tests"], 0)
        # Measured results are untouched and nothing already passed runs again.
        self.assertEqual(self.outcomes(continued, "first"), self.outcomes(run, "first"))
        self.assertEqual(endpoint.sent("first"), 0)
        self.assertEqual([status for _, _, status, _ in self.outcomes(continued, "last")], ["ok"] * 4)
        self.assertEqual(sorted(kind for _, _, _, kind in self.outcomes(continued, "drafter")),
                         sorted([runner.FAIL_ERROR, runner.FAIL_UNAVAILABLE, runner.FAIL_SKIPPED, runner.FAIL_SKIPPED]))

    def test_a_lost_endpoint_can_be_continued_once_it_is_back(self):
        endpoint = self.serve(lambda model, kind, nth: "vanish" if (model, kind) == ("second", "test") else "ok")
        stopped = self.benchmark(endpoint, ["first", "second", "third"], sizes=[16], concurrency_levels=[1])
        self.assertEqual(stopped["status"], "error")
        endpoint = self.serve(lambda model, kind, nth: "ok", models=["first", "second", "third"])
        bench = runner.BenchRunner()
        selection = {"run_id": stopped["id"], "connection": self.connection(endpoint),
                     "model_modes": {model: ["off"] for model in ("first", "second", "third")},
                     "sizes": [16], "concurrency_levels": [1]}
        # The interrupted test stays a recorded failure; only the test that never ran is owed.
        self.assertEqual(bench.plan_extension(selection)["total_tests"], 1)
        bench.extend(dict(selection, retry_failed=True))
        continued = self.finish(bench)
        self.assertEqual(continued["status"], "completed")
        self.assertEqual([row["status"] for row in continued["results"]], ["ok"] * 3)
        self.assertEqual(continued["previous_attempts"][0]["fail_kind"], runner.FAIL_ENDPOINT)


class VerdictTests(unittest.TestCase):
    """Each way a check can end maps to one decision."""

    def setUp(self):
        self.bench = runner.BenchRunner()
        self.cfg = dict(runner.DEFAULT_CONFIG, provider="openai", endpoint_base_url="http://127.0.0.1:9/v1")
        for name, value in (("ENDPOINT_GRACE_S", 0.2), ("ENDPOINT_RETRY_S", 0.02)):
            active = patch.object(runner, name, value)
            active.start()
            self.addCleanup(active.stop)

    def verdict(self, outcome=None, reachable=True):
        with patch.object(endpoints, "reachable", return_value=reachable), \
             patch.object(self.bench, "_measure_completion", side_effect=outcome, return_value={}) as request:
            verdict, reason, status = self.bench._check_endpoint("model", self.cfg)
        self.status = status
        return verdict, reason, request

    def test_verdicts(self):
        self.assertEqual(self.verdict()[:2], ("ok", None))
        verdict, reason, _ = self.verdict(lmstudio.Refused("HTTP 404 from external endpoint: no such model", 404))
        self.assertEqual((verdict, reason, self.status), ("model", "HTTP 404 from external endpoint: no such model", 404))
        self.assertEqual(self.verdict(RuntimeError("Invalid JSON in completion stream."))[0], "model")
        self.assertIsNone(self.status)
        self.assertEqual(self.verdict(lmstudio.Refused("HTTP 401 from external endpoint: no", 401))[0], "credentials")
        self.assertEqual(self.status, 401)
        self.assertEqual(self.verdict(lmstudio.Unreachable("Could not reach external endpoint (refused)"))[0], "endpoint")
        self.assertEqual(self.verdict(lmstudio.Unreachable("Could not reach external endpoint (timed out)"))[0], "unknown")
        self.assertEqual(self.verdict(lmstudio.Refused("HTTP 504 from external endpoint: gateway timeout", 504))[0], "unknown")

    def test_an_unreachable_endpoint_is_never_sent_a_check_request(self):
        verdict, reason, request = self.verdict(reachable=False)
        self.assertEqual(verdict, "endpoint")
        self.assertIn("stopped responding", reason)
        request.assert_not_called()

    def test_stopping_the_benchmark_ends_the_wait_without_a_verdict_against_the_model(self):
        self.bench.cancel_event.set()
        self.assertEqual(self.verdict(reachable=False)[:2], ("ok", None))
        self.assertEqual(self.verdict(lmstudio.Refused("HTTP 409 from external endpoint: no", 409))[:2], ("ok", None))

    def test_reachable_means_any_http_answer(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(401)
                self.end_headers()

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        cfg = {"endpoint_base_url": "http://127.0.0.1:%d/v1" % httpd.server_port}
        try:
            self.assertTrue(endpoints.reachable(cfg, timeout=2))
        finally:
            httpd.shutdown()
            httpd.server_close()
        self.assertFalse(endpoints.reachable(cfg, timeout=2))

    def test_request_errors_keep_their_kind_and_lose_their_secrets(self):
        cfg = dict(self.cfg, endpoint_api_key="typed-secret-key")
        with patch.object(lmstudio, "measure_completion",
                          side_effect=lmstudio.Refused("HTTP 409 from external endpoint: typed-secret-key", 409)):
            with self.assertRaises(lmstudio.Refused) as refused:
                self.bench._measure_completion(cfg, "model", "prompt", 8)
        self.assertEqual(refused.exception.status, 409)
        self.assertNotIn("typed-secret-key", str(refused.exception))
        with patch.object(lmstudio, "measure_completion",
                          side_effect=lmstudio.Unreachable("Could not reach external endpoint (http://127.0.0.1:9/v1)")):
            with self.assertRaises(lmstudio.Unreachable) as unreachable:
                self.bench._measure_completion(cfg, "model", "prompt", 8)
        self.assertNotIn("127.0.0.1", str(unreachable.exception))


if __name__ == "__main__":
    unittest.main()
