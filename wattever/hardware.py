"""Best-effort, non-identifying hardware inventory.

GPU indices identify sensors only and do not determine where a model runs.
Values unavailable on a host are returned as None or empty lists.
"""
import json
import math
import glob
import os
import platform
import re
import subprocess
from datetime import datetime, timezone

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


def _positive(value):
    number = _number(value)
    return number if not isinstance(value, bool) and number is not None and number > 0 else None


def _memory_inventory():
    """Physical DIMMs where readable without elevation; DIMM count is not channels.

    CIM clock fields retain their reported MHz units; do not silently reinterpret
    these values as independently measured clocks, transfer rates or bandwidth.
    """
    modules, source = [], None
    if pu.IS_WINDOWS:
        script = ("$ErrorActionPreference='Stop'; @(Get-CimInstance Win32_PhysicalMemory | "
                  "Select-Object Capacity,Speed,ConfiguredClockSpeed,SMBIOSMemoryType,DataWidth,TotalWidth) "
                  "| ConvertTo-Json -Compress")
        data = _json_command(['powershell', '-NoProfile', '-NonInteractive', '-Command', script], timeout=10)
        source = 'Win32_PhysicalMemory'
        for raw in _entries(data):
            if not any(k in raw for k in ('Capacity', 'Speed', 'ConfiguredClockSpeed', 'SMBIOSMemoryType', 'DataWidth', 'TotalWidth')):
                continue
            modules.append({'capacity_bytes': _positive(raw.get('Capacity')),
                            'speed_mhz_reported': _positive(raw.get('Speed')),
                            'configured_clock_mhz_reported': _positive(raw.get('ConfiguredClockSpeed')),
                            'smbios_memory_type': _positive(raw.get('SMBIOSMemoryType')),
                            'data_width_bits': _positive(raw.get('DataWidth')),
                            'total_width_bits': _positive(raw.get('TotalWidth'))})
    elif pu.IS_LINUX:
        source = 'linux_edac_sysfs'
        for path in sorted(glob.glob('/sys/devices/system/edac/mc/mc*/dimm*')):
            try:
                with open(os.path.join(path, 'size'), encoding='utf-8') as f:
                    capacity = _positive(f.read().strip())
                with open(os.path.join(path, 'dimm_mem_type'), encoding='utf-8') as f:
                    kind = f.read().strip()
                if capacity:
                    modules.append({'capacity_bytes': capacity * 1024 ** 2, 'type_reported': kind})
            except OSError:
                continue
    elif pu.IS_MAC:
        # Unified-memory topology is not inferred from marketing chip names.
        source = 'sysctl'
    capacities = [m['capacity_bytes'] for m in modules if m.get('capacity_bytes')]
    installed = sum(capacities) if capacities and len(capacities) == len(modules) else None
    if pu.IS_MAC:
        installed = _positive(pu.sysctl('hw.memsize'))
    return {'modules': modules, 'module_count': len(modules) if modules else None,
            'installed_capacity_bytes': installed,
            'configured_clock_mhz_reported': sorted(set(m['configured_clock_mhz_reported'] for m in modules
                                                       if m.get('configured_clock_mhz_reported'))),
            'channel_count': None, 'measured_bandwidth_gb_s': None,
            'source': source,
            'note': 'Module count does not establish active memory channels. Clocks are firmware/OS reports; bandwidth has not been benchmarked.'}


def _pcie(gen, width, max_gen=None, max_width=None, source=None):
    gen, width, max_gen, max_width = map(_positive, (gen, width, max_gen, max_width))
    # Gen 6 FLIT/FEC encoding differs. Leave unknown rather than extend this rule.
    rates = {1: (2.5, .8), 2: (5., .8), 3: (8., 128 / 130),
             4: (16., 128 / 130), 5: (32., 128 / 130)}
    ceiling = None
    if gen in rates and width:
        transfers, efficiency = rates[gen]
        ceiling = transfers * 1e9 * efficiency * width / 8
    return {'generation_current': gen, 'width_current': width,
            'generation_max_reported': max_gen, 'width_max_reported': max_width,
            'link_encoding_ceiling_bytes_per_second': ceiling,
            'bandwidth_kind': 'derived_one_direction_encoding_ceiling',
            'measured_host_to_device_bytes_per_second': None,
            'source': source,
            'note': 'Snapshot may show an idle downshift. Reported maxima can reflect platform limits. Encoding ceiling excludes packet/system overhead and is not measured transfer bandwidth.'}


