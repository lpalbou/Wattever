"""Optional one-click CPU temperature sensor for Windows.

Windows lets no ordinary program read the CPU package sensor. When the user
clicks "Enable CPU temperature", Wattever:

  1. downloads the signed PawnIO 2.2.0 installer and the signed PawnIO module
     release 0.2.11 from their GitHub releases, and checks both SHA-256 hashes;
  2. starts ``cpu_sensor/setup.ps1`` elevated, which shows one UAC prompt. It copies
     the files into ``%ProgramFiles%\\Wattever\\CpuSensor``, checks them again there,
     installs PawnIO if needed and registers a read-only SYSTEM task;
  3. reads the task's ``%ProgramData%\\Wattever\\CpuSensor\\cpu-temperature.json``
     without elevation (see windows_cpu_temperature.WatteverSensorReader).

Nothing happens until the user asks; "Remove" undoes all of it.
"""
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, 'cpu_sensor')
TASK_NAME = 'Wattever CPU temperature'

PAWNIO_SETUP = {
    'url': 'https://github.com/namazso/PawnIO.Setup/releases/download/2.2.0/PawnIO_setup.exe',
    'sha256': '1f519a22e47187f70a1379a48ca604981c4fcf694f4e65b734aaa74a9fba3032',
    'max_bytes': 16 << 20,
}
PAWNIO_MODULES = {
    'url': 'https://github.com/namazso/PawnIO.Modules/releases/download/0.2.11/release_0_2_11.zip',
    'sha256': '43608cb89bc84247fef1368a139013f7d043e17db6d6c8dfc9b46bf0905a81f4',
    'max_bytes': 4 << 20,
}
MODULES = {
    'IntelMSR.bin': 'd6ed85d65ab17a22f813ef98207d6d537155ee2ded5976a21cb48413c9b92e5f',
    'AMDFamily17.bin': 'dae74615761b78bdf064dfb3e136252ddcc6fc727d88f14738d0e5800d427a91',
}
PAWNIO_KEY = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO'
ERROR_CANCELLED = 1223
NO_WINDOW = 0x08000000


def install_dir():
    return os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'), 'Wattever', 'CpuSensor')


def reading_path():
    return os.path.join(os.environ.get('ProgramData', r'C:\ProgramData'), 'Wattever', 'CpuSensor', 'cpu-temperature.json')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def download(spec, opener=None):
    """Fetch a pinned file; refuse anything over its size limit or with another hash."""
    opener = opener or urllib.request.build_opener()
    request = urllib.request.Request(spec['url'], headers={'User-Agent': 'Wattever'})
    with opener.open(request, timeout=60) as response:
        data = response.read(spec['max_bytes'] + 1)
    if len(data) > spec['max_bytes']:
        raise RuntimeError('Download larger than expected: %s' % spec['url'])
    digest = sha256(data)
    if digest != spec['sha256']:
        raise RuntimeError('Download failed verification (SHA-256 %s): %s' % (digest, spec['url']))
    return data


def stage(folder, fetch=download):
    """Write everything setup.ps1 needs into folder, each file verified."""
    with open(os.path.join(folder, 'PawnIO_setup.exe'), 'wb') as fh:
        fh.write(fetch(PAWNIO_SETUP))
    archive = zipfile.ZipFile(io.BytesIO(fetch(PAWNIO_MODULES)))
    for name, expected in MODULES.items():
        data = archive.read(name)
        if sha256(data) != expected:
            raise RuntimeError('%s in the PawnIO module release failed verification.' % name)
        with open(os.path.join(folder, name), 'wb') as fh:
            fh.write(data)
    with open(os.path.join(SCRIPTS, 'cpu-sensor.ps1'), 'rb') as fh:
        sensor = fh.read()
    with open(os.path.join(folder, 'cpu-sensor.ps1'), 'wb') as fh:
        fh.write(sensor)
    with open(os.path.join(folder, 'cpu-sensor.ps1.sha256'), 'w', encoding='ascii') as fh:
        fh.write(sha256(sensor))
    with open(os.path.join(SCRIPTS, 'setup.ps1'), 'rb') as src, open(os.path.join(folder, 'setup.ps1'), 'wb') as dst:
        dst.write(src.read())


def setup_arguments(action, folder, log):
    args = ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
            '-File', os.path.join(folder, 'setup.ps1'), '-Action', action, '-Log', log]
    if action == 'install':
        args += ['-Source', folder]
    return args


def _quote(arg):
    return '"%s"' % arg if (' ' in arg or not arg) and not arg.startswith('"') else arg


