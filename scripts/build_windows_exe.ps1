$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path
$iconRelativePath = "assets/multi-screen-pass.ico"
$iconPath = Join-Path $repoRoot $iconRelativePath
$distPath = Join-Path $repoRoot "build/dist"
$workPath = Join-Path $repoRoot "build/pyinstaller"
$specPath = Join-Path $repoRoot "build/spec"
$recoveryWorkPath = Join-Path $repoRoot "build/pyinstaller-recovery"
$recoverySpecPath = Join-Path $repoRoot "build/spec-recovery"
$watchdogWorkPath = Join-Path $repoRoot "build/pyinstaller-watchdog"
$watchdogSpecPath = Join-Path $repoRoot "build/spec-watchdog"
$updaterWorkPath = Join-Path $repoRoot "build/pyinstaller-updater"
$updaterSpecPath = Join-Path $repoRoot "build/spec-updater"
$mainPath = Join-Path $repoRoot "main.py"
$recoveryScriptPath = Join-Path $repoRoot "scripts/mouse_unlock_tool.py"
$watchdogScriptPath = Join-Path $repoRoot "scripts/recovery_watchdog.py"
$updaterScriptPath = Join-Path $repoRoot "scripts/update_installer.py"
$exportScriptPath = Join-Path $repoRoot "scripts/export_app_icon.py"
$recoveryBuildName = "MouseUnlockRecovery"
$watchdogExeName = "MultiScreenPassRecoveryWatchdog"
$updaterExeName = "MultiScreenPassUpdater"

function Get-RecoveryOutputName {
    return (
        -join @(
            [char]0x5B, [char]0xC7A5, [char]0xC560, [char]0xBCF5, [char]0xAD6C, [char]0xC6A9, [char]0x5D,
            [char]0x20,
            [char]0xB9C8, [char]0xC6B0, [char]0xC2A4,
            [char]0x20,
            [char]0xC7A0, [char]0xAE08,
            [char]0x20,
            [char]0xD574, [char]0xC81C,
            ".exe"
        )
    )
}

$recoveryOutputName = Get-RecoveryOutputName

Push-Location $repoRoot
try {
    Write-Host "[build] export app icon"
    python $exportScriptPath

    if (-not (Test-Path $iconPath)) {
        throw "$iconRelativePath was not created"
    }

    Write-Host "[build] PyInstaller onefile windowed build"
    python -m PyInstaller --noconfirm --onefile --windowed $mainPath --name MultiScreenPass --icon $iconPath --hidden-import certifi --collect-data certifi --distpath $distPath --workpath $workPath --specpath $specPath

    $exePath = Join-Path $distPath "MultiScreenPass.exe"
    if (-not (Test-Path $exePath)) {
        throw "build/dist/MultiScreenPass.exe was not created"
    }

    Write-Host "[build] PyInstaller recovery build"
    python -m PyInstaller --noconfirm --onefile --windowed $recoveryScriptPath --name $recoveryBuildName --icon $iconPath --distpath $distPath --workpath $recoveryWorkPath --specpath $recoverySpecPath

    $recoveryBuiltExePath = Join-Path $distPath "$recoveryBuildName.exe"
    if (-not (Test-Path $recoveryBuiltExePath)) {
        throw "build/dist/$recoveryBuildName.exe was not created"
    }
    $recoveryExePath = Join-Path $distPath $recoveryOutputName
    if (Test-Path -LiteralPath $recoveryExePath) {
        Remove-Item -LiteralPath $recoveryExePath -Force
    }
    [System.IO.File]::Move($recoveryBuiltExePath, $recoveryExePath)

    Write-Host "[build] PyInstaller watchdog build"
    python -m PyInstaller --noconfirm --onefile --windowed $watchdogScriptPath --name $watchdogExeName --icon $iconPath --distpath $distPath --workpath $watchdogWorkPath --specpath $watchdogSpecPath

    $watchdogExePath = Join-Path $distPath "$watchdogExeName.exe"
    if (-not (Test-Path $watchdogExePath)) {
        throw "build/dist/$watchdogExeName.exe was not created"
    }

    Write-Host "[build] PyInstaller updater build"
    python -m PyInstaller --noconfirm --onefile --windowed $updaterScriptPath --name $updaterExeName --icon $iconPath --hidden-import certifi --collect-data certifi --distpath $distPath --workpath $updaterWorkPath --specpath $updaterSpecPath

    $updaterExePath = Join-Path $distPath "$updaterExeName.exe"
    if (-not (Test-Path $updaterExePath)) {
        throw "build/dist/$updaterExeName.exe was not created"
    }

    Write-Host "[build] complete -> $exePath"
    Write-Host "[build] complete -> $recoveryExePath"
    Write-Host "[build] complete -> $watchdogExePath"
    Write-Host "[build] complete -> $updaterExePath"
}
finally {
    Pop-Location
}
