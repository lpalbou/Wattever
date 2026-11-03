"""Connection-default tests, with no model server or external requests."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from wattever import lmstudio


class ManagedAddressTests(unittest.TestCase):
    def test_fresh_default_uses_ipv4_and_explicit_environment_is_preserved(self):
        def isolated():
            spec=importlib.util.spec_from_file_location("wattever._address_test_module",Path(lmstudio.__file__))
            module=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(isolated().DEFAULT_BASE,"http://127.0.0.1:1234")
        with patch.dict(os.environ,{"LMS_BASE_URL":"http://localhost:9999/custom"}):
            self.assertEqual(isolated().DEFAULT_BASE,"http://localhost:9999/custom")

    def test_explicit_per_call_url_is_not_rewritten(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return b'{"data":[]}'
        with patch.object(lmstudio.urllib.request,"urlopen",return_value=Response()) as open_request:
            self.assertEqual(lmstudio.list_models(base="http://localhost:4321",timeout=1),[])
        self.assertTrue(open_request.call_args_list[0].args[0].full_url.startswith("http://localhost:4321/"))


if __name__=="__main__":unittest.main()
