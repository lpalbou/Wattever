import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from wattever import server


class EndpointOriginTests(unittest.TestCase):
    def setUp(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_port

    def test_unrelated_webpage_cannot_forward_environment_credentials(self):
        body = json.dumps({"endpoint_base_url":"https://attacker.invalid/v1", "endpoint_api_key_env":"PRIVATE_TEST_KEY",
                           "endpoint_api_key":"typed-private-key"}).encode()
        for origin, content_type in (("https://attacker.invalid", "text/plain"),
                                     ("https://attacker.invalid", "application/json"),
                                     ("null", "application/json"), (None, "text/plain")):
            headers = {"Content-Type":content_type}
            if origin: headers["Origin"] = origin
            request = urllib.request.Request(self.base + "/api/endpoint/check", data=body, headers=headers)
            with self.subTest(origin=origin, content_type=content_type), patch.object(server.endpoints, "list_models") as fetch:
                with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(request)
                self.assertEqual(error.exception.code, 403)
                fetch.assert_not_called()

    def test_same_origin_and_python_clients_can_connect(self):
        body = json.dumps({"endpoint_base_url":"http://localhost:8086/v1"}).encode()
        for origin in (self.base, None):
            headers = {"Content-Type":"application/json"}
            if origin: headers["Origin"] = origin
            with patch.object(server.endpoints, "list_models", return_value=[{"id":"fixture"}]):
                with urllib.request.urlopen(urllib.request.Request(self.base + "/api/endpoint/check", data=body, headers=headers)) as response:
                    self.assertTrue(json.load(response)["ok"])
        for field in ("endpoint_api_key_env", "endpoint_api_key"):
            with self.subTest(field=field), self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(self.base + "/api/models?provider=openai&%s=PRIVATE_TEST_KEY" % field)
            self.assertEqual(error.exception.code, 400)

    def test_typed_key_reaches_discovery_and_is_never_echoed(self):
        seen = []
        def discover(cfg, timeout=10):
            seen.append(server.endpoints.api_key(cfg))
            return [{"id":"fixture"}]
        body = json.dumps({"endpoint_base_url":"http://localhost:8086/v1", "endpoint_api_key":"typed-private-key"}).encode()
        request = urllib.request.Request(self.base + "/api/endpoint/check", data=body, headers={"Content-Type":"application/json"})
        with patch.object(server.endpoints, "list_models", side_effect=discover):
            with urllib.request.urlopen(request) as response:
                text = response.read().decode()
        self.assertEqual(seen, ["typed-private-key"])
        self.assertTrue(json.loads(text)["ok"])
        self.assertNotIn("typed-private-key", text)


if __name__ == "__main__": unittest.main()
