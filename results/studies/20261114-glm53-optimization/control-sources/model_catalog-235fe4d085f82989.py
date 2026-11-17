"""Prepared package identities and Control-owned, hardware-aware API loading."""
from __future__ import annotations
import asyncio
import logging
import math
import os
from pathlib import Path
import re
from dataclasses import dataclass
from urllib.parse import urlsplit


class ModelSwitchError(RuntimeError):
    def __init__(self, message, code="model_switch_failed"):
        super().__init__(message)
        self.message, self.code = message, code


@dataclass(frozen=True)
class ModelSpec:
    id: str
    preset: str  # Legacy filename only; no launch settings are read from it.
    family: str
    quantization: str
    model: str
    weights: tuple[str, ...]
    pack: str
    packed: bool = True

    @property
    def slug(self):
        return self.preset.lower()


SPECS = (
    ModelSpec("qwen3.8-flash-next-unsloth-ud-iq4_xs", "FlashNext32K", "unsloth", "UD-IQ4_XS", "UD-IQ4_XS",
              tuple("models/unsloth-UD-IQ4_XS/Qwen3.8-Flash-Next-UD-IQ4_XS-%05d-of-00003.gguf" % i for i in range(1, 4)), "unsloth-ud-iq4_xs"),
    ModelSpec("qwen3.8-flash-next-iq3_s", "CleanIQ3S64K", "qwen", "IQ3_S", "IQ3_S",
              tuple("models/IQ3_S/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-%05d-of-00002.gguf" % i for i in range(1, 3)), "iq3_s"),
    ModelSpec("qwen3.6-35b-a3b-ud-q4_k_xl", "CleanQwen3664K", "qwen36", "UD-Q4_K_XL", "UD-Q4_K_XL",
              ("models/qwen36-UD-Q4_K_XL/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf",), "qwen36-ud-q4_k_xl", False),
)
_SPECS = {spec.id: spec for spec in SPECS}
_EXTRA_FLAGS = {"--conversation-cache-mib", "--expert-cache", "--prefill", "--spec", "--spec-min-p",
                "--vram-reserve-mib", "--expert-vram", "--resident-budget-gib", "--mmap-experts", "--resident-experts", "--kv-resident",
                "STRATA_HC_Q8", "STRATA_ROUTE_TAIL_SKIP", "STRATA_VRAM_FREE"}


def _safe_override(key, value):
    if key not in _EXTRA_FLAGS or not isinstance(value, str) or len(value) > 40:
        return False
    if key == "STRATA_VRAM_FREE":
        return value in ("device", "auto")
    if key in ("STRATA_HC_Q8", "STRATA_ROUTE_TAIL_SKIP"):
        return value in ("0", "1")
    if key == "--mmap-experts":
        return value in ("off", "on")
    if value == "off":
        return key in ("--expert-cache", "--resident-budget-gib", "--resident-experts", "--kv-resident")
    if value == "auto":
        return key in ("--expert-cache", "--prefill", "--resident-experts", "--vram-reserve-mib")
    if not re.fullmatch(r"\d+(?:\.\d+)?", value):
        return False
    number = float(value)
    if not math.isfinite(number):
        return False
    if key == "--spec-min-p":
        return 0 <= number <= 1
    if key in ("--expert-vram", "--resident-budget-gib"):
        return 0 <= number <= 1024
    return number.is_integer() and 0 <= number <= 100000000