def _nvidia_pcie(index):
    if type(index) is not int or index < 0:
        return _pcie(None, None)
    try:
        result = pu.run(['nvidia-smi', '--id=%d' % index,
                         '--query-gpu=pcie.link.gen.current,pcie.link.width.current,pcie.link.gen.max,pcie.link.width.max',
                         '--format=csv,noheader,nounits'], timeout=5)
        rows = result.stdout.strip().splitlines()
        fields = [v.strip() for v in rows[0].split(',')] if result.returncode == 0 and rows else []
        if len(fields) == 4:
            return _pcie(*fields, source='nvidia-smi')
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return _pcie(None, None)


def _linux_pcie(pci):
    if not pci or not re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]', pci):
        return _pcie(None, None)
    def read(name):
        try:
            with open('/sys/bus/pci/devices/' + pci + '/' + name, encoding='utf-8') as f:
                return f.read().strip()
        except OSError:
            return None
    def generation(value):
        match = re.match(r'([0-9.]+)\s*GT/s', value or '')
        return {2.5: 1, 5.: 2, 8.: 3, 16.: 4, 32.: 5, 64.: 6}.get(float(match.group(1))) if match else None
    return _pcie(generation(read('current_link_speed')), read('current_link_width'),
                 generation(read('max_link_speed')), read('max_link_width'), source='linux_pci_sysfs')


def _capabilities(details=None):
    details = details or {}
    capability = details.get('compute_cap')
    power_limit = details.get('power_limit_w')
    if type(power_limit) not in (int, float) or not math.isfinite(power_limit) or power_limit <= 0:
        power_limit = None
    return {'compute_capability_reported': capability if isinstance(capability, str) and re.fullmatch(r'\d+\.\d+', capability) else None,
            'power_limit_w_reported': power_limit,
            'compute_peaks': [], 'measured_memory_bandwidth_gb_s': None,
            'note': 'No FLOPS/TOPS or device-memory bandwidth measurement was performed. Model quantization bit width is not hardware arithmetic precision; peak compute also depends on operation, clock and sparsity.'}


def _windows():
    # Select only public, non-identifying fields at the source.
    script = ("$ErrorActionPreference='Stop'; "
              "@{cpu=@(Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed,Architecture); "
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
            'physical_cores': total('NumberOfCores'), 'logical_cores': total('NumberOfLogicalProcessors'),
            'socket_count': len(cpus) or None, 'architecture': platform.machine(),
            'max_clock_mhz_reported': _positive(cpus[0].get('MaxClockSpeed')) if cpus else None,
            'source': 'Win32_Processor'}, {
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
            'physical_cores': len(pairs) or None, 'logical_cores': os.cpu_count(),
            'socket_count': len({c['physical id'] for c in cpus if c.get('physical id') is not None}) or None,
            'architecture': platform.machine(), 'max_clock_mhz_reported': None,
            'source': 'proc_cpuinfo'}, {
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
            'logical_cores': _number(pu.sysctl('hw.logicalcpu')),
            'architecture': platform.machine(), 'socket_count': None,
            'max_clock_mhz_reported': None, 'source': 'sysctl'}, {
            'manufacturer': 'Apple', 'model': pu.sysctl('hw.model'),
            'memory_bytes': _number(pu.sysctl('hw.memsize'))}, gpus


