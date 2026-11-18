"""Merging benchmarks of one machine: detection, plan, merge and undo."""
import copy
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from wattever import consolidation, endpoints, runner, server, store
from wattever.validation import validate_run

PC = {"os": {"name": "Windows", "release": "10", "architecture": "AMD64"},
      "cpu": {"name": "Intel(R) Core(TM) i7-5960X CPU @ 3.00GHz", "physical_cores": 8, "logical_cores": 16},
      "system": {"manufacturer": "EVGA", "model": "X99", "memory_bytes": 68636971008},
      "gpus": [{"name": "NVIDIA GeForce RTX 5070 Ti", "vendor": "NVIDIA", "memory_bytes": 17094934528,
                "driver": "617.42", "sensor_index": index} for index in (0, 1)]}
MAC = {"os": {"name": "macOS", "release": "27.0", "architecture": "arm64"},
       "cpu": {"name": "Apple M5 Max", "physical_cores": 18, "logical_cores": 18},
       "system": {"manufacturer": "Apple", "model": "Mac17,7", "memory_bytes": 137438953472},
       "gpus": [{"name": "Apple M5 Max", "vendor": "Apple", "memory_bytes": 137438953472, "driver": None, "sensor_index": 0}]}


def benchmark(run_id, machine=PC, provider="lmstudio", models=("gemma",), created="2026-11-12T10:00:00+00:00",
              local=False, base="http://127.0.0.1:8086/v1", service="Strata", timestamp=None, status="ok"):
    """A schema v2 benchmark shaped by the same code that shapes real ones."""
    cfg = dict(runner.DEFAULT_CONFIG, provider=provider, models=list(models), sizes=[512], concurrency_levels=[1],
               repeats=1, endpoint_inference_local=local)
    card = machine["gpus"][0]
    run = {"id": run_id, "label": run_id, "created_at": created, "finished_at": created, "status": "completed",
           "schema_version": 2, "application": {"name": "Wattever", "version": "test", "protocol_version": "2"},
           "hardware": copy.deepcopy(machine), "host": {"platform": "windows", "cpu": machine["cpu"]["name"]},
           "gpu": {"name": card["name"], "vram_total_mb": 16303.0, "driver": card["driver"]},
           "classification": store.classification(machine, "windows"),
           "measurement_scope": {"power_gpu_sensor_index": 0, "power_boundary": "selected GPU only",
                                 "runtime_gpu_placement": "unknown", "runtime_gpu_placement_source": "unknown"},
           "corpus": {"sha256": "c" * 64}, "measurement_mode": "helper", "remaining_tests": 0, "lms_version": "0.4.1"}
    if provider == "openai":
        cfg.update(endpoint_base_url=base, endpoint_label=service + " endpoint", endpoint_id="route-" + run_id,
                   endpoint_identity={"id": "route-" + run_id, "label": service + " endpoint", "source": "detected",
                                      "service": service, "version": "unknown"})
        run["lms_version"] = None
    endpoints.describe_run(run, cfg)
    run["config"] = endpoints.public_config(cfg)
    run["sessions"] = [{"id": "session-" + run_id, "started_at": created, "status": "completed",
                        "config": copy.deepcopy(run["config"]), "hardware": copy.deepcopy(run["hardware"]),
                        "measurement_scope": copy.deepcopy(run["measurement_scope"])}]
    run["results"] = [{"model": model, "status": status, "target_tokens": 512, "concurrency": 1, "repeat": 0,
                       "mtp": None if provider == "openai" else False, "prefill_tps": 100.0 + index,
                       "configuration_id": provider + ":" + model, "timestamp": timestamp or created,
                       "session_id": "session-" + run_id} for index, model in enumerate(models)]
    assert validate_run(run) == [], validate_run(run)
    return run


