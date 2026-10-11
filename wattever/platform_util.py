"""Platform differences, in one place.

The benchmark logic is identical everywhere; only three things are not:
subprocess flags, where machine identity comes from, and how power is read.
This module owns the first and is imported by whoever needs the others.
"""
import os
import subprocess
import sys

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# Hides the console window a subprocess would otherwise flash on Windows.
# Passing it on POSIX is not merely ignored -- CPython raises
# ValueError("creationflags is only supported on Windows platforms") -- so it
# has to be applied conditionally rather than defensively.
CREATE_NO_WINDOW = 0x08000000


def popen_kwargs():
    return {"creationflags": CREATE_NO_WINDOW} if IS_WINDOWS else {}


def run(args, timeout=15, env=None, text=True):
    """subprocess.run with the platform's flags and no inherited console."""
    return subprocess.run(args, capture_output=True, text=text, timeout=timeout,
                          env=env, errors="replace" if text else None,
                          **popen_kwargs())


def popen(args, **kwargs):
    kwargs.update(popen_kwargs())
    return subprocess.Popen(args, **kwargs)


def sysctl(name, default=None):
    """Read one sysctl value. macOS/BSD only; returns default elsewhere."""
    if not IS_MAC:
        return default
    try:
        p = run(["sysctl", "-n", name], timeout=8)
    except (OSError, subprocess.SubprocessError):
        return default
    if p.returncode != 0:
        return default
    value = (p.stdout or "").strip()
    return value or default


def platform_label():
    if IS_WINDOWS:
        return "windows"
    if IS_MAC:
        return "macos"
    if IS_LINUX:
        return "linux"
    return sys.platform