def run_elevated(executable, args):
    """ShellExecuteEx 'runas' and wait; returns the exit code, or ERROR_CANCELLED if the user declines UAC."""
    import ctypes
    from ctypes import wintypes

    class Info(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('fMask', ctypes.c_ulong), ('hwnd', wintypes.HWND),
                    ('lpVerb', wintypes.LPCWSTR), ('lpFile', wintypes.LPCWSTR), ('lpParameters', wintypes.LPCWSTR),
                    ('lpDirectory', wintypes.LPCWSTR), ('nShow', ctypes.c_int), ('hInstApp', wintypes.HINSTANCE),
                    ('lpIDList', ctypes.c_void_p), ('lpClass', wintypes.LPCWSTR), ('hkeyClass', wintypes.HKEY),
                    ('dwHotKey', wintypes.DWORD), ('hIconOrMonitor', wintypes.HANDLE), ('hProcess', wintypes.HANDLE)]

    shell32 = ctypes.WinDLL('shell32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(Info)]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    info = Info(cbSize=ctypes.sizeof(Info), fMask=0x00000040, lpVerb='runas', lpFile=executable,  # NOCLOSEPROCESS
                lpParameters=' '.join(_quote(a) for a in args), nShow=0)
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == ERROR_CANCELLED:
            return ERROR_CANCELLED
        raise OSError(error, 'Could not start the elevated setup')
    try:
        kernel32.WaitForSingleObject(info.hProcess, 15 * 60 * 1000)
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return code.value
    finally:
        kernel32.CloseHandle(info.hProcess)


def pawnio_version():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, PAWNIO_KEY) as key:
            return str(winreg.QueryValueEx(key, 'DisplayVersion')[0])
    except (ImportError, OSError):
        return None


def task_installed():
    try:
        done = subprocess.run(['schtasks', '/Query', '/TN', TASK_NAME], capture_output=True, timeout=10,
                              creationflags=NO_WINDOW if sys.platform == 'win32' else 0)
        return done.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class CpuSensorSetup:
    """One install or removal at a time, run on a worker thread; status() is cheap."""

    def __init__(self, platform=sys.platform, fetch=download, elevate=run_elevated,
                 probe_pawnio=pawnio_version, probe_task=task_installed):
        self.platform, self.fetch, self.elevate = platform, fetch, elevate
        self.probe_pawnio, self.probe_task = probe_pawnio, probe_task
        self.lock = threading.Lock()
        self.state = {'phase': 'idle', 'action': None, 'message': None, 'log': [], 'finished_at': None}
        self.thread = None

    @property
    def supported(self):
        return self.platform == 'win32'

    def status(self):
        with self.lock:
            state = dict(self.state, log=list(self.state['log']))
        if not self.supported:
            return dict(state, supported=False, installed=False, pawnio_version=None,
                        note='The optional CPU sensor setup is for Windows. Linux reads the CPU sensor directly.')
        return dict(state, supported=True, installed=self.probe_task(), pawnio_version=self.probe_pawnio(),
                    install_dir=install_dir(), reading_path=reading_path())

    def start(self, action):
        if action not in ('install', 'uninstall'):
            raise ValueError('action must be install or uninstall.')
        if not self.supported:
            raise ValueError('The optional CPU sensor setup is only available on Windows.')
        with self.lock:
            if self.state['phase'] in ('downloading', 'waiting_for_approval'):
                raise RuntimeError('CPU sensor setup is already running.')
            self.state = {'phase': 'downloading' if action == 'install' else 'waiting_for_approval',
                          'action': action, 'message': None, 'log': [], 'finished_at': None}
        self.thread = threading.Thread(target=self._run, args=(action,), name='wattever-cpu-sensor-setup', daemon=True)
        self.thread.start()
        return self.status()

    def _set(self, **changes):
        with self.lock:
            self.state.update(changes)

    def _run(self, action):
        folder = tempfile.mkdtemp(prefix='wattever-cpu-sensor-')
        log = os.path.join(folder, 'setup.log')
        try:
            if action == 'install':
                stage(folder, self.fetch)
            else:
                with open(os.path.join(SCRIPTS, 'setup.ps1'), 'rb') as src, open(os.path.join(folder, 'setup.ps1'), 'wb') as dst:
                    dst.write(src.read())
            self._set(phase='waiting_for_approval')
            powershell = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
            code = self.elevate(powershell, setup_arguments(action, folder, log))
            lines = []
            if os.path.exists(log):
                with open(log, encoding='utf-8-sig', errors='replace') as fh:
                    lines = [line.rstrip() for line in fh if line.strip()]
            if code == ERROR_CANCELLED:
                self._set(phase='cancelled', message='Administrator approval was declined; nothing was changed.', log=lines)
            elif code == 0:
                done = 'CPU temperature is enabled.' if action == 'install' else 'The CPU temperature sensor was removed.'
                self._set(phase='done', message=done, log=lines)
            else:
                error = next((line[len('ERROR: '):] for line in reversed(lines) if line.startswith('ERROR: ')), None)
                self._set(phase='failed', message=error or 'Setup exited with code %s.' % code, log=lines)
        except Exception as exc:  # noqa: BLE001 - reported to the page
            self._set(phase='failed', message=str(exc))
        finally:
            self._set(finished_at=time.time())
            for name in os.listdir(folder):
                try:
                    os.remove(os.path.join(folder, name))
                except OSError:
                    pass
            try:
                os.rmdir(folder)
            except OSError:
                pass


SETUP = CpuSensorSetup()