class MergeCase(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for name, value in (("RUNS_DIR", self.temp.name + "/runs"), ("TRASH_DIR", self.temp.name + "/trash")):
            active = patch.object(store, name, value)
            active.start()
            self.addCleanup(active.stop)

    def save(self, *runs):
        for run in runs:
            store.save(run)
        return [run["id"] for run in runs]

    def library(self):
        return {path.name: path.read_bytes() for path in Path(store.RUNS_DIR).glob("*.json")}


class MachineDetectionTests(MergeCase):
    def test_endpoint_run_is_matched_through_the_computer_that_launched_it(self):
        lm, strata = benchmark("lm"), benchmark("strata", provider="openai", models=("qwen",))
        self.assertEqual(strata["classification"]["hardware_class"], "remote-unverified")
        self.assertEqual(strata["hardware"]["gpus"], [])
        local, remote = consolidation.facts(lm), consolidation.facts(strata)
        self.assertEqual((local["attribution"], remote["attribution"]), ("local", "client"))
        self.assertEqual(remote["machine"], local["machine"])
        self.assertEqual(remote["machine_name"], "2× RTX 5070 Ti · Intel Core i7-5960X · 64 GB RAM")
        self.assertEqual(remote["engines"], ["Strata"])
        self.assertTrue(remote["loopback"])
        declared = consolidation.facts(benchmark("declared", provider="openai", local=True))
        self.assertEqual((declared["attribution"], declared["machine"]), ("declared", local["machine"]))
        orphan = copy.deepcopy(strata)
        del orphan["measurement_client"]
        self.assertEqual((consolidation.facts(orphan)["attribution"], consolidation.facts(orphan)["machine"]), ("unknown", None))

    def test_suggestions_group_different_engines_on_one_machine_only(self):
        self.save(benchmark("lm"), benchmark("strata", provider="openai", models=("qwen",)),
                  benchmark("mac-a", machine=MAC), benchmark("mac-b", machine=MAC, models=("other",)),
                  dict(benchmark("live", provider="openai", models=("live",)), status="running"))
        found = consolidation.suggestions()
        self.assertEqual(len(found), 1)  # Two LM Studio runs on the Mac are not a cross-engine suggestion.
        self.assertEqual(found[0]["run_ids"], ["lm", "strata"])
        self.assertEqual(found[0]["engines"], ["LM Studio", "Strata"])
        self.assertEqual(found[0]["needs_confirmation"], 1)
        self.assertIn("RTX 5070 Ti", found[0]["machine"])

    def test_facts_follow_the_file_and_are_cached_between_changes(self):
        run = benchmark("lm")
        self.save(run)
        with patch.object(store, "load", wraps=store.load) as load:
            first = consolidation.library_facts()
            self.assertIs(consolidation.library_facts()[0], first[0])
            self.assertEqual(load.call_count, 1)
            run["label"] = "renamed with a longer name"
            store.save(run)
            self.assertEqual(consolidation.library_facts()[0]["label"], "renamed with a longer name")
            self.assertEqual(load.call_count, 2)
        store.delete("lm")
        self.assertEqual(consolidation.library_facts(), [])


class MergePlanTests(MergeCase):
    def test_plan_orders_explains_and_never_writes(self):
        small, large = benchmark("small", created="2026-11-10T10:00:00+00:00"), benchmark("large", models=("a", "b", "c"))
        strata = benchmark("strata", provider="openai", models=("qwen",))
        other = benchmark("other-strata", provider="openai", models=("glm",), created="2026-11-13T10:00:00+00:00")
        mac = benchmark("mac", machine=MAC)
        self.save(small, large, strata, other, mac)
        before = self.library()
        plan = consolidation.merge_plan(["strata", "small", "large"])
        self.assertEqual(plan["run_ids"], ["large", "small", "strata"])  # Destination: the largest local benchmark.
        self.assertFalse(plan["ok"])
        pending = {run["id"]: [issue["code"] for issue in run["issues"]] for run in plan["runs"]}
        self.assertEqual(pending, {"large": [], "small": [], "strata": ["confirm_local"]})
        self.assertEqual([entry["id"] for entry in plan["also_here"]], ["other-strata"])
        confirmed = consolidation.merge_plan(["strata", "small", "large"], ["strata"])
        self.assertTrue(confirmed["ok"])
        self.assertEqual([(engine["label"], engine["benchmarks"]) for engine in confirmed["engines"]], [("LM Studio", 2), ("Strata", 1)])
        self.assertEqual((confirmed["tests"], confirmed["models"], confirmed["superseded"]), (5, 5, 0))
        self.assertEqual(confirmed["label"], "large")
        blocked = consolidation.merge_plan(["large", "mac"])
        self.assertFalse(blocked["ok"])
        message = blocked["runs"][1]["issues"][0]["message"]
        self.assertRegex(message, "Hardware differs: “mac” ran on Apple M5 Max · 128 GB RAM, not on 2× RTX 5070 Ti")
        self.assertEqual(blocked["also_here"][0]["id"], "other-strata")
        single = consolidation.merge_plan(["large"])  # The dialog can open from one benchmark.
        self.assertFalse(single["ok"])
        self.assertEqual({entry["id"] for entry in single["also_here"]}, {"small", "strata", "other-strata"})
        self.assertEqual(self.library(), before)

    def test_every_obstacle_is_named(self):
        base = benchmark("base")
        cases = {
            "running": (dict(benchmark("x"), status="running"), "marked as running"),
            "driver": (benchmark("x"), "GPU driver differs"),
            "corpus": (dict(benchmark("x"), corpus={"sha256": "d" * 64}), "Corpus or measurement protocol differs"),
            "browser": (dict(benchmark("x"), measurement_mode="browser"), "browser timings"),
            "orphan": ({k: v for k, v in benchmark("x", provider="openai").items() if k != "measurement_client"}, "no record of the computer"),
            "sessions": ({k: v for k, v in benchmark("x").items() if k != "sessions"}, "execution session"),
        }
        cases["driver"][0]["gpu"]["driver"] = "620.10"
        for name, (other, expected) in cases.items():
            with self.subTest(name=name):
                issues = consolidation._inspect([consolidation.facts(base), consolidation.facts(other)], {"x"})
                self.assertRegex(" ".join(issue["message"] for issue in issues if issue["level"] == "block"), expected)
        clash = benchmark("x")
        clash["sessions"][0]["id"] = clash["results"][0]["session_id"] = "session-base"
        issues = consolidation._inspect([consolidation.facts(base), consolidation.facts(clash)])
        self.assertRegex(issues[0]["message"], "session IDs overlap")
        longer = benchmark("x", models=("other",))
        longer["config"]["max_tokens"] = 1024
        issues = consolidation._inspect([consolidation.facts(base), consolidation.facts(longer)])
        self.assertEqual([(issue["level"], issue["code"]) for issue in issues], [("warn", "settings")])
        self.assertIn("answer length", issues[0]["message"])


class MergeTests(MergeCase):
    def test_two_engines_become_one_benchmark_after_local_inference_is_declared(self):
        lm, strata = benchmark("lm"), benchmark("strata", provider="openai", models=("qwen",))
        self.save(lm, strata)
        with patch.object(store, "backup") as backup:
            with self.assertRaisesRegex(ValueError, "Confirm local inference"):
                consolidation.consolidate(["lm", "strata"], "PC")
            backup.assert_not_called()
        merged = consolidation.consolidate(["lm", "strata"], "PC", assume_local=["strata"])
        self.assertEqual(validate_run(merged), [])
        self.assertEqual(merged["results"], lm["results"] + strata["results"])  # Measurements are untouched.
        self.assertEqual([group["label"] for group in merged["engine_groups"]], ["LM Studio", "Strata"])
        self.assertEqual(merged["provider"]["label"], "LM Studio + Strata")
        self.assertEqual((merged["status"], merged["remaining_tests"]), ("completed", 0))
        group = merged["engine_groups"][1]
        self.assertTrue(group["config"]["endpoint_inference_local"])
        self.assertEqual(group["hardware"]["gpus"], PC["gpus"])
        assertion = group["inference_local_assertion"]
        self.assertEqual((assertion["source"], assertion["declared_during"], assertion["power_sampled"]), ("user_reported", "merge", False))
        source = merged["source_runs"][1]
        self.assertEqual(source["classification"], lm["classification"])
        self.assertNotIn("measurement_client", source)
        self.assertIn("user_reported after the run", source["measurement_scope"]["hardware_attribution"])
        self.assertIn("not sampled", source["measurement_scope"]["power_boundary"])
        session = merged["sessions"][1]
        self.assertFalse(session["config"]["endpoint_inference_local"])  # Settings stay as executed.
        self.assertEqual(session["hardware"]["cpu"], PC["cpu"])
        self.assertEqual(merged["consolidation_history"][-1]["local_inference_declared"], ["strata"])
        self.assertEqual([run["id"] for run in store.list_runs()], ["lm"])
        self.assertEqual(store.summarise(merged)["merge"]["sources"], 2)

    def test_an_endpoint_launched_from_another_computer_is_never_attributed(self):
        self.save(benchmark("lm"), benchmark("elsewhere", machine=MAC, provider="openai", models=("qwen",)))
        before = self.library()
        with self.assertRaisesRegex(ValueError, "Hardware differs"):
            consolidation.consolidate(["lm", "elsewhere"], "PC", assume_local=["elsewhere"])
        self.assertEqual(self.library(), before)

    def test_one_engine_measured_twice_is_one_plain_benchmark_with_more_sessions(self):
        first, second = benchmark("first"), benchmark("second", models=("qwen",), created="2026-11-13T10:00:00+00:00")
        self.save(first, second)
        merged = consolidation.consolidate(["first", "second"], "Both")
        self.assertEqual(validate_run(merged), [])
        self.assertNotIn("engine_groups", merged)
        self.assertEqual(merged["config"]["provider"], "lmstudio")
        self.assertEqual(merged["config"]["models"], ["gemma", "qwen"])
        self.assertEqual([session["id"] for session in merged["sessions"]], ["session-first", "session-second"])
        self.assertTrue(all("engine_group_id" not in session for session in merged["sessions"]))
        self.assertEqual(merged["finished_at"], "2026-11-13T10:00:00+00:00")
        # It extends like any single-engine benchmark.
        self.assertEqual(runner.extension_config(merged, {"model_modes": {"qwen": ["off"]}})["provider"], "lmstudio")

    def test_a_test_measured_twice_keeps_the_best_measurement_and_archives_the_other(self):
        old = benchmark("old", models=("gemma", "qwen"), timestamp="2026-11-10T10:00:00+00:00")
        new = benchmark("new", models=("gemma",), created="2026-11-13T10:00:00+00:00")
        failed = benchmark("failed", models=("qwen",), created="2026-11-14T10:00:00+00:00", status="failed")
        new["results"][0]["prefill_tps"] = 555.0
        self.save(old, new, failed)
        self.assertEqual(consolidation.merge_plan(["old", "new", "failed"])["superseded"], 2)
        with self.assertRaisesRegex(ValueError, "cases overlap"):
            consolidation.consolidate(["old", "new", "failed"], "All")
        merged = consolidation.consolidate(["old", "new", "failed"], "All", overlap="newest")
        kept = {row["model"]: row for row in merged["results"]}
        self.assertEqual(kept["gemma"]["prefill_tps"], 555.0)                      # newer completed measurement
        self.assertEqual((kept["qwen"]["status"], kept["qwen"]["session_id"]), ("ok", "session-old"))  # completed beats a later failure
        archived = {(row["model"], row["session_id"], row["archive_reason"]) for row in merged["previous_attempts"]}
        self.assertEqual(archived, {("gemma", "session-old", "superseded_by_merge"), ("qwen", "session-failed", "superseded_by_merge")})
        self.assertEqual(merged["consolidation_history"][-1]["superseded_measurements"], 2)
        self.assertEqual(len(merged["results"]) + len(merged["previous_attempts"]), 4)  # Nothing is discarded.

    def test_merging_into_a_merged_benchmark_and_keeping_the_managed_version(self):
        self.save(benchmark("strata", provider="openai", models=("qwen",), local=True), benchmark("lm"),
                  benchmark("later", models=("phi",), created="2026-11-15T10:00:00+00:00"))
        merged = consolidation.consolidate(["strata", "lm"], "PC")
        self.assertEqual(merged["lms_version"], "0.4.1")  # LM Studio additions compare against the managed version.
        plan = consolidation.merge_plan(["later", "strata"])
        self.assertEqual(plan["run_ids"], ["strata", "later"])  # The merged benchmark stays the destination.
        again = consolidation.consolidate(plan["run_ids"], plan["label"])
        self.assertEqual([group["label"] for group in again["engine_groups"]], ["Strata", "LM Studio"])
        self.assertEqual(sorted(again["engine_groups"][1]["config"]["models"]), ["gemma", "phi"])
        self.assertEqual(len(again["consolidation_history"]), 2)


class UndoTests(MergeCase):
    def test_undo_restores_the_exact_files(self):
        self.save(benchmark("lm"), benchmark("strata", provider="openai", models=("qwen",)))
        before = self.library()
        merged = consolidation.consolidate(["lm", "strata"], "PC", assume_local=["strata"])
        summary = store.summarise(store.load("lm"))
        self.assertTrue(consolidation.undo_available(summary))
        self.assertEqual(consolidation.undo_merge(merged["id"]), {"restored": ["lm", "strata"]})
        self.assertEqual(self.library(), before)
        self.assertFalse(consolidation.undo_available(store.summarise(store.load("lm"))))
        with self.assertRaisesRegex(ValueError, "no merge that can be undone"):
            consolidation.undo_merge("lm")

    def test_undo_is_refused_once_the_merged_benchmark_changed(self):
        self.save(benchmark("lm"), benchmark("strata", provider="openai", models=("qwen",), local=True))
        merged = consolidation.consolidate(["lm", "strata"], "PC")
        merged["results"].pop()
        store.save(merged)
        self.assertFalse(consolidation.undo_available(store.summarise(merged)))
        before = self.library()
        with self.assertRaisesRegex(ValueError, "changed after the merge"):
            consolidation.undo_merge("lm")
        self.assertEqual(self.library(), before)
        for bad in ("../lm", None, "missing"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                consolidation.undo_merge(bad)


class MergeApiTests(MergeCase):
    def setUp(self):
        super().setUp()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_port

    def call(self, path, body=None):
        request = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def test_plan_merge_and_undo_over_http(self):
        self.save(benchmark("lm"), benchmark("strata", provider="openai", models=("qwen",)))
        self.assertEqual(self.call("/api/bench/merge-suggestions")["suggestions"][0]["run_ids"], ["lm", "strata"])
        plan = self.call("/api/bench/merge-plan", {"run_ids": ["strata", "lm"], "assume_local": ["strata"]})
        self.assertTrue(plan["ok"])
        with patch.object(server, "_any_running", return_value=True):
            with self.assertRaises(urllib.error.HTTPError) as busy:
                self.call("/api/bench/consolidate", {"run_ids": plan["run_ids"], "label": "PC"})
            self.assertEqual(busy.exception.code, 409)
        with self.assertRaises(urllib.error.HTTPError) as unconfirmed:
            self.call("/api/bench/consolidate", {"run_ids": plan["run_ids"], "label": "PC"})
        self.assertEqual(unconfirmed.exception.code, 400)
        self.assertIn("Confirm local inference", json.load(unconfirmed.exception)["error"])
        summary = self.call("/api/bench/consolidate", {"run_ids": plan["run_ids"], "label": "PC",
                                                       "assume_local": ["strata"], "overlap": "newest"})
        self.assertEqual((summary["id"], summary["label"], summary["n_results"]), ("lm", "PC", 2))
        self.assertEqual(summary["merge"], {"sources": 2, "at": summary["updated_at"], "undo": True})
        listed = self.call("/api/runs")["runs"]
        self.assertEqual([(run["id"], run["merge"]["undo"]) for run in listed], [("lm", True)])
        self.assertEqual(self.call("/api/bench/merge-undo", {"run_id": "lm"}), {"restored": ["lm", "strata"]})
        self.assertEqual(sorted((run["id"], run["merge"]) for run in self.call("/api/runs")["runs"]),
                         [("lm", None), ("strata", None)])

    def test_merge_requests_are_same_origin_only(self):
        self.save(benchmark("lm"), benchmark("other", models=("qwen",)))
        request = urllib.request.Request(self.base + "/api/bench/consolidate",
                                         data=json.dumps({"run_ids": ["lm", "other"], "label": "x"}).encode(),
                                         headers={"Content-Type": "application/json", "Origin": "https://attacker.invalid"})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        self.assertEqual(error.exception.code, 403)
        self.assertEqual(len(store.list_runs()), 2)


if __name__ == "__main__":
    unittest.main()
