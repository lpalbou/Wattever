"""Dependency-free contribution checks for versioned benchmark files."""
import datetime
import math
import re
import statistics


def validate_transfer_bandwidth(profile):
    errors = []
    if not isinstance(profile, dict):
        return ["transfer_bandwidth must be an object"]
    if profile.get("protocol") != "wattever-transfer-v1" or profile.get("schema_version") != 1:
        errors.append("transfer_bandwidth has an unsupported protocol")
    if profile.get("unit") != "bytes_per_second" or profile.get("scope") != "benchmark_client":
        errors.append("transfer_bandwidth needs explicit byte units and client scope")
    try:
        datetime.datetime.fromisoformat(profile.get("captured_at", "").replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        errors.append("transfer_bandwidth.captured_at must be an ISO timestamp")
    for key in ("storage_to_ram", "ram_to_vram"):
        value = profile.get(key)
        if not isinstance(value, dict) or value.get("status") not in ("ok", "unavailable"):
            errors.append("transfer_bandwidth.%s needs a supported status" % key)
            continue
        if value["status"] == "unavailable":
            if not isinstance(value.get("reason"), str) or not value["reason"] or "bytes_per_second" in value:
                errors.append("Unavailable transfers need a reason and no measured rate")
            continue
        samples = value.get("samples")
        def positive(n):
            return type(n) in (int, float) and math.isfinite(n) and n > 0
        if not isinstance(samples, list) or not samples or len(samples) > 100 or any(
                not isinstance(s, dict) or not positive(s.get("bytes")) or
                not positive(s.get("elapsed_s")) for s in samples):
            errors.append("Successful transfers need finite positive completed-byte timing samples")
        elif not positive(value.get("bytes_per_second")) or not math.isclose(value["bytes_per_second"],
                statistics.median(s["bytes"] / s["elapsed_s"] for s in samples), rel_tol=1e-6):
            errors.append("Transfer rate must match the median of its samples")
        if not isinstance(value.get("method"), str) or value.get("statistic") != "median":
            errors.append("Successful transfers need an explicit method and median statistic")
    return errors


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
        if hw.get("transfer_bandwidth") is not None:
            errors.extend(validate_transfer_bandwidth(hw["transfer_bandwidth"]))
        for session in run.get("sessions", []):
            if isinstance(session, dict) and session.get("transfer_bandwidth") is not None:
                errors.extend(validate_transfer_bandwidth(session["transfer_bandwidth"]))
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
    # Public scientific annotations remain explicitly operator reported. Validate
    # them without resolving endpoints or inspecting credentials on import.
    for path, block in (("config", run["config"]), ("run", run)):
        claims = block.get("performance_claims", [])
        if not isinstance(claims, list) or len(claims) > 20:
            errors.append(path + ".performance_claims must be an array of at most 20 claims")
            continue
        for index, claim in enumerate(claims):
            prefix = "%s.performance_claims[%d]" % (path, index)
            if not isinstance(claim, dict):
                errors.append(prefix + " must be an object")
                continue
            precision = claim.get("precision")
            if precision not in ("FP4", "INT4", "FP8", "INT8", "FP16", "BF16", "FP32"):
                errors.append(prefix + ".precision is unsupported")
            unit = "TOPS" if precision in ("INT4", "INT8") else "TFLOP/s"
            if claim.get("unit") != unit:
                errors.append(prefix + ".unit must be " + unit)
            value = claim.get("value")
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                errors.append(prefix + ".value must be finite and non-negative")
            if claim.get("kind") not in ("advertised_peak", "measured", "estimate"):
                errors.append(prefix + ".kind is invalid")
            if claim.get("sparsity") not in ("dense", "sparse", "unknown"):
                errors.append(prefix + ".sparsity is invalid")
            for key in ("source", "execution_path"):
                value = claim.get(key)
                if not isinstance(value, str) or not value.strip() or len(value) > 300:
                    errors.append(prefix + "." + key + " must describe the source or execution path (1..300 characters)")
            if claim.get("provenance") != "user_reported":
                errors.append(prefix + ".provenance must be user_reported")
    reported = run.get("reproducibility")
    if reported is not None and (not isinstance(reported, dict) or reported.get("source") != "user_reported" or not isinstance(reported.get("settings"), dict)):
        errors.append("reproducibility must include user_reported source and settings object")
    if "model_configurations" in run:
        from .identity import verify_model_configuration
        configurations = run["model_configurations"]
        if not isinstance(configurations, list):
            errors.append("model_configurations must be an array")
        else:
            for configuration in configurations:
                try: verify_model_configuration(configuration, require_endpoint=False)
                except (ValueError, TypeError, KeyError) as exc: errors.append("model_configurations: " + str(exc))
    for index, row in enumerate(run["results"]):
        prefix = "results[%d]" % index
        if not isinstance(row, dict):
            errors.append(prefix + " must be an object")
            continue
        if "model_configuration" in row:
            from .identity import verify_model_configuration
            try:
                verify_model_configuration(row["model_configuration"], require_endpoint=False)
                if row.get("configuration_id") != row["model_configuration"]["configuration_id"]:
                    errors.append(prefix + ".configuration_id must match its public model configuration")
            except (ValueError, TypeError, KeyError) as exc:
                errors.append(prefix + ".model_configuration: " + str(exc))
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
