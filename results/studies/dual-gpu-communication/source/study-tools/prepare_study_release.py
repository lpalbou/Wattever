"""Prepare a public study/source bundle AFTER all timed work; default is plan.

Copies exact source bytes with SHA-256, retains captured source fingerprints,
and projects explicitly selected JSON/CSV through Wattever's privacy filter.
Never modifies the measured/frozen sources, original artifacts, or Git state.
No model, GPU, service, downloader, archive/executable, raw trace or SQLite work.

Optional --snapshot-source LOGICAL_PATH=ARCHIVED_PATH supplies older source bytes
for a captured fingerprint. Missing captured bytes fail closed unless explicitly
allowed as unavailable. Final bundle is published by a non-overwriting directory
rename, preserving any existing bundle. Inspect it before staging for release.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
INV = ROOT / "untracked/communication-investigation"
DEFAULT_DESTINATION = ROOT / "results/studies/dual-gpu-communication"
PREFIX = "dual-gpu-communication-and-placement-"
GZIP_JSON_THRESHOLD = 20 * 1024**2
GIT_BLOB_LIMIT = 100 * 1024**2
MAX_RAW_JSON = 1024**3
REVIEWED_RAW_NAME = PREFIX + "raw.json.gz"
REVIEWED_RAW_BYTES = 9066738
REVIEWED_PRODUCER_SHA = "ebcdfa1c5b5c70aafe9a89a29f5493e804516255216e00c7ced2d164201f5868"
SOURCES = (
    "tools/standalone_screen.py", "tools/diagnostic_telemetry.py", "tools/gpu_communication_probe.py",
    "tools/nsight_capture.py", "wattever/lmstudio.py", "wattever/power.py",
)
EXTRAS = (
    "untracked/report_communication_investigation.py", "untracked/reanalyze_nsight.py",
    "untracked/run_communication_capture.py", "untracked/host_pin_diagnostic.py",
    "untracked/host_pin_pool_diagnostic.py", "untracked/5960-pny-experiment/gpu_compute_probe.py",
    "untracked/prepare_study_release.py", "untracked/run_sustained_vram_reference.py",
    "untracked/run_fixed_output_screen.py", "untracked/plan_screen_expansion.py",
    "untracked/run_row_screen.py",
    "untracked/run_kv_screen.py",
    "untracked/refine_closeout_evidence.py",
)
PRIVATE_TEXT = re.compile(
    r"(?i)(?:[A-Z]:[\\/]+Users[\\/]+[A-Za-z0-9_. -]+[\\/]|/(?:Users|home)/[A-Za-z0-9_.-]+/|"
    r"GPU-[0-9a-f]{8}-[0-9a-f-]{27,}|luid_0x[0-9a-f]+_0x[0-9a-f]+|"
    r"PCI[\\/]+VEN_[^\\/\s\"']+[\\/]+[^\\/\s\"']+|\b(?:hf_|sk-)[A-Za-z0-9]{20,})"
)
PRIVATE_URL = re.compile(r"https?://(?:[^/\s'\"]+:[^/@\s'\"]+@|(?:10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)\d|[^/\s'\"]+\.(?:local|lan|internal|home|corp)(?:[:/\s'\"]|$))", re.I)
ADDRESS_KEYS = {"pointer", "hostpointer", "devicepointer", "contextpointer", "contextaddress", "memoryaddress",
                "hostaddress", "deviceaddress", "context_handle", "luid", "pnpinstanceid", "deviceinstanceid"}
NORMALIZED_ADDRESS_KEYS = {re.sub(r"[^a-z0-9]", "", key) for key in ADDRESS_KEYS}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_relative(value):
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or not path.parts or any(part in (".", "..") or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", part) for part in path.parts):
        raise ValueError("Unsafe logical/public relative path")
    return str(path)


def local_file(value):
    path = Path(value)
    path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
        raise ValueError("Input must be an existing repository-local file")
    return path


def reject_private(raw, label, *, source_sha=None):
    text = raw.decode("utf-8", "replace")
    if label == "source" and source_sha == REVIEWED_PRODUCER_SHA:
        # Reviewed, intentionally fake parser-test fixture; exact source remains
        # byte-identical. Never exempt this prefix in captured/public data.
        text = re.sub(r"C:[\\/]+Users[\\/]+SUPERSECRET[\\/]+", "<synthetic-source-fixture>/", text)
    if PRIVATE_TEXT.search(text) or PRIVATE_URL.search(text):
        # Do not echo the matched private text or a local absolute path.
        raise ValueError("Recognizable private detail remains in selected " + label)


def deterministic_gzip(raw):
    compressed = bytearray(gzip.compress(raw, compresslevel=9, mtime=0))
    # Python/zlib versions may choose a platform OS header byte. Normalize it to
    # 'unknown' while keeping the zero timestamp and no embedded filename.
    compressed[9] = 255
    return bytes(compressed)


def reviewed_public_json(path, declared_sha):
    original = path.read_bytes()
    producer = ROOT / "untracked/report_communication_investigation.py"
    if (path.name != REVIEWED_RAW_NAME or len(original) != REVIEWED_RAW_BYTES or
        not re.fullmatch(r"[0-9a-f]{64}", declared_sha or "") or sha(original) != declared_sha or
        sha(producer.read_bytes()) != REVIEWED_PRODUCER_SHA):
        raise ValueError("Reviewed-producer receipt does not match the one authorized final artifact/source")
    digest, total, tail = hashlib.sha256(), 0, b""
    forbidden = re.compile(rb"https?://(?:localhost|127\.[0-9.]+|\[::1\])|\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{12,}", re.I)
    with gzip.open(path, "rb") as handle:
        while True:
            block = handle.read(1024**2)
            if not block:
                break  # gzip validates the CRC and decompressed size at EOF.
            digest.update(block)
            total += len(block)
            if total > MAX_RAW_JSON:
                raise ValueError("Reviewed producer JSON exceeds the bounded stream size")
            checked = tail + block
            reject_private(checked, "reviewed producer public JSON")
            if forbidden.search(checked):
                raise ValueError("Reviewed producer JSON contains a recognizable private endpoint or credential")
            tail = checked[-4096:]
    receipt = {"method": "reviewed_public_producer", "artifact_sha256": declared_sha,
               "producer_source_sha256": REVIEWED_PRODUCER_SHA, "compressed_bytes": len(original),
               "uncompressed_sha256": digest.hexdigest(), "uncompressed_bytes": total,
               "gzip_crc_validated": True, "strong_private_pattern_stream_scan": "passed",
               "independent_deep_json_reprojection": False,
               "basis": "Explicit root review of tested153-condition public-projection producer; exact final artifact/source SHA bound"}
    return original, receipt


def scrub_addresses(value):
    if isinstance(value, dict):
        return {key: scrub_addresses(item) for key, item in value.items()
                if re.sub(r"[^a-z0-9]", "", key.lower()) not in NORMALIZED_ADDRESS_KEYS}
    if isinstance(value, list):
        return [scrub_addresses(item) for item in value]
    return value


def project_json(raw):
    from wattever.privacy import public_projection
    def no_nonfinite(value):
        raise ValueError("Non-finite JSON constant is not publishable")
    original = json.loads(raw.decode("utf-8-sig"), parse_constant=no_nonfinite)
    projected = scrub_addresses(public_projection(original))
    return (json.dumps(projected, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")


def project_csv(raw, *, trace_ordinals=False):
    from wattever.privacy import public_projection
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
    if reader.fieldnames is None or any(key is None for key in reader.fieldnames):
        raise ValueError("CSV must have named columns")
    aliases = {"deviceId": "trace_device_id", "srcDeviceId": "trace_source_device_id", "dstDeviceId": "trace_destination_device_id"} if trace_ordinals else {}
    header = scrub_addresses(public_projection({aliases.get(key, key): "" for key in reader.fieldnames}))
    names = list(header)
    rows = []
    old_limit = csv.field_size_limit()
    try:
        csv.field_size_limit(max(old_limit, len(raw)))
        source_rows = list(reader)
    finally:
        csv.field_size_limit(old_limit)
    for source in source_rows:
        if None in source:
            raise ValueError("CSV row width differs from its header")
        decoded = {}
        for key, value in source.items():
            if key in aliases:
                if value not in (None, "") and not re.fullmatch(r"\d{1,5}", value):
                    raise ValueError("Corrected trace device identifiers must be non-unique numeric ordinals")
                key = aliases[key]
            if value is not None and value.lstrip().startswith(("{", "[")):
                try:
                    value = json.loads(value)
                except ValueError:
                    pass
            decoded[key] = value
        projected = scrub_addresses(public_projection(decoded))
        for key in projected:
            if key not in names:
                names.append(key)
        rows.append(projected)
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=names)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: json.dumps(value, ensure_ascii=False, allow_nan=False) if isinstance(value, (dict, list)) else value
                         for key, value in row.items()})
    return stream.getvalue().encode("utf-8")


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument("--capture-manifest", action="append", default=[], type=Path)
    parser.add_argument("--snapshot-source", action="append", default=[], metavar="LOGICAL_PATH=ARCHIVED_PATH")
    parser.add_argument("--extra-source", action="append", default=[], type=Path)
    parser.add_argument("--public-data", action="append", default=[], type=Path)
    parser.add_argument("--control-capture", action="append", default=[], type=Path,
                        help="Completed sustained local-copy result.json; defaults to optional study-directory discovery")
    parser.add_argument("--allow-unavailable-source", action="store_true", help="Retain missing captured SHA explicitly without claiming source bytes exist")
    parser.add_argument("--no-review-mirror", action="store_true", help="Omit ignored exact-byte mirror; default CLI also keeps final bundle/report under investigation")
    parser.add_argument("--reviewed-public-raw-sha256", help="Explicit SHA-bound copy of ONLY the authorized final reviewed producer raw.json.gz; CRC/stream scan retained")
    parser.add_argument("--sources-only", action="store_true", help="Package verified source snapshots only; existing reviewed aggregate data remain at results/aggregates and are not duplicated")
    return parser.parse_args(argv)


def plan(args):
    sources = [(name, ROOT / name) for name in SOURCES]
    extras = [ROOT / name for name in EXTRAS if (ROOT / name).is_file()] + args.extra_source
    for path in extras:
        path = local_file(path)
        # Preserve directory context for the historical same-name probe.
        name = "historical/gpu_compute_probe.py" if path.parts[-2:] == ("5960-pny-experiment", "gpu_compute_probe.py") else "study-tools/" + path.name
        sources.append((name, path))
    snapshots = []
    final_manifest = INV / "final-measurement-source-20261103/manifest.json"
    if final_manifest.is_file():
        frozen_index = json.loads(final_manifest.read_text(encoding="utf-8-sig"))
        for entry in frozen_index.get("files", []):
            snapshots.append((safe_relative(entry["logical_path"]), local_file(entry["archive_path"])))
    frozen = INV / "frozen-nsight_capture-pre-enum-fix.py"
    if frozen.is_file():
        snapshots.append(("tools/nsight_capture.py", frozen))
    for declaration in args.snapshot_source:
        logical, separator, path = declaration.partition("=")
        if not separator:
            raise ValueError("snapshot-source requires LOGICAL_PATH=ARCHIVED_PATH")
        snapshots.append((safe_relative(logical), local_file(path)))
    manifests = args.capture_manifest or sorted(INV.glob("*/manifest-private.json"))
    public = args.public_data or sorted((ROOT / "results/aggregates").glob(PREFIX + "*"))
    if not args.public_data:
        # A final compressed derivative can coexist with its untracked original;
        # publish one logical artifact without modifying either input.
        public = [path for path in public if path.suffix != ".json" or not path.with_name(path.name + ".gz").is_file()]
    for logical, path in sources + snapshots:
        safe_relative(logical)
        if local_file(path).suffix.lower() != ".py":
            raise ValueError("Source snapshots must be Python source, never model weights, binaries or traces")
    manifests = [local_file(path) for path in manifests]
    public = [local_file(path) for path in public]
    if any(path.suffix.lower() not in (".json", ".csv", ".png", ".svg") and not path.name.endswith(".json.gz") for path in public):
        raise ValueError("Public study inputs accept JSON/CSV/generated plots only, never raw traces or weights")
    destination = args.destination.resolve()
    if not destination.is_relative_to((ROOT / "results/studies").resolve()):
        raise ValueError("Bundle destination must stay inside results/studies")
    return sources, snapshots, manifests, public, destination


def package(args):
    sources, snapshots, manifests, public, destination = plan(args)
    # Canonicalize once: Windows short profile paths and their resolved long
    # forms identify the same source but do not compare equal as Path objects.
    sources = [(safe_relative(logical), local_file(path)) for logical, path in sources]
    snapshots = [(safe_relative(logical), local_file(path)) for logical, path in snapshots]
    if destination.exists():
        raise ValueError("Preserve the existing bundle; select a new destination")
    if not manifests or not public:
        raise ValueError("A final source bundle requires captured source manifests and final public artifacts")
    controls = []
    excluded_controls = []
    control_paths = getattr(args, "control_capture", []) or sorted(INV.glob("sustained-local-vram-*/result.json"))
    for path in control_paths:
        path = local_file(path)
        raw = path.read_bytes()
        control = json.loads(raw.decode("utf-8-sig"))
        cards = control.get("cards")
        valid_cards = (isinstance(cards, list) and len(cards) == 2 and
                       all(card.get("status") == "completed" and card.get("internal_samples") == 15 and
                           len(card.get("samples", [])) == 15 and card.get("validation", {}).get("passed") is True
                           for card in cards))
        if (control.get("protocol") != "sustained-local-vram-reference-v1" or
            control.get("capture_kind") != "diagnostic_sustained_local_vram_copy" or
            control.get("status") != "completed" or control.get("cleanup_confirmed") is not True or not valid_cards):
            excluded_controls.append({"input_sha256": sha(raw), "reason": "Control incomplete, unsupported, or cleanup unconfirmed"})
            continue
        controls.append((path, sha(raw)))
    # Control result manifests carry the exact frozen dependency fingerprints;
    # retain those requirements rather than replacing them with current hashes.
    manifests = list(manifests) + [path for path, _ in controls]
    files, source_records, candidates, current = {}, [], {}, {}
    for logical, path in sources + snapshots:
        logical, path = safe_relative(logical), local_file(path)
        raw = path.read_bytes()
        checksum = sha(raw)
        reject_private(raw, "source", source_sha=checksum)
        key = (logical, checksum)
        if key not in candidates:
            public_path = "source/by-sha256/" + checksum + "/" + logical
            candidates[key] = public_path
            files[public_path] = raw
            source_records.append({"logical_path": logical, "sha256": checksum, "bytes": len(raw), "path": public_path})
        if path.is_relative_to(ROOT.resolve() / "untracked"):
            alias = path.relative_to(ROOT.resolve()).as_posix()
            candidates[(alias, checksum)] = candidates[key]
            record = next(item for item in source_records if item["path"] == candidates[key])
            record.setdefault("captured_logical_aliases", [])
            if alias not in record["captured_logical_aliases"]:
                record["captured_logical_aliases"].append(alias)
        if (logical, path) in sources:
            if logical in current and current[logical] != checksum:
                raise ValueError("Two different current source candidates use the same logical path")
            current[logical] = checksum
            files["source/" + logical] = raw
    final_manifest = INV / "final-measurement-source-20261103/manifest.json"
    frozen_manifest_record = None
    if final_manifest.is_file():
        frozen_raw = final_manifest.read_bytes()
        frozen = json.loads(frozen_raw.decode("utf-8-sig"))
        frozen_manifest_record = {"manifest_sha256": sha(frozen_raw), "files": []}
        for entry in frozen.get("files", []):
            logical, expected = safe_relative(entry["logical_path"]), entry["sha256"]
            snapshot = local_file(entry["archive_path"])
            if sha(snapshot.read_bytes()) != expected or (logical, expected) not in candidates:
                raise ValueError("Final measured source snapshot does not match its recorded original SHA")
            frozen_manifest_record["files"].append({"logical_path": logical, "sha256": expected,
                                                     "path": candidates[(logical, expected)]})
    captures, unavailable = [], []
    for path in manifests:
        raw = path.read_bytes()
        manifest = json.loads(raw.decode("utf-8-sig"))
        expected = manifest.get("source_sha256")
        if not isinstance(expected, dict) or not expected:
            raise ValueError("Capture manifest lacks recorded source fingerprints")
        capture = {"manifest_sha256": sha(raw), "sources": []}
        for logical, checksum in sorted(expected.items()):
            logical = safe_relative(logical)
            if not logical.endswith(".py"):
                raise ValueError("Captured source requirements must refer to Python source")
            if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
                raise ValueError("Captured source SHA-256 is invalid")
            snapshot = candidates.get((logical, checksum))
            record = {"logical_path": logical, "recorded_sha256": checksum,
                      "status": "exact_source_bytes_available" if snapshot else "source_bytes_unavailable",
                      "path": snapshot}
            capture["sources"].append(record)
            if not snapshot:
                unavailable.append({"capture_manifest_sha256": capture["manifest_sha256"], **record})
        captures.append(capture)
    if unavailable and not args.allow_unavailable_source:
        names = sorted({item["logical_path"] for item in unavailable})
        raise ValueError("Captured source bytes unavailable; supply snapshot-source or explicitly record unavailable: " + ", ".join(names))
    data_records = []
    artifacts = [(path, "data/" + path.name) for path in public]
    corrected_profiles = []
    for base in ([] if getattr(args, "sources_only", False) else sorted({path.parent for path in manifests})):
        latest = {}
        for index in base.glob("cases/*/analysis-corrected*/index.json"):
            case = index.parent.parent
            match = re.search(r"-v(\d+)$", index.parent.name)
            version = int(match[1]) if match else 0
            if case not in latest or version > latest[case][0]:
                latest[case] = (version, index)
        for version, index in latest.values():
            identifier = sha(index.read_bytes())[:16]
            public_directory = "data/profiles/" + identifier
            allowed = [index] + sorted(path for path in index.parent.glob("trace-*/*")
                                       if path.name in ("summary.json", "timeline.csv", "analysis.json") and path.is_file())
            for path in allowed:
                artifacts.append((local_file(path), public_directory + "/" + path.relative_to(index.parent).as_posix()))
            corrected_profiles.append({"corrected_index_sha256": sha(index.read_bytes()), "version": version,
                                       "public_directory": public_directory, "artifact_count": len(allowed)})
    for result_path, checksum in ([] if getattr(args, "sources_only", False) else controls):
        identifier = checksum[:16]
        for name in ("result.json", "clocks-raw.json", "power-raw.json"):
            path = result_path.parent / name
            if path.is_file():
                artifacts.append((local_file(path), "data/controls/" + identifier + "/" + name))
    for path, public_name in ([] if getattr(args, "sources_only", False) else artifacts):
        declared = getattr(args, "reviewed_public_raw_sha256", None)
        if path.name == REVIEWED_RAW_NAME and declared:
            packaged, receipt = reviewed_public_json(path, declared)
            name = safe_relative(public_name)
            files[name] = packaged
            data_records.append({"name": path.name, "path": name, "original_sha256": sha(packaged),
                                 "public_sha256": sha(packaged), "bytes": len(packaged),
                                 "compression": "reviewed-producer-gzip-exact-bytes", "compressed_sha256": sha(packaged),
                                 "compressed_bytes": len(packaged), "uncompressed_sha256": receipt["uncompressed_sha256"],
                                 "uncompressed_bytes": receipt["uncompressed_bytes"], "privacy_projection_applied": False,
                                 "source_bytes_changed_by_projection_or_formatting": False, "producer_receipt": receipt})
            continue
        original = path.read_bytes()
        compressed_input = path.name.endswith(".json.gz")
        if compressed_input:
            with gzip.open(path, "rb") as handle:
                raw = handle.read(MAX_RAW_JSON + 1)
            if len(raw) > MAX_RAW_JSON:
                raise ValueError("Derived JSON exceeds the bounded public packaging size")
        else:
            raw = original
        suffix = ".json" if compressed_input else path.suffix.lower()
        if suffix == ".json" and len(raw) > MAX_RAW_JSON:
            raise ValueError("Derived JSON exceeds the bounded public packaging size")
        trace_ordinals = "/profiles/" in public_name and path.name == "timeline.csv"
        projected = project_json(raw) if suffix == ".json" else project_csv(raw, trace_ordinals=trace_ordinals) if suffix == ".csv" else raw
        if suffix in (".json", ".csv", ".svg"):
            reject_private(projected, "public study data")
        if suffix == ".svg" and re.search(rb"(?:href|src)\s*=\s*['\"](?:https?:|file:)", projected, re.I):
            raise ValueError("SVG external resources are not allowed in a portable study bundle")
        compress = suffix == ".json" and (len(projected) > GZIP_JSON_THRESHOLD or compressed_input)
        basename = public_name[:-3] if compressed_input else public_name
        name = safe_relative(basename + (".gz" if compress else ""))
        if name in files:
            raise ValueError("Public artifact basenames collide")
        packaged = deterministic_gzip(projected) if compress else projected
        files[name] = packaged
        data_records.append({"name": path.name, "path": name, "original_sha256": sha(original),
                             "public_sha256": sha(packaged), "bytes": len(packaged),
                             "compression": "gzip-mtime0-os255" if compress else None,
                             "uncompressed_sha256": sha(projected), "uncompressed_bytes": len(projected),
                             "compressed_sha256": sha(packaged) if compress else None,
                             "compressed_bytes": len(packaged) if compress else None,
                             "privacy_projection_applied": suffix in (".json", ".csv"),
                             "trace_device_id_policy": "Typed numeric CUPTI deviceId/srcDeviceId/dstDeviceId renamed trace_device_id/trace_source_device_id/trace_destination_device_id; values unchanged" if trace_ordinals else None,
                             "source_bytes_changed_by_projection_or_formatting": raw != projected})
    bundle = {"schema": "wattever-study-source-bundle-v1", "study": "dual-gpu-communication",
              "created_at": datetime.now(timezone.utc).isoformat(), "capture_kind": "study_supporting_artifacts",
              "normal_benchmark_aggregate_eligible": False, "source_snapshots": source_records,
              "bundle_scope": "verified_sources_only_existing_public_aggregates_not_duplicated" if getattr(args, "sources_only", False) else "verified_sources_and_projected_supporting_data",
              "current_source_sha256": current, "capture_source_requirements": captures,
              "final_measured_source_manifest": frozen_manifest_record,
              "unavailable_captured_sources": unavailable, "data": data_records,
              "optional_sustained_copy_controls": [{"input_sha256": checksum, "public_directory": "data/controls/" + checksum[:16]}
                                                   for _, checksum in controls],
              "excluded_optional_controls": excluded_controls,
              "corrected_profile_artifacts": corrected_profiles,
              "source_policy": "Exact copied bytes, not rebased or redacted. Each captured SHA remains the recorded original; postprocessing/current sources are separately identified.",
              "reviewed_synthetic_source_fixture": "Only exact frozen generatorSHA admits its intentionally fake C:/Users/SUPERSECRET parser-test fixture; no such exemption applies to public/captured data.",
              "privacy_policy": "Private manifests, paths, UUIDs/LUIDs, model weights, executables, downloaded archives and raw profiler/SQLite files are excluded. JSON/CSV use privacy projection plus memory-address-key removal. Generated plots require visual review.",
              "compression_policy": "Normally projected derived JSON over20MiB uses mtime0/OS255 gzip. The explicitly SHA-bound reviewed-producer gzip remains byte-exact and records its separate receipt; both compressed/uncompressed SHA/size are retained. No emitted Git blob may reach100MiB.",
              "source_machine_binding": "Captured recipes contain fixed physical5070/5060 indices, prepared Windows study folders and public model filenames. Generic loopback defaults and study-relative paths are configuration examples, not exported private connection identities. Preserve snapshots for provenance; adapt public tools separately.",
              "execution_policy": "Snapshots provide audit provenance. Execute public adapters from the matching full Wattever checkout; snapshot files are not an independent installed package."}
    readme = """# Dual-GPU communication study sources

