import hashlib
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from tools.build_release import build


class ReleaseTests(unittest.TestCase):
    def test_archive_uses_committed_source_and_keeps_local_results_out(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "wattever").mkdir()
            (root / "wattever" / "__init__.py").write_text('__version__ = "2.0.0"\n', encoding="utf-8")
            (root / "results").mkdir()
            shared = root / "results" / "shared.json"
            shared.write_text('{"shared":true}', encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "-c", "user.name=Release Test", "-c", "user.email=test@example.invalid",
                            "commit", "-qm", "Fixture"], cwd=root, check=True)
            # A local UI deletion must not remove shared data from a software release.
            shared.unlink()
            (root / "results" / "private-live.json").write_text("{}", encoding="utf-8")
            (root / "wattever" / "__init__.py").write_text('__version__ = "99.0.0"\n', encoding="utf-8")
            asset, checksum = build(root, "HEAD", Path(directory) / "dist")
            self.assertEqual(asset.name, "Wattever-2.0.0.zip")
            with ZipFile(asset) as archive:
                self.assertIn("Wattever-2.0.0/results/shared.json", archive.namelist())
                self.assertNotIn("Wattever-2.0.0/results/private-live.json", archive.namelist())
                self.assertNotIn("Wattever-2.0.0/.git/", archive.namelist())
                self.assertIn(b'"2.0.0"', archive.read("Wattever-2.0.0/wattever/__init__.py"))
            self.assertEqual(checksum.read_text(encoding="utf-8"),
                             hashlib.sha256(asset.read_bytes()).hexdigest() + "  " + asset.name + "\n")


if __name__ == "__main__":
    unittest.main()