def _equipment():
    """Return non-identifying motherboard, firmware and physical disk details."""
    board = {'manufacturer': None, 'model': None}
    firmware = {'vendor': None, 'version': None}
    storage = []
    if pu.IS_WINDOWS:
        script = ("$ErrorActionPreference='Stop'; $physical=@(); "
                  "try {$physical=@(Get-PhysicalDisk -ErrorAction Stop | Select-Object DeviceId,MediaType,BusType)} catch {}; "
                  "@{board=(Get-CimInstance Win32_BaseBoard | Select-Object Manufacturer,Product); "
                  "firmware=(Get-CimInstance Win32_BIOS | Select-Object Manufacturer,SMBIOSBIOSVersion); "
                  "physical_storage=$physical; "
                  "storage=@(Get-CimInstance Win32_DiskDrive | Select-Object Index,Model,Size,InterfaceType)} | ConvertTo-Json -Depth 4 -Compress")
        data = _json_command(['powershell', '-NoProfile', '-NonInteractive', '-Command', script]) or {}
        raw_board, raw_fw = data.get('board') or {}, data.get('firmware') or {}
        board = {'manufacturer': raw_board.get('Manufacturer'), 'model': raw_board.get('Product')}
        firmware = {'vendor': raw_fw.get('Manufacturer'), 'version': raw_fw.get('SMBIOSBIOSVersion')}
        storage = [{'model': d.get('Model'), 'capacity_bytes': _number(d.get('Size')),
                    'interface': d.get('InterfaceType')} for d in _entries(data.get('storage'))]
        physical = {str(d.get('DeviceId')): d for d in _entries(data.get('physical_storage')) if d.get('DeviceId') is not None}
        for disk, raw in zip(storage, _entries(data.get('storage'))):
            detail = physical.get(str(raw.get('Index')), {})
            media = {3: 'HDD', 4: 'SSD', '3': 'HDD', '4': 'SSD'}.get(detail.get('MediaType'), detail.get('MediaType'))
            bus = {17: 'NVMe', 11: 'SATA', 7: 'USB', 8: 'RAID', '17': 'NVMe', '11': 'SATA', '7': 'USB', '8': 'RAID'}.get(detail.get('BusType'), detail.get('BusType'))
            if media in ('SSD', 'HDD'):
                disk['media_type'] = media
            if isinstance(bus, str) and bus in ('NVMe', 'SATA', 'USB', 'RAID', 'SAS', 'SCSI'):
                disk['bus_type'] = bus
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
            rotational = read(os.path.join(path, 'queue', 'rotational'))
            if rotational in ('0', '1'):
                storage[-1]['rotational'] = rotational == '1'
                storage[-1]['media_type'] = 'HDD' if rotational == '1' else 'SSD'
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
        gpu['pcie'] = _linux_pcie(pci) if pu.IS_LINUX and not pu.IS_WINDOWS else _pcie(None, None)
        gpu['capabilities'] = _capabilities()
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
                nvidia_link = _nvidia_pcie(match.get('index'))
                if nvidia_link['generation_current'] is not None or nvidia_link['width_current'] is not None:
                    gpu['pcie'] = nvidia_link
                gpu['capabilities'] = _capabilities(details)
            else:
                gpu['memory_architecture'] = power.gpu_info(match.get('index')).get('memory_architecture')
            if match.get('vram_total_mb') is not None:
                gpu['memory_bytes'] = int(match['vram_total_mb'] * 1024 * 1024)
            sensors.remove(match)
    for sensor in sensors:
        details = power.gpu_info(sensor.get('index'))
        gpus.append({'name': sensor.get('name'), 'vendor': details.get('vendor'),
                     'memory_bytes': int(sensor['vram_total_mb'] * 1024 * 1024) if sensor.get('vram_total_mb') is not None else None,
                     'driver': details.get('driver'), 'memory_architecture': details.get('memory_architecture'), 'sensor_index': sensor.get('index'),
                     'pcie': _pcie(None, None) if pu.IS_MAC else _nvidia_pcie(sensor.get('index')),
                     'capabilities': _capabilities(details)})
    board, firmware, storage = _equipment()
    return {'metadata_schema_version': 1,
            'collection': {'scope': 'benchmark_client', 'inference_server_hardware_verified': False,
                           'captured_at': datetime.now(timezone.utc).isoformat(),
                           'python_version': platform.python_version(),
                           'policy': 'Read-only allow-listed OS/driver probes without elevation; no unique device IDs, host names, network addresses or user paths.'},
            'memory': _memory_inventory(),
            'motherboard': board, 'firmware': firmware, 'storage': storage,
            'os': {'name': platform.system(), 'release': platform.release(),
                   'architecture': platform.machine()}, 'cpu': cpu, 'system': system, 'gpus': gpus}
