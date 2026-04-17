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
$mainSpecRoot = $repoRoot
$mainPath = Join-Path $repoRoot "main.py"
$recoveryScriptPath = Join-Path $repoRoot "scripts/mouse_unlock_tool.py"
$watchdogScriptPath = Join-Path $repoRoot "scripts/recovery_watchdog.py"
$updaterScriptPath = Join-Path $repoRoot "scripts/update_installer.py"
$exportScriptPath = Join-Path $repoRoot "scripts/export_app_icon.py"
$recoveryBuildName = "MouseUnlockRecovery"
$watchdogExeName = "MultiScreenPassRecoveryWatchdog"
$updaterExeName = "MultiScreenPassUpdater"
$mainSpecFile = Join-Path $mainSpecRoot "MultiScreenPass.spec"

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

function Reset-PyInstallerState {
    param(
        [string]$WorkPath,
        [string]$SpecPath,
        [string]$SpecFile
    )

    if ($WorkPath -and (Test-Path $WorkPath)) {
        Remove-Item -Recurse -Force $WorkPath
    }
    if ($SpecPath -and (Test-Path $SpecPath)) {
        Remove-Item -Recurse -Force $SpecPath
    }
    if ($SpecFile -and (Test-Path $SpecFile)) {
        Remove-Item -Force $SpecFile
    }
}

Push-Location $repoRoot
try {
    Write-Host "[build] export app icon"
    python $exportScriptPath

    if (-not (Test-Path $iconPath)) {
        throw "$iconRelativePath was not created"
    }

    Write-Host "[build] PyInstaller onefile windowed build"
    Reset-PyInstallerState -WorkPath $workPath -SpecFile $mainSpecFile
    python -m PyInstaller --noconfirm --clean --onefile --windowed $mainPath --name MultiScreenPass --icon $iconPath --hidden-import certifi --collect-data certifi --distpath $distPath --workpath $workPath --specpath $mainSpecRoot

    $exePath = Join-Path $distPath "MultiScreenPass.exe"
    if (-not (Test-Path $exePath)) {
        throw "build/dist/MultiScreenPass.exe was not created"
    }

    Write-Host "[build] PyInstaller recovery build"
    Reset-PyInstallerState -WorkPath $recoveryWorkPath -SpecPath $recoverySpecPath
    python -m PyInstaller --noconfirm --clean --onefile --windowed $recoveryScriptPath --name $recoveryBuildName --icon $iconPath --distpath $distPath --workpath $recoveryWorkPath --specpath $recoverySpecPath

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
    Reset-PyInstallerState -WorkPath $watchdogWorkPath -SpecPath $watchdogSpecPath
    python -m PyInstaller --noconfirm --clean --onefile --windowed $watchdogScriptPath --name $watchdogExeName --icon $iconPath --distpath $distPath --workpath $watchdogWorkPath --specpath $watchdogSpecPath

    $watchdogExePath = Join-Path $distPath "$watchdogExeName.exe"
    if (-not (Test-Path $watchdogExePath)) {
        throw "build/dist/$watchdogExeName.exe was not created"
    }

    Write-Host "[build] PyInstaller updater build"
    Reset-PyInstallerState -WorkPath $updaterWorkPath -SpecPath $updaterSpecPath
    python -m PyInstaller --noconfirm --clean --onefile --windowed $updaterScriptPath --name $updaterExeName --icon $iconPath --hidden-import certifi --collect-data certifi --distpath $distPath --workpath $updaterWorkPath --specpath $updaterSpecPath

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
    if (Test-Path $mainSpecFile) {
        Remove-Item -Force $mainSpecFile
    }
    Pop-Location
}
