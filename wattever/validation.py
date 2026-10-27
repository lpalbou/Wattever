"""Dependency-free contribution checks for versioned benchmark files."""
import datetime
import math
import re


def validate_run(run):
    """Return actionable errors; legacy v1 files remain readable and shareable."""
    errors = []
    if not isinstance(run, dict):
        return ["Benchmark must be a JSON object, not an export bundle."]
    for key, kind in (("id", str), ("created_at", str), ("config", dict),
                      ("results", list), ("gpu", dict), ("host", dict)):
        if not isinstance(run.get(key), kind):
            errors.append("%s must be %s" % (key, kind.__name__))
    if errors:
        return errors
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", run["id"]):
        errors.append("id must be a safe filename stem (1..200 characters)")
    try:
        datetime.datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
    except ValueError:
        errors.append("created_at must be an ISO 8601 timestamp")
    version = run.get("schema_version", 1)
    if version not in (1, 2):
        errors.append("unsupported schema_version: %r" % version)
    if version == 2:
        def object_field(key):
            value = run.get(key)
            return value if isinstance(value, dict) else {}
        for key in ("application", "hardware", "classification", "measurement_scope", "corpus"):
            if not isinstance(run.get(key), dict):
                errors.append("%s must be an object for schema v2" % key)
        hw = object_field("hardware")
        for key, kind in (("os", dict), ("cpu", dict), ("system", dict), ("gpus", list)):
            if not isinstance(hw.get(key), kind):
                errors.append("hardware.%s must be %s" % (key, kind.__name__))
        required = {
            "application": ("name", "version", "protocol_version"),
            "classification": ("hardware_class", "gpu_names", "platform"),
            "measurement_scope": ("power_gpu_sensor_index", "power_boundary", "runtime_gpu_placement", "runtime_gpu_placement_source"),
            "config": ("models", "sizes", "concurrency_levels"),
        }
        for key, fields in required.items():
            for field in fields:
                if field not in object_field(key):
                    errors.append(key + "." + field + " is required for schema v2")
        for key, fields in (("os", ("name", "release", "architecture")),
                            ("cpu", ("name", "physical_cores", "logical_cores")),
                            ("system", ("manufacturer", "model", "memory_bytes"))):
            block = hw.get(key)
            if isinstance(block, dict):
                for field in fields:
                    if field not in block:
                        errors.append("hardware.%s.%s is required" % (key, field))
        cards = hw.get("gpus")
        for card in cards if isinstance(cards, list) else []:
            if not isinstance(card, dict) or any(k not in card for k in ("name", "vendor", "memory_bytes", "driver", "sensor_index")):
                errors.append("Each hardware.gpus entry must describe name, vendor, memory_bytes, driver and sensor_index")
        digest = object_field("corpus").get("sha256")
        if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
            errors.append("corpus.sha256 must be a 64-character SHA256 hex digest")
        levels = run["config"].get("concurrency_levels")
        if not isinstance(levels, list) or not levels or any(type(n) is not int or not 1 <= n <= 100 for n in levels):
            errors.append("config.concurrency_levels must be a nonempty integer list in 1..100")
        elif len(set(levels)) != len(levels):
            errors.append("config.concurrency_levels must be unique")
    if run.get("status") not in ("running", "completed", "cancelled", "error", "interrupted"):
        errors.append("unknown run status")
    for index, row in enumerate(run["results"]):
        prefix = "results[%d]" % index
        if not isinstance(row, dict):
            errors.append(prefix + " must be an object")
            continue
        if not isinstance(row.get("model"), str) or not row["model"]:
            errors.append(prefix + ".model is required")
        if row.get("status") not in ("ok", "failed", "skipped", "cancelled"):
            errors.append(prefix + ".status is invalid")
        if type(row.get("target_tokens")) is not int or row["target_tokens"] < 1:
            errors.append(prefix + ".target_tokens must be a positive integer")
        if version == 2 and "concurrency" not in row:
            errors.append(prefix + ".concurrency is required for schema v2")
        level = row.get("concurrency", 1)
        if type(level) is not int or not 1 <= level <= 100:
            errors.append(prefix + ".concurrency must be an integer in 1..100")
        elif level > 1 and row.get("status") == "ok":
            requests = row.get("requests")
            if not isinstance(requests, list) or len(requests) != level:
                errors.append(prefix + ".requests must contain one entry per concurrent request")
            if row.get("completed_requests") != level:
                errors.append(prefix + ": successful group must complete every request")
    def finite(value, path):
        if isinstance(value, float) and not math.isfinite(value):
            errors.append(path + " contains a non-finite number")
        elif isinstance(value, dict):
            for key, item in value.items():
                finite(item, path + "." + key)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                finite(item, path + "[%d]" % i)
    finite(run, "run")
    return errors
