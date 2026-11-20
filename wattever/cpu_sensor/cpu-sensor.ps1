# Wattever CPU temperature sensor (Windows).
#
# Runs as SYSTEM from a scheduled task that Wattever's optional "Enable CPU
# temperature" setup registers. It reads the CPU package temperature through
# the signed PawnIO driver and its signed IntelMSR / AMDFamily17 modules, and
# writes one small JSON file every second that any local user can read:
#
#   %ProgramData%\Wattever\CpuSensor\cpu-temperature.json
#
# It only reads two thermal registers (Intel) or one SMN thermal register
# (AMD); it never writes hardware. It lives in %ProgramFiles%\Wattever\CpuSensor,
# which only administrators can change, because SYSTEM executes it.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$outDir = Join-Path $env:ProgramData 'Wattever\CpuSensor'
$outFile = Join-Path $outDir 'cpu-temperature.json'
$tmpFile = "$outFile.tmp"

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Threading;

public static class WatteverPawnIO {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr LoadLibraryW(string path);
    [DllImport("PawnIOLib.dll")] static extern int pawnio_open(out IntPtr handle);
    [DllImport("PawnIOLib.dll")] static extern int pawnio_load(IntPtr handle, byte[] blob, UIntPtr size);
    [DllImport("PawnIOLib.dll", CharSet = CharSet.Ansi)]
    static extern int pawnio_execute(IntPtr handle, string name, ulong[] input, UIntPtr inSize,
                                     ulong[] output, UIntPtr outSize, out UIntPtr returned);
    [DllImport("PawnIOLib.dll")] static extern int pawnio_close(IntPtr handle);

    static IntPtr handle = IntPtr.Zero;

    // The library is loaded by full path from PawnIO's admin-only install folder,
    // so DllImport binds to it and never searches the current directory.
    public static void Open(string libraryPath, byte[] module) {
        if (LoadLibraryW(libraryPath) == IntPtr.Zero)
            throw new Exception("Cannot load " + libraryPath + " (error " + Marshal.GetLastWin32Error() + ")");
        int hr = pawnio_open(out handle);
        if (hr < 0) throw new Exception(String.Format("pawnio_open failed: 0x{0:X8}", hr));
        hr = pawnio_load(handle, module, (UIntPtr)module.Length);
        if (hr < 0) { Close(); throw new Exception(String.Format("pawnio_load failed: 0x{0:X8}", hr)); }
    }

    public static ulong Call(string name, ulong argument) {
        ulong[] output = new ulong[1];
        UIntPtr returned;
        int hr = pawnio_execute(handle, name, new ulong[] { argument }, (UIntPtr)1, output, (UIntPtr)1, out returned);
        if (hr < 0) throw new Exception(String.Format("{0}(0x{1:X}) failed: 0x{2:X8}", name, argument, hr));
        return output[0];
    }

    public static void Close() {
        if (handle != IntPtr.Zero) { pawnio_close(handle); handle = IntPtr.Zero; }
    }

    // Intel: IA32_TEMPERATURE_TARGET (0x1A2) bits 23:16 = TjMax;
    // IA32_PACKAGE_THERM_STATUS (0x1B1) bit 31 = reading valid, bits 22:16 = degrees below TjMax.
    public static double IntelPackage() {
        ulong target = Call("ioctl_read_msr", 0x1A2);
        double tjmax = (target >> 16) & 0xFF;
        if (tjmax < 50 || tjmax > 130) tjmax = 100;
        ulong status = Call("ioctl_read_msr", 0x1B1);
        if (((status >> 31) & 1) == 0) throw new Exception("IA32_PACKAGE_THERM_STATUS reading is not valid");
        return tjmax - ((status >> 16) & 0x7F);
    }

    // AMD Family 17h and later: THM_TCON_CUR_TMP (SMN 0x00059800) bits 31:21 in 1/8 degree;
    // the -49 degree range applies when RANGE_SEL (bit 19) or TJ_SEL (bits 17:16 = 3) is set.
    // Other hardware tools take the shared Access_PCI mutex around PCI configuration access.
    public static double AmdTctl() {
        using (Mutex pci = new Mutex(false, "Global\\Access_PCI")) {
            bool owned = false;
            try {
                try { owned = pci.WaitOne(100); } catch (AbandonedMutexException) { owned = true; }
                ulong raw = Call("ioctl_read_smn", 0x00059800) & 0xFFFFFFFF;
                double value = (raw >> 21) * 0.125;
                if ((raw & 0x80000) != 0 || (raw & 0x30000) == 0x30000) value -= 49;
                return value;
            } finally {
                if (owned) pci.ReleaseMutex();
            }
        }
    }
}
'@

function Write-Reading([hashtable]$reading) {
    $reading.v = 1
    $reading.at = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    $reading.pid = $PID
    [IO.File]::WriteAllText($tmpFile, ($reading | ConvertTo-Json -Compress))
    if (Test-Path -LiteralPath $outFile) { [IO.File]::Replace($tmpFile, $outFile, $null) }
    else { [IO.File]::Move($tmpFile, $outFile) }
}

function Get-PawnIOLibrary {
    $key = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO'
    $location = (Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue).InstallLocation
    if (-not $location) { $location = Join-Path $env:ProgramFiles 'PawnIO' }
    $library = Join-Path $location 'PawnIOLib.dll'
    if (-not (Test-Path -LiteralPath $library)) { throw "PawnIO is not installed ($library not found)." }
    return $library
}

$cpu = Get-ItemProperty -LiteralPath 'HKLM:\HARDWARE\DESCRIPTION\System\CentralProcessor\0'
$vendor = [string]$cpu.VendorIdentifier
if ($vendor -eq 'GenuineIntel') { $module = 'IntelMSR.bin'; $sensor = 'intel_package' }
elseif ($vendor -eq 'AuthenticAMD' -and [string]$cpu.Identifier -match 'Family (\d+)' -and [int]$Matches[1] -ge 23) {
    $module = 'AMDFamily17.bin'; $sensor = 'amd_tctl'
}
else { $module = $null; $sensor = $null }

while ($true) {
    try {
        if (-not $module) { throw "Unsupported CPU for the PawnIO sensor: $vendor $($cpu.Identifier)" }
        [WatteverPawnIO]::Open((Get-PawnIOLibrary), [IO.File]::ReadAllBytes((Join-Path $here $module)))
        try {
            while ($true) {
                $celsius = if ($sensor -eq 'intel_package') { [WatteverPawnIO]::IntelPackage() } else { [WatteverPawnIO]::AmdTctl() }
                Write-Reading @{ celsius = [math]::Round($celsius, 2); sensor = $sensor; error = $null }
                Start-Sleep -Seconds 1
            }
        } finally { [WatteverPawnIO]::Close() }
    } catch {
        # Report why there is no value, then retry: PawnIO may still be starting after boot.
        try { Write-Reading @{ celsius = $null; sensor = $sensor; error = $_.Exception.Message } } catch {}
        Start-Sleep -Seconds 30
    }
}
