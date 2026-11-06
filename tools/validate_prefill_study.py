"""Validate published prefill evidence without network, model loading or GPU work."""
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path


def validate(folder):
    folder = Path(folder)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["artifacts"] + manifest.get("figures", []):
        path = (folder / entry["file"]).resolve()
        if not path.is_relative_to(folder.resolve()):
            raise ValueError("Artifact path leaves the study folder")
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("Artifact checksum mismatch: " + entry["file"])
    cases = json.loads(gzip.decompress((folder / "cases.json.gz").read_bytes()))["conditions"]
    prompts = json.loads(gzip.decompress((folder / "prompts.json.gz").read_bytes()))
    by_prompt = {}
    for prompt in prompts:
        checksum = hashlib.sha256(prompt["prompt"].encode()).hexdigest()
        if checksum != prompt["prompt_sha256"]:
            raise ValueError("Canonical prompt checksum mismatch")
        by_prompt[checksum] = prompt
    ids = [case["condition"]["case_id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate condition IDs")
    complete = [case for case in cases if case["status"] == "ok"]
    if len(complete) != manifest["valid_performance_groups"]:
        raise ValueError("Coverage count mismatch")
    close = lambda a,b: math.isclose(a,b,rel_tol=1e-7,abs_tol=1e-6)
    for case in complete:
        condition, summary, requests = case["condition"], case["summary"], case["requests"]
        if len(requests) != condition["clients"] or any(r["status"] != "ok" for r in requests):
            raise ValueError("Incomplete group marked successful")
        if condition["measured_repeats"] != 1:
            raise ValueError("Unexpected repeat count")
        if len(case["prompts"]) != condition["clients"] or sorted(r["client"] for r in requests) != list(range(condition["clients"])):
            raise ValueError("Prompt/client identity coverage differs")
        if [p["key"]["client"] for p in case["prompts"]] != [r["client"] for r in requests]:
            raise ValueError("Prompt ordering differs from request identity")
        if set(case["stream_chunk_arrivals"]) != {p["prompt_sha256"] for p in case["prompts"]}:
            raise ValueError("Actual dispatched prompt checksum differs from canonical metadata")
        total = sum(r["completion_tokens"] for r in requests)
        if any(r.get("completion_tokens_estimated") is not False for r in requests):
            raise ValueError("Estimated output count in a controlled condition")
        if not close(total / summary["group_wall_s"], summary["whole_group_output_tps"]):
            raise ValueError("Group throughput does not match counts and elapsed time")
        if condition["output_stop_policy"] == "ignore_eos_fixed256" and any(r["completion_tokens"] != 256 for r in requests):
            raise ValueError("Fixed-output condition has unmatched counts")
        for request in requests:
            start, first, finish = (request[k] for k in ("start_offset_s", "first_token_offset_s", "finish_offset_s"))
            if not start <= first <= finish or not close(first-start,request["ttft_s"]) or not close(finish-start,request["wall_s"]):
                raise ValueError("Request clock origins are inconsistent")
        for prompt in case["prompts"]:
            if prompt["prompt_sha256"] not in by_prompt:
                raise ValueError("Missing canonical prompt")
        energy = summary["combined_gpu_j"]
        if energy is not None:
            if not close(energy/total,summary["gpu_j_per_output"]):
                raise ValueError("Energy/token count mismatch")
            card_energy = [card.get("gpu_energy_j") for card in case["per_card_power"]]
            if card_energy and all(value is not None for value in card_energy) and not close(sum(card_energy),energy):
                raise ValueError("Combined and per-card energy disagree")
        if case["engine"] == "LM Studio":
            if condition.get("prompt_text_encoding") != "utf-8":
                raise ValueError("LM Studio prompt decoding is not explicit UTF-8")
            fields = {f["key"]:f["value"] for f in case["server_reported_load_config"]}
            for key,value in (("llm.load.numParallelSessions",condition["clients"]),("llm.load.llama.evalBatchSize",condition["batch_size"]),
                              ("llm.load.llama.flashAttention",True),("llm.load.llama.speculativeDecoding.draftMtp",False)):
                if fields.get(key) != value:
                    raise ValueError("LM Studio effective-control mismatch: " + key)
            if fields.get("llm.load.speculativeDecoding.draftMtp") is not False:
                raise ValueError("LM Studio speculative-decoding control is unverified")
            for key in ("llm.load.llama.kCacheQuantizationType","llm.load.llama.vCacheQuantizationType"):
                if fields.get(key) != {"checked":True,"value":condition["kv_type_k"]}:
                    raise ValueError("Unverified cache precision")
    for model,profiles in (("lfm25",["lfm25-layer_equal-5clients","lfm25-independent-5clients"]),
                           ("qwen27",["qwen27-layer_equal-5clients","qwen27-independent-5clients"]),
                           ("qwen27q4",["compiled-greedy","fair512"])):
        pair = [next(c for c in complete if c["condition"]["profile"] == p and c["condition"]["target_tokens"] == 20000) for p in profiles]
        signatures = [[(p["prompt_sha256"],r["prompt_tokens"],r["completion_tokens"]) for p,r in zip(c["prompts"],c["requests"])] for c in pair]
        if signatures[0] != signatures[1]:
            raise ValueError("Paired prompt/output evidence differs: " + model)
    print(f"Validated {len(cases)} captures, {len(complete)} valid groups, {len(prompts)} canonical prompts, clocks, controls and shared GPU energy.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", nargs="?", default="results/studies/20261106-prefill-scheduling")
    validate(parser.parse_args().folder)
