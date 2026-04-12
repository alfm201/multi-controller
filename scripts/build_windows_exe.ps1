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
$mainPath = Join-Path $repoRoot "main.py"
$recoveryScriptPath = Join-Path $repoRoot "scripts/mouse_unlock_tool.py"
$exportScriptPath = Join-Path $repoRoot "scripts/export_app_icon.py"
$recoveryExeName = "MouseUnlockRecovery"

Push-Location $repoRoot
try {
    Write-Host "[build] export app icon"
    python $exportScriptPath

    if (-not (Test-Path $iconPath)) {
        throw "$iconRelativePath was not created"
    }

    Write-Host "[build] PyInstaller onefile windowed build"
    python -m PyInstaller --noconfirm --onefile --windowed $mainPath --name MultiScreenPass --icon $iconPath --distpath $distPath --workpath $workPath --specpath $specPath

    $exePath = Join-Path $distPath "MultiScreenPass.exe"
    if (-not (Test-Path $exePath)) {
        throw "build/dist/MultiScreenPass.exe was not created"
    }

    Write-Host "[build] PyInstaller recovery build"
    python -m PyInstaller --noconfirm --onefile --windowed $recoveryScriptPath --name $recoveryExeName --icon $iconPath --distpath $distPath --workpath $recoveryWorkPath --specpath $recoverySpecPath

    $recoveryExePath = Join-Path $distPath "$recoveryExeName.exe"
    if (-not (Test-Path $recoveryExePath)) {
        throw "build/dist/$recoveryExeName.exe was not created"
    }

    Write-Host "[build] complete -> $exePath"
    Write-Host "[build] complete -> $recoveryExePath"
}
finally {
    Pop-Location
}
