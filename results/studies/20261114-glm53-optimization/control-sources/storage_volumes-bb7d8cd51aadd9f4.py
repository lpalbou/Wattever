"""Detect local NVMe volumes and stage a verified model package onto one."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import time


STAGE_FOLDER = 'StrataModels'


def _run_inventory():
    """Ask Windows for disk topology. Callers inject inventory in tests."""
    script = (
        "Get-PhysicalDisk | ForEach-Object {"
        "  $disk = $_;"
        "  Get-Partition -DiskNumber $disk.DeviceId -ErrorAction SilentlyContinue |"
        "    Where-Object { $_.DriveLetter } | ForEach-Object {"
        "      $vol = Get-Volume -DriveLetter $_.DriveLetter -ErrorAction SilentlyContinue;"
        "      if ($null -eq $vol) { return };"
        "      [pscustomobject]@{"
        "        DiskNumber = [int]$disk.DeviceId;"
        "        FriendlyName = [string]$disk.FriendlyName;"
        "        BusType = [string]$disk.BusType;"
        "        MediaType = [string]$disk.MediaType;"
        "        DriveLetter = [string]$_.DriveLetter;"
        "        Label = [string]$vol.FileSystemLabel;"
        "        Size = [int64]$vol.Size;"
        "        SizeRemaining = [int64]$vol.SizeRemaining"
        "      }"
        "    }"
        "} | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ['powershell', '-NoProfile', '-Command', script],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Disk inventory failed')
    text = result.stdout.strip()
    if not text:
        return []
    data = json.loads(text)
    return data if isinstance(data, list) else [data]


def normalize_inventory(rows):
    """Turn raw Windows inventory rows into stable volume records."""
    volumes = []
    for row in rows or []:
        letter = str(row.get('DriveLetter') or '').strip().rstrip(':').upper()
        bus = str(row.get('BusType') or '').strip().upper()
        if len(letter) != 1 or not letter.isalpha():
            continue
        try:
            free = int(row.get('SizeRemaining') or 0)
            total = int(row.get('Size') or 0)
            disk = int(row.get('DiskNumber'))
        except (TypeError, ValueError):
            continue
        volumes.append({
            'letter': letter,
            'path': f'{letter}:\\',
            'label': str(row.get('Label') or '').strip(),
            'friendly_name': str(row.get('FriendlyName') or '').strip() or f'Disk {disk}',
            'bus_type': bus,
            'media_type': str(row.get('MediaType') or '').strip().upper(),
            'free_bytes': max(0, free),
            'total_bytes': max(0, total),
            'disk_number': disk,
        })
    volumes.sort(key=lambda item: item['letter'])
    return volumes


def nvme_volumes(inventory=None):
    """Return mounted volumes whose physical disk reports NVMe."""
    rows = _run_inventory() if inventory is None else inventory
    return [volume for volume in normalize_inventory(rows) if volume['bus_type'] == 'NVME']


def volume_label(volume):
    name = volume.get('friendly_name') or 'NVMe'
    tag = volume.get('label')
    free_gb = volume['free_bytes'] / (1024 ** 3)
    total_gb = volume['total_bytes'] / (1024 ** 3)
    suffix = f' · {tag}' if tag else ''
    return f"{volume['letter']}: {name}{suffix} ({free_gb:.0f}/{total_gb:.0f} GB free)"


def stage_root_for(volume):
    return Path(volume['path']) / STAGE_FOLDER


def required_sources(models_root, spec):
    """Every prepared file that must exist before an engine can load this model."""
    original = Path(models_root).resolve()
    required = [original / value for value in spec.weights]
    required += [path.with_name(path.name + '.done') for path in required]
    pack = original / 'packs' / spec.pack
    required += [path for path in pack.rglob('*') if path.is_file()]
    if spec.packed:
        required += [path for path in (original / 'mtp/rt').rglob('*') if path.is_file()]
    return required


def package_complete(models_root, destination_root, spec):
    """True only when the destination mirrors the prepared package byte-for-byte on required files."""
    original = Path(models_root).resolve()
    target = Path(destination_root).resolve()
    if not target.is_dir():
        return False
    for source in required_sources(original, spec):
        destination = (target / source.relative_to(original)).resolve()
        if (not destination.is_relative_to(target) or not destination.is_file()
                or destination.stat().st_size != source.stat().st_size):
            return False
        if source.suffix == '.done' and destination.read_bytes() != source.read_bytes():
            return False
    return True


def _copy_file(source, destination, on_chunk=None, cancel=None):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + '.part')
    copied = 0
    try:
        with source.open('rb') as inbound, temporary.open('wb') as outbound:
            while True:
                if cancel is not None and cancel():
                    raise RuntimeError('Model staging cancelled.')
                chunk = inbound.read(8 * 1024 * 1024)
                if not chunk:
                    break
                outbound.write(chunk)
                copied += len(chunk)
                if on_chunk:
                    on_chunk(len(chunk))
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


def stage_model_package(models_root, destination_root, spec, progress=None, cancel=None):
    """
    Copy the prepared package onto destination_root when needed.
    progress(dict) receives phase, bytes_done, bytes_total, path, message.
    Returns the destination root used for launch redirection.
    """
    original = Path(models_root).resolve()
    target = Path(destination_root).resolve()
    if target == original:
        raise ValueError('Choose an NVMe destination that is not the source models folder')
    sources = required_sources(original, spec)
    missing = []
    for source in sources:
        if not source.is_file():
            raise ValueError(f'Missing prepared asset: {source}')
        destination = (target / source.relative_to(original)).resolve()
        if not destination.is_relative_to(target):
            raise ValueError('A staged path escaped the destination root')
        if (not destination.is_file() or destination.stat().st_size != source.stat().st_size
                or (source.suffix == '.done' and destination.read_bytes() != source.read_bytes())):
            missing.append((source, destination))
    if not missing and package_complete(original, target, spec):
        if progress:
            progress({'phase': 'ready', 'bytes_done': 0, 'bytes_total': 0, 'path': '',
                      'message': f'Using existing package on {target}'})
        return target

    total = sum(source.stat().st_size for source, _ in missing)
    free = shutil.disk_usage(target.anchor if target.exists() else Path(target.drive + '\\')).free
    # Allow for the bytes already present on incomplete files that will be rewritten.
    if free < total:
        raise ValueError(f'Not enough free space on {target.drive}: need {total / (1024 ** 3):.1f} GB')

    done = 0
    started = time.monotonic()
    if progress:
        progress({'phase': 'staging', 'bytes_done': 0, 'bytes_total': total, 'path': '',
                  'message': f'Copying model package to {target}'})

    def tick(amount, path):
        nonlocal done
        done += amount
        elapsed = max(time.monotonic() - started, 0.001)
        rate = done / elapsed
        if progress:
            progress({
                'phase': 'staging',
                'bytes_done': done,
                'bytes_total': total,
                'path': str(path),
                'message': (
                    f'Copying to {target.drive}\\ · {done / (1024 ** 3):.2f}/'
                    f'{total / (1024 ** 3):.2f} GB · {rate / (1024 ** 2):.0f} MB/s'
                ),
            })

    for source, destination in missing:
        if cancel is not None and cancel():
            raise RuntimeError('Model staging cancelled.')
        _copy_file(source, destination, on_chunk=lambda n, path=source: tick(n, path), cancel=cancel)

    if not package_complete(original, target, spec):
        raise RuntimeError('Staged model package failed verification')
    if progress:
        progress({'phase': 'ready', 'bytes_done': total, 'bytes_total': total, 'path': '',
                  'message': f'Model package ready on {target}'})
    return target
