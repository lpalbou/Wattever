import hashlib
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from tools.build_pages import build_catalog
from wattever.validation import validate_run


class PagesCatalogTests(unittest.TestCase):
    def test_committed_results_only_and_preliminary_superseded(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            folder = root / "results" / "runs"
            folder.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            def result(run_id, publication=None):
                value = {"id": run_id, "created_at": "2026-10-27T00:00:00Z", "status": "completed",
                         "config": {}, "host": {}, "gpu": {}, "results": []}
                if publication is not None:
                    value["publication"] = publication
                (folder / (run_id + ".json")).write_text(json.dumps(value), encoding="utf-8")
            result("latest")
            path = folder / "latest.json"
            fixture = json.loads(path.read_text(encoding="utf-8"))
            fixture.update(status="error", notes="see C:\\Users\\someone\\notes.txt",
                           error="[WinError 5] C:\\Users\\someone\\sample.json",
                           sessions=[{"error":"file:///home/someone/private", "status":"error"}],
                           results=[{"model":"model", "target_tokens":512, "status":"failed",
                                     "fail_kind":"timeout", "concurrency":1,
                                     "message":"failed at /home/someone/private", "decode_tps":12.5}],
                           remaining_tests=0)
            path.write_text(json.dumps(fixture), encoding="utf-8")
            result("preliminary", {"source_run_id": "latest"})
            result("other", "optional legacy text")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "-c", "user.name=Catalog Test", "-c", "user.email=test@example.invalid",
                            "commit", "-qm", "Shared results"], cwd=root, check=True)
            original = (folder / "latest.json").read_bytes()
            (folder / "latest.json").write_text('{"private":"working change"}', encoding="utf-8")
            (folder / "other.json").unlink()
            result("private-untracked")
            output = Path(directory) / "site"
            entries = build_catalog(output, root)
            self.assertEqual({entry["id"] for entry in entries}, {"latest", "other"})
            public_bytes = (output / "benchmarks" / "latest.json").read_bytes()
            public = json.loads(public_bytes)
            self.assertNotEqual(public_bytes, original)
            self.assertEqual(validate_run(public), [])
            self.assertEqual(public["status"], "error")
            self.assertEqual(public["remaining_tests"], 0)
            self.assertEqual(public["results"][0]["fail_kind"], "timeout")
            self.assertEqual(public["results"][0]["decode_tps"], 12.5)
            self.assertEqual(public["diagnostic_category"], "storage_access")
            self.assertTrue(public["sessions"][0]["diagnostic_redacted"])
            for private in (b"WinError", b"someone", b"file://", b"/home/"):
                self.assertNotIn(private, public_bytes)
            self.assertEqual(public["public_export"], {"diagnostics":"omitted", "source":"github"})
            self.assertTrue((output / "benchmarks" / "other.json").is_file())
            self.assertTrue((output / "benchmarks" / "preliminary.json").is_file())
            self.assertFalse((output / "benchmarks" / "private-untracked.json").exists())
            latest = next(entry for entry in entries if entry["id"] == "latest")
            self.assertEqual(latest["sha256"], hashlib.sha256(public_bytes).hexdigest())
            manifest = json.loads((output / "benchmarks" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest, {"version": 1, "benchmarks": entries})


if __name__ == "__main__":
    unittest.main()
