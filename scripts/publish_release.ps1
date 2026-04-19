param(
    [string]$Version = "",
    [string]$NotesFile = "",
    [switch]$SkipExeBuild,
    [switch]$SkipPush,
    [switch]$SkipCreateRelease
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path
$buildInstallerScript = Join-Path $scriptDir "build_windows_installer.ps1"

function Get-AppVersion {
    param([string]$RepoRoot)

    if ($Version) {
        return $Version
    }

    $identityPath = Join-Path $RepoRoot "app\meta\identity.py"
    if (Test-Path $identityPath) {
        foreach ($line in Get-Content $identityPath -Encoding utf8) {
            if ($line -match '^\s*APP_VERSION\s*=\s*"([^"]+)"') {
                return $matches[1]
            }
        }
    }

    throw "APP_VERSION could not be resolved. Pass -Version explicitly."
}

function Resolve-NotesFile {
    param(
        [string]$RepoRoot,
        [string]$AppVersion,
        [string]$ExplicitPath
    )

    if ($ExplicitPath) {
        $resolved = Resolve-Path -LiteralPath $ExplicitPath -ErrorAction Stop
        return $resolved.Path
    }

    return (Join-Path $RepoRoot "build\release-notes-$AppVersion.md")
}

function Ensure-ReleaseNotesExist {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Release notes file was not found: $Path"
    }
}

function Ensure-Command {
    param([string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) {
        throw "Required command was not found: $Name"
    }
    return $command.Source
}

function Git-TagExists {
    param(
        [string]$RepoRoot,
        [string]$Tag
    )

    & git -C $RepoRoot rev-parse -q --verify "refs/tags/$Tag" *> $null
    return $LASTEXITCODE -eq 0
}

function Git-RemoteTagExists {
    param(
        [string]$RepoRoot,
        [string]$Tag
    )

    $output = & git -C $RepoRoot ls-remote --tags origin "refs/tags/$Tag" 2>$null
    return -not [string]::IsNullOrWhiteSpace(($output | Out-String))
}

function Release-Exists {
    param([string]$Tag)

    & gh release view $Tag --json tagName *> $null
    return $LASTEXITCODE -eq 0
}

Push-Location $repoRoot
try {
    Ensure-Command -Name "git" | Out-Null
    Ensure-Command -Name "gh" | Out-Null

    $appVersion = Get-AppVersion -RepoRoot $repoRoot
    $notesPath = Resolve-NotesFile -RepoRoot $repoRoot -AppVersion $appVersion -ExplicitPath $NotesFile
    Ensure-ReleaseNotesExist -Path $notesPath

    Write-Host "[release] version -> $appVersion"
    Write-Host "[release] notes   -> $notesPath"

    if (-not $SkipExeBuild) {
        Write-Host "[release] building installer"
        powershell -ExecutionPolicy Bypass -File $buildInstallerScript -Version $appVersion
    }

    $installerPath = Join-Path $repoRoot "build\installer\MultiScreenPass-Setup-$appVersion.exe"
    if (-not (Test-Path -LiteralPath $installerPath)) {
        throw "Installer was not found: $installerPath"
    }

    if (-not (Git-TagExists -RepoRoot $repoRoot -Tag $appVersion)) {
        Write-Host "[release] creating git tag $appVersion"
        & git -C $repoRoot tag $appVersion
    }
    else {
        Write-Host "[release] tag already exists locally -> $appVersion"
    }

    if (-not $SkipPush) {
        Write-Host "[release] pushing main"
        & git -C $repoRoot push origin main
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to push main."
        }

        if (-not (Git-RemoteTagExists -RepoRoot $repoRoot -Tag $appVersion)) {
            Write-Host "[release] pushing tag $appVersion"
            & git -C $repoRoot push origin $appVersion
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to push tag $appVersion."
            }
        }
        else {
            Write-Host "[release] tag already exists on origin -> $appVersion"
        }
    }

    if (-not $SkipCreateRelease) {
        if (Release-Exists -Tag $appVersion) {
            Write-Host "[release] GitHub release already exists -> $appVersion"
        }
        else {
            Write-Host "[release] creating GitHub release $appVersion"
            & gh release create $appVersion "$installerPath#MultiScreenPass-Setup-$appVersion.exe" --title $appVersion --notes-file $notesPath
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to create GitHub release $appVersion."
            }
        }
    }

    Write-Host "[release] complete -> $appVersion"
    Write-Host "[release] installer -> $installerPath"
}
finally {
    Pop-Location
}
