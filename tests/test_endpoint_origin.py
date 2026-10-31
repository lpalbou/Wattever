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
        body = json.dumps({"endpoint_base_url":"https://attacker.invalid/v1", "endpoint_api_key_env":"PRIVATE_TEST_KEY"}).encode()
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
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.base + "/api/models?provider=openai&endpoint_api_key_env=PRIVATE_TEST_KEY")
        self.assertEqual(error.exception.code, 400)


if __name__ == "__main__": unittest.main()
