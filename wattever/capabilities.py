"""Auditable, deterministic rule-scored capability evaluation, separate from speed.

Wattever Core is an original convenience test, not IQ and not an official
leaderboard. Public prompts and expected answers make contamination possible.
No generated code is executed and no subjective model judge is used.
"""
import copy
import datetime
import decimal
import json
import math
import os
import random
import re
import tempfile
import threading
import time
import uuid

from . import __version__, endpoints, identity, lmstudio
from .privacy import LOCAL_DETAIL, NETWORK_DETAIL, private_key, public_projection, public_source

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_DIR = os.path.join(ROOT, "results", "capabilities")
SCORING_VERSION = "wattever-rule-scorers-v1"
PROTOCOL_VERSION = "wattever-capability-v1"
COMPLETION_POLICY = "finished-stream-visible-answer-v2"
TERMINAL_FINISH_REASONS = ("stop","length","tool_calls","content_filter","function_call")
SELECTION_ALGORITHM = "sha256-ranked-items-v1"
DEFAULT_CONFIG = {"provider":"openai", "suite_id":"wattever-core-v1", "sample_size":24,
                  "repeats":1, "max_tokens":32768, "timeout_s":0, "seed":0,
                  "temperature":0.0, "top_p":None, "send_seed":False, "token_limit_field":"max_tokens", "endpoint_reasoning_effort":"none",
                  "endpoint_inference_local":False, "endpoint_label":"", "endpoint_id":""}


def _task(item_id, category, prompt, expected, kind="exact_text"):
    return {"id":item_id,"category":category,"prompt":prompt,
            "scorer":{"kind":kind,"expected":expected}}


CORE_TASKS = [
    _task("arithmetic-01","arithmetic","Compute 347 + 586. Reply with only the integer.","933"),
    _task("arithmetic-02","arithmetic","Compute 84 * 17. Reply with only the integer.","1428"),
    _task("arithmetic-03","arithmetic","Compute (125 - 37) * 6. Reply with only the integer.","528"),
    _task("arithmetic-04","arithmetic","What is three eighths of 240? Reply with only the integer.","90"),
    _task("logic-01","logic","All norks are tavs. No tav is a mip. Can any nork be a mip? Reply exactly YES or NO.","NO"),
    _task("logic-02","logic","Four people stand in a line. Ava is before Bex, Bex before Cy, and Dax after Cy. Who is second? Reply with only the name.","Bex"),
    _task("logic-03","logic","A prize is in exactly one of RED, BLUE, GREEN. Exactly one label is true. RED says 'the prize is in BLUE'. BLUE says 'the prize is not in BLUE'. GREEN says 'the prize is not in RED'. Which box contains the prize? Reply only RED, BLUE, or GREEN.","RED"),
    _task("logic-04","logic","Evaluate (true AND false) OR ((NOT false) AND true). Reply exactly TRUE or FALSE.","TRUE"),
    _task("json-01","json","Return only a JSON object with exactly two keys: count with integer value 3, and colors with the ordered list amber, violet, cyan. Do not use Markdown fences.",{"count":3,"colors":["amber","violet","cyan"]},"json_exact"),
    _task("json-02","json","Rows: A has quantity 3; B has quantity 1; C has quantity 4. Return only JSON {\"ids\":[...]} listing IDs with quantity at least 3 in row order.",{"ids":["A","C"]},"json_exact"),
    _task("json-03","json","Return only a JSON object with exactly the key message. Its value must be the text line1, then a newline character, then line2. Encode the newline correctly in JSON.",{"message":"line1\nline2"},"json_exact"),
    _task("json-04","json","Sort 7, -2, 11, 0 numerically ascending. Return only a JSON array of integers.",[-2,0,7,11],"json_exact"),
    _task("instructions-01","instructions","Output exactly cobalt|birch|7 and nothing else.","cobalt|birch|7"),
    _task("instructions-02","instructions","Select the last word in this list: ivory falcon cedar. Reply with only that lowercase word, without punctuation.","cedar"),
    _task("instructions-03","instructions","Select only the capitalized words from: Apple pear Birch plum Cedar. Preserve order and join with commas without spaces. Output nothing else.","Apple,Birch,Cedar"),
    _task("instructions-04","instructions","Reverse the characters NEBULA. Reply only with the reversed uppercase string.","ALUBEN"),
    _task("retrieval-01","retrieval","Registry:\nR17 | north | VELA\nR23 | south | ORBIT\nR31 | east | LYRA\nWhat is the code for R23? Reply only with that code.","ORBIT"),
    _task("retrieval-02","retrieval","Shipment manifest:\nP-12: 2.50 kg\nP-42: 4.75 kg\nP-81: 6.00 kg\nWhat is the mass of P-42 in kg? Reply only with the decimal number exactly as written.","4.75"),
    _task("retrieval-03","retrieval","Revision log: Revision 1 sets the project codename to EMBER. Revision 2 replaces that codename with SABLE. Revision 3 changes only the launch month, leaving the codename unchanged. What is the latest codename? Reply only with the uppercase codename.","SABLE"),
    _task("retrieval-04","retrieval","Organization facts: Mira reports to Oren. Oren reports to Tala. Juno reports to Mira. Who is the manager of Mira's manager? Reply only with the name.","Tala"),
    _task("code-output-01","code_output","Without executing code, determine what this Python 3 code prints. Reply only with the output.\nn = 1\nfor _ in range(4):\n    n = n * 2 + 1\nprint(n)","31"),
    _task("code-output-02","code_output","Without executing code, determine what this Python 3 code prints. Reply only with the output.\ns = 'orbit'\nprint(s[1:4].upper())","RBI"),
    _task("code-output-03","code_output","Without executing code, determine what this Python 3 code prints. Reply only with the integer.\nxs = [3, 1, 3, 2]\nprint(sum(set(xs)))","6"),
    _task("code-output-04","code_output","Without executing code, determine what this Python 3 code prints. Reply only with the integer.\ndef f(x):\n    return x*x - 2*x\nprint(f(5) - f(2))","15"),
]

