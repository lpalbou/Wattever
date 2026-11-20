# Wattever optional CPU temperature setup (Windows). Run elevated by Wattever
# after the user clicks "Enable CPU temperature" and approves the UAC prompt.
#
#   setup.ps1 -Action install   -Source <folder with the verified downloads>
#   setup.ps1 -Action uninstall
#
# install:   installs PawnIO 2.2.0 if needed, copies the sensor into
#            %ProgramFiles%\Wattever\CpuSensor, re-verifies every file there,
#            creates %ProgramData%\Wattever\CpuSensor (SYSTEM/Administrators write,
#            Users read) and registers the "Wattever CPU temperature" task (SYSTEM, at startup).
# uninstall: removes the task and both folders, and uninstalls PawnIO only when
#            this setup installed it.
param(
    [Parameter(Mandatory = $true)][ValidateSet('install', 'uninstall')][string]$Action,
    [string]$Source = '',
    [string]$Log = ''
)
$ErrorActionPreference = 'Stop'
$TaskName = 'Wattever CPU temperature'
$Install = Join-Path $env:ProgramFiles 'Wattever\CpuSensor'
$Data = Join-Path $env:ProgramData 'Wattever\CpuSensor'
$PawnKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO'
$Marker = Join-Path $Install 'pawnio-installed-by-wattever'
# Must match wattever/cpu_sensor_setup.py (tests check both).
$Expected = @{
    'PawnIO_setup.exe' = '1f519a22e47187f70a1379a48ca604981c4fcf694f4e65b734aaa74a9fba3032'
    'IntelMSR.bin'     = 'd6ed85d65ab17a22f813ef98207d6d537155ee2ded5976a21cb48413c9b92e5f'
    'AMDFamily17.bin'  = 'dae74615761b78bdf064dfb3e136252ddcc6fc727d88f14738d0e5800d427a91'
}

function Say([string]$text) {
    if ($Log) { Add-Content -LiteralPath $Log -Value $text -Encoding UTF8 }
}

function Remove-Sensor {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Say 'Removed the scheduled task.'
    }
    # The sensor's PowerShell process may outlive the task for a moment.
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
        Where-Object { $_.CommandLine -like '*Wattever\CpuSensor\cpu-sensor.ps1*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

try {
    if ($Action -eq 'uninstall') {
        $removeDriver = Test-Path -LiteralPath $Marker
        Remove-Sensor
        if ($removeDriver) {
            $uninstall = (Get-ItemProperty -LiteralPath $PawnKey -ErrorAction SilentlyContinue).UninstallString
            if ($uninstall) {
                $exe = if ($uninstall -match '^"([^"]+)"') { $Matches[1] } else { ($uninstall -split ' ')[0] }
                $p = Start-Process -FilePath $exe -ArgumentList '-uninstall', '-silent' -Wait -PassThru
                Say "PawnIO uninstaller exit code: $($p.ExitCode)"
            }
        } else { Say 'PawnIO was already installed before Wattever; it is left in place.' }
        foreach ($dir in @($Install, $Data)) {
            if (Test-Path -LiteralPath $dir) { Remove-Item -LiteralPath $dir -Recurse -Force }
        }
        $parent = Join-Path $env:ProgramFiles 'Wattever'
        if ((Test-Path -LiteralPath $parent) -and -not (Get-ChildItem -LiteralPath $parent -Force)) { Remove-Item -LiteralPath $parent }
        Say 'CPU temperature sensor removed.'
        exit 0
    }

    if (-not $Source -or -not (Test-Path -LiteralPath $Source)) { throw "Source folder not found: $Source" }
    Remove-Sensor
    New-Item -ItemType Directory -Force -Path $Install | Out-Null
    # Copy first, then verify the copies: only files in this admin-only folder are ever executed.
    foreach ($name in @('cpu-sensor.ps1', 'PawnIO_setup.exe', 'IntelMSR.bin', 'AMDFamily17.bin')) {
        Copy-Item -LiteralPath (Join-Path $Source $name) -Destination (Join-Path $Install $name) -Force
    }
    foreach ($name in $Expected.Keys) {
        $hash = (Get-FileHash -LiteralPath (Join-Path $Install $name) -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($hash -ne $Expected[$name]) { throw "$name failed verification (SHA-256 $hash)." }
    }
    $sensorHash = (Get-FileHash -LiteralPath (Join-Path $Install 'cpu-sensor.ps1') -Algorithm SHA256).Hash.ToLowerInvariant()
    $sourceHash = (Get-Content -LiteralPath (Join-Path $Source 'cpu-sensor.ps1.sha256') -Raw).Trim().ToLowerInvariant()
    if ($sensorHash -ne $sourceHash) { throw 'cpu-sensor.ps1 changed after Wattever verified it.' }
    Say 'All files verified in the Program Files folder.'

    $version = (Get-ItemProperty -LiteralPath $PawnKey -ErrorAction SilentlyContinue).DisplayVersion
    if (-not $version -or [version]($version -replace '[^0-9.].*$', '') -lt [version]'2.0') {
        $p = Start-Process -FilePath (Join-Path $Install 'PawnIO_setup.exe') -ArgumentList '-install', '-silent' -Wait -PassThru
        Say "PawnIO installer exit code: $($p.ExitCode)"
        if ($p.ExitCode -notin 0, 3010) { throw "The PawnIO installer failed with exit code $($p.ExitCode)." }
        if (-not $version) { Set-Content -LiteralPath $Marker -Value (Get-Date -Format o) }
        if ($p.ExitCode -eq 3010) { Say 'PawnIO asks for a restart before it can be used.' }
    } else { Say "PawnIO $version is already installed." }
    Remove-Item -LiteralPath (Join-Path $Install 'PawnIO_setup.exe') -Force

    # The reading folder: recreated so no one else owns it; SYSTEM and Administrators write, Users only read.
    if (Test-Path -LiteralPath $Data) { Remove-Item -LiteralPath $Data -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $Data | Out-Null
    & icacls.exe $Data /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "icacls failed with exit code $LASTEXITCODE." }

    $script = Join-Path $Install 'cpu-sensor.ps1'
    $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
        -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId 'S-1-5-18' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
        -Description 'Reads the CPU package temperature for Wattever through the PawnIO driver (read-only).' | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Say 'Registered and started the Wattever CPU temperature task.'

    $reading = Join-Path $Data 'cpu-temperature.json'
    for ($i = 0; $i -lt 40 -and -not (Test-Path -LiteralPath $reading); $i++) { Start-Sleep -Milliseconds 250 }
    if (Test-Path -LiteralPath $reading) { Say ('First reading: ' + (Get-Content -LiteralPath $reading -Raw)) }
    else { Say 'The sensor has not written a reading yet.' }
    exit 0
} catch {
    Say ('ERROR: ' + $_.Exception.Message)
    exit 1
}
