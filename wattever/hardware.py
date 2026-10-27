"""Best-effort, non-identifying hardware inventory.

GPU indices identify sensors only and do not determine where a model runs.
Values unavailable on a host are returned as None or empty lists.
"""
import json
import glob
import os
import platform
import re
import subprocess

from . import platform_util as pu


def _json_command(args, timeout=20):
    try:
        result = pu.run(args, timeout=timeout)
        return json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _number(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _entries(value):
    return value if isinstance(value, list) else [value] if isinstance(value, dict) else []


def _windows():
    # Select only public, non-identifying fields at the source.
    script = ("$ErrorActionPreference='Stop'; "
              "@{cpu=@(Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors); "
              "gpus=@(Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion); "
              "system=(Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model,TotalPhysicalMemory)} | ConvertTo-Json -Depth 4 -Compress")
    data = _json_command(['powershell', '-NoProfile', '-NonInteractive', '-Command', script]) or {}
    cpus = _entries(data.get('cpu'))
    system = data.get('system') or {}
    gpu = [{'name': item.get('Name'), 'vendor': None,
            'memory_bytes': None, 'driver': item.get('DriverVersion')}
           for item in _entries(data.get('gpus')) if item.get('Name')]
    def total(key):
        vals = [_number(c.get(key)) for c in cpus]
        return sum(v for v in vals if v is not None) if any(v is not None for v in vals) else None
    return {'name': cpus[0].get('Name') if cpus else None,
            'physical_cores': total('NumberOfCores'), 'logical_cores': total('NumberOfLogicalProcessors')}, {
            'manufacturer': system.get('Manufacturer'), 'model': system.get('Model'),
            'memory_bytes': _number(system.get('TotalPhysicalMemory'))}, gpu


def _linux():
    cpus = []
    try:
        with open('/proc/cpuinfo', encoding='utf-8', errors='replace') as f:
            for block in f.read().strip().split('\n\n'):
                cpus.append(dict(line.split(':', 1) for line in block.splitlines() if ':' in line))
    except OSError:
        pass
    cpus = [{k.strip(): v.strip() for k, v in c.items()} for c in cpus]
    pairs = {(c.get('physical id'), c.get('core id')) for c in cpus
             if c.get('physical id') is not None and c.get('core id') is not None}
    memory = None
    try:
        with open('/proc/meminfo', encoding='utf-8') as f:
            match = re.search(r'^MemTotal:\s*(\d+)\s*kB', f.read(), re.M)
            memory = int(match.group(1)) * 1024 if match else None
    except OSError:
        pass
    def dmi(name):
        try:
            with open('/sys/class/dmi/id/' + name, encoding='utf-8') as f:
                return f.read().strip() or None
        except OSError:
            return None
    gpus = []
    try:
        p = pu.run(['lspci', '-D', '-mm'], timeout=10)
        if p.returncode == 0:
            import shlex
            for line in p.stdout.splitlines():
                fields = shlex.split(line)
                if len(fields) >= 4 and any(x in fields[1].lower() for x in ('vga', '3d controller', 'display controller')):
                    gpus.append({'name': fields[3], 'vendor': fields[2], 'memory_bytes': None, 'driver': None, '_pci': fields[0]})
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    # DRM remains available even when pciutils/lspci is not installed.
    if not gpus:
        vendors = {'0x10de': 'NVIDIA', '0x1002': 'AMD', '0x8086': 'Intel'}
        for path in sorted(glob.glob('/sys/class/drm/card[0-9]*/device')):
            try:
                with open(os.path.join(path, 'vendor'), encoding='utf-8') as f:
                    vendor_id = f.read().strip().lower()
                with open(os.path.join(path, 'device'), encoding='utf-8') as f:
                    device_id = f.read().strip().lower()
                bus = os.path.basename(os.path.realpath(path))
                vendor = vendors.get(vendor_id, vendor_id)
                gpus.append({'name': '%s GPU (%s)' % (vendor, device_id), 'vendor': vendor,
                             'memory_bytes': None, 'driver': None, '_pci': bus})
            except OSError:
                continue
    return {'name': (cpus[0].get('model name') or cpus[0].get('Processor')) if cpus else None,
            'physical_cores': len(pairs) or None, 'logical_cores': os.cpu_count()}, {
            'manufacturer': dmi('sys_vendor'), 'model': dmi('product_name'), 'memory_bytes': memory}, gpus


def _mac():
    data = _json_command(['system_profiler', '-json', 'SPDisplaysDataType'], timeout=30) or {}
    gpus = []
    for item in _entries(data.get('SPDisplaysDataType')):
        name = item.get('sppci_model') or item.get('_name')
        if name:
            gpus.append({'name': name, 'vendor': item.get('spdisplays_vendor'),
                         'memory_bytes': None, 'driver': None})
    return {'name': pu.sysctl('machdep.cpu.brand_string'),
            'physical_cores': _number(pu.sysctl('hw.physicalcpu')),
            'logical_cores': _number(pu.sysctl('hw.logicalcpu'))}, {
            'manufacturer': 'Apple', 'model': pu.sysctl('hw.model'),
            'memory_bytes': _number(pu.sysctl('hw.memsize'))}, gpus


def _equipment():
    """Return non-identifying motherboard, firmware and physical disk details."""
    board = {'manufacturer': None, 'model': None}
    firmware = {'vendor': None, 'version': None}
    storage = []
    if pu.IS_WINDOWS:
        script = ("$ErrorActionPreference='Stop'; "
                  "@{board=(Get-CimInstance Win32_BaseBoard | Select-Object Manufacturer,Product); "
                  "firmware=(Get-CimInstance Win32_BIOS | Select-Object Manufacturer,SMBIOSBIOSVersion); "
                  "storage=@(Get-CimInstance Win32_DiskDrive | Select-Object Model,Size,InterfaceType)} | ConvertTo-Json -Depth 4 -Compress")
        data = _json_command(['powershell', '-NoProfile', '-NonInteractive', '-Command', script]) or {}
        raw_board, raw_fw = data.get('board') or {}, data.get('firmware') or {}
        board = {'manufacturer': raw_board.get('Manufacturer'), 'model': raw_board.get('Product')}
        firmware = {'vendor': raw_fw.get('Manufacturer'), 'version': raw_fw.get('SMBIOSBIOSVersion')}
        storage = [{'model': d.get('Model'), 'capacity_bytes': _number(d.get('Size')),
                    'interface': d.get('InterfaceType')} for d in _entries(data.get('storage'))]
    elif pu.IS_LINUX:
        def read(path):
            try:
                with open(path, encoding='utf-8', errors='replace') as f:
                    return f.read().strip() or None
            except OSError:
                return None
        base = '/sys/class/dmi/id/'
        board = {'manufacturer': read(base + 'board_vendor'), 'model': read(base + 'board_name')}
        firmware = {'vendor': read(base + 'bios_vendor'), 'version': read(base + 'bios_version')}
        for path in sorted(glob.glob('/sys/block/*')):
            name = os.path.basename(path)
            if not re.match(r'^(sd[a-z]+|hd[a-z]+|nvme\d+n\d+|mmcblk\d+|vd[a-z]+|xvd[a-z]+)$', name):
                continue
            sectors = _number(read(os.path.join(path, 'size')))
            model = read(os.path.join(path, 'device', 'model'))
            interface = 'NVMe' if name.startswith('nvme') else ('MMC' if name.startswith('mmc') else None)
            storage.append({'model': model, 'capacity_bytes': sectors * 512 if sectors is not None else None,
                            'interface': interface})
    elif pu.IS_MAC:
        # Only allow-listed fields; profiler can return serials and volume names.
        data = _json_command(['system_profiler', '-json', 'SPNVMeDataType'], timeout=30) or {}
        for controller in _entries(data.get('SPNVMeDataType')):
            for disk in _entries(controller.get('_items')):
                storage.append({'model': disk.get('device_model'),
                                'capacity_bytes': _number(disk.get('size_in_bytes')), 'interface': 'NVMe'})
    return board, firmware, storage


def inventory():
    """Return OS, CPU, system and all discoverable GPUs without unique IDs.

    GPU entries have name, vendor, memory_bytes, driver and sensor_index; a
    sensor index is only populated where a matching NVIDIA sensor is known.
    Motherboard, firmware and physical storage fields are best effort; missing
    values are None and empty lists do not prove a device is absent.
    """
    from . import power
    if pu.IS_WINDOWS:
        cpu, system, gpus = _windows()
    elif pu.IS_MAC:
        cpu, system, gpus = _mac()
    elif pu.IS_LINUX:
        cpu, system, gpus = _linux()
    else:
        cpu, system, gpus = {'name': None, 'physical_cores': None, 'logical_cores': os.cpu_count()}, {'manufacturer': None, 'model': None, 'memory_bytes': None}, []
    # Match duplicate names one-to-one; do not collapse identical adapters.
    sensors = list(power.list_gpus())
    for gpu in gpus:
        gpu['sensor_index'] = None
        def bus(value):
            if not value:
                return None
            # nvidia-smi may use an eight-digit PCI domain.
            return re.sub(r'^0+(?=[0-9a-fA-F]{4}:)', '', value.lower())
        pci = bus(gpu.pop('_pci', None))
        match = next((s for s in sensors if pci and bus(s.get('pci_bus_id')) == pci), None)
        if match is None and not pci:
            match = next((s for s in sensors if s.get('name') == gpu['name']), None)
        if match:
            gpu['sensor_index'] = match.get('index')
            if match.get('pci_bus_id'):
                gpu['name'] = match.get('name') or gpu['name']
            if not pu.IS_MAC:
                gpu['vendor'] = 'NVIDIA'
                details = power.gpu_info(match.get('index'))
                gpu['driver'] = details.get('driver') or gpu['driver']
                gpu['memory_architecture'] = details.get('memory_architecture')
            else:
                gpu['memory_architecture'] = power.gpu_info(match.get('index')).get('memory_architecture')
            if match.get('vram_total_mb') is not None:
                gpu['memory_bytes'] = int(match['vram_total_mb'] * 1024 * 1024)
            sensors.remove(match)
    for sensor in sensors:
        details = power.gpu_info(sensor.get('index'))
        gpus.append({'name': sensor.get('name'), 'vendor': details.get('vendor'),
                     'memory_bytes': int(sensor['vram_total_mb'] * 1024 * 1024) if sensor.get('vram_total_mb') is not None else None,
                     'driver': details.get('driver'), 'memory_architecture': details.get('memory_architecture'), 'sensor_index': sensor.get('index')})
    board, firmware, storage = _equipment()
    return {'motherboard': board, 'firmware': firmware, 'storage': storage,
            'os': {'name': platform.system(), 'release': platform.release(),
                   'architecture': platform.machine()}, 'cpu': cpu, 'system': system, 'gpus': gpus}
