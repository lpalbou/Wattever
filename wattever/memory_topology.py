"""Dependency-free memory topology probes and explicit historical annotations.

Populated firmware channels are not proof of the controller's active interleave
mode. Never derive channels from DIMM count or a bandwidth microbenchmark.
"""
import copy
import ctypes
import glob
import os
import re
from datetime import datetime, timezone

from . import platform_util as pu


def _now():
    return datetime.now(timezone.utc).isoformat()


def unavailable(reason="The OS/firmware does not expose memory channel topology."):
    return {"status": "unavailable", "source": None, "method": None,
            "captured_at": _now(), "channel_count": None,
            "active_channel_count": None, "populated_channel_count": None,
            "reason": reason}


def _structures(data):
    """Yield bounded SMBIOS structures; strings stay local to the parser."""
    offset = 0
    while offset + 4 <= len(data):
        kind, length = data[offset], data[offset + 1]
        if length < 4 or offset + length > len(data):
            return
        end = data.find(b"\0\0", offset + length)
        if end < 0:
            return
        strings = data[offset + length:end].split(b"\0")
        yield kind, int.from_bytes(data[offset + 2:offset + 4], "little"), data[offset:offset + length], strings
        offset = end + 2
        if kind == 127:
            return


def _slot_channel(label):
    # Only explicit, common channel+slot conventions. Slot numbers on their
    # own, BANK numbers, ranks and the CPU's supported maximum are not channels.
    match = re.fullmatch(r"(?:(CPU\d+|P\d+)[_ -])?DIMM[_ -]?([A-Z])\d+", label.upper().strip())
    return (match[1] or "", match[2]) if match else None


def parse_smbios(data, module_count=None):
    records = list(_structures(data))
    devices = {}
    for kind, handle, raw, strings in records:
        if kind != 17 or len(raw) < 18 or int.from_bytes(raw[12:14], "little") in (0, 65535):
            continue
        index = raw[16]
        label = strings[index - 1].decode("ascii", errors="replace") if 0 < index <= len(strings) else ""
        devices[handle] = _slot_channel(label)
    if not devices or (module_count and len(devices) != module_count):
        return unavailable("Firmware memory-device coverage is incomplete.")
    channels, covered = [], []
    for kind, _, raw, _ in records:
        if kind != 37 or len(raw) < 7:
            continue
        count = raw[6]
        if len(raw) < 7 + count * 3:
            return unavailable("Firmware memory-channel records are incomplete.")
        members = [int.from_bytes(raw[8 + i * 3:10 + i * 3], "little") for i in range(count)]
        populated = [member for member in members if member in devices]
        if populated:
            channels.append(populated)
            covered.extend(populated)
    if set(covered) == set(devices) and len(covered) == len(set(covered)):
        return {"status": "reported", "source": "SMBIOS Type 37",
                "method": "populated_firmware_channel_records", "captured_at": _now(),
                "channel_count": len(channels), "populated_channel_count": len(channels),
                "active_channel_count": None,
                "note": "Populated channels reported by firmware; active controller mode was not independently read."}
    labels = list(devices.values())
    if all(label is not None for label in labels):
        count = len(set(labels))
        return {"status": "inferred", "source": "SMBIOS Type 17",
                "method": "explicit_firmware_channel_slot_labels", "captured_at": _now(),
                "channel_count": count, "populated_channel_count": count,
                "active_channel_count": None,
                "note": "Populated channels inferred from explicit firmware channel/slot labels, not DIMM count. Active controller mode is unverified."}
    return unavailable()


def _windows(module_count):
    # GetSystemFirmwareTable avoids WMI dependencies and external utilities.
    # Raw firmware can contain identifiers; only topology counts leave this call.
    try:
        function = ctypes.windll.kernel32.GetSystemFirmwareTable
        function.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
        function.restype = ctypes.c_uint32
        provider = int.from_bytes(b"RSMB", "big")
        size = function(provider, 0, None, 0)
        if not 8 <= size <= 16 * 1024 * 1024:
            return unavailable()
        buffer = ctypes.create_string_buffer(size)
        actual = function(provider, 0, buffer, size)
        if actual != size:
            return unavailable()
        length = int.from_bytes(buffer.raw[4:8], "little")
        if length > size - 8:
            return unavailable()
        return parse_smbios(buffer.raw[8:8 + length], module_count)
    except (AttributeError, OSError, ValueError):
        return unavailable()