`source/` contains the frozen source copies and study analysis utilities. `source/by-sha256/` preserves exact versions matched to captured source fingerprints; `manifest.json` distinguishes those versions from analysis tools and records any explicitly unavailable source bytes. Original data and measured source files are not modified by packaging.

`data/` contains privacy-projected JSON/CSV and generated plots. These diagnostic captures remain separate from normal benchmark repeats. The measurement definitions, configurations, reproducible commands and interpretation are in the [engineering report](../../../docs/dual-gpu-communication-and-placement.md).

Use the matching complete Wattever checkout for imports and dependencies. The source copies are audit snapshots, not a standalone installed package. Public diagnostic adapters are documented separately from these captured versions. GPU diagnostics require an explicit run flag and verified idle inference engines.

Captured recipes bind the measured physical GPU indices, Windows study folders and public model filenames. Keep these versions for provenance and configure public diagnostic adapters for your own machine. Their generic loopback defaults do not establish a connection to your inference engine.

Large derived JSON is stored as deterministic `.json.gz`. The manifest retains SHA-256 and byte counts for both the decompressed public JSON and compressed file. Original captured inputs are unchanged.

When present, completed sustained local-copy controls are included under `data/controls/`. Their raw clock/power observations pass the same privacy projection as other JSON. They retain a separate diagnostic protocol and exact source fingerprints; sparse clock observations do not establish why another protocol produced a different rate.

