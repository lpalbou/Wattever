import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.build_pages import build_capability_catalog
from wattever import capabilities


class CapabilityPublicationTests(unittest.TestCase):
    def test_only_committed_rescorable_results_are_published(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            folder = root / "results" / "capabilities"
            folder.mkdir(parents=True)
            answers = {task["prompt"]: task["scorer"] for task in capabilities.CORE_TASKS}
            def complete(model, prompt, *args, **kwargs):
                scorer = answers[prompt]
                answer = json.dumps(scorer["expected"]) if scorer["kind"] == "json_exact" else str(scorer["expected"])
                return {"output_text":answer, "finish_reason":"stop"}
            with patch.object(capabilities, "RUNS_DIR", str(folder)), patch.object(capabilities.lmstudio, "measure_completion", side_effect=complete):
                runner = capabilities.CapabilityRunner()
                run_id = runner.start({"model":"fixture", "provider":"openai", "endpoint_base_url":"http://localhost:9/v1",
                                       "endpoint_id":"community-route", "endpoint_label":"Shared fixture", "sample_size":1})
                runner.thread.join(timeout=5)
                self.assertFalse(runner.running)
            path = folder / (run_id + ".json")
            result = json.loads(path.read_text())
            result["debug"] = {"authorization":"Bearer publication-private-token"}
            path.write_text(json.dumps(result), encoding="utf-8")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Capability fixture"], cwd=root, check=True)
            path.write_text('{"private":"uncommitted changes"}', encoding="utf-8")
            (folder / "untracked.json").write_text("{}", encoding="utf-8")
            output = Path(directory) / "site"
            entries = build_capability_catalog(output, root)
            self.assertEqual([entry["id"] for entry in entries], [run_id])
            raw = (output / "capabilities" / (run_id + ".json")).read_bytes()
            public = json.loads(raw)
            self.assertNotIn(b"publication-private-token", raw)
            self.assertNotIn(b"uncommitted changes", raw)
            self.assertEqual(entries[0]["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(public["model_configuration"]["endpoint"]["id"], "community-route")
            self.assertEqual(capabilities.validate_import(public)["summary"]["correct"], 1)
            self.assertFalse((output / "capabilities" / "untracked.json").exists())


if __name__ == "__main__": unittest.main()
