import hashlib
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from tools.build_pages import build_catalog


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
            self.assertEqual((output / "benchmarks" / "latest.json").read_bytes(), original)
            self.assertTrue((output / "benchmarks" / "other.json").is_file())
            self.assertTrue((output / "benchmarks" / "preliminary.json").is_file())
            self.assertFalse((output / "benchmarks" / "private-untracked.json").exists())
            latest = next(entry for entry in entries if entry["id"] == "latest")
            self.assertEqual(latest["sha256"], hashlib.sha256(original).hexdigest())
            manifest = json.loads((output / "benchmarks" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest, {"version": 1, "benchmarks": entries})


if __name__ == "__main__":
    unittest.main()
