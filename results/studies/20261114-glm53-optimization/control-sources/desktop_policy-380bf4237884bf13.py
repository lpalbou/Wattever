"""Keep workspace launch paths under the desktop controller."""
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parent


def desktop_only(root=ROOT):
    return (Path(root) / 'desktop-only.json').is_file()


def control_parent_alive():
    """True when this process was spawned by a still-running Strata Control owner."""
    raw = os.environ.get('STRATA_CONTROL_PARENT', '')
    if not raw.isdigit():
        return False
    try:
        import psutil
        return psutil.Process(int(raw)).is_running()
    except Exception:
        return False


def require_desktop(root=ROOT):
    if not desktop_only(root):
        return
    if Path(sys.argv[0]).resolve() == ROOT / 'strata_app.py':
        return
    # Control-owned gateway/backend children may run prepared-preset lifecycle.
    if control_parent_alive():
        return
    raise RuntimeError('Start and load Strata from the Strata Control desktop app.')
