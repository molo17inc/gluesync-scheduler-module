# This file is part of Gluesync.
#
# Gluesync is dual-licensed under the following licenses:
#
# 1. GNU General Public License (GPL) Version 3
#    You may use, modify, and distribute this software under the terms of the GPL v3.
#    See the LICENSE-GPL file or <http://www.gnu.org/licenses/gpl-3.0.html> for details.
#    This option is available at no cost, but any derivative works must also be licensed under GPL v3.
#
# 2. MOLO17 Commercial License
#    Alternatively, you may use this software under the MOLO17 Commercial License,
#    which includes a warranty and permits proprietary use. Contact MOLO17 at info@molo17.com
#    for licensing terms and conditions.
#
# You must choose one of these licenses to use this software. Using this software implies
# acceptance of one of these licenses. See the accompanying LICENSE files or contact
# MOLO17 for more information.
#
# Copyright (C) 2025 MOLO17. All rights reserved.

$ErrorActionPreference = 'Stop'

# Resolve script directory (works in PS5+ and pwsh)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# After assembly, this script will sit inside the 'gluesync-docker' folder.
# The docker compose file may be in the same folder or in the parent directory.
# Search in both locations and use the first one found.

# Define search directories: script directory first, then parent directory
$SearchDirs = @($ScriptDir, (Split-Path -Parent $ScriptDir))

# Function to find compose file in a directory
function Find-ComposeFile {
    param([string]$Directory)
    
    $YamlFile = Join-Path $Directory 'docker-compose.yaml'
    $YmlFile = Join-Path $Directory 'docker-compose.yml'
    
    if (Test-Path -Path $YamlFile) {
        return $YamlFile
    }
    elseif (Test-Path -Path $YmlFile) {
        return $YmlFile
    }
    else {
        return $null
    }
}

# Search for compose file in order of preference
$ComposeFile = $null
$ComposeDir = $null
foreach ($SearchDir in $SearchDirs) {
    $FoundFile = Find-ComposeFile -Directory $SearchDir
    if ($FoundFile) {
        $ComposeFile = $FoundFile
        $ComposeDir = $SearchDir
        break
    }
}

if (-not $ComposeFile) {
    $DirsList = $SearchDirs -join ', '
    Write-Error "docker compose file not found (.yaml or .yml) in any of these directories: $DirsList"
    exit 1
}

$SetupCompletedPath = Join-Path $ComposeDir ".setup_completed"

Write-Host "Granting Docker Engine access to Gluesync installation directory..." -ForegroundColor Cyan
$dockerAccount = "NT SERVICE\docker"
try {
    # (OI) Object Inherit - Files will inherit the permission
    # (CI) Container Inherit - Folders will inherit the permission
    # (F) Full Control
    # /T Recursive
    icacls $ComposeDir /grant "${dockerAccount}:(OI)(CI)(F)" /T | Out-Null
    Write-Host "Permissions updated successfully." -ForegroundColor Green
}
catch {
    Write-Warning "Failed to set permissions: $($_.Exception.Message). You might need to run this script as Administrator."
}

# Ensure Docker is available
if (-not (Get-Command docker-compose -ErrorAction SilentlyContinue)) {
    Write-Error "'docker-compose' command not found. Please install Docker Compose."
}

# --- PROXY DETECTION SECTION ---

function Get-HostIPAddress {
    $ip = Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object {
            $_.IPAddress -notlike "127.*" -and
            $_.IPAddress -notlike "169.254.*" -and
            $_.IPAddress -notlike "0.0.0.0"
        } |
        Select-Object -First 1 -ExpandProperty IPAddress

    return $ip
}

$EnvFilePath = Join-Path $ComposeDir ".env"

