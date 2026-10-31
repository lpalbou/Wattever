import errno
import unittest
from unittest.mock import MagicMock

from wattever.server import Handler


class ResponseDisconnectTests(unittest.TestCase):
    def handler(self):
        result = object.__new__(Handler)
        result.send_response = MagicMock()
        result.send_header = MagicMock()
        result.end_headers = MagicMock()
        result.wfile = MagicMock()
        return result

    def test_disconnect_during_headers_or_body_is_not_retried(self):
        for phase in ("end_headers", "body"):
            for error in (ConnectionAbortedError(10053, "closed"), ConnectionResetError(), BrokenPipeError()):
                with self.subTest(phase=phase, error=type(error).__name__):
                    handler = self.handler()
                    target = handler.end_headers if phase == "end_headers" else handler.wfile.write
                    target.side_effect = error
                    self.assertFalse(handler._send(200, "{}"))
                    self.assertTrue(handler.close_connection)
                    self.assertEqual(handler.send_response.call_count, 1)

    def test_other_write_errors_are_not_silenced(self):
        handler = self.handler()
        handler.wfile.write.side_effect = OSError(errno.EIO, "unrelated I/O failure")
        with self.assertRaises(OSError): handler._send(200, "{}")


if __name__ == "__main__": unittest.main()
