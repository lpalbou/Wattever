import copy
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from wattever import capabilities as cap, identity
from wattever.privacy import public_projection


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        patcher=patch.object(cap.endpoints,"ROUTE_REGISTRY_PATH",self.temp.name+"/routes.json")
        patcher.start();self.addCleanup(patcher.stop)
    def config(self,**kwargs):
        return dict(provider="openai",model="local-alias",endpoint_id="lab-strata",endpoint_label="Lab Strata",
                    canonical_model_id="org/canonical-model",endpoint_reasoning_effort="none",**kwargs)

    def test_configuration_tracks_public_requested_reported_and_verified_fields(self):
        cfg=self.config(reproducibility={"quantization":"IQ3_S","engine":"Strata","model_revision":"abc123"},reported_mtp="on")
        value=identity.build_model_configuration(cfg)
        self.assertEqual(value["model"]["value"],"org/canonical-model")
        self.assertEqual(value["request_model"]["value"],"local-alias")
        self.assertEqual(value["reasoning"]["requested"],"none")
        self.assertEqual(value["reasoning"]["effective"],"unknown")
        self.assertEqual(value["mtp"]["user_reported"],"on")
        self.assertEqual(value["mtp"]["effective"],"unknown")
        self.assertTrue(identity.verify_model_configuration(value,require_endpoint=True))
        controlled=identity.build_model_configuration(cfg,effective={"mtp":False,"quantization":"Q4_K_M","reasoning_disabled":True})
        self.assertEqual(controlled["mtp"]["effective"],"off")
        self.assertEqual(controlled["quantization"]["source"],"control_plane_verified")
        self.assertEqual(controlled["reasoning"]["effective"],"unknown")
        self.assertNotEqual(value["configuration_id"],controlled["configuration_id"])

    def test_no_private_connection_hashes_or_self_consistent_extra_fields(self):
        first=identity.build_model_configuration(self.config(endpoint_base_url="https://private-one/v1"))
        second=identity.build_model_configuration(self.config(endpoint_base_url="https://private-two/v1"))
        self.assertEqual(first,second)
        self.assertNotIn("private-one",json.dumps(first))
        for key,value in (("endpoint_id","127.0.0.1"),("endpoint_label","http://private-one/v1")):
            cfg=self.config();cfg[key]=value
            with self.assertRaises(ValueError): identity.build_model_configuration(cfg)
        first["engine"]["extra"]="https://secret.example"
        first["configuration_id"]=identity.canonical_digest({k:v for k,v in first.items() if k!="configuration_id"})
        with self.assertRaises(ValueError): identity.verify_model_configuration(first)
        legacy=identity.build_model_configuration({"model":"m"},require_endpoint=False)
        self.assertTrue(identity.verify_model_configuration(legacy))
        self.assertTrue(identity.verify_model_configuration(legacy,require_endpoint=True))

    def test_private_transport_model_paths_use_public_canonical_identity_without_hashing_path(self):
        cfg=self.config()
        cfg["model"]="/models/private/customer-model.gguf"
        first=identity.build_model_configuration(cfg)
        cfg["model"]=r"C:\private\other-model.gguf"
        second=identity.build_model_configuration(cfg)
        self.assertEqual(first,second)
        self.assertEqual(first["model"]["value"],"org/canonical-model")
        self.assertEqual(first["request_model"],{"value":"private model identifier","source":"redacted_transport_identifier"})
        self.assertTrue(identity.verify_model_configuration(first,require_endpoint=True))
        cfg.pop("canonical_model_id")
        self.assertEqual(identity.build_model_configuration(cfg)["model"]["value"],"unknown")

    def test_explicit_public_model_source_citation_survives_fingerprint_projection(self):
        cfg=self.config(model_source="https://huggingface.co/public/model")
        value=identity.build_model_configuration(cfg)
        self.assertTrue(identity.verify_model_configuration(value,require_endpoint=True))
        self.assertEqual(public_projection(value),value)
        with self.assertRaises(ValueError):identity.build_model_configuration(self.config(model_source="http://127.0.0.1/private-model"))


