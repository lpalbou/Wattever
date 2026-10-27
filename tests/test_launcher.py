import io
import json
import unittest
from unittest.mock import patch

from wattever import __version__, server


def response(payload, identity="Wattever/2.1.0"):
    result = io.BytesIO(json.dumps(payload).encode())
    result.headers = {"Server": identity}
    return result


class LauncherTests(unittest.TestCase):
    def test_reuses_current_version(self):
        with patch.dict(server.os.environ, {"GPU_BENCH_NO_BROWSER": "1"}), patch.object(
                server.urllib.request, "urlopen", side_effect=[
                    response({"running": False}), response({"version": __version__})]) as request:
            self.assertTrue(server.reuse_existing(8713))
            self.assertEqual(request.call_count, 2)

    def test_preserves_active_older_benchmark(self):
        with patch.dict(server.os.environ, {"GPU_BENCH_NO_BROWSER": "1"}), patch.object(
                server.urllib.request, "urlopen", side_effect=[
                    response({"running": True}), response({"version": "1.0.0"})]) as request:
            self.assertTrue(server.reuse_existing(8713))
            self.assertEqual(request.call_count, 2)

    def test_replaces_idle_older_version(self):
        with patch.object(server.urllib.request, "urlopen", side_effect=[
                response({"running": False}), response({"version": "1.0.0"}),
                response({"ok": True})]) as request, patch.object(server.socket, "socket") as connection:
            connection.return_value.__enter__.return_value.connect_ex.return_value = 1
            self.assertFalse(server.reuse_existing(8713))
            shutdown_request = request.call_args_list[-1].args[0]
            self.assertEqual(shutdown_request.get_method(), "POST")
            self.assertTrue(shutdown_request.full_url.endswith("/api/shutdown"))

    def test_leaves_other_applications_alone(self):
        with patch.object(server.urllib.request, "urlopen", return_value=response({}, "AnotherApp/1")) as request:
            self.assertFalse(server.reuse_existing(8713))
            self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