# Ensure .env exists
if (-not (Test-Path -Path $EnvFilePath)) {
    Write-Host ".env does not exist, creating..." -ForegroundColor Yellow
    New-Item -Path $EnvFilePath -ItemType File -Force | Out-Null
    $ExistingContent = ""
} else {
    Write-Host ".env already exists, reading content." -ForegroundColor Green
    try {
        $ExistingContent = Get-Content -Path $EnvFilePath -Raw -ErrorAction Stop
    } catch {
        Write-Warning "Failed to read .env; treating as empty."
        $ExistingContent = ""
    }
}

Write-Host "Detecting system proxy settings..." -ForegroundColor Cyan

$Proxy = [System.Net.WebRequest]::GetSystemWebProxy()
$ProxyUri = $Proxy.GetProxy("https://api.backoffice.molo17.com/agent/core-hub/version")

if ($ProxyUri -and $ProxyUri.Host -and $ProxyUri.Host -ne "api.backoffice.molo17.com") {

    Write-Host "System proxy detected: $($ProxyUri.ToString())" -ForegroundColor Yellow

    $OriginalHost = $ProxyUri.Host
    $Port = $ProxyUri.Port

    if (-not $Port -or $Port -eq -1) {
        $Port = 8080
    }

    # Decide whether to replace host
    $NeedsReplacement = $OriginalHost -eq "127.0.0.1" -or $OriginalHost -eq "localhost"

    if ($NeedsReplacement) {
        $HostIP = Get-HostIPAddress

        if (-not $HostIP) {
            Write-Warning "Could not determine host IP. Falling back to host.docker.internal"
            $HostIP = "host.docker.internal"
        }

        Write-Host "Replacing proxy host '$OriginalHost' with '$HostIP'" -ForegroundColor Yellow
        $FinalHost = $HostIP
    } else {
        Write-Host "Using original proxy host '$OriginalHost'" -ForegroundColor Yellow
        $FinalHost = $OriginalHost
    }

    $ProxyString = "http://$FinalHost`:$Port"

    Write-Host "Using container-compatible proxy: $ProxyString" -ForegroundColor Yellow

    $HasHttp = $ExistingContent -match "PROXY_HTTP="
    $HasHttps = $ExistingContent -match "PROXY_HTTPS="

    if (-not $HasHttp) {
        Write-Host "Adding PROXY_HTTP to .env" -ForegroundColor Green
        "PROXY_HTTP=$ProxyString" | Out-File -FilePath $EnvFilePath -Encoding utf8 -Append
    } else {
        Write-Host "PROXY_HTTP already present, skipping." -ForegroundColor Green
    }

    if (-not $HasHttps) {
        Write-Host "Adding PROXY_HTTPS to .env" -ForegroundColor Green
        "PROXY_HTTPS=$ProxyString" | Out-File -FilePath $EnvFilePath -Encoding utf8 -Append
    } else {
        Write-Host "PROXY_HTTPS already present, skipping." -ForegroundColor Green
    }

} else {
    Write-Host "No system proxy detected." -ForegroundColor Green
}

# -------------------------------

$env:BASE_PATH = $ComposeDir
Write-Host "Using BASE_PATH=$($env:BASE_PATH)"

if (Test-Path -Path $SetupCompletedPath) {
    Write-Host ".setup_completed already exists, skipping docker-compose pull." -ForegroundColor Green
} else {
    Write-Host "Running: docker-compose -f $ComposeFile pull"
    Write-Host "Please note that Windows container image pulls may appear stuck at 99% for several minutes. This is normal - do not restart the process; just wait for it to finish." -ForegroundColor Cyan
    try {
        docker-compose -f "$ComposeFile" pull
    }
    catch {
        Write-Warning "docker-compose pull failed (possibly due to offline environment). Continuing with locally available images."
    }
}

Write-Host "Running: docker-compose -f $ComposeFile up -d --remove-orphans"
docker-compose -f "$ComposeFile" up -d --remove-orphans

if (-not (Test-Path -Path $SetupCompletedPath)) {
    New-Item -Path $SetupCompletedPath -ItemType File -Force | Out-Null
    Write-Host ".setup_completed created at $SetupCompletedPath" -ForegroundColor Green
}

Write-Host "Gluesync stack started (detached)."