class ModelManager:
    def __init__(self, root=None, backend="http://127.0.0.1:8081", *, controller_factory=None,
                 strata_home=None):
        self.root = Path(root or Path(__file__).resolve().parent).resolve()
        self.models = self.root / "models"
        self.backend = backend.rstrip("/")
        parsed = urlsplit(self.backend)
        self._local_backend = (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")
                               and parsed.port == 8081 and not (parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path))
        self.strata_home = Path(strata_home or Path(os.environ.get("LOCALAPPDATA", "")) / "Strata").resolve()
        self.launch_dir = self.strata_home / "launch"
        self.controller_factory = controller_factory
        self._controller = None
        self._operation = None
        self._queue_lock = asyncio.Lock()

    @staticmethod
    def _present(path):
        try:
            return path.is_file() and path.stat().st_size > 0
        except OSError:
            return False

    def _workspace_path(self, value, expected):
        try:
            path = Path(value).resolve()
            return path == Path(expected).resolve() and path.is_relative_to(self.root)
        except (TypeError, ValueError, OSError):
            return False

    def _prepared(self, spec):
        """Verify assets without reading old presets, probing a GPU or starting anything."""
        for relative in spec.weights:
            path = self.models / relative
            if not self._workspace_path(path, path) or not self._present(path):
                raise ModelSwitchError("The requested model package is not completely prepared.", "model_not_prepared")
            try:
                marker = path.with_name(path.name + ".done").read_text(encoding="utf-8").strip()
            except OSError:
                marker = ""
            if not re.fullmatch(r"sha256 [0-9a-fA-F]{64}", marker):
                raise ModelSwitchError("The requested model is missing a verified download marker.", "model_not_prepared")
        pack = self.models / "packs" / spec.pack
        required = [pack / "tokenizer/tokenizer.json"]
        if spec.packed:
            required += [pack / name for name in ("dense.bin", "index.txt", "native_experts.txt", "conversions.json")]
            required += [self.models / "mtp/rt" / name for name in ("dense.bin", "dense.txt", "draft_vocab.bin", "experts.bin")]
        if any(not self._workspace_path(path, path) or not self._present(path) for path in required):
            raise ModelSwitchError("The requested model's prepared pack or draft assets are incomplete.", "model_not_prepared")

    def base_config(self, spec):
        """Create a clean package manifest; the Controller plans memory and devices."""
        self._prepared(spec)
        terminal = (self.strata_home / "terminal/current").resolve()
        if not terminal.is_dir() or not terminal.is_relative_to(self.strata_home / "terminal"):
            raise ModelSwitchError("The installed Strata runtime could not be verified.", "runtime_unavailable")
        pack = self.models / "packs" / spec.pack
        args = ["--native", str(self.models / spec.weights[0]), "--prefill", "auto"]
        if spec.packed:
            profile = terminal / "data/expert-profile.bin"
            if not self._present(profile):
                raise ModelSwitchError("The prepared expert profile is unavailable.", "model_not_prepared")
            args += ["--pack", str(pack), "--expert-profile", str(profile),
                     "--mtp", str(self.models / "mtp/rt"), "--kv", "int8", "--expert-cache", "auto",
                     "--spec", "4", "--spec-min-p", "0.5"]
            if len(spec.weights) == 2:
                args += ["--ple-gguf", str(self.models / spec.weights[1])]
        else:
            args += ["--mtp", "2"]
        return {"exe": "component:strata-engine", "args": args, "cwd": str(terminal),
                "tokenizer": str(pack / "tokenizer"), "model_name": spec.id,
                "arch": "qwen4exp" if spec.packed else "qwen35moe", "port": 8081,
                "host": "127.0.0.1", "parallel": 1, "open_browser": False,
                "sampling": {"presence_penalty": 0, "frequency_penalty": 0},
                "env": {"STRATA_HC_Q8": "1", "STRATA_ROUTE_TAIL_SKIP": "0", "STRATA_VRAM_FREE": "device"}}

    def metadata(self, canonical):
        spec = _SPECS.get(canonical)
        if spec is None:
            raise ModelSwitchError("The requested model is not in the prepared local catalog.", "unsupported_model")
        self._prepared(spec)
        from strata_desktop import gguf_context
        return {"id": spec.id, "quantization": spec.quantization, "family": spec.family,
                "context_length": gguf_context(self.models / spec.weights[0]), "model": spec.model,
                "context_policy": "hardware_auto"}

    def _owner(self, data):
        """Read-only compatibility for old status reports; never used to launch."""
        rows = [row for row in data.get('running', []) if row.get('port') == 8081]
        if not rows:
            return None
        if len(rows) != 1:
            raise ModelSwitchError('Backend ownership is ambiguous.', 'unowned_backend')
        row = rows[0]
        spec = next((item for item in SPECS if row.get('name') == item.slug
                     and row.get('profile') == 'launch-' + item.slug), None)
        try:
            valid = spec is not None and Path(row['config']).resolve() == (self.launch_dir / (spec.slug + '.json')).resolve()
        except (KeyError, TypeError, ValueError, OSError):
            valid = False
        if not valid or row.get('host') != '127.0.0.1':
            raise ModelSwitchError('Backend ownership could not be verified.', 'unowned_backend')
        return spec

    def available_models(self):
        if not self._local_backend:
            return []
        result = []
        for spec in SPECS:
            try:
                self._prepared(spec)
            except ModelSwitchError:
                continue
            result.append({"id": spec.id, "object": "model", "created": 0, "owned_by": "local-strata"})
        return result

    def _control(self):
        if self._controller is None:
            if self.controller_factory is None:
                from strata_desktop import Controller
                self._controller = Controller()
            else:
                self._controller = self.controller_factory()
        return self._controller

    async def _ensure(self, canonical, client):
        from desktop_policy import require_desktop
        require_desktop(self.root)
        spec = _SPECS.get(canonical)
        if spec is None:
            raise ModelSwitchError("The requested model is not in the prepared local catalog.", "unsupported_model")
        if not self._local_backend:
            raise ModelSwitchError("Automatic loading is restricted to the owned local backend on port8081.", "unowned_backend")
        self._prepared(spec)
        try:
            return await asyncio.to_thread(self._control().gateway_load, canonical)
        except ModelSwitchError:
            raise
        except Exception as exc:
            # Never restore an old model or start a second model after failure.
            logging.getLogger(__name__).exception('Strata Control configuration/load failed for %s', canonical)
            reason = str(exc)
            public_prefixes = ('Insufficient ', 'Context ', 'No GPUs ', 'Invalid available RAM',
                               'Invalid VRAM ', 'This model supports ', 'This model requires ',
                               'RAM cache use ', 'CPU expert workers ', 'GPU share of cache misses ',
                               'MTP verify window ', 'Draft confidence ', 'Control settings or GPU hardware changed',
                               'The hardware-aware Qwen35 engine ', 'Engine context is ',
                               'Model server exited.', 'Model loading timed out.', 'Loading cancelled.', 'NVMe volume ')
            if isinstance(exc, (ValueError, RuntimeError)) and reason.startswith(public_prefixes) and not any(char in reason for char in ('\\', '/', '\n', '\r')):
                raise ModelSwitchError(reason[:300], 'model_load_failed') from None
            raise ModelSwitchError("Strata Control could not configure or load the selected model. Inspect its status and desktop-model.log.", "model_load_failed") from None

    async def ensure_model(self, canonical, client):
        async with self._queue_lock:
            if self._operation and not self._operation.done():
                await asyncio.shield(self._operation)
            self._operation = asyncio.create_task(self._ensure(canonical, client))
            return await asyncio.shield(self._operation)
