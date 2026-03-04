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
# Copyright (C) 2026 MOLO17. All rights reserved.

# Gluesync platform official run script
# Script version: 1.1
# 
# This script is used to run the Gluesync docker compose file.
# It will search for the docker compose file in the current directory or in the parent directory.
#
# Usage:
#   ./run.ps1
#

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

# Ensure Docker is available
if (-not (Get-Command docker-compose -ErrorAction SilentlyContinue)) {
    Write-Error "'docker-compose' command not found. Please install Docker Compose."
}

$env:BASE_PATH = $ComposeDir
Write-Host "Using BASE_PATH=$($env:BASE_PATH)"

Write-Host "Running: docker-compose -f $ComposeFile pull"
Write-Host "Please note that Windows container image pulls may appear stuck at 99% for several minutes. This is normal - do not restart the process; just wait for it to finish." -ForegroundColor Cyan
docker-compose -f "$ComposeFile" pull

Write-Host "Running: docker-compose -f $ComposeFile up -d --remove-orphans"
docker-compose -f "$ComposeFile" up -d --remove-orphans

Write-Host "Gluesync stack started (detached)."