def _linux(module_count):
    channels, populated = set(), 0
    try:
        for path in glob.glob('/sys/devices/system/edac/mc/mc*/dimm*'):
            with open(os.path.join(path, 'size'), encoding='ascii') as stream:
                size = int(stream.read().strip())
            if size <= 0:
                continue
            populated += 1
            with open(os.path.join(path, 'dimm_location'), encoding='ascii') as stream:
                match = re.search(r'\bchannel\s+(\d+)\b', stream.read())
            if not match:
                return unavailable()
            channels.add((os.path.dirname(path), int(match[1])))
    except (OSError, ValueError):
        return unavailable()
    if not channels or (module_count and populated != module_count):
        return unavailable()
    return {"status": "reported", "source": "Linux EDAC sysfs",
            "method": "populated_edac_channels", "captured_at": _now(),
            "channel_count": len(channels), "populated_channel_count": len(channels),
            "active_channel_count": None,
            "note": "Populated channels reported by the OS memory controller driver; not a bandwidth-derived count."}


def capture(module_count=None):
    if pu.IS_WINDOWS:
        return _windows(module_count)
    if pu.IS_LINUX:
        return _linux(module_count)
    return unavailable("This platform does not expose channel topology through the built-in probe.")


def matches(run, current):
    """Require selected GPU plus CPU, board, RAM capacity and reported clocks."""
    old, new = run.get("hardware") or {}, current.get("hardware") or {}
    gpu, selected = run.get("gpu") or {}, current.get("gpu") or {}
    if not gpu.get("name") or any(gpu.get(key) != selected.get(key) for key in ("name", "vram_total_mb", "driver")):
        return False
    if old.get("collection", {}).get("scope") == "remote_unverified" or not old.get("cpu", {}).get("name"):
        return False
    fields = [(old.get("cpu", {}).get("name"), new.get("cpu", {}).get("name")),
              (old.get("motherboard"), new.get("motherboard"))]
    for key in ("installed_capacity_bytes", "module_count", "configured_clock_mhz_reported"):
        fields.append((old.get("memory", {}).get(key), new.get("memory", {}).get(key)))
    return all(a is not None and a == b for a, b in fields)


def backfill(run, current, topology):
    if not matches(run, current):
        raise ValueError("GPU, CPU, motherboard or memory settings differ or are unverified; memory topology was not updated.")
    if topology.get("status") == "unavailable" or not topology.get("channel_count"):
        raise ValueError("Memory channel topology is unavailable; existing metadata was preserved.")
    value = copy.deepcopy(topology)
    value.update(provenance="detected_later_on_matching_hardware", historical_inference_conditions_verified=False)
    memory = run.setdefault("hardware", {}).setdefault("memory", {})
    memory.update(channel_count=value["channel_count"], channel_topology=value)
    memory["note"] = "Channel topology includes source, method and capture time. Populated channels and active controller mode are distinct. Clocks are firmware/OS reports; transfer probes are stored separately."
    run.setdefault("hardware_measurement_history", []).append({"kind": "memory_topology",
        "captured_at": value["captured_at"], "provenance": value["provenance"],
        "memory_topology": copy.deepcopy(value)})
    for request in run.get("hardware_follow_ups", []):
        if request.get("kind") == "memory_topology" and request.get("status") == "pending":
            request.update(status="completed", completed_at=value["captured_at"])
    return value


def pending(runs, gpu):
    """Only remind for the GPU actually present, leaving other cards pending."""
    cards = gpu if isinstance(gpu, list) else [gpu]
    present = {card["name"]: card.get("index", 0) for card in cards if card.get("name")}
    return [{"run_id": run["id"], "label": run.get("label") or run["id"], "kind": "memory_topology",
             "gpu_index": present[(run.get("gpu") or {})["name"]]}
            for run in runs if (run.get("gpu") or {}).get("name") in present
            and any(item.get("kind") == "memory_topology" and item.get("status") == "pending"
                    for item in run.get("hardware_follow_ups", []))]
