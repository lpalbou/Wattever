"""Controlled llama.cpp dual-GPU screening, separate from Wattever run aggregates.

Default invocation prints a plan and does not start a process. Actual measurements
require --run, completion of the preceding 180-case cohort/report, idle-engine
checks, existing model files and psutil. No Strata lifecycle operations exist here.
Run with the flash-next venv Python on this machine (it already has psutil).
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PROTOCOL = "standalone-communication-screen-v1"
INVESTIGATION = ROOT / "untracked/communication-investigation"
DEFAULT_EXE = INVESTIGATION / "llama-b12058/llama-server.exe"
PRIOR = ROOT / "untracked/dual-gpu-5960-experiment"
FLASH = ROOT.parent / "flash-next"


@dataclass(frozen=True)
class Model:
    name: str
    quant: str
    architecture: str
    relative_path: str
    inventory_file: str

    def path(self):
        return Path.home() / ".lmstudio/models" / self.relative_path


MODELS = {
    "lfm25": Model("LFM2.5-24B-A2B", "QAD-Q4_0", "lfm2moe",
        "LiquidAI/LFM2.5-24B-A2B-GGUF/LFM2.5-24B-A2B-QAD-Q4_0.gguf", "lfm25-gguf.json"),
    "qwen27": Model("Qwen3.8-27B", "UD-Q3_K_XL", "qwen35",
        "unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-UD-Q3_K_XL.gguf", "qwen27q3-gguf.json"),
    "qwen35": Model("Qwen3.6-35B-A3B", "Q4_K_M", "qwen35moe",
        "lmstudio-community/Qwen3.6-35B-A3B-GGUF/Qwen3.6-35B-A3B-Q4_K_M.gguf", "qwen35q4-gguf.json"),
    "gemmae2b": Model("Gemma-4-E2B-it", "Q4_K_M", "gemma4",
        "lmstudio-community/gemma-4-E2B-it-GGUF/gemma-4-E2B-it-Q4_K_M.gguf", "gemmae2b-gguf.json"),
}


@dataclass(frozen=True)
class Strategy:
    physical_indices: tuple
    split: str
    fractions: str | None = None
    independent: bool = False


STRATEGIES = {
    "single5070": Strategy((0,), "none"),
    "single5060": Strategy((1,), "none"),
    "layer_equal": Strategy((0, 1), "layer", "1,1"),
    "layer_favor5070": Strategy((0, 1), "layer", "2,1"),
    "tensor_equal": Strategy((0, 1), "tensor", "1,1"),
    "independent": Strategy((0, 1), "none", independent=True),
    "gemma_kv_local": Strategy((1, 0), "layer", "1,2"),
}


def utc():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def source_fingerprints(profile=False, root=ROOT):
    """Fingerprint actual source, independently of operator batching controls."""
    files = ["tools/standalone_screen.py", "tools/diagnostic_telemetry.py",
             "wattever/lmstudio.py", "wattever/power.py"]
    if profile:
        files.append("tools/nsight_capture.py")
    return {relative: hashlib.sha256((Path(root) / relative).read_bytes()).hexdigest()
            for relative in files}


def validate_resume_manifest(existing, current):
    fields = ("plan", "executable_sha256", "gpu_mapping", "model_files", "cuda_overrides",
              "host_hardware", "source_sha256")
    if any(existing.get(field) != current.get(field) for field in fields):
        raise RuntimeError("Resume condition/tool/source mismatch; use a new diagnostic directory.")


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def context_per_slot(target, output):
    # Explicit and identical for each strategy, including independent instances.
    return math.ceil((target * 1.05 + output + 512) / 256) * 256


def make_cases(args):
    cases = []
    for model in args.models:
        for strategy in args.strategies:
            if strategy == "gemma_kv_local" and model != "gemmae2b":
                continue
            for target in args.sizes:
                for clients in args.clients:
                    case = dict(model=model, strategy=strategy, target_tokens=target,
                        clients=clients, measured_repeats=1, max_tokens=args.max_tokens,
                        context_per_slot=context_per_slot(target, args.max_tokens),
                        kv_type_k="f16", kv_type_v="f16", flash_attention="on",
                        reasoning="none", mtp="none", temperature=0.0, top_p=1.0,
                        seed=args.seed, cache_prompt=False, context_shift=False,
                        kv_layout="unified", conversation_cache_ram_mib=0,
                        cpu_threads=args.threads, cpu_threads_batch=args.threads_batch,
                        server_logging="verbose; same level across strategies; diagnostic logging overhead possible",
                        endpoint_transport="explicit IPv4 loopback", capture_kind="profiling" if args.profile else "screening",
                        batch_size=args.batch_size, ubatch_size=args.ubatch_size)
                    case["case_id"] = digest(case)[:16]
                    cases.append(case)
    return cases


def server_command(exe, case, port, physical_indices):
    strategy = STRATEGIES[case["strategy"]]
    np = 1 if strategy.independent else case["clients"]
    tensor = strategy.split == "tensor"
    command = [str(exe), "--model", str(MODELS[case["model"]].path()),
        "--host", "127.0.0.1", "--port", str(port), "--alias", case["case_id"],
        "--ctx-size", str(case["context_per_slot"] * np), "--parallel", str(np),
        "--kv-unified-per-slot", str(case["context_per_slot"]),
        "--kv-unified", "--cache-ram", "0", "--no-cache-idle-slots",
        "--device", ",".join("CUDA" + str(i) for i in range(len(physical_indices))),
        "--main-gpu", "0", "--split-mode", strategy.split,
        "--gpu-layers", "all" if tensor else "auto", "--fit", "off" if tensor else "on",
        "--fit-target", "1500", "--flash-attn", "on", "--cache-type-k", "f16",
        "--cache-type-v", "f16", "--spec-type", "none", "--reasoning", "off",
        "--no-cache-prompt", "--no-context-shift", "--slots", "--offline",
        "--batch-size", str(case["batch_size"]), "--ubatch-size", str(case["ubatch_size"]),
        "--threads", str(case["cpu_threads"]), "--threads-batch", str(case["cpu_threads_batch"]),
        "--log-colors", "off", "--log-timestamps", "--verbose", "--perf"]
    if strategy.fractions:
        command += ["--tensor-split", strategy.fractions]
    return command


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def http_json(base, route, payload=None, timeout=3):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(base + route, data=data,
        headers={"Content-Type": "application/json"})
    # Loopback control must not accidentally traverse a configured HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.load(response)


def validate_prior_completion(directory=PRIOR):
    progress = json.loads((directory / "progress.json").read_text(encoding="utf-8-sig"))
    final = json.loads((directory / "finalization.json").read_text(encoding="utf-8-sig"))
    stages = progress.get("stages", [])
    if not stages or any(s.get("status") != "completed" for s in stages):
        raise RuntimeError("Prior LM Studio + 36 Strata cases are not all completed; screening gate remains closed.")
    if final.get("partial", True) or final.get("cases", 0) < progress.get("planned_cases", 180):
        raise RuntimeError("Prior cohort report/finalization is still partial; screening gate remains closed.")
    report = ROOT / "docs/5070-plus-5060-comparison.md"
    if not report.is_file():
        raise RuntimeError("Prior comparison report is missing.")
    return {"progress_sha256": digest(progress), "finalization": final,
            "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest()}


def validate_strata_observation(state):
    if state.get("uncertain") is not False or state.get("state") not in ("Stopped", "Server ready"):
        raise RuntimeError("Strata engine state is active or unknown; no GPU work permitted.")
    if state.get("model") or any(p.get("role") != "gateway" for p in state.get("processes", [])):
        raise RuntimeError("A Strata model/engine remains present; stop it in Strata Control first.")
    return state


def verify_idle():
    # Read-only observer; this does not instantiate Controller or call lifecycle APIs.
    observer = ("import json;from strata_desktop import observe;s=observe();"
        "print(json.dumps({k:s.get(k) for k in ('state','uncertain','model','processes')}))")
    result = subprocess.run([str(FLASH / ".venv/Scripts/python.exe"), "-c", observer],
        cwd=FLASH, capture_output=True, text=True, timeout=15, check=True)
    state = validate_strata_observation(json.loads(result.stdout))
    # A missing/stale management check is unknown, never evidence of no model.
    native = http_json("http://127.0.0.1:1234", "/api/v1/models", timeout=5)
    entries = native.get("models", native.get("data"))
    if not isinstance(entries, list):
        raise RuntimeError("LM Studio management response is unknown; cannot establish idle state.")
    if any(m.get("loaded_instances") or m.get("state") == "loaded" for m in entries):
        raise RuntimeError("LM Studio still has loaded models; preserve them and postpone screening.")
    return {"checked_at": utc(), "strata": state, "lmstudio_model_count": len(entries)}


def verify_gpu_mapping():
    result = subprocess.run(["nvidia-smi", "--query-gpu=index,name,pci.bus_id,memory.total,driver_version",
        "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=15, check=True)
    rows = []
    for row in csv.reader(result.stdout.splitlines()):
        if len(row) != 5:
            raise RuntimeError("Unknown NVIDIA inventory format.")
        index, name, bus, memory, driver = [s.strip() for s in row]
        rows.append(dict(physical_sensor_index=int(index), name=name, pci_bus_id=bus,
                         memory_mib=float(memory), driver_version=driver))
    names = {r["physical_sensor_index"]:r["name"] for r in rows}
    if "5070 Ti" not in names.get(0, "") or "5060 Ti" not in names.get(1, ""):
        raise RuntimeError("Physical NVIDIA indices changed; revise and review the GPU mapping before running.")
    # PCI_BUS_ID ordering must agree with NVIDIA index order for these recipes.
    buses = [r["pci_bus_id"] for r in sorted(rows, key=lambda r:r["physical_sensor_index"])]
    if buses != sorted(buses):
        raise RuntimeError("NVIDIA index and CUDA PCI_BUS_ID order differ; recipes need an explicit remap.")
    return rows


def verify_no_standalone_engines(psutil_module):
    # A prior interrupted harness may leave a listener outside LM/Strata's own
    # catalogs. Do not load another model on top of it or terminate it by name.
    names = {"llama-server.exe", "llama-server", "llama-cli.exe", "llama-cli",
             "llama-bench.exe", "llama-bench", "llama-perplexity.exe", "llama-perplexity"}
    occupied = []
    for process in psutil_module.process_iter(attrs=["pid", "name"]):
        try:
            if (process.info.get("name") or "").lower() in names:
                occupied.append({"pid": process.pid, "name": process.info.get("name")})
        except psutil_module.NoSuchProcess:
            pass
    if occupied:
        raise RuntimeError("An existing standalone engine/benchmark is present; preserve it and postpone: %s" % occupied)
    return {"standalone_engine_names_checked": sorted(names), "existing_matches": []}


class OwnedServer:
    """Owns a Popen handle, not an arbitrary listener/PID selected for termination."""
    def __init__(self, command, physical_indices, directory, psutil_module):
        self.command = list(command)
        self.indices = tuple(physical_indices)
        self.directory = Path(directory)
        self.psutil = psutil_module
        self.process = None
        self.log_handle = None
        self.born = None
        self.log_path = self.directory / "server.log"
        self.port = int(self.command[self.command.index("--port") + 1])
        self.base = "http://127.0.0.1:%d" % self.port

    @property
    def engine_pid(self):
        return self.process.pid if self.process else None

    def start(self):
        # Race is handled by readiness verifying listener ownership, not by killing
        # whoever won the port. Never reuse an already occupied port.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", self.port))
        self.directory.mkdir(parents=True, exist_ok=True)
        self.log_handle = self.log_path.open("wb")
        env = {k: v for k, v in os.environ.items() if not k.startswith("LLAMA_ARG_")}
        env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
        env["CUDA_VISIBLE_DEVICES"] = ",".join(map(str, self.indices))
        self.process = subprocess.Popen(self.command, stdout=self.log_handle,
            stderr=subprocess.STDOUT, env=env, cwd=ROOT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.born = self.psutil.Process(self.process.pid).create_time()
        write_json(self.directory / "launch-private.json", dict(command=self.command,
            pid=self.process.pid, born=self.born, cuda_device_order="PCI_BUS_ID",
            visible_to_physical=dict(enumerate(self.indices)),
            inherited_cuda_overrides={k:v for k,v in os.environ.items() if k.startswith("GGML_CUDA_")},
            endpoint=self.base, started_at=utc()))
        return self

    def owned(self):
        if self.process is None or self.process.poll() is not None:
            return False
        try:
            return abs(self.psutil.Process(self.process.pid).create_time() - self.born) < .001
        except self.psutil.NoSuchProcess:
            return False

    def ready(self, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.owned():
                raise RuntimeError("Owned llama-server exited during startup; inspect server.log.")
            listeners = [c for c in self.psutil.net_connections(kind="tcp4")
                         if c.status == "LISTEN" and c.laddr.port == self.port]
            if any(c.pid != self.process.pid for c in listeners):
                raise RuntimeError("Port now belongs to another process; it will be preserved.")
            if listeners:
                try:
                    health = http_json(self.base, "/health", timeout=1)
                    if health.get("status") == "ok":
                        return
                except (OSError, ValueError):
                    pass
            time.sleep(.25)
        raise TimeoutError("Owned llama-server did not become ready in the startup budget.")

    def stop(self):
        status = {"pid": self.process.pid if self.process else None,
                  "owned_process_handle_only": True, "exit_confirmed": True}
        if self.process is not None and self.process.poll() is None:
            if not self.owned():
                raise RuntimeError("PID birth identity changed; refusing to terminate an unverified process.")
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                # Same Popen handle, birth identity rechecked; no tree/port/name kill.
                if not self.owned():
                    raise RuntimeError("Lost owned process identity during cleanup.")
                self.process.kill()
                self.process.wait(timeout=8)
        if self.process is not None:
            status["returncode"] = self.process.poll()
            status["exit_confirmed"] = self.process.poll() is not None
        if self.log_handle:
            self.log_handle.close()
            self.log_handle = None
        return status


class ProfiledServer(OwnedServer):
    """A separate profiled condition, with verified launcher-child ownership.

    Nsight injects at application start; collection begins after warmup. A
    listener is never accepted simply because it answers on the chosen port.
    """
    def __init__(self, command, physical_indices, directory, psutil_module, nsys_exe):
        from tools.nsight_capture import NsightCapture
        super().__init__(command, physical_indices, directory, psutil_module)
        self.model_command = list(command)
        self.capture = NsightCapture(nsys_exe=nsys_exe,
            output_dir=self.directory / "nsight", label=self.directory.name, execute=True)
        self.engine = None
        self.engine_born = None
        self.collection_started = False

    @property
    def engine_pid(self):
        return self.engine.pid if self.engine else None

    def start(self):
        # Version query only: fail before starting a model or CUDA process.
        self.capture._check_version()
        self.command = self.capture.launch_command(self.model_command)
        super().start()
        self.capture.adopt_launcher(self.process)
        return self

    def _matching_owned_children(self):
        if not self.owned():
            raise RuntimeError("Nsight launcher exited before child ownership could be established.")
        expected_exe = os.path.normcase(os.path.realpath(self.model_command[0]))
        alias = self.model_command[self.model_command.index("--alias") + 1]
        matches = []
        for child in self.psutil.Process(self.process.pid).children(recursive=True):
            try:
                actual_exe = os.path.normcase(os.path.realpath(child.exe()))
                argv = child.cmdline()
                if actual_exe == expected_exe and "--alias" in argv and argv[argv.index("--alias") + 1] == alias:
                    if child.create_time() < self.born - .01:
                        raise RuntimeError("Candidate target predates its owned launcher.")
                    matches.append(child)
            except self.psutil.NoSuchProcess:
                pass
        if len(matches) > 1:
            raise RuntimeError("Multiple matching Nsight targets; refuse ambiguous ownership.")
        return matches

    def _engine_owned(self):
        if not self.engine:
            return False
        try:
            return (self.engine.is_running() and
                    abs(self.psutil.Process(self.engine.pid).create_time() - self.engine_born) < .001)
        except self.psutil.NoSuchProcess:
            return False

    def ready(self, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            candidates = self._matching_owned_children()
            if candidates and self.engine is None:
                self.engine = candidates[0]
                self.engine_born = self.engine.create_time()
            listeners = [c for c in self.psutil.net_connections(kind="tcp4")
                         if c.status == "LISTEN" and c.laddr.port == self.port]
            if listeners:
                if not self._engine_owned() or any(c.pid != self.engine.pid for c in listeners):
                    raise RuntimeError("Profile port listener is not the verified owned model descendant.")
                try:
                    if http_json(self.base, "/health", timeout=1).get("status") == "ok":
                        return
                except (OSError, ValueError):
                    pass
            time.sleep(.25)
        raise TimeoutError("Profiled engine startup timeout; preserving trace and owned process evidence.")

    def begin_collection(self):
        self.capture.start("prefill-decode")
        self.collection_started = True

    def end_collection(self):
        if self.collection_started:
            self.capture.stop()
            self.collection_started = False

    def stop(self):
        errors = []
        target_exit = False
        try:
            self.end_collection()
        except Exception as exc:
            errors.append({"phase": "stop trace", "error": repr(exc)})
        try:
            if self.engine is None and self.owned():
                candidates = self._matching_owned_children()
                if candidates:
                    self.engine = candidates[0]
                    self.engine_born = self.engine.create_time()
            if self.engine is not None:
                if self._engine_owned():
                    # psutil's Process identity checks and our birth check guard
                    # PID reuse. The descendant proof was retained at startup.
                    self.engine.terminate()
                    try:
                        self.engine.wait(timeout=8)
                    except self.psutil.TimeoutExpired:
                        if not self._engine_owned():
                            raise RuntimeError("Profile target identity changed during cleanup.")
                        self.engine.kill()
                        self.engine.wait(timeout=8)
                target_exit = not self._engine_owned()
            else:
                # An unobserved launch may race target creation. Preserve that
                # uncertainty instead of claiming an unseen child was stopped.
                target_exit = self.process is None
        except Exception as exc:
            errors.append({"phase": "stop target", "error": repr(exc)})
        try:
            self.capture.close()  # Uniquely named session; shutdown --kill=false.
        except Exception as exc:
            errors.append({"phase": "close session", "error": repr(exc)})
        launcher = super().stop()
        return dict(launcher, model_pid=self.engine_pid, model_exit_confirmed=target_exit,
            exit_confirmed=launcher["exit_confirmed"] and target_exit,
            profiling=True, profile_cleanup_errors=errors)


def validate_slots(slots, case, independent=False):
    expected_count = 1 if independent else case["clients"]
    if not isinstance(slots, list) or len(slots) != expected_count:
        raise RuntimeError("Actual parallel slot count differs from the requested condition.")
    capacities = [s.get("n_ctx") for s in slots]
    if any(n != case["context_per_slot"] for n in capacities):
        raise RuntimeError("Actual per-slot context differs; no silent context adjustment is allowed: %s" % capacities)
    return capacities


def placement_evidence(log, physical_indices=()):
    """Retain raw placement lines. Unseen assignments remain unknown."""
    relevant = [line for line in log.splitlines() if re.search(
        r"offload|layer.*(?:device|CUDA|CPU)|(?:KV|compute|model).*buffer|n_ctx|flash.attn|cache.*type|shared|tensor.split", line, re.I)]
    offload = re.findall(r"offloaded\s+(\d+)/(\d+)\s+layers", log)
    layers = {}
    for match in re.finditer(r"layer\s+(\d+)\s+assigned to device\s+([^\s,]+)", log, re.I):
        layers[int(match[1])] = match[2]
    last = offload[-1] if offload else None
    local = all(i in layers for i in range(13, 35)) and len({layers[i] for i in range(13, 35)}) == 1
    device = layers[13] if local else None
    visible_index = re.fullmatch(r"CUDA(\d+)", device or "")
    physical = None
    if visible_index and int(visible_index[1]) < len(physical_indices):
        physical = physical_indices[int(visible_index[1])]
    return {"raw_lines": relevant, "offloaded_layers": int(last[0]) if last else None,
        "total_offloadable_layers": int(last[1]) if last else None,
        "cpu_offload": (int(last[0]) < int(last[1])) if last else None,
        "layer_device_assignments": layers,
        "gemma_weight_layers_13_to_34_colocated": local,
        "gemma_weight_colocation_device": device, "gemma_weight_colocation_physical_gpu": physical,
        "gemma_weight_colocation_on_5070": physical == 0 if local and physical is not None else None,
        "gemma_kv_locality_verified": None,
        "gemma_kv_note": "Weight-layer assignments alone do not prove actual shared KV source/allocation placement; inspect KV logs/trace separately.",
        "placement_note": "Raw startup evidence; absent tensor/KV assignments are unknown, never inferred from split fractions."}


def backend_timing_evidence(log):
    lines = [line for line in log.splitlines() if re.search(r"(?:prompt eval time|eval time|total time|print_timings)", line)]
    native = []
    for line in lines:
        match = re.search(r"(prompt eval|eval|total) time\s*=\s*([\d.]+)\s*ms.*?/\s*(\d+)\s*(?:tokens|runs).*?([\d.]+)\s*tokens per second", line)
        if match:
            native.append({"phase": match[1], "elapsed_ms": float(match[2]),
                           "count": int(match[3]), "tokens_per_second": float(match[4])})
    return {"raw_lines": lines, "parsed": native,
        "clock_alignment": "Server durations only; no retrospective subtraction from client TTFT.",
        "request_assignment": "Unassigned unless server slot/task identifiers make matching unambiguous."}


def canonical_prompt(server, model, target, client, seed, cache_dir, max_tokens, n_ctx):
    from wattever import corpus
    key = dict(model=model, target=target, client=client, seed=seed, corpus_sha256=
        hashlib.sha256(Path(corpus.MASTER).read_bytes()).hexdigest())
    cache = cache_dir / (digest(key) + ".json")
    if cache.exists():
        result = json.loads(cache.read_text(encoding="utf-8"))
        if result["key"] != key:
            raise RuntimeError("Canonical prompt cache identity mismatch.")
        return result
    ratio = corpus.DEFAULT_CHARS_PER_TOKEN
    nonce = digest(key)[:12]
    for _ in range(5):
        prompt, meta = corpus.build_prompt(target, chars_per_token=ratio, nonce=nonce)
        # This CPU tokenizer check is outside measured inference; chat template
        # overhead is reported separately through actual API usage.
        tokenized = http_json(server.base, "/tokenize", {"content": prompt, "add_special": False}, timeout=30)
        count = len(tokenized["tokens"])
        if count < 1:
            raise RuntimeError("Tokenizer returned an empty prompt.")
        if abs(count-target) / target <= .015:
            break
        ratio *= target / count
    if count + max_tokens + 128 > n_ctx:
        raise RuntimeError("Canonical prompt cannot fit fixed context; refusing to shrink it.")
    result = {"key": key, "prompt": prompt, "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "corpus": meta, "raw_prompt_tokens": count, "target_relative_error": (count-target)/target,
        "tokenization_method": "llama-server /tokenize add_special=false; API usage includes chat template"}
    write_json(cache, result)
    return result


def group_statistics(results, start, end, power):
    """Energy boundary covers the group ONCE, including overlapping requests."""
    requests_ok = len(results) > 0 and all(r.get("status") == "ok" for r in results)
    usage_exact = requests_ok and all(isinstance(r.get("completion_tokens"), int) and
        not r.get("completion_tokens_estimated") for r in results)
    total = sum(r.get("completion_tokens", 0) or 0 for r in results) if usage_exact else None
    monitored = {g.get("index") for g in power.get("gpus", [])}
    all_cards = power.get("inventory_complete") is True and {0, 1}.issubset(monitored)
    energy = power.get("gpu_energy_j") if power.get("gpu_power_complete") and all_cards else None
    return dict(t_start=start, t_end=end, wall_s=end-start, request_count=len(results),
        all_requests_completed=requests_ok, completion_tokens=total,
        aggregate_output_tps=total/(end-start) if total and end>start else None,
        gpu_energy_j=energy, gpu_energy_per_output_token_including_prefill_j=
            energy/total if energy is not None and total else None,
        all_card_energy_available=energy is not None,
        energy_accounting="One all-card PowerSampler window over the union group; never sum request windows.",
        power=power)


def measure_group(servers, prompts, case, sampler, telemetry, timeout):
    from wattever.lmstudio import measure_completion
    count = case["clients"]
    barrier = threading.Barrier(count + 1)
    cancel = threading.Event()
    responses = []
    lock = threading.Lock()
    def response_opened(response):
        with lock:
            responses.append(response)
    def run(client):
        server = servers[client % len(servers)]
        barrier.wait()
        try:
            result = measure_completion(case["case_id"], prompts[client]["prompt"], case["max_tokens"],
                base=server.base + "/v1", api_base=True, timeout=timeout, cancel=cancel,
                temperature=0.0, top_p=1.0, seed=case["seed"], reasoning_effort="none",
                provider_name="standalone llama.cpp", capture_output=True, on_response=response_opened)
            result.update(status="ok", client=client, server=servers.index(server))
            return result
        except Exception as exc:
            return {"status": "request_error", "client": client, "server": servers.index(server),
                    "error": repr(exc), "t_end": time.perf_counter()}
    offsets = [s.log_path.stat().st_size for s in servers]
    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(run, i) for i in range(count)]
        start = time.perf_counter()
        barrier.wait()
        try:
            results = [f.result(timeout=timeout + 20) for f in futures]
        except BaseException:
            cancel.set()
            with lock:
                for response in responses:
                    response.close()
            raise
        finally:
            end = time.perf_counter()
    # Allow a final telemetry observation beyond the end boundary, not another
    # inference repeat. PowerSampler itself reports coverage/confidence.
    time.sleep(.25)
    power = sampler.window(start, end)
    group = group_statistics(results, start, end, power)
    group["telemetry"] = telemetry.window(start, end) if telemetry else {"available": False}
    # Phase windows are disjoint for one request. For concurrent requests they
    # overlap and are deliberately not presented as an additive energy budget.
    if count == 1 and results[0].get("t_first_token"):
        first = results[0]["t_first_token"]
        group["single_request_phases"] = {
            "prefill": sampler.window(results[0]["t_start"], first),
            "decode": sampler.window(first, results[0]["t_end"])}
        decode_window = group["single_request_phases"]["decode"]
        tokens = results[0].get("decode_token_count")
        if (tokens and decode_window.get("gpu_power_complete") and
            decode_window.get("inventory_complete") is True):
            energy = decode_window.get("gpu_energy_j")
            group["single_request_decode_gpu_energy_per_token_j"] = energy / tokens if energy is not None else None
        if telemetry:
            group["single_request_phase_telemetry"] = {
                "prefill": telemetry.window(results[0]["t_start"], first),
                "decode": telemetry.window(first, results[0]["t_end"])}
    timings = []
    for s, offset in zip(servers, offsets):
        with s.log_path.open("rb") as handle:
            handle.seek(offset)
            timings.append(backend_timing_evidence(handle.read().decode("utf-8", "replace")))
    return {"requests": results, "group": group, "backend_timings": timings}


def classify_failure(message):
    if re.search(r"not supported|unsupported|not implemented|requires.*(?:tensor|flash|KV)", message, re.I):
        return "unsupported_configuration"
    if re.search(r"out of memory|failed to allocate|not enough memory", message, re.I):
        return "allocation_failed"
    return "failed"


def validate_request_evidence(request, case):
    prompt = request.get("prompt_tokens")
    output = request.get("completion_tokens")
    prompt_valid = type(prompt) is int and prompt > 0
    output_valid = type(output) is int and output > 0 and not request.get("completion_tokens_estimated", True)
    cached = request.get("cached_prompt_tokens")
    reported = request.get("reported_timings")
    caches = [cached] if type(cached) is int else []
    if isinstance(reported, dict) and type(reported.get("cache_n")) is int:
        caches.append(reported["cache_n"])
    contaminated = any(c > 0 for c in caches)
    overflow = prompt_valid and prompt + case["max_tokens"] > case["context_per_slot"]
    return {"actual_prompt_count_verified": prompt_valid, "actual_output_count_verified": output_valid,
        "fixed_context_overflow": bool(overflow), "cache_counter_values": caches,
        "positive_cache_reuse_observed": contaminated,
        "cold_prefill_counter_verified": not contaminated if caches else None,
        "prefill_comparison_eligibility": "excluded_cache_reuse" if contaminated else
            "excluded_missing_prompt_count" if not prompt_valid else "excluded_context_overflow" if overflow else
            "verified_zero_cache_counter" if caches else "conditional_on_owned_cache_disable; cache_counter_unavailable",
        "decode_comparison_eligible": request.get("status") == "ok" and output_valid and not overflow,
        "cache_configuration": "Owned engine --no-cache-prompt --cache-ram0 --no-cache-idle-slots; fresh process per condition."}


def validate_saved_result(result, case):
    if result.get("condition") != case:
        raise RuntimeError("Saved condition identity mismatch; refusing resume.")
    if result.get("status") in (None, "starting", "running"):
        raise RuntimeError("Saved result is not a terminal condition; inspect ownership before resume.")
    if any(not cleanup.get("exit_confirmed") for cleanup in result.get("cleanup", [])):
        raise RuntimeError("Saved result contains an unconfirmed owned process exit; halt resume.")
    if result.get("measurement_attempts", 0):
        expected = 2 if STRATEGIES[case["strategy"]].independent else 1
        if len(result.get("cleanup", [])) != expected:
            raise RuntimeError("Saved measured condition lacks complete owned-process cleanup evidence.")
    if result.get("capture_cleanup_errors"):
        raise RuntimeError("Saved result has unresolved capture cleanup errors; halt resume.")
    return result


def run_case(case, args, output, psutil_module):
    from wattever.power import PowerSampler
    from wattever.lmstudio import measure_completion
    from tools.diagnostic_telemetry import DiagnosticTelemetry
    directory = output / "cases" / case["case_id"]
    directory.mkdir(parents=True, exist_ok=True)
    result = dict(protocol=PROTOCOL, condition=case, capture_kind=case["capture_kind"], started_at=utc(),
        model=asdict(MODELS[case["model"]]), status="starting", measured_repeats=0, measurement_attempts=0,
        prior_comparison_note="Legacy LM/Strata KV precision, native MTP and reasoning may differ; never pool these conditions.")
    result["idle_checks"] = verify_idle()
    result["idle_checks"]["standalone"] = verify_no_standalone_engines(psutil_module)
    strategy = STRATEGIES[case["strategy"]]
    groups = [(i,) for i in strategy.physical_indices] if strategy.independent else [strategy.physical_indices]
    servers = []
    sampler = None
    telemetry = None
    interrupted = False
    try:
        for i, indices in enumerate(groups):
            command = server_command(args.exe, case, free_port(), indices)
            server = (ProfiledServer(command, indices, directory / ("server%d" % i), psutil_module, args.nsys)
                      if args.profile else
                      OwnedServer(command, indices, directory / ("server%d" % i), psutil_module))
            servers.append(server)
            server.start()
            server.ready(args.startup_timeout)
            props = http_json(server.base, "/props")
            slots = http_json(server.base, "/slots")
            validate_slots(slots, case, strategy.independent)
            write_json(server.directory / "actual-server-private.json", {"props": props, "slots": slots})
        result["actual_context_verified"] = True
        result["placement"] = [placement_evidence(s.log_path.read_text(encoding="utf-8", errors="replace"), s.indices) for s in servers]
        result["visible_to_physical"] = [dict(enumerate(s.indices)) for s in servers]
        prompts = [canonical_prompt(servers[i % len(servers)], case["model"], case["target_tokens"],
            i, case["seed"], output / "canonical-prompts-private", case["max_tokens"], case["context_per_slot"])
            for i in range(case["clients"])]
        result["prompts"] = [{k:v for k,v in p.items() if k != "prompt"} for p in prompts]
        # One separately marked tiny warmup per owned engine, not a measured repeat.
        result["warmups"] = []
        for server in servers:
            warmup = measure_completion(case["case_id"], "Reply with the word ready.", 8,
                base=server.base + "/v1", api_base=True, timeout=120, temperature=0.0,
                reasoning_effort="none", seed=case["seed"], top_p=1.0, capture_output=True)
            result["warmups"].append({"capture_kind": "warmup", "tokens": warmup.get("completion_tokens"),
                "wall_s": warmup.get("wall_s"), "excluded_from_measurement": True})
        sampler = PowerSampler(interval_ms=200)
        telemetry = DiagnosticTelemetry(pids=[s.engine_pid for s in servers], interval_ms=200)
        sampler.start()
        telemetry.start()
        time.sleep(.6)
        if args.profile:
            result["profiling"] = {"overhead": "Nsight injection/collection overhead present; never include in normal inference aggregates.",
                "trace_window": "Collection starts after warmup and ends after the measured group plus final telemetry sample.",
                "kernel_activity": "CUDA/NVTX; CPU stack sampling/context switches disabled by helper."}
            for server in servers:
                server.begin_collection()
        result["measurement_attempts"] = 1
        result.update(measure_group(servers, prompts, case, sampler, telemetry, args.timeout))
        result["measured_repeats"] = 1
        if args.profile:
            for server in reversed(servers):
                server.end_collection()
        result["status"] = "ok" if result["group"]["all_requests_completed"] else "request_error"
        for request in result["requests"]:
            evidence = validate_request_evidence(request, case)
            request["measurement_validity"] = evidence
            if evidence["fixed_context_overflow"]:
                result["status"] = "context_invalid"
            elif evidence["positive_cache_reuse_observed"]:
                result["status"] = "cache_contaminated"
            elif request.get("status") == "ok" and not (evidence["actual_prompt_count_verified"] and evidence["actual_output_count_verified"]):
                result["status"] = "usage_incomplete"
        result["scope"] = sampler.scope_metadata()
    except KeyboardInterrupt:
        interrupted = True
        result.update(status="interrupted", error="Operator interrupted; partial capture retained.")
    except Exception as exc:
        logs = "\n".join(s.log_path.read_text(encoding="utf-8", errors="replace") for s in servers if s.log_path.exists())
        result.update(status=classify_failure(str(exc) + logs[-12000:]), error=repr(exc))
    finally:
        result["capture_cleanup_errors"] = []
        if telemetry:
            try:
                telemetry.stop()
                write_json(directory / "diagnostic-telemetry.json", {"history": telemetry.history(), "summary": telemetry.summary()})
            except Exception as exc:
                result["capture_cleanup_errors"].append({"resource": "diagnostic telemetry", "error": repr(exc)})
        if sampler:
            try:
                sampler.stop()
                write_json(directory / "power-raw.json", {"history": sampler.history(), "summary": sampler.summary()})
            except Exception as exc:
                result["capture_cleanup_errors"].append({"resource": "power sampler", "error": repr(exc)})
        cleanup = []
        for server in reversed(servers):
            try:
                cleanup.append(server.stop())
            except Exception as exc:
                cleanup.append({"pid": server.process.pid if server.process else None,
                                "exit_confirmed": False, "error": repr(exc)})
        result["cleanup"] = cleanup
        if args.profile:
            result["trace_exports"] = []
            for server in servers:
                try:
                    result["trace_exports"].append(server.capture.export_and_summarize())
                except Exception as exc:
                    result["trace_exports"].append({"available": False, "error": repr(exc)})
        result["finished_at"] = utc()
        write_json(directory / "result-private.json", result)
    if any(not s.get("exit_confirmed") for s in result["cleanup"]):
        raise RuntimeError("Owned server exit was not verified; halt the matrix and investigate.")
    if result["capture_cleanup_errors"]:
        raise RuntimeError("Capture cleanup failed; owned model servers were stopped but the matrix must halt.")
    if interrupted:
        raise KeyboardInterrupt
    return result


def publish(output, results):
    from wattever.privacy import public_projection
    # Explicit new diagnostic tree; nothing is written to existing Wattever runs.
    capture_kind = results[0]["capture_kind"] if results else "screening"
    manifest_path = output / "manifest-private.json"
    runtime = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        runtime = {k:manifest.get(k) for k in ("software_version", "actual_version", "executable_sha256", "source_sha256", "gpu_mapping",
            "model_files", "host_hardware", "loopback_http_proxy_bypassed", "cuda_overrides")}
    public = public_projection({"protocol": PROTOCOL, "capture_kind": capture_kind, "runtime": runtime,
        "normal_benchmark_aggregate": False, "conditions": results})
    write_json(output / "screening-public.json", public)
    columns = ["case_id", "model", "model_name", "quant", "reasoning", "mtp", "kv_type_k", "kv_type_v",
        "strategy", "target_tokens", "clients", "context_per_slot", "capture_kind", "measured_repeats",
        "status", "wall_s", "completion_tokens", "aggregate_output_tps", "gpu_energy_j",
        "gpu_energy_per_output_token_including_prefill_j", "single_request_decode_gpu_energy_per_token_j",
        "request_metrics_json", "backend_timings_json", "placement_json", "per_card_json"]
    with (output / "screening-public.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for result in public["conditions"]:
            condition, group = result["condition"], result.get("group", {})
            row = {k:condition.get(k) for k in columns if k in condition}
            row.update({k:group.get(k) for k in columns if k in group})
            row.update(status=result["status"], model_name=result.get("model", {}).get("name"),
                quant=result.get("model", {}).get("quant"), measured_repeats=result.get("measured_repeats"),
                request_metrics_json=json.dumps(result.get("requests", [])),
                backend_timings_json=json.dumps(result.get("backend_timings", [])),
                placement_json=json.dumps(result.get("placement", [])),
                per_card_json=json.dumps(group.get("power", {}).get("gpus", [])))
            writer.writerow(row)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="Print plan only (default).")
    mode.add_argument("--run", action="store_true", help="Explicitly execute after prior-cohort and idle checks pass.")
    p.add_argument("--models", nargs="+", choices=MODELS, default=list(MODELS))
    p.add_argument("--strategies", nargs="+", choices=STRATEGIES, default=list(STRATEGIES))
    p.add_argument("--sizes", nargs="+", type=int, default=[512, 20000, 80000])
    p.add_argument("--clients", nargs="+", type=int, choices=[1, 2], default=[1, 2])
    p.add_argument("--max-tokens", type=int, default=256)
    p.add_argument("--seed", type=int, default=73129)
    p.add_argument("--batch-size", type=int, default=2048)
    p.add_argument("--ubatch-size", type=int, default=512)
    p.add_argument("--threads", type=int, default=8)
    p.add_argument("--threads-batch", type=int, default=8)
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--startup-timeout", type=int, default=300)
    p.add_argument("--profile", action="store_true", help="Separate Nsight diagnostic conditions; excluded from normal aggregates.")
    p.add_argument("--nsys", type=Path, help="Portable x64 nsys.exe, required for --run --profile.")
    p.add_argument("--exe", type=Path, default=DEFAULT_EXE)
    p.add_argument("--output", type=Path, help="New diagnostic directory; --resume skips every existing terminal condition.")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--stop-after", type=int, help="Execute at most N NEW conditions, preserving the full plan for --resume.")
    return p


def execute_case_plan(cases, args, output, psutil_module):
    """Run only new conditions; stop-after never changes the saved full plan."""
    # Load every saved terminal condition before executing any new work. This
    # preserves exports even if a diagnostic directory contains non-prefix cases.
    saved_results = {}
    for case in cases:
        saved = output / "cases" / case["case_id"] / "result-private.json"
        if saved.exists():
            saved_results[case["case_id"]] = validate_saved_result(
                json.loads(saved.read_text(encoding="utf-8")), case)
    new_conditions = 0
    for number, case in enumerate(cases, 1):
        if case["case_id"] in saved_results:
            continue
        if args.stop_after is not None and new_conditions >= args.stop_after:
            break
        print("[%d/%d] %s %s %d tokens %d clients" % (number, len(cases), case["model"], case["strategy"], case["target_tokens"], case["clients"]), flush=True)
        result = run_case(case, args, output, psutil_module)
        saved_results[case["case_id"]] = result
        new_conditions += 1
        results = [saved_results[c["case_id"]] for c in cases if c["case_id"] in saved_results]
        publish(output, results)
        write_json(output / "progress.json", {"updated_at": utc(), "completed_conditions": len(results),
            "planned_conditions": len(cases), "last_status": result["status"], "repeats": 1,
            "new_conditions_this_invocation": new_conditions,
            "operator_stop_after": args.stop_after,
            "status": "completed" if len(results) == len(cases) else "partial"})
    if args.stop_after is not None and new_conditions >= args.stop_after and len(saved_results) < len(cases):
        print("Stopped after %d new conditions; full plan retained for --resume." % new_conditions, flush=True)
    return new_conditions


def main(argv=None):
    args = parser().parse_args(argv)
    if any(n < 1 for n in args.sizes) or args.max_tokens < 2 or args.timeout < 1:
        raise SystemExit("Positive prompt sizes/timeout and max-tokens >=2 required.")
    if args.stop_after is not None and args.stop_after < 1:
        raise SystemExit("--stop-after must be a positive integer.")
    cases = make_cases(args)
    plan = {"protocol": PROTOCOL, "capture_kind": "profiling" if args.profile else "screening", "case_count": len(cases),
        "measured_repeats_per_condition": 1, "tensor_note": "Experimental architecture/KV restrictions may reject; never auto-fallback.",
        "gemma_note": "Reverse devices and 1:2 are a hypothesis; actual layers13/14 and shared tail placement must be verified.",
        "independent_note": "Two model copies loaded. One-client screen uses 5070 with 5060 idle; two clients run one per GPU.",
        "comparability": "All new strategies use F16 KV, reasoning off, MTP off, same weights, canonical prompts and slot context. Legacy conditions stay separate.",
        "historical_data_mutated": False, "cases": cases}
    if not args.run:
        print(json.dumps(plan, indent=2))
        return 0
    prior = validate_prior_completion()
    import psutil  # Read-only identity/listener checks; no CUDA context.
    for model in args.models:
        if not MODELS[model].path().is_file():
            raise SystemExit("Existing GGUF missing: " + str(MODELS[model].path()))
    if not args.exe.is_file():
        raise SystemExit("Pinned llama-server is missing.")
    if args.profile:
        if args.nsys is None:
            matches = list((INVESTIGATION / "nsight-2026.3.2").glob("**/target-windows-x64/nsys.exe"))
            if len(matches) == 1:
                args.nsys = matches[0]
        if args.nsys is None or not args.nsys.is_file():
            raise SystemExit("Specify the prepared portable x64 Nsight CLI with --nsys.")
    verify_idle()
    verify_no_standalone_engines(psutil)
    gpu_mapping = verify_gpu_mapping()
    # This affects only this CLI process, not a service or the user's environment.
    # The reused SSE client uses urllib's default opener and only IPv4 loopback.
    urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))
    output = args.output or INVESTIGATION / (("profiled-screening-" if args.profile else "screening-") + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    model_files = {}
    for name in args.models:
        model = MODELS[name]
        inventory = json.loads((INVESTIGATION / model.inventory_file).read_text(encoding="utf-8-sig"))
        if model.path().stat().st_size != inventory["file_bytes"]:
            raise SystemExit("GGUF size no longer matches inspected inventory: " + name)
        with model.path().open("rb") as handle:
            header_sha = hashlib.sha256(handle.read(inventory["header_bytes"])).hexdigest()
        if header_sha != inventory["header_sha256"]:
            raise SystemExit("GGUF header/tensor-directory identity changed: " + name)
        model_files[name] = {k: inventory.get(k) for k in
            ("file_name", "file_bytes", "header_bytes", "header_sha256", "full_file_sha256")}
    cpu_name = platform.processor()
    if sys.platform == "win32":
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as reg:
            cpu_name = winreg.QueryValueEx(reg, "ProcessorNameString")[0].strip()
    hardware = dict(cpu_name=cpu_name, logical_cpu_count=os.cpu_count(),
        physical_cpu_count=psutil.cpu_count(logical=False), ram_total_bytes=psutil.virtual_memory().total,
        operating_system=platform.system(), operating_system_release=platform.release(),
        architecture=platform.machine(), python_version=platform.python_version())
    manifest = dict(plan=plan, prior=prior, created_at=utc(), argv=sys.argv, host_hardware=hardware,
        gpu_mapping=gpu_mapping, model_files=model_files, loopback_http_proxy_bypassed=True,
        cuda_overrides={k:v for k,v in os.environ.items() if k.startswith("GGML_CUDA_")},
        source_sha256=source_fingerprints(profile=args.profile),
        idle_check_scope="Known Strata engine state, LM Studio loaded instances and existing llama.cpp process names. Other desktop GPU work is observed in telemetry, not assumed absent.",
        software_version="expected pinned llama.cpp b12058 commit3f8af15a7 CUDA13.4; see actual_version",
        executable_sha256=hashlib.sha256(args.exe.read_bytes()).hexdigest())
    version = subprocess.run([str(args.exe), "--version"], capture_output=True, text=True,
        timeout=30, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    manifest["actual_version"] = {"stdout": version.stdout.strip(), "stderr": version.stderr.strip()}
    if output.exists():
        if not args.resume:
            raise SystemExit("Output exists; choose a new directory or explicitly --resume.")
        existing = json.loads((output / "manifest-private.json").read_text(encoding="utf-8"))
        try:
            validate_resume_manifest(existing, manifest)
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc
    else:
        write_json(output / "manifest-private.json", manifest)
    execute_case_plan(cases, args, output, psutil)
    print(str(output / "screening-public.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