_SUITES = {}


def _strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    def finite_float(value):
        number=float(value)
        if not math.isfinite(number):raise ValueError("JSON number exceeds finite float range")
        return number
    return json.loads(text, object_pairs_hook=pairs,parse_float=finite_float,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non-finite JSON")))


def _typed_equal(a, b):
    if type(a) is not type(b): return False
    if isinstance(a, dict): return a.keys() == b.keys() and all(_typed_equal(a[k], b[k]) for k in a)
    if isinstance(a, list): return len(a) == len(b) and all(_typed_equal(x,y) for x,y in zip(a,b))
    return a == b


def score_response(task, response, finish_reason=None):
    """Return an auditable rule outcome. Whitespace outside the answer is ignored."""
    if not isinstance(response, str): raise ValueError("Response must be text.")
    scorer = task["scorer"]
    kind, expected = scorer["kind"], scorer["expected"]
    answer = response.strip()
    normalized = answer
    reason = "exact_match" if answer == expected else "answer_mismatch"
    try:
        if kind == "exact_text": correct = answer == expected
        elif kind == "json_exact":
            normalized = _strict_json(answer)
            correct = _typed_equal(normalized, expected)
            reason = "json_structural_match" if correct else "json_value_or_type_mismatch"
        elif kind == "numeric_final":
            final = answer.rsplit("####", 1)[-1].strip() if "####" in answer else answer
            if not re.fullmatch(r"[-+]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", final): raise ValueError("missing final numeric answer")
            normalized = str(decimal.Decimal(final.replace(",", "")).normalize())
            correct = decimal.Decimal(final.replace(",", "")) == decimal.Decimal(str(expected).replace(",", ""))
            reason = "numeric_match" if correct else "numeric_mismatch"
        elif kind == "multiple_choice":
            match = re.search(r"(?:^|\n)ANSWER:\s*([A-Za-z0-9]+)\s*$", answer)
            normalized = match.group(1) if match else answer
            correct = normalized == str(expected)
            reason = "choice_match" if correct else "choice_mismatch"
        else: raise ValueError("Unknown scorer.")
    except (ValueError, RecursionError, decimal.DecimalException):
        correct, reason = False, "invalid_answer_format"
    if finish_reason == "length": correct, reason = False, "output_token_limit_reached"
    return {"scored":True,"correct":bool(correct),"score":1 if correct else 0,
            "rule":kind,"scoring_version":SCORING_VERSION,"expected":copy.deepcopy(expected),
            "normalized_answer":normalized,"reason":reason,"truncated":finish_reason == "length"}


def _score_completion(task,response,finish_reason,completion_policy):
    result=score_response(task,response,finish_reason)
    if completion_policy==COMPLETION_POLICY and not response.strip() and finish_reason!="length":
        result.update(correct=False,score=0,reason="missing_visible_answer")
    return result


def _validate_completed_observation(row,completion_policy):
    if completion_policy!=COMPLETION_POLICY:return
    if not isinstance(row.get("response"),str):raise ValueError("Completed v2 observations require a text response.")
    finish=row.get("finish_reason")
    if not isinstance(finish,str) or not finish.strip():
        raise ValueError("Completed v2 observations require a nonempty finish_reason.")
    if finish not in TERMINAL_FINISH_REASONS:
        raise ValueError("Completed v2 observations require a standard Chat Completions finish_reason.")
    observed=row.get("observed_output")
    if observed is None:return
    if not isinstance(observed,dict):raise ValueError("Observed-output evidence must be an object.")
    if "stream_complete" in observed and observed["stream_complete"] is not True:
        raise ValueError("Completed v2 observations require consistent stream_complete evidence.")
    if "visible_answer_present" in observed:
        present=observed["visible_answer_present"]
        if type(present) is not bool or present!=bool(row.get("response","").strip()):
            raise ValueError("Visible-answer evidence contradicts the recorded response.")


def register_suite(manifest,register=True):
    """Register an immutable public suite; arbitrary executable scorers are rejected."""
    if not isinstance(manifest,dict): raise ValueError("Suite manifest must be an object.")
    manifest = copy.deepcopy(manifest)
    if set(manifest)-{"id","name","version","description","source","scoring_version","tasks","digest"}:
        raise ValueError("Unexpected suite manifest fields.")
    supplied_digest = manifest.pop("digest", None)
    if not isinstance(manifest.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", manifest["id"]):
        raise ValueError("Invalid suite ID.")
    for key in ("name", "version", "description"):
        if not isinstance(manifest.get(key), str) or not manifest[key] or len(manifest[key]) > 2000:
            raise ValueError("Suite %s is required and must be bounded text." % key)
    if manifest.get("scoring_version") != SCORING_VERSION: raise ValueError("Unsupported scoring version.")
    if not isinstance(manifest.get("source"), dict): raise ValueError("Suite source provenance is required.")
    source = manifest["source"]
    if not source or len(source)>30: raise ValueError("Provide bounded public suite source metadata.")
    for key,value in source.items():
        if not isinstance(key,str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}",key) or private_key(key): raise ValueError("Suite source contains a private or invalid metadata field.")
        if not isinstance(value,(str,int,float,bool)) or isinstance(value,str) and len(value)>3000:
            raise ValueError("Suite source values must be bounded public scalars.")
        if isinstance(value,str) and (LOCAL_DETAIL.search(value) or NETWORK_DETAIL.search(value)) and not public_source(value):
            raise ValueError("Suite source must not contain private addresses, credentials or paths.")
    tasks = manifest.get("tasks")
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 10000: raise ValueError("Suite must contain 1..10000 tasks.")
    seen = set()
    total_prompt_chars = 0
    for task in tasks:
        if not isinstance(task, dict): raise ValueError("Each task must be an object.")
        if set(task)!={"id","category","prompt","scorer"}: raise ValueError("Unexpected task fields.")
        item_id = task.get("id")
        if not isinstance(item_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", item_id) or item_id in seen:
            raise ValueError("Task IDs must be unique safe identifiers.")
        seen.add(item_id)
        if not isinstance(task.get("category"), str) or not task["category"] or len(task["category"]) > 100: raise ValueError("Task category is invalid.")
        if not isinstance(task.get("prompt"), str) or not 1 <= len(task["prompt"]) <= 200000: raise ValueError("Task prompt is invalid.")
        total_prompt_chars += len(task["prompt"])
        if total_prompt_chars>20000000: raise ValueError("Suite prompt material exceeds the bounded import size.")
        scorer = task.get("scorer")
        if not isinstance(scorer, dict) or scorer.get("kind") not in ("exact_text", "json_exact", "numeric_final", "multiple_choice") or "expected" not in scorer:
            raise ValueError("Task needs an allowed deterministic scorer and expected answer.")
        if set(scorer)-{"kind","expected","choices"}: raise ValueError("Unexpected scorer metadata.")
        if scorer["kind"] != "json_exact" and not isinstance(scorer["expected"], (str,int,float)):
            raise ValueError("Expected answer is invalid.")
        if scorer["kind"] == "exact_text" and not isinstance(scorer["expected"],str): raise ValueError("Exact text answers must be strings.")
        if scorer["kind"] == "numeric_final":
            try:
                number=decimal.Decimal(str(scorer["expected"]).replace(",",""))
                if not number.is_finite(): raise ValueError("non-finite expected number")
            except (decimal.InvalidOperation,ValueError): raise ValueError("Invalid expected numeric answer.")
        if type(scorer["expected"]) is bool and scorer["kind"]!="json_exact": raise ValueError("Expected scalar answer cannot be a boolean.")
        if "choices" in scorer:
            choices=scorer["choices"]
            if not isinstance(choices,list) or not 1<=len(choices)<=100 or any(not isinstance(c,str) or not 1<=len(c)<=30 for c in choices) or len(set(choices))!=len(choices): raise ValueError("Invalid answer choices.")
            if scorer["kind"]=="multiple_choice" and str(scorer["expected"]) not in choices: raise ValueError("Expected choice is not in the declared labels.")
    digest = identity.canonical_digest(manifest)
    if supplied_digest and supplied_digest != digest: raise ValueError("Suite manifest digest mismatch.")
    if manifest["id"] in _SUITES and _SUITES[manifest["id"]]["digest"] != digest:
        raise ValueError("Suite ID is already pinned to different content; use a new versioned ID.")
    manifest["digest"] = digest
    if register: _SUITES[manifest["id"]] = manifest
    return copy.deepcopy(manifest)


register_suite({"id":"wattever-core-v1","name":"Wattever Core v1","version":"1.0.0",
                "description":"24 original deterministic arithmetic, logic, JSON, instruction, retrieval and code-output tasks. Not IQ, not an official leaderboard; no code execution or subjective judge.",
                "source":{"kind":"bundled_original","author":"Wattever contributors","license":"project license; original task text"},
                "scoring_version":SCORING_VERSION,"tasks":CORE_TASKS})


def suite_manifest(suite_id="wattever-core-v1"):
    if suite_id not in _SUITES: raise ValueError("Unknown capability suite.")
    return copy.deepcopy(_SUITES[suite_id])


def list_suites():
    return [{key:copy.deepcopy(value) for key,value in suite.items() if key != "tasks"} |
            {"task_count":len(suite["tasks"]),"categories":sorted({t["category"] for t in suite["tasks"]})}
            for suite in _SUITES.values()]


def select_item_ids(item_ids,sample_size,seed,algorithm=SELECTION_ALGORITHM):
    """Machine-independent public subset selection; legacy Python sampling remains readable."""
    ordered=sorted(item_ids)
    if type(sample_size) is not int or sample_size<0 or type(seed) is not int or seed<0:
        raise ValueError("Subset size and seed must be non-negative integers.")
    if algorithm==SELECTION_ALGORITHM:
        ranked=sorted(ordered,key=lambda item_id:(identity.canonical_digest({"seed":seed,"item_id":item_id}),item_id))
        return sorted(ranked[:sample_size]) if sample_size else ordered
    if algorithm in (None,"sort IDs; random.Random(seed).sample if bounded; sort selected IDs","python-random-sample-v1"):
        return sorted(random.Random(seed).sample(ordered,sample_size)) if sample_size and sample_size<len(ordered) else ordered
    raise ValueError("Unsupported capability subset selection algorithm.")


def wilson(correct, n):
    if not n: return {"low":None,"high":None,"n":0,"correct":0}
    z = 1.959963984540054
    p = correct / n
    center = (p + z*z/(2*n)) / (1+z*z/n)
    radius = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1+z*z/n)
    return {"low":max(0.0,center-radius),"high":min(1.0,center+radius),"n":n,"correct":correct}


def aggregate(results, selected_ids, repeats):
    scored = [r for r in results if (r.get("scoring") or {}).get("scored")]
    correct = sum(bool(r["scoring"]["correct"]) for r in scored)
    first = [r for r in scored if r.get("repeat") == 0]
    interval = wilson(sum(bool(r["scoring"]["correct"]) for r in first),len(first))
    interval.update(scope="first repeat of distinct items only",assumption="descriptive binomial approximation; tasks may be correlated and are not a representative intelligence sample")
    categories = []
    for category in sorted({r["category"] for r in results}):
        rows = [r for r in scored if r["category"] == category]
        passed = sum(bool(r["scoring"]["correct"]) for r in rows)
        distinct = [r for r in rows if r.get("repeat") == 0]
        categories.append({"category":category,"scored":len(rows),"correct":passed,
                           "accuracy":passed/len(rows) if rows else None,"percent":100*passed/len(rows) if rows else None,
                           "wilson_95":wilson(sum(bool(r["scoring"]["correct"]) for r in distinct),len(distinct))})
    repeat_scores = []
    for repeat in range(repeats):
        rows = [r for r in scored if r["repeat"] == repeat]
        passed = sum(bool(r["scoring"]["correct"]) for r in rows)
        repeat_scores.append({"repeat":repeat,"scored":len(rows),"correct":passed,"accuracy":passed/len(rows) if rows else None})
    planned = len(selected_ids)*repeats
    return {"planned":planned,"attempts":len(results),"scored":len(scored),"correct":correct,"incorrect":len(scored)-correct,
            "errors":sum(r.get("status") == "error" for r in results),"cancelled":sum(r.get("status") == "cancelled" for r in results),
            "truncated":sum(bool((r.get("scoring") or {}).get("truncated")) for r in results),
            "accuracy":correct/len(scored) if scored else None,"percent":100*correct/len(scored) if scored else None,
            "penalized_accuracy":correct/planned if planned else None,"attempt_success_rate":len(scored)/planned if planned else None,
            "unique_items":len(selected_ids),"unique_scored_items":len({r["item_id"] for r in scored}),
            "wilson_95":interval,"category_scores":categories,"repeat_scores":repeat_scores,
            "repeat_interpretation":"Repeated responses are dependent observations of the same items, not additional independent questions."}


def _now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _path(run_id):
    if not isinstance(run_id,str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}",run_id): raise ValueError("Invalid capability run ID.")
    return os.path.join(RUNS_DIR,run_id+".json")


def _save(run):
    os.makedirs(RUNS_DIR,exist_ok=True)
    destination = _path(run["id"])
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",dir=RUNS_DIR,prefix=".capability-",suffix=".tmp",delete=False) as fh:
            name = fh.name
            json.dump(run,fh,ensure_ascii=False,indent=1,allow_nan=False)
        os.replace(name,destination)
    finally:
        if name:
            try: os.unlink(name)
            except FileNotFoundError: pass


def _public_run(run):
    for row in run["results"]:
        if row.get("status")=="ok":_validate_completed_observation(row,(run.get("protocol") or {}).get("completion_policy"))
    clean = public_projection(run)
    identity.verify_model_configuration(run["model_configuration"],require_endpoint=True)
    registered=register_suite(run["suite"],register=False)
    if run.get("suite_digest")!=registered["digest"]: raise ValueError("Stored capability suite digest mismatch.")
    clean["model_configuration"] = copy.deepcopy(run["model_configuration"])
    # These are audited public task materials; changing a JSON answer's `message`
    # key or a task's public URL would invalidate its suite hash and score audit.
    clean["suite"] = copy.deepcopy(run["suite"])
    for original,projected in zip(run["results"],clean["results"]):
        if original.get("response") != projected.get("response"):
            projected["audit_response_redacted"] = True
        elif original.get("scoring"):
            projected["scoring"] = copy.deepcopy(original["scoring"])
    return clean


def _validate_config(config):
    if not isinstance(config,dict): raise ValueError("Capability configuration must be an object.")
    cfg = dict(DEFAULT_CONFIG)
    allowed = set(DEFAULT_CONFIG) | {"model","models","endpoint_base_url","endpoint_api_key_env","reproducibility","reported_mtp","reported_reasoning","requested_mtp","model_source","canonical_model_id","label","notes","gpu_index","power_interval_ms","other_idle_w","cpu_tdp_w","endpoint_identity","endpoint_name_override","storage_probe_directory","storage_probe_directories"}
    cfg.update({key:value for key,value in config.items() if key in allowed})
    if "models" in cfg and (not isinstance(cfg["models"],list) or any(not isinstance(m,str) for m in cfg["models"])): raise ValueError("Models must be a list of identifiers.")
    cfg["model"] = cfg.get("model") or next(iter(cfg.get("models") or []),None)
    if cfg["provider"] == "lmstudio":
        cfg["endpoint_base_url"] = endpoints.normalize_base(cfg.get("endpoint_base_url") or lmstudio.DEFAULT_BASE)
        endpoints.api_key(cfg)
    elif cfg["provider"] == "openai":
        endpoints.validate(cfg)
    else: raise ValueError("Unsupported provider.")
    for key, minimum, maximum in (("repeats",1,20),("max_tokens",1,131072),("timeout_s",0,86400),("seed",0,2147483627),("sample_size",0,10000)):
        if type(cfg[key]) is not int or not minimum <= cfg[key] <= maximum: raise ValueError("%s must be an integer in %d..%d." % (key,minimum,maximum))
    for key, minimum, maximum in (("temperature",0,2),("top_p",0.00001,1)):
        if cfg[key] is None: continue
        if type(cfg[key]) not in (int,float) or not math.isfinite(cfg[key]) or not minimum <= cfg[key] <= maximum: raise ValueError("Invalid sampling %s." % key)
    if type(cfg["send_seed"]) is not bool: raise ValueError("send_seed must be true or false.")
    if cfg["token_limit_field"] not in ("max_tokens","max_completion_tokens"): raise ValueError("Invalid token limit field.")
    if cfg.get("endpoint_reasoning_effort") not in (None,"none","minimal","low","medium","high","xhigh"): raise ValueError("Invalid reasoning effort.")
    for key,minimum,maximum in (("gpu_index",0,100),("power_interval_ms",50,10000)):
        if key in cfg and (type(cfg[key]) is not int or not minimum<=cfg[key]<=maximum): raise ValueError("Invalid telemetry setting "+key)
    if type(cfg.get("endpoint_inference_local")) is not bool: raise ValueError("Local inference assertion must be true or false.")
    endpoints.resolve_identity(cfg)
    identity.build_model_configuration(cfg)
    suite_manifest(cfg["suite_id"])
    return cfg


class CapabilityRunner:
    def __init__(self,on_run_start=None,on_item=None,on_run_finish=None):
        self.thread = None
        self.run = None
        self.live = {}
        self.events = []
        self._seq = 0
        self._lock = threading.RLock()
        self._start_lock = threading.Lock()
        self.cancel_event = threading.Event()
        self._responses = set()
        self._response_lock = threading.Lock()
        self._pending_worker = None
        self.on_run_start,self.on_item,self.on_run_finish = on_run_start,on_item,on_run_finish
        self._telemetry_handle = None

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive() or self._pending_worker and self._pending_worker.is_alive())

    def _emit(self,kind,**payload):
        with self._lock:
            self.events.append(dict(kind=kind,seq=self._seq,t=time.time(),**payload))
            self._seq += 1
            if len(self.events)>4000: del self.events[:1000]

    def events_since(self,cursor):
        with self._lock: return [copy.deepcopy(e) for e in self.events if e["seq"]>=cursor],self._seq

    @staticmethod
    def _summary(run):
        return {key:copy.deepcopy(run.get(key)) for key in ("id","benchmark_kind","created_at","finished_at","status","configuration_id","model_configuration","suite_id","suite_version","suite_digest","evaluation_key","protocol","summary","label")}

    def status(self):
        with self._lock:
            live = copy.deepcopy(self.live)
            if self._telemetry_handle and hasattr(self._telemetry_handle,"now"):
                live["telemetry"] = self._telemetry_handle.now()
            if self._pending_worker and self._pending_worker.is_alive() and self.run and self.run.get("status") != "running":
                live["phase"] = "waiting for request worker; server cancellation unverified"
            return {"running":self.running,"run_id":(self.run or {}).get("id"),"live":live,
                    "run":self._summary(self.run) if self.run else None,"cancelling":self.cancel_event.is_set() and self.running}

    def start(self,config):
        with self._start_lock:
            if self.running: raise RuntimeError("A capability benchmark is already running.")
            if self._pending_worker and self._pending_worker.is_alive(): raise RuntimeError("A previous request worker is still active; check the inference endpoint before continuing.")
            cfg = _validate_config(config)
            suite = suite_manifest(cfg["suite_id"])
            tasks = sorted(suite["tasks"],key=lambda t:t["id"])
            selected=select_item_ids([t["id"] for t in tasks],cfg["sample_size"],cfg["seed"])
            task_map={task["id"]:task for task in tasks}
            tasks=[task_map[item_id] for item_id in selected]
            model_configuration = identity.build_model_configuration(cfg)
            protocol = {"version":PROTOCOL_VERSION,"scoring_version":SCORING_VERSION,"suite_digest":suite["digest"],
                        "completion_policy":COMPLETION_POLICY,
                        "sample_size_requested":cfg["sample_size"],"selection_algorithm":SELECTION_ALGORITHM,
                        "selected_item_ids":selected,"selected_item_digest":identity.canonical_digest(selected),
                        "sampling":{"temperature":cfg["temperature"],"top_p":cfg["top_p"],"seed":cfg["seed"],
                                    "seed_scope":"deterministic item selection", "send_seed":cfg["send_seed"],
                                    "requested_seed":cfg["seed"] if cfg["send_seed"] else None,
                                    "seed_behavior":"request seed+repeat; engine determinism not verified" if cfg["send_seed"] else "selection seed only; API seed omitted"},
                        "max_tokens":cfg["max_tokens"],"token_limit_field":cfg["token_limit_field"],"timeout_s":cfg["timeout_s"],"reasoning_effort_requested":cfg["endpoint_reasoning_effort"],
                        "budget_scope":"requested total generation, including reasoning and visible answer",
                        "time_policy":"no client wall-clock deadline" if cfg["timeout_s"]==0 else "explicit client wall-clock deadline",
                        "repeats":cfg["repeats"],"request_isolation":"one fresh chat per task and repeat; externally managed endpoint; no model loading/unloading",
                        "output_limit_chars":200000,"truncation_policy":"finish_reason length is scored incorrect; transport errors are unscored and counted separately"}
            evaluation = {key:value for key,value in protocol.items() if key != "repeats"}
            run = {"benchmark_kind":"capability","schema_version":1,"id":"cap-%s-%s" % (datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S"),uuid.uuid4().hex[:10]),
                   "created_at":_now(),"status":"running","label":str(cfg.get("label") or cfg["endpoint_label"]),
                   "application":{"name":"Wattever","version":__version__},"config":endpoints.public_config(cfg),
                   "model_configuration":model_configuration,"configuration_id":model_configuration["configuration_id"],
                   "suite_id":suite["id"],"suite_version":suite["version"],"suite_digest":suite["digest"],"suite":suite,
                   "protocol":protocol,"evaluation_key":identity.canonical_digest(evaluation),"results":[],
                   "interpretation":"Rule-scored capability task accuracy, not IQ. Public tasks may be contaminated; scores are not equivalent to official leaderboard harnesses."}
            run["summary"] = aggregate([],selected,cfg["repeats"])
            _save(run)
            with self._lock:
                self.run,self.events,self._seq = run,[],0
                self.live = {"phase":"starting","done":0,"total":len(tasks)*cfg["repeats"]}
            self.cancel_event.clear()
            self.thread = threading.Thread(target=self._run,args=(run,cfg,tasks),daemon=True)
            self.thread.start()
            return run["id"]

    def cancel(self):
        with self._start_lock:
            self._cancel_current()

    def cancel_if_current(self, expected_run_id):
        """Use the start mutex so a stale client can never cancel a new run."""
        if not isinstance(expected_run_id, str) or not expected_run_id.strip():
            raise ValueError("expected_run_id must be a non-empty string.")
        with self._start_lock:
            if (self.run or {}).get("id") != expected_run_id:
                return False
            self._cancel_current()
            return True

    def _cancel_current(self):
        self.cancel_event.set()
        with self._response_lock: responses = list(self._responses)
        for response in responses: threading.Thread(target=response.close,daemon=True).start()

    def _request(self,cfg,task,repeat):
        state = {}
        def register(response):
            with self._response_lock: self._responses.add(response)
            if self.cancel_event.is_set(): response.close()
        def worker():
            try:
                state["value"] = lmstudio.measure_completion(cfg["model"],task["prompt"],cfg["max_tokens"],
                    base=cfg["endpoint_base_url"],api_base=True,api_key=endpoints.api_key(cfg),provider_name="capability endpoint",
                    timeout=cfg["timeout_s"] or None,cancel=self.cancel_event,on_response=register,
                    temperature=cfg["temperature"],top_p=cfg["top_p"],seed=cfg["seed"]+repeat if cfg["send_seed"] else None,
                    token_limit_field=cfg["token_limit_field"],
                    reasoning_effort=cfg["endpoint_reasoning_effort"],capture_output=True,output_limit_chars=200000)
            except Exception as exc: state["error"] = exc
        thread = threading.Thread(target=worker,daemon=True)
        self._pending_worker = thread
        thread.start()
        deadline = time.monotonic()+cfg["timeout_s"] if cfg["timeout_s"] else None
        while thread.is_alive() and not self.cancel_event.is_set() and (deadline is None or time.monotonic()<deadline):
            thread.join(timeout=.05 if deadline is None else min(.05,max(0,deadline-time.monotonic())))
        if thread.is_alive():
            self.cancel()  # Stop the whole evaluation; server cancellation cannot be verified.
            if deadline is not None and time.monotonic()>=deadline: raise TimeoutError("Request exceeded its wall-clock deadline; endpoint cancellation is unverified and no later tasks were sent.")
            raise lmstudio.Cancelled()
        with self._response_lock: self._responses.clear()
        if self.cancel_event.is_set(): raise lmstudio.Cancelled()
        if "error" in state: raise state["error"]
        return state["value"]

    def _run(self,run,cfg,tasks):
        handle = None
        failed_deadline = False
        try:
            if self.on_run_start: handle = self.on_run_start(run,cfg)
            self._telemetry_handle = handle
            for repeat in range(cfg["repeats"]):
                for task in tasks:
                    if self.cancel_event.is_set(): break
                    self.live.update(phase="request",item_id=task["id"],category=task["category"],repeat=repeat)
                    self._emit("item",**self.live)
                    row = {"item_id":task["id"],"category":task["category"],"repeat":repeat,
                           "requested_seed":cfg["seed"]+repeat if cfg["send_seed"] else None,"started_at":_now()}
                    started = time.monotonic()
                    try:
                        value = self._request(cfg,task,repeat)
                        output = value["output_text"]
                        row.update(status="ok",response=output,finish_reason=value.get("finish_reason"),
                                   usage={key:value.get(key) for key in ("prompt_tokens","completion_tokens","reasoning_tokens","cached_prompt_tokens","completion_tokens_estimated")},
                                   timing={key:value.get(key) for key in ("ttft_s","wall_s","decode_s","decode_token_count","timing_scope","metric_sources","reported_timings")},
                                   observed_output=value.get("output_capture"),timing_scope=value.get("timing_scope"),
                                   answer_observation="visible_answer" if output.strip() else "missing_visible_answer",
                                   response_model=identity.public_request_model(value["response_model"])["value"] if value.get("response_model") else None,
                                   response_model_source="provider_reported response alias; weights not independently verified")
                        _validate_completed_observation(row,run["protocol"].get("completion_policy"))
                        row["scoring"]=_score_completion(task,output,value.get("finish_reason"),run["protocol"].get("completion_policy"))
                    except lmstudio.Cancelled:
                        row.update(status="cancelled",scoring={"scored":False,"reason":"cancelled"})
                    except Exception as exc:
                        message = endpoints.sanitize_error(exc,cfg)
                        row.update(status="error",scoring={"scored":False,"reason":"request_error"},
                                   diagnostic=public_projection(message),error_type=type(exc).__name__)
                        if isinstance(exc,TimeoutError) or "timed out" in str(exc).lower():
                            failed_deadline = True
                            self.cancel()
                    row["elapsed_s"] = time.monotonic()-started
                    if self.on_item: self.on_item(run,row,handle)
                    with self._lock:
                        run["results"].append(row)
                        run["summary"] = aggregate(run["results"],run["protocol"]["selected_item_ids"],cfg["repeats"])
                        self.live["done"] = len(run["results"])
                    _save(run)
                    self._emit("result",result=copy.deepcopy(row),summary=copy.deepcopy(run["summary"]),done=self.live["done"],total=self.live["total"])
                if self.cancel_event.is_set(): break
            run["status"] = "error" if failed_deadline else "cancelled" if self.cancel_event.is_set() else "completed"
        except Exception as exc:
            run.update(status="error",diagnostic=public_projection(endpoints.sanitize_error(exc,cfg)))
        finally:
            run["finished_at"] = _now()
            if self.on_run_finish:
                try: self.on_run_finish(run,handle)
                except Exception as exc: run["telemetry_diagnostic"] = public_projection(str(exc))
            run["summary"] = aggregate(run["results"],run["protocol"]["selected_item_ids"],cfg["repeats"])
            _save(run)
            self.live["phase"] = "idle"
            self._telemetry_handle = None
            self._emit("run_done",run_id=run["id"],status=run["status"],summary=copy.deepcopy(run["summary"]))

    def load(self,run_id):
        try:
            with open(_path(run_id),encoding="utf-8") as fh: run = json.load(fh)
        except FileNotFoundError: return None
        if run.get("status") == "running" and not (self.running and (self.run or {}).get("id") == run_id):
            run["status"] = "interrupted"
        return run

    def list_runs(self,full=False):
        if not os.path.isdir(RUNS_DIR): return []
        rows = []
        for filename in os.listdir(RUNS_DIR):
            if not filename.endswith(".json"): continue
            try:
                run = self.load(filename[:-5])
                if run and run.get("benchmark_kind") == "capability": rows.append(run if full else self._summary(run))
            except (OSError,ValueError,AttributeError): continue
        return sorted(rows,key=lambda r:r.get("created_at", ""),reverse=True)

    def export(self,run_id):
        run = self.load(run_id)
        if not run: raise ValueError("No such capability run.")
        return _public_run(run)

    def import_run(self,payload):
        runs = payload.get("capability_runs") if isinstance(payload,dict) and "capability_runs" in payload else [payload]
        if not isinstance(runs,list) or not 1<=len(runs)<=100: raise ValueError("Import 1..100 capability runs.")
        validated = [validate_import(run) for run in runs]
        for run in validated:
            if self.running and (self.run or {}).get("id") == run["id"]: raise ValueError("Cannot overwrite an active capability run.")
            prior = self.load(run["id"])
            if prior and identity.canonical_digest(prior) != identity.canonical_digest(run):
                raise ValueError("A different capability run already uses this ID.")
        for run in validated: _save(run)
        return [run["id"] for run in validated]


def validate_import(value):
    """Check fingerprints and re-score public audit answers before importing."""
    if not isinstance(value,dict) or value.get("benchmark_kind") != "capability" or value.get("schema_version") != 1:
        raise ValueError("Expected a versioned capability run.")
    run = copy.deepcopy(value)
    identity.canonical_digest(run)  # Reject non-finite numbers before any import writes.
    _path(run.get("id"))
    if run.get("status") not in ("completed","cancelled","error","interrupted"): raise ValueError("Import a finished capability run.")
    try: datetime.datetime.fromisoformat(run["created_at"].replace("Z","+00:00"))
    except (KeyError,ValueError,AttributeError): raise ValueError("Invalid capability timestamp.")
    identity.verify_model_configuration(run.get("model_configuration"),require_endpoint=True)
    if run.get("configuration_id") != run["model_configuration"]["configuration_id"]: raise ValueError("Configuration ID mismatch.")
    manifest = run.get("suite")
    if not isinstance(manifest,dict): raise ValueError("Full public suite manifest is required for import.")
    plain = {k:v for k,v in manifest.items() if k!="digest"}
    digest = identity.canonical_digest(plain)
    if manifest.get("digest") != digest or run.get("suite_digest") != digest: raise ValueError("Capability suite digest mismatch.")
    if run.get("suite_id") != manifest.get("id") or run.get("suite_version") != manifest.get("version"): raise ValueError("Suite identity mismatch.")
    # Validate without registering an untrusted imported suite globally.
    register_suite(manifest,register=False)
    protocol = run.get("protocol") or {}
    if protocol.get("version") != PROTOCOL_VERSION or protocol.get("scoring_version") != SCORING_VERSION or protocol.get("suite_digest") != digest:
        raise ValueError("Unsupported or inconsistent capability protocol.")
    if "completion_policy" in protocol and protocol["completion_policy"]!=COMPLETION_POLICY:raise ValueError("Unsupported capability completion policy.")
    if run.get("config") is not None and not isinstance(run["config"],dict):raise ValueError("Saved capability configuration must be an object.")
    for key,minimum,maximum in (("max_tokens",1,131072),("timeout_s",0,86400)):
        if type(protocol.get(key)) is not int or not minimum<=protocol[key]<=maximum: raise ValueError("Invalid capability protocol limit.")
        configured=(run.get("config") or {}).get(key)
        if configured is not None and (type(configured) is not int or not minimum<=configured<=maximum):raise ValueError("Invalid saved capability configuration limit.")
    if protocol.get("token_limit_field") not in ("max_tokens","max_completion_tokens"): raise ValueError("Invalid capability token limit field.")
    sampling=protocol.get("sampling")
    if not isinstance(sampling,dict): raise ValueError("Sampling protocol must be explicit.")
    for key,minimum,maximum in (("temperature",0,2),("top_p",.00001,1)):
        val=sampling.get(key)
        if val is None:continue
        if type(val) not in (int,float) or not math.isfinite(val) or not minimum<=val<=maximum:raise ValueError("Invalid capability sampling protocol.")
    if type(sampling.get("send_seed")) is not bool:raise ValueError("API seed request policy must be explicit.")
    if sampling.get("requested_seed")!=(sampling.get("seed") if sampling["send_seed"] else None):raise ValueError("Sampling seed request metadata mismatch.")
    effort=protocol.get("reasoning_effort_requested")
    if effort not in (None,"none","minimal","low","medium","high","xhigh"): raise ValueError("Invalid requested reasoning effort.")
    if run["model_configuration"]["reasoning"]["requested"]!=(effort if effort is not None else "omitted"): raise ValueError("Reasoning request differs between protocol and model identity.")
    evaluation = {key:item for key,item in protocol.items() if key!="repeats"}
    if run.get("evaluation_key") != identity.canonical_digest(evaluation): raise ValueError("Evaluation protocol fingerprint mismatch.")
    selected = protocol.get("selected_item_ids")
    tasks = {task["id"]:task for task in manifest["tasks"]}
    if not isinstance(selected,list) or not selected or len(set(selected))!=len(selected) or any(k not in tasks for k in selected): raise ValueError("Invalid selected task IDs.")
    if protocol.get("selected_item_digest") != identity.canonical_digest(selected): raise ValueError("Selected item digest mismatch.")
    sample_size = protocol.get("sample_size_requested")
    seed = (protocol.get("sampling") or {}).get("seed")
    if type(sample_size) is not int or not 0<=sample_size<=10000 or type(seed) is not int or not 0<=seed<=2147483627: raise ValueError("Invalid deterministic subset configuration.")
    deterministic=select_item_ids(tasks,sample_size,seed,protocol.get("selection_algorithm"))
    if selected!=deterministic: raise ValueError("Selected tasks do not match the declared deterministic subset.")
    repeats = protocol.get("repeats")
    if type(repeats) is not int or not 1<=repeats<=20: raise ValueError("Invalid repeat count.")
    results = run.get("results")
    if not isinstance(results,list) or len(results)>len(selected)*repeats: raise ValueError("Invalid capability results.")
    if run["status"]=="completed" and len(results)!=len(selected)*repeats: raise ValueError("A completed run must contain all planned observations.")
    seen = set()
    for row in results:
        if not isinstance(row,dict) or row.get("item_id") not in selected or type(row.get("repeat")) is not int or not 0<=row["repeat"]<repeats: raise ValueError("Invalid item observation.")
        key = (row["item_id"],row["repeat"])
        if key in seen: raise ValueError("Duplicate item observation.")
        seen.add(key)
        task = tasks[row["item_id"]]
        if row.get("category") != task["category"]: raise ValueError("Item category mismatch.")
        if row.get("requested_seed")!=(seed+row["repeat"] if sampling["send_seed"] else None):raise ValueError("Item sampling seed mismatch.")
        if row.get("status") == "ok":
            if not isinstance(row.get("response"),str) or len(row["response"])>200000:raise ValueError("Capability answer exceeds the bounded audit size.")
            if row.get("audit_response_redacted"): raise ValueError("Redacted answers cannot be independently rescored; import the reviewed full audit file.")
            _validate_completed_observation(row,protocol.get("completion_policy"))
            scoring = _score_completion(task,row.get("response"),row.get("finish_reason"),protocol.get("completion_policy"))
            if row.get("scoring") != scoring: raise ValueError("Recorded score does not match the audited answer.")
        elif row.get("status") not in ("error","cancelled") or (row.get("scoring") or {}).get("scored"):
            raise ValueError("Request failures must be unscored.")
    run["summary"] = aggregate(results,selected,repeats)
    run["config"] = endpoints.public_config(run.get("config") or {})
    return _public_run(run)


def export_run(run):
    """Validate and project a finished run for public publishers and exporters."""
    return validate_import(run)
