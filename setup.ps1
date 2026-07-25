# Bootstrap + launcher.
# Finds or installs Python and FFmpeg, installs the Python packages, starts the app.
# Everything it downloads lands in the local "runtime" folder and touches nothing
# else on this computer. Delete that folder to undo it all.

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root    = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Runtime = Join-Path $Root "runtime"
$PyDir   = Join-Path $Runtime "python"
$FfDir   = Join-Path $Runtime "ffmpeg"
$Marker  = Join-Path $Runtime "packages-installed.txt"

$PY_VERSION = "3.12.8"
$PY_URL     = "https://www.python.org/ftp/python/$PY_VERSION/python-$PY_VERSION-embed-amd64.zip"
$PIP_URL    = "https://bootstrap.pypa.io/get-pip.py"
$FF_URL     = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

function Say  ($m) { Write-Host "  $m" }
function Step ($m) { Write-Host ""; Write-Host "  $m" -ForegroundColor Cyan }
function Good ($m) { Write-Host "  $m" -ForegroundColor Green }
function Warn ($m) { Write-Host "  $m" -ForegroundColor Yellow }
function Fail ($m) { Write-Host ""; Write-Host "  $m" -ForegroundColor Red }

Write-Host ""
Write-Host "  AI YouTube Shorts Generator" -ForegroundColor Cyan
Write-Host "  ---------------------------"

New-Item -ItemType Directory -Force -Path $Runtime | Out-Null

# ---------------------------------------------------------------
# Python
# ---------------------------------------------------------------
function Test-Python($exe) {
    if (-not $exe) { return $false }
    try {
        $out = & $exe -c "import sys; print(sys.version_info[0]*100+sys.version_info[1])" 2>$null
        return ($LASTEXITCODE -eq 0 -and [int]$out -ge 310)
    } catch { return $false }
}

function Find-Python {
    # Our own copy first, so a broken system install cannot get in the way.
    $local = Join-Path $PyDir "python.exe"
    if ((Test-Path $local) -and (Test-Python $local)) { return $local }

    foreach ($candidate in @("python", "python3")) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        # The Microsoft Store stub is also named python.exe but only opens the Store.
        if ($found -and $found.Source -notlike "*WindowsApps*" -and (Test-Python $found.Source)) {
            return $found.Source
        }
    }

    # The py launcher is very common and does not put python.exe on PATH.
    if (Get-Command "py" -ErrorAction SilentlyContinue) {
        try {
            $exe = & py -3 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and (Test-Python $exe)) { return $exe }
        } catch { }
    }
    return $null
}

function Install-Python {
    Step "Python was not found. Downloading it into this folder (about 15 MB)."
    $zip = Join-Path $Runtime "python.zip"
    Invoke-WebRequest -Uri $PY_URL -OutFile $zip
    New-Item -ItemType Directory -Force -Path $PyDir | Out-Null
    Expand-Archive -Path $zip -DestinationPath $PyDir -Force
    Remove-Item $zip -Force

    # The embeddable build ships with imports switched off. Turning site back on
    # is what lets pip and installed packages work.
    Get-ChildItem -Path $PyDir -Filter "*._pth" | ForEach-Object {
        (Get-Content $_.FullName) -replace '^#\s*import\s+site', 'import site' |
            Set-Content $_.FullName -Encoding ASCII
    }

    Say "Adding pip..."
    $getpip = Join-Path $Runtime "get-pip.py"
    Invoke-WebRequest -Uri $PIP_URL -OutFile $getpip
    & (Join-Path $PyDir "python.exe") $getpip --no-warn-script-location | Out-Null
    Remove-Item $getpip -Force

    $exe = Join-Path $PyDir "python.exe"
    if (-not (Test-Python $exe)) { throw "The downloaded Python did not start." }
    Good "Python is ready."
    return $exe
}

$python = Find-Python
if ($python) {
    Good "Found Python: $python"
} else {
    $python = Install-Python
}

# ---------------------------------------------------------------
# FFmpeg
# ---------------------------------------------------------------
function Find-Ffmpeg {
    $local = Join-Path $FfDir "bin\ffmpeg.exe"
    if (Test-Path $local) { return (Join-Path $FfDir "bin") }
    $found = Get-Command "ffmpeg" -ErrorAction SilentlyContinue
    if ($found) { return (Split-Path -Parent $found.Source) }
    return $null
}

function Install-Ffmpeg {
    Step "FFmpeg was not found. Downloading it into this folder (about 80 MB, this is the slow part)."
    $zip = Join-Path $Runtime "ffmpeg.zip"
    $tmp = Join-Path $Runtime "ffmpeg_tmp"
    Invoke-WebRequest -Uri $FF_URL -OutFile $zip
    Say "Unpacking..."
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    Remove-Item $zip -Force

    # The archive holds one versioned folder whose name changes every release.
    $inner = Get-ChildItem -Path $tmp -Directory | Select-Object -First 1
    if (-not $inner) { throw "The FFmpeg archive did not look the way it usually does." }
    if (Test-Path $FfDir) { Remove-Item $FfDir -Recurse -Force }
    Move-Item -Path $inner.FullName -Destination $FfDir
    Remove-Item $tmp -Recurse -Force

    $bin = Join-Path $FfDir "bin"
    if (-not (Test-Path (Join-Path $bin "ffmpeg.exe"))) { throw "ffmpeg.exe is missing after unpacking." }
    Good "FFmpeg is ready."
    return $bin
}

$ffbin = Find-Ffmpeg
if ($ffbin) {
    Good "Found FFmpeg: $ffbin"
} else {
    try {
        $ffbin = Install-Ffmpeg
    } catch {
        Warn "Could not install FFmpeg automatically: $($_.Exception.Message)"
        Warn "Videos will not render until it is installed. See docs\SETUP.md."
    }
}
if ($ffbin) { $env:PATH = "$ffbin;$env:PATH" }

# ---------------------------------------------------------------
# Python packages
# ---------------------------------------------------------------
$reqHash = (Get-FileHash (Join-Path $Root "requirements.txt") -Algorithm MD5).Hash
$needInstall = $true
if (Test-Path $Marker) {
    $recorded = Get-Content $Marker -Raw -ErrorAction SilentlyContinue
    if ($recorded -and $recorded.Trim() -eq $reqHash) { $needInstall = $false }
}

if ($needInstall) {
    Step "Installing the Python packages. First run only, this takes a minute."
    & $python -m pip install --upgrade pip --quiet --no-warn-script-location 2>$null | Out-Null
    & $python -m pip install -r (Join-Path $Root "requirements.txt") --quiet --no-warn-script-location
    if ($LASTEXITCODE -ne 0) {
        Fail "The packages did not install. Check your internet connection and run this again."
        Read-Host "  Press Enter to close"
        exit 1
    }
    Set-Content -Path $Marker -Value $reqHash -Encoding ASCII
    Good "Packages installed."
} else {
    Good "Packages already installed."
}

# ---------------------------------------------------------------
# Go
# ---------------------------------------------------------------
Step "Starting. The panel opens in your browser in a moment."
Say "Keep this window open. Closing it stops the app."
Write-Host ""

& $python (Join-Path $Root "app.py")

Write-Host ""
Read-Host "  The app has stopped. Press Enter to close"
