param(
    [string]$Version = "",
    [switch]$SkipExeBuild
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path
$buildExeScript = Join-Path $scriptDir "build_windows_exe.ps1"
$innoScript = Join-Path $repoRoot "installer\MultiScreenPass.iss"
$iconPath = Join-Path $repoRoot "assets\multi-screen-pass.ico"
$distDir = Join-Path $repoRoot "build\dist"
$outputDir = Join-Path $repoRoot "build\installer"

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

function Get-UpdaterExeName {
    return "MultiScreenPassUpdater"
}

function Get-IdentityVersion {
    param([string]$RepoRoot)

    $identityPath = Join-Path $RepoRoot "app\meta\identity.py"
    if (Test-Path $identityPath) {
        foreach ($line in Get-Content $identityPath -Encoding utf8) {
            if ($line -match '^\s*APP_VERSION\s*=\s*"([^"]+)"') {
                return $matches[1]
            }
        }
    }

    return ""
}

function Get-AppVersion {
    param([string]$RepoRoot)

    $identityVersion = Get-IdentityVersion -RepoRoot $RepoRoot

    if ($Version) {
        if ($identityVersion -and ($Version -ne $identityVersion)) {
            throw "Requested installer version '$Version' does not match app/meta/identity.py APP_VERSION '$identityVersion'. Update app/meta/identity.py before building."
        }
        return $Version
    }

    if ($identityVersion) {
        return $identityVersion
    }

    $tagOutput = & git -C $RepoRoot tag --sort=-creatordate 2>$null
    if ($LASTEXITCODE -eq 0 -and $tagOutput) {
        return ($tagOutput | Select-Object -First 1).Trim()
    }

    return "0.0.0"
}

function Find-Iscc {
    $candidates = @(
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe",
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    throw "ISCC.exe was not found. Install Inno Setup 6 first."
}

Push-Location $repoRoot
try {
    $appVersion = Get-AppVersion -RepoRoot $repoRoot
    $isccPath = Find-Iscc
    $recoveryOutputName = Get-RecoveryOutputName
    $updaterExeName = Get-UpdaterExeName

    if (-not $SkipExeBuild) {
        Write-Host "[build] build application executables"
        powershell -ExecutionPolicy Bypass -File $buildExeScript
    }

    foreach ($required in @(
        (Join-Path $distDir "MultiScreenPass.exe"),
        (Join-Path $distDir $recoveryOutputName),
        (Join-Path $distDir "MultiScreenPassRecoveryWatchdog.exe"),
        (Join-Path $distDir "$updaterExeName.exe"),
        $iconPath,
        $innoScript
    )) {
        if (-not (Test-Path -LiteralPath $required)) {
            throw "Required file was not found: $required"
        }
    }

    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null

    Write-Host "[build] Inno Setup installer"
    & $isccPath `
        "/DMyAppVersion=$appVersion" `
        "/DMySourceRoot=$repoRoot" `
        "/DMyDistDir=$distDir" `
        "/DMyOutputDir=$outputDir" `
        "/DMyIconFile=$iconPath" `
        "/DMyRecoveryExeName=$recoveryOutputName" `
        "/DMyUpdaterExeName=$updaterExeName" `
        $innoScript

    $installerPath = Join-Path $outputDir "MultiScreenPass-Setup-$appVersion.exe"
    if (-not (Test-Path $installerPath)) {
        throw "Installer was not created: $installerPath"
    }

    Write-Host "[build] complete -> $installerPath"
}
finally {
    Pop-Location
}
