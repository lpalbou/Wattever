"""Pinned, resumable, verified downloads of model assets for Strata Control.

Every asset names an exact Hugging Face revision, byte size and SHA-256 (the
same pins the Strata installer carries), so a download is either the expected
file or it is rejected. Nothing here starts an engine.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

HF_ENDPOINT = 'https://huggingface.co'
CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Asset:
    id: str
    title: str
    repo: str
    revision: str
    filename: str
    size: int
    sha256: str
    relative: str          # destination below the models root
    families: tuple        # model families (ModelSpec.family) this asset serves
    kind: str = 'vision'

    @property
    def url(self):
        endpoint = (os.environ.get('HF_ENDPOINT') or '').strip().rstrip('/') or HF_ENDPOINT
        return f'{endpoint}/{self.repo}/resolve/{self.revision}/{self.filename}'

    @property
    def size_gb(self):
        return self.size / 1e9


# Image encoders ("mmproj"): one file per model family, read by strata-vision.
ASSETS = (
    Asset('vision-qwen3.8-flash-next', 'Image encoder · Qwen3.8 Flash Next',
          'ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF', 'ed59f92082b1e93c0e96d60a8b11aab089b52f09',
          'mmproj-Qwen3.8-Flash-Next-BF16.gguf', 907543008,
          'b1a82259702816a5330d7bd7607cd9676b11780e79ff7348c21103ff3ce49bd0',
          'models/mmproj-Qwen3.8-Flash-Next-BF16.gguf', ('qwen', 'unsloth')),
    Asset('vision-qwen3.6-35b', 'Image encoder · Qwen3.6 35B',
          'unsloth/Qwen3.6-35B-A3B-MTP-GGUF', '5bc3e238d916f48a861bac2f8a1990a0e9b7e98d',
          'mmproj-F16.gguf', 899283584,
          '71f3cbc1f7cc0f30d09d41cfa924c0060827ebc33bf15ace7e86661e856f0160',
          'models/mmproj-F16.gguf', ('qwen36',)),
)


def asset(asset_id):
    found = next((item for item in ASSETS if item.id == asset_id), None)
    if found is None:
        raise ValueError(f'Unknown downloadable asset: {asset_id}')
    return found


def vision_encoder_for(family):
    """The image encoder that serves a model family, or None when it has none."""
    return next((item for item in ASSETS if item.kind == 'vision' and family in item.families), None)


def asset_path(item, models_root):
    return Path(models_root) / item.relative


def _marker(path):
    return path.with_name(path.name + '.done')


def is_ready(item, models_root):
    """Present, the pinned size, and carrying the marker written after a SHA-256 match."""
    path = asset_path(item, models_root)
    try:
        if not path.is_file() or path.stat().st_size != item.size:
            return False
        return _marker(path).read_text(encoding='utf-8').strip() == 'sha256 ' + item.sha256
    except OSError:
        return False


def missing(models_root, items=ASSETS):
    return [item for item in items if not is_ready(item, models_root)]


def _digest(path, cancel=None):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while True:
            if cancel and cancel():
                raise RuntimeError('Download cancelled.')
            block = stream.read(8 * CHUNK)
            if not block:
                return digest.hexdigest()
            digest.update(block)


def download(item, models_root, progress=None, cancel=None, client=None):
    """
    Fetch one asset into the models root. Resumes a `.part` file with a Range
    request, verifies size and SHA-256, then publishes the file and its marker.
    progress(dict) receives phase, bytes_done, bytes_total, path, message.
    """
    destination = asset_path(item, models_root)
    if is_ready(item, models_root):
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + '.part')
    done = partial.stat().st_size if partial.exists() else 0
    if done > item.size:
        partial.unlink()
        done = 0

    def report(phase, message):
        if progress:
            progress({'phase': phase, 'bytes_done': done, 'bytes_total': item.size,
                      'path': str(destination), 'message': message})

    owned = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=httpx.Timeout(60, connect=15))
    try:
        if done < item.size:
            headers = {'Range': f'bytes={done}-'} if done else {}
            started, base = time.monotonic(), done
            with client.stream('GET', item.url, headers=headers) as response:
                if response.status_code == 200 and done:
                    done = 0            # the host ignored the Range: start over
                    base = 0
                elif response.status_code not in (200, 206):
                    raise RuntimeError(f'{item.title}: download failed with HTTP {response.status_code}.')
                if response.status_code == 206:
                    span = re.fullmatch(r'bytes (\d+)-\d+/(\d+)', response.headers.get('content-range', ''))
                    if not span or int(span.group(1)) != done or int(span.group(2)) != item.size:
                        raise RuntimeError(f'{item.title}: the host returned an unexpected byte range.')
                with partial.open('ab' if done else 'wb') as stream:
                    for block in response.iter_bytes(CHUNK):
                        if cancel and cancel():
                            raise RuntimeError('Download cancelled. It resumes from where it stopped.')
                        stream.write(block)
                        done += len(block)
                        if done > item.size:
                            raise RuntimeError(f'{item.title}: the host sent more data than the pinned size.')
                        rate = (done - base) / max(time.monotonic() - started, 0.001)
                        report('downloading', f'Downloading {item.title} · {done / 1e9:.2f}/{item.size_gb:.2f} GB'
                                              f' · {rate / 1e6:.0f} MB/s')
        if done != item.size:
            raise RuntimeError(f'{item.title}: download ended early; click Download again to resume.')
        report('verifying', f'Verifying {item.title}…')
        if _digest(partial, cancel) != item.sha256:
            partial.unlink(missing_ok=True)   # a wrong file must not be resumed or used
            raise RuntimeError(f'{item.title}: checksum mismatch; the file was discarded.')
        os.replace(partial, destination)
        _marker(destination).write_text('sha256 ' + item.sha256, encoding='utf-8')
        report('ready', f'{item.title} ready.')
        return destination
    finally:
        if owned:
            client.close()
