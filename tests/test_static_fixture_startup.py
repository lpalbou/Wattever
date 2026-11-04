"""Fake builders only: no real Pages build, server, provider or GPU."""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("static_fixture_under_test", Path(__file__).with_name("static_fixture.py"))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class StaticFixtureStartupTests(unittest.TestCase):
    def test_inline_fake_builder_logs_to_stderr_without_protocol_stdout(self):
        calls = []
        stderr, stdout = io.StringIO(), io.StringIO()
        with redirect_stderr(stderr), redirect_stdout(stdout):
            result = fixture.build_site(Path("fake-output"), lambda path: calls.append(path) or path)
        self.assertEqual(result, Path("fake-output"))
        self.assertEqual(calls, [Path("fake-output")])
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Pages build started", stderr.getvalue())
        self.assertIn("Pages build completed in", stderr.getvalue())

    def test_fake_builder_failure_propagates_and_is_logged(self):
        def fail(path):
            raise ValueError("synthetic build failure")
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaisesRegex(ValueError, "synthetic build failure"):
            fixture.build_site(Path("fake-output"), fail)
        self.assertIn("Pages build failed after", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
