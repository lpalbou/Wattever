"""Atomic capability persistence retries writes, never model requests."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from wattever import capabilities as cap


class CapabilityPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        active=patch.object(cap,"RUNS_DIR",self.temp.name);active.start();self.addCleanup(active.stop)
        self.value={"id":"cap-save-fixture","results":[{"item_id":"observed","response":"A"}]}

    def test_transient_sharing_error_retries_identical_observation_without_request_replay(self):
        original=cap.os.replace
        replace_payloads=[];blocked=[];requests=[]
        def replace(source,destination):
            raw=Path(source).read_bytes();value=json.loads(raw)
            if value.get("results"):
                replace_payloads.append(raw)
                if not blocked:
                    blocked.append(True)
                    raise PermissionError("transient test sharing conflict")
            return original(source,destination)
        tasks=cap.suite_manifest()["tasks"]
        answers={task["prompt"]:task["scorer"] for task in tasks}
        def complete(model,prompt,*args,**kwargs):
            requests.append(prompt);scorer=answers[prompt]
            answer=json.dumps(scorer["expected"]) if scorer["kind"]=="json_exact" else str(scorer["expected"])
            return {"output_text":answer,"finish_reason":"stop"}
        cfg=dict(provider="openai",model="cpu-fixture",endpoint_base_url="http://127.0.0.1:9/v1",
                 endpoint_id="cpu-fixture",endpoint_label="CPU fixture",sample_size=2,repeats=1,max_tokens=256,timeout_s=2)
        with patch.object(cap.os,"replace",side_effect=replace),patch.object(cap.time,"sleep") as sleep, \
             patch.object(cap.lmstudio,"measure_completion",side_effect=complete):
            runner=cap.CapabilityRunner();runner.start(cfg);runner.thread.join(timeout=5)
            self.assertFalse(runner.running)
        self.assertEqual(runner.run["status"],"completed")
        self.assertEqual(len(requests),2);self.assertEqual(len(set(requests)),2)
        self.assertEqual(len(runner.run["results"]),2)
        self.assertEqual(replace_payloads[0],replace_payloads[1],"Retry must commit exactly the same serialized observation")
        sleep.assert_called_once_with(.025)
        stored=json.loads(Path(cap._path(runner.run["id"])).read_text())
        self.assertEqual(stored["results"],runner.run["results"])

    def test_exhaustion_preserves_prior_file_and_original_error(self):
        path=Path(cap._path(self.value["id"]));prior=b'{"prior":"immutable"}';path.write_bytes(prior)
        error=PermissionError("persistent sharing conflict")
        with patch.object(cap.os,"replace",side_effect=error) as replace,patch.object(cap.time,"sleep") as sleep:
            with self.assertRaises(PermissionError) as caught:cap._save(self.value)
        self.assertIs(caught.exception,error);self.assertEqual(path.read_bytes(),prior)
        self.assertEqual(replace.call_count,7)
        self.assertEqual([call.args[0] for call in sleep.call_args_list],[.025,.05,.1,.2,.4,.8])
        self.assertFalse(list(self.folder.glob("*.tmp")))

    def test_unrelated_os_error_is_not_retried(self):
        error=OSError("unrelated disk failure")
        with patch.object(cap.os,"replace",side_effect=error) as replace,patch.object(cap.time,"sleep") as sleep:
            with self.assertRaises(OSError) as caught:cap._save(self.value)
        self.assertIs(caught.exception,error);replace.assert_called_once();sleep.assert_not_called()

    def test_cleanup_failure_never_masks_original_replace_error(self):
        original=OSError("original commit failure")
        with patch.object(cap.os,"replace",side_effect=original),patch.object(cap.os,"unlink",side_effect=PermissionError("cleanup blocked")):
            with self.assertRaises(OSError) as caught:cap._save(self.value)
        self.assertIs(caught.exception,original)

    def test_cleanup_failure_never_masks_serialization_error(self):
        original=TypeError("original serialization failure")
        with patch.object(cap.json,"dump",side_effect=original),patch.object(cap.os,"unlink",side_effect=PermissionError("cleanup blocked")):
            with self.assertRaises(TypeError) as caught:cap._save(self.value)
        self.assertIs(caught.exception,original)

    def test_successful_commit_is_not_reported_failed_if_cleanup_is_blocked(self):
        with patch.object(cap.os,"unlink",side_effect=PermissionError("cleanup blocked")):
            cap._save(self.value)
        self.assertEqual(json.loads(Path(cap._path(self.value["id"])).read_text()),self.value)

    def test_outer_error_records_exception_type_without_sensitive_path(self):
        def fail_start(*args):raise PermissionError(r"C:\private\sensitive-result.json")
        runner=cap.CapabilityRunner(on_run_start=fail_start)
        manifest=cap.suite_manifest();cfg=dict(repeats=1,endpoint_base_url="http://127.0.0.1:9/v1")
        run=dict(id="cap-outer-failure",status="running",results=[],protocol=dict(selected_item_ids=[manifest["tasks"][0]["id"]],completion_policy=cap.COMPLETION_POLICY))
        with patch.object(runner,"_request") as request:
            runner._run(run,cfg,manifest["tasks"][:1])
        request.assert_not_called();self.assertEqual(run["diagnostic_error_type"],"PermissionError")
        self.assertNotIn("private",run["diagnostic_error_type"])
        stored=json.loads(Path(cap._path(run["id"])).read_text())
        self.assertEqual(stored["diagnostic_error_type"],"PermissionError")


if __name__=="__main__":unittest.main()