The manifest records SHA-256 for every source and public artifact; `SHA256SUMS.txt` also covers the manifest and this guide. Private raw profiler reports, SQLite databases, model weights and local credentials are excluded.
"""
    files["README.md"] = readme.encode("utf-8")
    if getattr(args, "sources_only", False):
        files["README.md"] = ("# Dual-GPU communication study source snapshots\n\nThis bundle preserves verified exact source versions and SHA-256 provenance. It does not duplicate the reviewed public JSON/CSV/figures already published in `results/aggregates/dual-gpu-communication-and-placement-*`. See the [engineering report](../../../docs/dual-gpu-communication-and-placement.md) for those artifacts and their measurement definitions. Supplementary corrected trace timelines are not included in this source-only release bundle; the reviewed public raw aggregate retains the derived profiling summaries. Original captures remain unchanged.\n\nUse the complete matching Wattever checkout for dependencies. Captured source files retain their exact measured versions; adapt the public tools for other hardware.\n").encode("utf-8")
    files["manifest.json"] = (json.dumps(bundle, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    files["SHA256SUMS.txt"] = "".join(sha(raw) + "  " + name + "\n" for name, raw in sorted(files.items())).encode("utf-8")
    if any(len(raw) >= GIT_BLOB_LIMIT for raw in files.values()):
        raise ValueError("Bundle would contain a Git blob at or above100MiB; split or compress the derived artifact first")
    # Assemble only allowlisted/preflighted outputs. Never touch frozen inputs.
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / ("." + destination.name + ".pending-" + uuid.uuid4().hex[:12])
    temporary.mkdir(exist_ok=False)
    for name, raw in sorted(files.items()):
        output = temporary / Path(*PurePosixPath(name).parts)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(raw)
        if sha(output.read_bytes()) != sha(raw):
            raise RuntimeError("Written bundle checksum mismatch")
    if destination.exists():
        raise ValueError("Bundle destination appeared during preparation; temporary bundle preserved")
    temporary.rename(destination)
    mirror = None
    if not getattr(args, "no_review_mirror", True):
        mirror = INV / ("release-ready-study-" + sha(files["manifest.json"])[:12])
        if mirror.exists():
            raise ValueError("Preserve the existing ignored review mirror; public bundle was prepared")
        shutil.copytree(destination, mirror)
        for name, raw in files.items():
            if sha((mirror / Path(*PurePosixPath(name).parts)).read_bytes()) != sha(raw):
                raise RuntimeError("Ignored review mirror differs from prepared public bundle")
        report = ROOT / "docs/dual-gpu-communication-and-placement.md"
        if report.is_file():
            shutil.copyfile(report, mirror / "engineering-report.md")
            (mirror / "REPORT-SHA256.txt").write_text(sha(report.read_bytes()) + "  engineering-report.md\n", encoding="utf-8")
    return {"source_variants": len(source_records), "public_artifacts": len(data_records),
            "unavailable_source_versions": len(unavailable), "bundle_files": len(files),
            "ignored_review_mirror": mirror.relative_to(ROOT).as_posix() if mirror else None}


def main(argv=None):
    args = arguments(argv)
    if not args.run:
        sources, snapshots, manifests, public, destination = plan(args)
        print(json.dumps({"mode": "plan_only", "copies": 0, "native_calls": 0,
                          "current_sources": [logical for logical, _ in sources],
                          "archived_source_candidates": [logical for logical, _ in snapshots],
                          "capture_manifests": len(manifests), "public_artifacts": len(public),
                          "optional_control_candidates": len(getattr(args, "control_capture", []) or list(INV.glob("sustained-local-vram-*/result.json"))),
                          "destination": str(destination)}, indent=2))
        return 0
    try:
        print(json.dumps(package(args), indent=2))
        return 0
    except Exception as exc:
        # Validation messages are deliberately credential-free; unexpected IO
        # exceptions can embed local paths, so print only the exception class.
        print("Study bundle stopped (" + type(exc).__name__ + "). Inputs and existing bundles were preserved.")
        if type(exc) is ValueError:
            print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