class ScoringTests(unittest.TestCase):
    def test_original_suite_answers_have_unique_auditable_coverage(self):
        suite=cap.suite_manifest()
        self.assertEqual(len(suite["tasks"]),24)
        self.assertEqual(len({t["id"] for t in suite["tasks"]}),24)
        self.assertEqual(len({t["category"] for t in suite["tasks"]}),6)
        self.assertEqual(suite["digest"],identity.canonical_digest({k:v for k,v in suite.items() if k!="digest"}))
        for task in suite["tasks"]:
            response=json.dumps(task["scorer"]["expected"]) if task["scorer"]["kind"]=="json_exact" else task["scorer"]["expected"]
            with self.subTest(task=task["id"]): self.assertTrue(cap.score_response(task,response)["correct"])
        changed=copy.deepcopy(suite);changed["tasks"][0]["prompt"]+=" changed"
        with self.assertRaises(ValueError):cap.register_suite(changed)

    def test_json_scoring_is_typed_strict_and_truncation_is_incorrect(self):
        task=cap._task("j","json","prompt",{"message":"ok","n":1},"json_exact")
        self.assertTrue(cap.score_response(task,'{"n":1,"message":"ok"}')["correct"])
        for answer in ('{"n":true,"message":"ok"}','{"n":1,"n":1,"message":"ok"}','{"n":1e9999,"message":"ok"}','```json\n{"n":1,"message":"ok"}\n```','['*1100+']'*1100):
            self.assertFalse(cap.score_response(task,answer)["correct"])
        scored=cap.score_response(cap.CORE_TASKS[0],"933","length")
        self.assertTrue(scored["scored"]);self.assertFalse(scored["correct"]);self.assertTrue(scored["truncated"])
        empty_task=cap._task("empty","instructions","Say nothing.","")
        self.assertTrue(cap.score_response(empty_task,"")["correct"],"Original rule semantics remain available for historical records")
        missing=cap._score_completion(empty_task,"","stop",cap.COMPLETION_POLICY)
        self.assertFalse(missing["correct"])
        self.assertEqual(missing["reason"],"missing_visible_answer")
        self.assertTrue(cap._score_completion(empty_task,"","stop",None)["correct"])

    def test_registered_numeric_and_choice_rules_are_explicit(self):
        numeric=cap._task("n","math","prompt","1234","numeric_final")
        self.assertTrue(cap.score_response(numeric,"Work shown\n#### 1,234")["correct"])
        for answer in ("1,,234","1,2,34","The answer is 1234"):
            self.assertFalse(cap.score_response(numeric,answer)["correct"])
        choice=cap._task("c","science","prompt","B","multiple_choice")
        self.assertTrue(cap.score_response(choice,"Analysis\nANSWER: B")["correct"])
        self.assertFalse(cap.score_response(choice,"B because it is right")["correct"])

    def test_manifest_rejects_hidden_credentials_but_keeps_public_task_material(self):
        original=cap.suite_manifest();original.pop("digest")
        for mutate in (lambda s:s.update(authorization="secret"),
                       lambda s:s["source"].update(api_key="secret"),
                       lambda s:s["source"].update(url="http://127.0.0.1/private"),
                       lambda s:s["tasks"][0].update(connection={"key":"secret"})):
            value=copy.deepcopy(original);mutate(value)
            with self.assertRaises(ValueError):cap.register_suite(value)
        value=copy.deepcopy(original);value["id"]="public-url-fixture-v1"
        value["source"]["repository"]="https://github.com/public/benchmark"
        value["tasks"][0]["prompt"]="A public URL in task material: https://example.org/article. Reply 933."
        registered=cap.register_suite(value)
        self.assertEqual(registered["tasks"][0]["prompt"],value["tasks"][0]["prompt"])
        cap._SUITES.pop(value["id"])


class CapabilityRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        patcher=patch.object(cap,"RUNS_DIR",self.temp.name);patcher.start();self.addCleanup(patcher.stop)
        route_patcher=patch.object(cap.endpoints,"ROUTE_REGISTRY_PATH",self.temp.name+"/private/routes.json")
        route_patcher.start();self.addCleanup(route_patcher.stop)
        self.cfg=dict(provider="openai",endpoint_base_url="http://localhost:8086/v1",endpoint_id="test-route",
                      endpoint_label="Test route",model="model",suite_id="wattever-core-v1",sample_size=24,
                      max_tokens=256,timeout_s=2,seed=13,repeats=1)
        self.tasks={t["prompt"]:t for t in cap.suite_manifest()["tasks"]}

    def response(self,model,prompt,max_tokens,**kwargs):
        task=self.tasks[prompt]
        expected=task["scorer"]["expected"]
        answer=json.dumps(expected) if task["scorer"]["kind"]=="json_exact" else expected
        return {"output_text":answer,"finish_reason":"stop","prompt_tokens":30,"completion_tokens":4,
                "wall_s":.02,"ttft_s":.01,"response_model":"provider-alias"}

    def evaluate(self,cfg=None,side_effect=None,**kwargs):
        bench=cap.CapabilityRunner(**kwargs)
        with patch.object(cap.lmstudio,"measure_completion",side_effect=side_effect or self.response) as request:
            bench.start(cfg or self.cfg)
            bench.thread.join(timeout=5)
            self.assertFalse(bench.running)
        self.assertEqual(bench.run["status"],"completed",bench.run)
        return bench,request

    def test_complete_run_repeats_are_not_independent_and_metadata_stays_public(self):
        bench,request=self.evaluate(dict(self.cfg,repeats=2))
        run=bench.run
        self.assertEqual(request.call_count,48)
        self.assertEqual(run["summary"]["correct"],48)
        self.assertEqual(run["summary"]["wilson_95"]["n"],24)
        self.assertEqual(run["summary"]["unique_items"],24)
        self.assertEqual(run["summary"]["penalized_accuracy"],1)
        self.assertNotIn(self.cfg["endpoint_base_url"],json.dumps(run))
        self.assertNotIn("endpoint_base_url",run["config"])
        self.assertEqual(run["results"][0]["response_model"],"provider-alias")
        self.assertTrue(request.call_args.kwargs["capture_output"])
        self.assertEqual(request.call_args.kwargs["reasoning_effort"],"none")
        self.assertIsNone(request.call_args.kwargs["seed"])
        self.assertIsNone(request.call_args.kwargs["top_p"])
        events,cursor=bench.events_since(0)
        self.assertEqual(events[-1]["kind"],"run_done")
        self.assertEqual(cursor,len(events))

    def test_quality_defaults_allow_reasoning_beyond_speed_budget_and_keep_final_answer(self):
        cfg=dict(self.cfg,sample_size=1)
        cfg.pop("max_tokens");cfg.pop("timeout_s")
        def reasoning_model(model,prompt,max_tokens,**kwargs):
            value=self.response(model,prompt,max_tokens,**kwargs)
            value.update(completion_tokens=1027,reasoning_tokens=1024,
                         output_capture={"scope":"visible content only; streamed reasoning excluded","visible_content_observed":True,"visible_answer_present":True,"reasoning_observed":True,"observable_output_present":True,"stream_complete":True},
                         timing_scope={"first_output_kind":"reasoning_content","reasoning_streamed":True})
            if max_tokens<=1024:
                value.update(output_text="",finish_reason="length",completion_tokens=max_tokens,reasoning_tokens=max_tokens)
                value["output_capture"].update(visible_content_observed=False,visible_answer_present=False)
            return value
        bench,request=self.evaluate(cfg,reasoning_model)
        self.assertEqual(request.call_args.args[2],32768)
        self.assertIsNone(request.call_args.kwargs["timeout"])
        self.assertEqual(bench.run["summary"]["correct"],1)
        self.assertEqual(bench.run["results"][0]["usage"]["reasoning_tokens"],1024)
        self.assertEqual(bench.run["protocol"]["timeout_s"],0)
        self.assertEqual(bench.run["protocol"]["completion_policy"],cap.COMPLETION_POLICY)
        self.assertIn("reasoning",bench.run["protocol"]["budget_scope"])
        self.assertTrue(cap.validate_import(bench.export(bench.run["id"])))
        limited,_=self.evaluate(dict(cfg,max_tokens=256),reasoning_model)
        self.assertEqual(limited.run["summary"]["truncated"],1)
        self.assertEqual(limited.run["summary"]["scored"],1)
        self.assertEqual(limited.run["summary"]["errors"],0)

    def test_completed_empty_answers_preserve_finish_usage_observations_and_are_scored(self):
        for finish in ("length","stop"):
            with self.subTest(finish_reason=finish):
                value={"output_text":"","finish_reason":finish,"prompt_tokens":41,"completion_tokens":256,
                       "reasoning_tokens":256,"cached_prompt_tokens":0,"completion_tokens_estimated":False,
                       "ttft_s":None,"wall_s":12.5,"decode_s":None,"decode_token_count":None,
                       "timing_scope":{"boundary":"no observable output"},
                       "reported_timings":{"predicted_n":256},
                       "output_capture":{"scope":"visible content only","visible_content_observed":False,"visible_answer_present":False,"reasoning_observed":False,"observable_output_present":False,"stream_complete":True}}
                bench,_=self.evaluate(dict(self.cfg,sample_size=1),lambda *a,**kw:value)
                row=bench.run["results"][0]
                self.assertEqual(row["status"],"ok")
                self.assertEqual(row["response"],"")
                self.assertEqual(row["finish_reason"],finish)
                self.assertTrue(row["scoring"]["scored"])
                self.assertFalse(row["scoring"]["correct"])
                self.assertEqual(row["scoring"]["truncated"],finish=="length")
                self.assertEqual(row["scoring"]["reason"],"output_token_limit_reached" if finish=="length" else "missing_visible_answer")
                self.assertEqual(row["usage"]["completion_tokens"],256)
                self.assertEqual(row["usage"]["reasoning_tokens"],256)
                self.assertEqual(row["timing"]["wall_s"],12.5)
                self.assertIsNone(row["timing"]["ttft_s"])
                self.assertEqual(row["timing"]["reported_timings"],{"predicted_n":256})
                self.assertEqual(row["timing_scope"],value["timing_scope"])
                self.assertEqual(row["observed_output"],value["output_capture"])
                self.assertEqual(row["answer_observation"],"missing_visible_answer")
                self.assertEqual(bench.run["summary"]["errors"],0)
                self.assertEqual(bench.run["summary"]["scored"],1)
                self.assertEqual(bench.run["summary"]["truncated"],int(finish=="length"))
                imported=cap.validate_import(bench.export(bench.run["id"]))
                self.assertEqual(imported["results"][0]["scoring"],row["scoring"])

    def test_generation_and_optional_deadline_bounds_and_legacy_import_policy(self):
        maximum,_=self.evaluate(dict(self.cfg,sample_size=1,max_tokens=131072,timeout_s=86400))
        self.assertTrue(cap.validate_import(maximum.export(maximum.run["id"])))
        for key,values in (("max_tokens",(0,131073,True)),("timeout_s",(-1,86401,False))):
            for value in values:
                with self.subTest(key=key,value=value),self.assertRaises(ValueError):cap._validate_config(dict(self.cfg,**{key:value}))
        exported=maximum.export(maximum.run["id"])
        for key,value in (("max_tokens",131073),("timeout_s",86401),("timeout_s",-1)):
            bad=copy.deepcopy(exported);bad["protocol"][key]=value
            bad["evaluation_key"]=identity.canonical_digest({k:v for k,v in bad["protocol"].items() if k!="repeats"})
            with self.subTest(import_key=key,value=value),self.assertRaises(ValueError):cap.validate_import(bad)
            bad_config=copy.deepcopy(exported);bad_config["config"][key]=value
            with self.subTest(import_config=key,value=value),self.assertRaises(ValueError):cap.validate_import(bad_config)
        legacy=copy.deepcopy(exported)
        for key in ("completion_policy","budget_scope","time_policy"):legacy["protocol"].pop(key,None)
        legacy["protocol"].update(max_tokens=256,timeout_s=120)
        legacy["evaluation_key"]=identity.canonical_digest({k:v for k,v in legacy["protocol"].items() if k!="repeats"})
        previous=copy.deepcopy(legacy["results"])
        self.assertEqual(cap.validate_import(legacy)["results"],previous)
        legacy["results"][0].update(status="error",response="",scoring={"scored":False,"reason":"request_error"})
        restored=cap.validate_import(legacy)
        self.assertFalse(restored["results"][0]["scoring"]["scored"],"Historical transport/missing-answer failures must not be silently regraded")
        unknown=copy.deepcopy(exported);unknown["protocol"]["completion_policy"]="unknown-policy"
        unknown["evaluation_key"]=identity.canonical_digest({k:v for k,v in unknown["protocol"].items() if k!="repeats"})
        with self.assertRaises(ValueError):cap.validate_import(unknown)

    def test_v2_terminal_and_visible_evidence_tampering_is_rejected_on_import_and_export(self):
        bench,_=self.evaluate(dict(self.cfg,sample_size=1))
        original=bench.export(bench.run["id"])
        original["results"][0]["observed_output"]={"stream_complete":True,"visible_answer_present":True}
        self.assertTrue(cap.validate_import(original))
        mutations=[lambda r:r.pop("finish_reason")]
        mutations += [lambda r,value=value:r.update(finish_reason=value) for value in (None,""," ",False,1,["stop"],"max_tokens")]
        mutations += [lambda r,value=value:r["observed_output"].update(stream_complete=value) for value in (False,None,1)]
        mutations += [lambda r,value=value:r["observed_output"].update(visible_answer_present=value) for value in (False,None,1)]
        for index,mutate in enumerate(mutations):
            bad=copy.deepcopy(original);mutate(bad["results"][0])
            with self.subTest(case=index),self.assertRaises(ValueError):cap.validate_import(bad)
            cap._save(bad)
            with self.subTest(export_case=index),self.assertRaises(ValueError):bench.export(bad["id"])
        empty=copy.deepcopy(original);row=empty["results"][0];row["response"]=""
        task=next(t for t in empty["suite"]["tasks"] if t["id"]==row["item_id"])
        row["scoring"]=cap._score_completion(task,"","stop",cap.COMPLETION_POLICY)
        with self.assertRaises(ValueError):cap.validate_import(empty)
        row["observed_output"]["visible_answer_present"]=False
        self.assertTrue(cap.validate_import(empty))
        legacy=copy.deepcopy(original);legacy["protocol"].pop("completion_policy")
        legacy["evaluation_key"]=identity.canonical_digest({k:v for k,v in legacy["protocol"].items() if k!="repeats"})
        legacy["results"][0].pop("finish_reason")
        self.assertTrue(cap.validate_import(legacy),"Historical missing-terminal metadata retains its original import policy")
        legacy["results"][0]["finish_reason"]="max_tokens"
        self.assertTrue(cap.validate_import(legacy),"Historical terminal strings must retain the legacy policy")

    def test_malformed_transport_success_is_unscored_but_retains_completion_metadata(self):
        for finish,observed in ((None,None),("max_tokens",None),("stop",{"stream_complete":False}),("stop",{"visible_answer_present":False})):
            with self.subTest(finish=finish,observed=observed):
                def malformed(*args,**kwargs):
                    value=self.response(*args,**kwargs)
                    value.update(finish_reason=finish,completion_tokens=37,wall_s=4.5,output_capture=observed)
                    return value
                bench,_=self.evaluate(dict(self.cfg,sample_size=1),malformed)
                row=bench.run["results"][0]
                self.assertEqual(row["status"],"error")
                self.assertFalse(row["scoring"]["scored"])
                self.assertEqual(row["finish_reason"],finish)
                self.assertEqual(row["observed_output"],observed)
                self.assertEqual(row["usage"]["completion_tokens"],37)
                self.assertEqual(row["timing"]["wall_s"],4.5)
                self.assertTrue(row["response"])
                self.assertEqual(bench.run["summary"]["errors"],1)

    def test_direct_start_needs_no_manually_named_endpoint(self):
        cfg=dict(self.cfg);cfg.pop("endpoint_id");cfg.pop("endpoint_label")
        with patch.object(cap.endpoints,"_probe_identity",return_value={}):bench,_=self.evaluate(cfg)
        public=bench.run["model_configuration"]["endpoint"]
        self.assertTrue(public["id"].startswith("endpoint-local-"))
        self.assertEqual(public["label"],"Local endpoint")
        self.assertEqual(public["source"],"unknown")
        self.assertTrue(cap.validate_import(bench.export(bench.run["id"])))

    def test_errors_are_unscored_truncation_incorrect_and_denominators_explicit(self):
        calls=[0]
        def response(*args,**kwargs):
            calls[0]+=1
            if calls[0]==1: raise RuntimeError("HTTP 500 private http://host/private")
            value=self.response(*args,**kwargs)
            if calls[0]==2:value["finish_reason"]="length"
            return value
        bench,_=self.evaluate(side_effect=response)
        summary=bench.run["summary"]
        self.assertEqual(summary["errors"],1)
        self.assertEqual(summary["scored"],23)
        self.assertEqual(summary["correct"],22)
        self.assertEqual(summary["truncated"],1)
        self.assertAlmostEqual(summary["accuracy"],22/23)
        self.assertAlmostEqual(summary["penalized_accuracy"],22/24)
        self.assertNotIn("http://host",json.dumps(bench.run))

    def test_seeded_subset_import_export_roundtrip_and_tamper_rejection(self):
        bench,_=self.evaluate(dict(self.cfg,sample_size=5))
        exported=bench.export(bench.run["id"])
        self.assertEqual(cap.validate_import(exported)["configuration_id"],bench.run["configuration_id"])
        self.assertEqual(bench.import_run({"capability_runs":[exported]}),[bench.run["id"]])
        for mutate in (lambda r:r["results"][0]["scoring"].update(correct=False),
                       lambda r:r["suite"]["tasks"][0].update(prompt="tampered"),
                       lambda r:r["results"].pop(),
                       lambda r:r.update(id="../escape")):
            bad=copy.deepcopy(exported);mutate(bad)
            with self.assertRaises(ValueError):cap.validate_import(bad)
        other,_=self.evaluate(dict(self.cfg,sample_size=5))
        self.assertEqual(bench.run["evaluation_key"],other.run["evaluation_key"])
        self.assertEqual(bench.run["protocol"]["selected_item_ids"],other.run["protocol"]["selected_item_ids"])

    def test_selection_seed_is_separate_from_optional_provider_seed(self):
        omitted,request=self.evaluate(dict(self.cfg,sample_size=3,temperature=None))
        explicit,sent=self.evaluate(dict(self.cfg,sample_size=3,temperature=None,send_seed=True,top_p=.8))
        self.assertEqual(omitted.run["protocol"]["selected_item_ids"],explicit.run["protocol"]["selected_item_ids"])
        self.assertEqual(omitted.run["protocol"]["selected_item_ids"],["arithmetic-03","instructions-02","logic-01"])
        self.assertEqual(omitted.run["protocol"]["selection_algorithm"],"sha256-ranked-items-v1")
        reversed_ids=list(reversed([task["id"] for task in cap.CORE_TASKS]))
        self.assertEqual(cap.select_item_ids(reversed_ids,3,13),omitted.run["protocol"]["selected_item_ids"])
        self.assertEqual(cap.select_item_ids(reversed_ids,3,13,None),["instructions-01","instructions-02","retrieval-02"])
        self.assertNotEqual(omitted.run["evaluation_key"],explicit.run["evaluation_key"])
        self.assertIsNone(request.call_args.kwargs["seed"])
        self.assertIsNone(request.call_args.kwargs["temperature"])
        self.assertEqual(sent.call_args.kwargs["seed"],13)
        self.assertEqual(sent.call_args.kwargs["top_p"],.8)
        self.assertEqual(explicit.run["results"][0]["requested_seed"],13)
        self.assertTrue(cap.validate_import(explicit.export(explicit.run["id"])))

    def test_json_message_answer_is_preserved_by_public_export(self):
        bench,_=self.evaluate()
        exported=bench.export(bench.run["id"])
        row=next(r for r in exported["results"] if r["item_id"]=="json-03")
        self.assertEqual(row["scoring"]["expected"],{"message":"line1\nline2"})
        self.assertTrue(cap.validate_import(exported))

    def test_private_model_transport_id_is_runtime_only(self):
        model="/models/private/customer-model.gguf"
        bench,request=self.evaluate(dict(self.cfg,model=model,canonical_model_id="org/public-model",sample_size=1))
        self.assertEqual(request.call_args.args[0],model)
        self.assertNotIn("customer-model",json.dumps(bench.run))
        self.assertNotIn("/models",json.dumps(bench.export(bench.run["id"])))
        self.assertEqual(bench.run["model_configuration"]["model"]["value"],"org/public-model")

    def test_deadline_stops_later_tasks_and_pending_worker_blocks_new_runs(self):
        release=threading.Event()
        bench=cap.CapabilityRunner()
        def stalled(*args,**kwargs):release.wait(timeout=4);return self.response(*args,**kwargs)
        try:
            with patch.object(cap.lmstudio,"measure_completion",side_effect=stalled) as request:
                bench.start(dict(self.cfg,timeout_s=1))
                bench.thread.join(timeout=2)
                self.assertEqual(bench.run["status"],"error")
                self.assertEqual(request.call_count,1)
                self.assertEqual(bench.run["summary"]["errors"],1)
                self.assertTrue(bench.running)
                with self.assertRaises(RuntimeError):bench.start(self.cfg)
        finally:
            release.set()
            if bench._pending_worker:bench._pending_worker.join(timeout=2)

    def test_cancel_records_an_unscored_attempt_and_sends_no_later_tasks(self):
        entered=threading.Event()
        def cancellable(*args,**kwargs):
            entered.set()
            while not kwargs["cancel"].is_set():time.sleep(.005)
            raise cap.lmstudio.Cancelled()
        bench=cap.CapabilityRunner()
        with patch.object(cap.lmstudio,"measure_completion",side_effect=cancellable) as request:
            bench.start(dict(self.cfg,timeout_s=0))
            self.assertTrue(entered.wait(timeout=1))
            bench.cancel()
            bench.thread.join(timeout=2)
            if bench._pending_worker:bench._pending_worker.join(timeout=1)
        self.assertFalse(bench.running)
        self.assertEqual(bench.run["status"],"cancelled")
        self.assertEqual(bench.run["summary"]["cancelled"],1)
        self.assertEqual(bench.run["summary"]["scored"],0)
        self.assertEqual(request.call_count,1)

    def test_no_deadline_waits_until_completion_and_cancel_keeps_pending_worker_guard(self):
        entered=threading.Event();release=threading.Event();bench=cap.CapabilityRunner()
        def waiting(*args,**kwargs):entered.set();release.wait(timeout=4);return self.response(*args,**kwargs)
        try:
            with patch.object(cap.lmstudio,"measure_completion",side_effect=waiting) as request:
                bench.start(dict(self.cfg,sample_size=1,timeout_s=0))
                self.assertTrue(entered.wait(timeout=1))
                bench.thread.join(timeout=.1)
                self.assertTrue(bench.running,"Zero means no deadline, not an immediate timeout")
                self.assertIsNone(request.call_args.kwargs["timeout"])
                bench.cancel();bench.thread.join(timeout=1)
                self.assertEqual(bench.run["status"],"cancelled")
                self.assertTrue(bench.running,"The unfinished HTTP worker still blocks another benchmark")
                with self.assertRaises(RuntimeError):bench.start(self.cfg)
        finally:
            release.set()
            if bench._pending_worker:bench._pending_worker.join(timeout=2)

    def test_telemetry_callbacks_and_live_sampler_are_optional(self):
        class Handle:
            def now(self):return {"gpu_w":40}
        handle=Handle();started=[];finished=[]
        def begin(run,cfg):started.append(run["id"]);return handle
        def finish(run,h):finished.append(h);run["telemetry"]={"local":True}
        bench,_=self.evaluate(on_run_start=begin,on_run_finish=finish)
        self.assertEqual(started,[bench.run["id"]]);self.assertEqual(finished,[handle])
        self.assertEqual(bench.run["telemetry"],{"local":True})
        bench._telemetry_handle=handle
        self.assertEqual(bench.status()["live"]["telemetry"]["gpu_w"],40)


if __name__=="__main__":unittest.main()
