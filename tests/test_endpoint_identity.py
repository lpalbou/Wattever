import copy
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch

from wattever import endpoints,identity


class AutomaticEndpointIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        for patcher in (patch.object(endpoints,"ROUTE_REGISTRY_PATH",self.temp.name+"/routes.json"),
                        patch.dict(endpoints._ROUTE_CACHE,{},clear=True),patch.dict(endpoints._PROBE_CACHE,{},clear=True)):
            patcher.start();self.addCleanup(patcher.stop)

    def cfg(self,base="http://127.0.0.1:8086/v1",**values):
        return dict(provider="openai",endpoint_base_url=base,model="public/model",**values)

    def test_no_manual_fields_stable_random_routes_without_private_url_hash(self):
        evidence={"service":"Strata","version":"0.2.6","evidence":"same_origin_service_response"}
        with patch.object(endpoints,"_probe_identity",return_value=evidence) as probe:
            cfg=endpoints.resolve_identity(self.cfg())
            self.assertEqual(cfg["endpoint_label"],"Strata local endpoint")
            self.assertEqual(cfg["endpoint_identity"]["source"],"detected")
            self.assertEqual(probe.call_count,1)
            route=cfg["endpoint_id"]
            self.assertTrue(route.startswith("strata-local-"))
            self.assertNotIn("8086",route)
            endpoints._ROUTE_CACHE.clear()
            again=endpoints.resolve_identity(self.cfg())
            self.assertEqual(again["endpoint_id"],route)
            self.assertEqual(probe.call_count,1)
            other=endpoints.resolve_identity(self.cfg("http://127.0.0.1:8081/v1"))
            self.assertNotEqual(other["endpoint_id"],route)
        public=endpoints.public_config(cfg)
        self.assertNotIn("127.0.0.1",json.dumps(public))
        self.assertNotIn("8086",json.dumps(public))
        with open(endpoints.ROUTE_REGISTRY_PATH,encoding="utf-8") as handle:registry=json.load(handle)
        self.assertIn("http://127.0.0.1:8086/v1",registry["routes"])
        value=identity.build_model_configuration(cfg)
        self.assertEqual(value["engine"],{"value":"Strata","source":"endpoint_reported"})
        self.assertTrue(identity.verify_model_configuration(value,require_endpoint=True))

    def test_known_cloud_exact_host_and_canonical_path_only(self):
        with patch.object(endpoints,"_probe_identity",side_effect=AssertionError("Public origins must not be probed")):
            cfg=endpoints.resolve_identity(self.cfg("https://api.openai.com/v1"))
            self.assertEqual((cfg["endpoint_id"],cfg["endpoint_label"]),("openai-api","OpenAI API"))
            self.assertEqual(cfg["endpoint_identity"]["source"],"inferred")
            custom=endpoints.resolve_identity(self.cfg("https://api.openai.com/custom/v1"))
            self.assertNotEqual(custom["endpoint_id"],"openai-api")
            self.assertEqual(custom["endpoint_label"],"OpenAI API")
            lookalike=endpoints.resolve_identity(self.cfg("https://api.openai.com.evil.example/v1"))
            self.assertEqual(lookalike["endpoint_label"],"Remote endpoint")
            self.assertEqual(lookalike["endpoint_identity"]["source"],"unknown")

    def test_name_override_changes_only_display_and_legacy_fields_remain_supported(self):
        with patch.object(endpoints,"_probe_identity",return_value={}):
            auto=endpoints.resolve_identity(self.cfg())
            renamed=endpoints.resolve_identity(self.cfg(endpoint_name_override="My test route"))
            self.assertEqual(auto["endpoint_id"],renamed["endpoint_id"])
            self.assertEqual(renamed["endpoint_label"],"My test route")
            self.assertEqual(renamed["endpoint_identity"]["source"],"operator_override")
            legacy=endpoints.resolve_identity(self.cfg(endpoint_id="operator-route",endpoint_label="Operator route"))
            self.assertEqual(legacy["endpoint_id"],"operator-route")
            self.assertEqual(legacy["endpoint_identity"]["source"],"operator_override")
            self.assertEqual(endpoints.resolve_identity(copy.deepcopy(legacy))["endpoint_id"],"operator-route")
        original=identity.build_model_configuration(legacy)
        historical=copy.deepcopy(original);historical["endpoint"]["source"]="user_reported"
        historical["configuration_id"]=identity.canonical_digest({k:v for k,v in historical.items() if k!="configuration_id"})
        self.assertTrue(identity.verify_model_configuration(historical))

    def test_no_port_or_model_alias_guessing_and_safe_metadata_only(self):
        with patch.object(endpoints,"_probe_identity",return_value={}) as probe:
            cfg=endpoints.resolve_identity(self.cfg("http://localhost:8081/v1"))
            self.assertEqual(cfg["endpoint_label"],"Local endpoint")
            self.assertEqual(cfg["endpoint_identity"]["service"],"unknown")
        for evidence in ({"model_alias":"Strata"},{"name":"Strata"},{"service":"owner-workstation","version":"host.local"}):
            self.assertEqual(endpoints._safe_service_evidence(evidence),{})
        self.assertEqual(endpoints._safe_service_evidence({"backend":{"name":"vllm","version":"0.10.1"}}),{"service":"vLLM","version":"0.10.1"})
        self.assertEqual(endpoints._safe_service_evidence({"service":"Strata","version":"1.2.3-private-host"}),{"service":"Strata","version":"unknown"})
        self.assertEqual(endpoints._safe_service_evidence({"service":"Strata","version":"192.168.1.1"}),{"service":"Strata","version":"unknown"})
        self.assertEqual(endpoints._safe_service_evidence({},"llama.cpp/b1234 Python/3.13"),{"service":"llama.cpp","version":"b1234","evidence":"known_server_header"})
        self.assertEqual(endpoints._safe_service_evidence({},"uvicorn"),{})
        props={"default_generation_settings":{},"model_path":"/private/owner/model.gguf","build_info":"b1234"}
        inferred=endpoints._safe_service_evidence(props)
        self.assertEqual(inferred["identification_source"],"inferred")
        self.assertNotIn("/private",json.dumps(inferred))
        with patch.object(endpoints,"_probe_identity",return_value=inferred):
            heuristic=endpoints.resolve_identity(self.cfg("http://localhost:8123/v1"))
        self.assertEqual(heuristic["endpoint_identity"]["source"],"inferred")
        managed=endpoints.resolve_identity({"provider":"lmstudio","model":"m"},probe=False)
        self.assertEqual(managed["endpoint_identity"]["service"],"LM Studio")
        self.assertEqual(managed["endpoint_identity"]["source"],"inferred")
        forged=self.cfg(endpoint_id="pretend-provider",endpoint_label="Pretend provider",
                        endpoint_identity={"source":"detected","service":"Strata","version":"0.2.6"})
        with patch.object(endpoints,"_probe_identity",return_value={}):resolved=endpoints.resolve_identity(forged)
        self.assertEqual(resolved["endpoint_identity"]["service"],"unknown")
        self.assertEqual(resolved["endpoint_identity"]["source"],"unknown")
        self.assertEqual(resolved["endpoint_label"],"Local endpoint")
        self.assertNotEqual(resolved["endpoint_id"],"pretend-provider")

    def test_bounded_same_origin_health_props_and_version_probes(self):
        requests=[]
        payloads={"/health":{"status":"ok"},"/props":{"model_alias":"Strata"},"/version":{"version":"0.2.6"}}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                requests.append(self.path)
                self.send_response(200);self.end_headers()
                self.wfile.write(json.dumps(payloads.get(self.path,{})).encode())
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        cfg=self.cfg("http://127.0.0.1:%d/v1" % server.server_port)
        unknown=endpoints.resolve_identity(cfg)
        self.assertEqual(unknown["endpoint_label"],"Local endpoint")
        self.assertEqual(requests,["/health","/props","/version"])
        endpoints._PROBE_CACHE.clear();requests.clear()
        payloads["/health"]={"status":"ok","service":"Strata","version":"0.2.6"}
        known=endpoints.resolve_identity(self.cfg(cfg["endpoint_base_url"]))
        self.assertEqual(known["endpoint_label"],"Strata local endpoint")
        self.assertEqual(requests,["/health"])
        self.assertEqual(known["endpoint_identity"]["version"],"0.2.6")
        self.assertFalse(known.get("endpoint_inference_local",False))

    def test_known_server_headers_from_models_and_plain_health_are_detected_without_extra_probes(self):
        requests=[];header=["llama.cpp/b1234"]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def version_string(self):return header[0]
            def do_GET(self):
                requests.append(self.path);self.send_response(200);self.end_headers()
                if self.path.endswith("/models"):self.wfile.write(b'{"data":[{"id":"Strata-model-alias"}]}')
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base="http://127.0.0.1:%d/v1" % server.server_port
        cfg=self.cfg(base)
        endpoints.list_models(cfg)
        endpoints.resolve_identity(cfg)
        self.assertEqual(cfg["endpoint_identity"]["service"],"llama.cpp")
        self.assertEqual(cfg["endpoint_identity"]["source"],"detected")
        self.assertEqual(cfg["endpoint_identity"]["version"],"b1234")
        self.assertEqual(requests,["/v1/models"])
        endpoints._PROBE_CACHE.clear();requests.clear();header[0]="vllm/0.10.1"
        health=endpoints.resolve_identity(self.cfg(base))
        self.assertEqual(health["endpoint_label"],"vLLM local endpoint")
        self.assertEqual(health["endpoint_identity"]["source"],"detected")
        self.assertEqual(requests,["/health"])


if __name__=="__main__":unittest.main()
