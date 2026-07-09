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

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$SearchDirs = @($ScriptDir, (Split-Path -Parent $ScriptDir))

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

$ComposeFile = $null
foreach ($SearchDir in $SearchDirs) {
    $FoundFile = Find-ComposeFile -Directory $SearchDir
    if ($FoundFile) {
        $ComposeFile = $FoundFile
        break
    }
}

if (-not $ComposeFile) {
    $DirsList = $SearchDirs -join ', '
    Write-Error "docker compose file not found (.yaml or .yml) in any of these directories: $DirsList"
    exit 1
}

if (-not (Get-Command docker-compose -ErrorAction SilentlyContinue)) {
    Write-Error "'docker-compose' command not found. Please install Docker Compose."
    exit 1
}

Write-Host "Running: docker-compose -f $ComposeFile down --remove-orphans"
docker-compose -f "$ComposeFile" down --remove-orphans

Write-Host "Gluesync stack stopped."
