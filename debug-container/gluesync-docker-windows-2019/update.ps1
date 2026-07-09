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

param(
    [switch]$y,
    [switch]$beta,
    [switch]$alpha
)

$ErrorActionPreference = 'Stop'
$ApiBaseUrl = "https://api.backoffice.molo17.com/images"

# Determine release channel
if ($alpha) {
    $ReleaseChannel = "ALPHA"
} elseif ($beta) {
    $ReleaseChannel = "BETA"
} else {
    $ReleaseChannel = "GA"
}

# Show banner if not skipping confirmation
if (-not $y) {
    Clear-Host
    Write-Host "Gluesync by MOLO17 - Update Utility"
    if ($ReleaseChannel -eq "GA") {
        Write-Host "This script updates all Gluesync container images to their latest GA (Generally Available) versions."
    } elseif ($ReleaseChannel -eq "BETA") {
        Write-Host "This script updates all Gluesync container images to their latest BETA versions."
    } else {
        Write-Host "This script updates all Gluesync container images to their latest ALPHA versions."
    }
    Write-Host ""
    Write-Host "The script will:"
    Write-Host "  - Locate your docker-compose file"
    Write-Host "  - Check API connectivity"
    Write-Host "  - Fetch latest versions for all images"
    Write-Host "  - Update the compose file with new versions"
    Write-Host "  - Restart the stack with updated images"
    Write-Host ""
}

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

function Get-ErrorBody {
    param($ErrorRecord)
    try {
        if ($ErrorRecord.ErrorDetails -and $ErrorRecord.ErrorDetails.Message) {
            return $ErrorRecord.ErrorDetails.Message | ConvertFrom-Json -ErrorAction Stop
        }
        $stream = $ErrorRecord.Exception.Response.GetResponseStream()
        $reader = New-Object System.IO.StreamReader($stream)
        return $reader.ReadToEnd() | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        return $null
    }
}

function Show-MaintenanceMode {
    param([string]$Message)
    $msg = if ($Message) { $Message } else { "OTA updates are currently undergoing maintenance" }
    Write-Host ""
    Write-Host "╔══════════════════════════════════════════════════════════════╗"
    Write-Host "║                                                              ║"
    Write-Host "║              Maintenance Mode Active                         ║"
    Write-Host "║                                                              ║"
    Write-Host "╚══════════════════════════════════════════════════════════════╝"
    Write-Host ""
    Write-Host $msg
    Write-Host ""
    Write-Host "Please try again later."
    exit 0
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
    Write-Error "docker compose file not found - yaml or yml - in any of these directories: $DirsList"
    exit 1
}

Write-Host "Found compose file: $ComposeFile"

# Test reachability
Write-Host "Testing API reachability..."
try {
    $testResponse = Invoke-RestMethod -Uri "$ApiBaseUrl/gluesync-core-hub" -Method Get -ErrorAction Stop
}
catch {
    $statusCode = $_.Exception.Response.StatusCode.Value__
    if ($statusCode -eq 503) {
        $errorBody = Get-ErrorBody $_
        if ($errorBody -and $errorBody.code -eq "MAINTENANCE_MODE") {
            Show-MaintenanceMode -Message $errorBody.message
        }
    }
    Write-Error "Cannot reach API endpoint $ApiBaseUrl"
    exit 1
}
try {
    $agentProbe = Invoke-RestMethod -Uri "https://api.backoffice.molo17.com/agent/core-hub/version" -Method Get -ErrorAction Stop
}
catch {
    $statusCode = $_.Exception.Response.StatusCode.Value__
    if ($statusCode -eq 503) {
        $errorBody = Get-ErrorBody $_
        if ($errorBody -and $errorBody.code -eq "MAINTENANCE_MODE") {
            Show-MaintenanceMode -Message $errorBody.message
        }
    }
}
Write-Host "API is reachable."

# Double confirmation for non-GA channels
if ($ReleaseChannel -ne "GA") {
    Write-Host "WARNING: switching to $ReleaseChannel channel"
    if ($ReleaseChannel -eq "BETA") {
        Write-Host "Beta versions may contain bugs and are not recommended for production use."
    } else {
        Write-Host "Alpha versions are experimental and may be unstable."
    }
    $response1 = Read-Host "Proceed with $ReleaseChannel channel? (yes/no)"
    if ($response1 -ne "yes") {
        Write-Host "Update cancelled."
        exit 0
    }
    $response2 = Read-Host "Confirm again - switch to $ReleaseChannel? (yes/no)"
    if ($response2 -ne "yes") {
        Write-Host "Update cancelled."
        exit 0
    }
    Write-Host ""
}

# Confirmation prompt
if (-not $y) {
    Write-Host "This will update all Gluesync images in $ComposeFile to their latest $ReleaseChannel versions."
    $response = Read-Host "Do you want to proceed? (yes/no)"
    if ($response -ne "yes") {
        Write-Host "Update cancelled."
        exit 0
    }
}

function Get-LatestVersion {
    param(
        [string]$ImageName,
        [string]$Channel
    )
    
    $url = "$ApiBaseUrl/$ImageName"
    try {
        $response = Invoke-RestMethod -Uri $url -Method Get -ErrorAction Stop
        
        # Determine which version field to use based on release channel
        if ($Channel -eq "BETA") {
            $version = $response.latestVersionBeta
        } elseif ($Channel -eq "ALPHA") {
            $version = $response.latestVersionAlpha
        } else {
            $version = $response.latestVersionGA
        }
        
        return @{
            Version = $version
            InternalName = $response.internalName
            ModuleType = $response.moduleType
            Success = $true
        }
    }
    catch {
        $statusCode = $_.Exception.Response.StatusCode.Value__
        if ($statusCode -eq 503) {
            $errorBody = Get-ErrorBody $_
            if ($errorBody -and $errorBody.code -eq "MAINTENANCE_MODE") {
                return @{ Success = $false; MaintenanceMode = $true; Message = $errorBody.message }
            }
        }
        if ($statusCode -eq 404) {
            return @{ Success = $false }
        }
        return @{ Success = $false }
    }
}

# Read compose file
$content = Get-Content -Path $ComposeFile -Raw

# Pattern to match image lines: image: molo17/something:version
$pattern = '(\s+image:\s+molo17/)([^:]+):([^\s]+)'
$matches = [regex]::Matches($content, $pattern)

$updates = @()
$updatedContent = $content
$agentCandidates = @()
$agentLatestVersions = @()
$coreCandidates = @()

$ThirdPartyAgentNames = @{
    "traefik"    = "traefik-win-2019"
    "prometheus" = "prometheus-win-2019"
    "grafana"    = "grafana-win-2019"
    "portainer"  = "portainer-win-2019"
}

function Get-ThirdPartyVersion {
    param([string]$AgentName, [string]$Channel)
    $url = "https://api.backoffice.molo17.com/agent/$AgentName/version"
    try {
        $response = Invoke-RestMethod -Uri $url -Method Get -ErrorAction Stop
        if ($Channel -eq "BETA") { $version = $response.latestVersionBeta }
        elseif ($Channel -eq "ALPHA") { $version = $response.latestVersionAlpha }
        else { $version = $response.latestVersionGA }
        return @{ Success = $true; Version = $version }
    }
    catch {
        $statusCode = $_.Exception.Response.StatusCode.Value__
        if ($statusCode -eq 503) {
            $errorBody = Get-ErrorBody $_
            if ($errorBody -and $errorBody.code -eq "MAINTENANCE_MODE") {
                return @{ Success = $false; MaintenanceMode = $true; Message = $errorBody.message }
            }
        }
        return @{ Success = $false }
    }
}

foreach ($match in $matches) {
    $prefix = $match.Groups[1].Value
    $imageName = $match.Groups[2].Value
    $currentVersion = $match.Groups[3].Value
    $winSuffix = "-win-nanoserver-ltsc2019"

    if ($ThirdPartyAgentNames.ContainsKey($imageName)) {
        $agentName = $ThirdPartyAgentNames[$imageName]
        $tpInfo = Get-ThirdPartyVersion -AgentName $agentName -Channel $ReleaseChannel
        if (-not $tpInfo.Success) {
            if ($tpInfo.MaintenanceMode) { Show-MaintenanceMode -Message $tpInfo.Message }
            Write-Host "Skipping $imageName (Third-party module, not managed by update script)"
            continue
        }
        $latestVersion = $tpInfo.Version
        if (-not $latestVersion) {
            Write-Host "Skipping $imageName (Third-party module, not managed by update script)"
            continue
        }
        $targetVersion = "$latestVersion$winSuffix"
        $winPattern = "^" + [regex]::Escape($latestVersion) + "(" + [regex]::Escape($winSuffix) + ")*$"
        if ($currentVersion -match $winPattern) {
            Write-Host "[OK] ${imageName}: already at latest $ReleaseChannel version $currentVersion (Third-party)"
        } else {
            $updatedContent = $updatedContent -replace ([regex]::Escape("$prefix${imageName}:$currentVersion")), "$prefix${imageName}:$targetVersion"
            $updates += @{ Name = $imageName; From = $currentVersion; To = $targetVersion }
            Write-Host "[UP] ${imageName}: $currentVersion -> $targetVersion ($ReleaseChannel, Third-party)"
        }
        continue
    }

    $versionInfo = Get-LatestVersion -ImageName $imageName -Channel $ReleaseChannel
    
    if (-not $versionInfo.Success) {
        if ($versionInfo.MaintenanceMode) {
            Show-MaintenanceMode -Message $versionInfo.Message
        }
        Write-Host "Skipping $imageName (not found in API or error)"
        continue
    }
    
    $latestVersion = $versionInfo.Version
    $internalName = $versionInfo.InternalName
    $moduleType = $versionInfo.ModuleType
    
    # Skip any remaining Third-party modules returned by the /images API
    if ($moduleType -eq "Third-party") {
        Write-Host "Skipping $imageName (Third-party module, not managed by update script)"
        continue
    }
    
    if (-not $latestVersion) {
        Write-Host "Skipping $imageName (no $ReleaseChannel version in API response)"
        continue
    }
    
    $targetVersion = "$latestVersion$winSuffix"
    $winPattern = "^" + [regex]::Escape($latestVersion) + "(" + [regex]::Escape($winSuffix) + ")*$"

    if ($currentVersion -match $winPattern) {
        Write-Host "[OK] ${internalName}: already at latest $ReleaseChannel version $currentVersion"
        continue
    }

    if ($moduleType -eq "Agent") {
        $agentCandidates += ,@($prefix, $imageName, $internalName, $currentVersion, $targetVersion)
        $agentLatestVersions += ,$latestVersion
        Write-Host "[PENDING] ${internalName}: $currentVersion -> $latestVersion ($ReleaseChannel) (waiting for all agents to match)" -ForegroundColor Yellow
    }
    elseif ($moduleType -eq "Module" -or $moduleType -eq "Core Hub" -or $imageName -eq "gluesync-core-hub") {
        # Core components (Module type includes conductor/chronos, Core Hub type): defer update until agents are ready
        $coreCandidates += ,@($prefix, $imageName, $internalName, $currentVersion, $targetVersion)
        Write-Host "[PENDING] ${internalName}: $currentVersion -> $latestVersion ($ReleaseChannel) (core component, syncing with agents)" -ForegroundColor Yellow
    }
    else {
        # Replace the version immediately for other modules
        $oldLine = "$prefix${imageName}:$currentVersion"
        $newLine = "$prefix${imageName}:$targetVersion"
        $updatedContent = $updatedContent -replace [regex]::Escape($oldLine), $newLine
        
        $updates += @{
            Name = $internalName
            From = $currentVersion
            To = $targetVersion
        }
        
        Write-Host "[UPDATE] ${internalName}: $currentVersion -> $targetVersion ($ReleaseChannel)" -ForegroundColor Cyan
    }
}

if ($agentCandidates.Count -gt 0) {
    $uniqueVersions = $agentLatestVersions | Sort-Object -Unique
    if ($uniqueVersions.Count -eq 1) {
        # Update all agents
        foreach ($entry in $agentCandidates) {
            $prefix = $entry[0]
            $imageName = $entry[1]
            $internalName = $entry[2]
            $currentVersion = $entry[3]
            $targetVersion = $entry[4]
            $oldLine = "$prefix${imageName}:$currentVersion"
            $newLine = "$prefix${imageName}:$targetVersion"
            $updatedContent = $updatedContent -replace [regex]::Escape($oldLine), $newLine
            $updates += @{
                Name = $internalName
                From = $currentVersion
                To = $targetVersion
            }
            Write-Host "[UPDATE] ${internalName}: $currentVersion -> $targetVersion ($ReleaseChannel) (agents aligned)" -ForegroundColor Cyan
        }
        # Update core components (conductor, chronos, core-hub) alongside agents
        foreach ($entry in $coreCandidates) {
            $prefix = $entry[0]
            $imageName = $entry[1]
            $internalName = $entry[2]
            $currentVersion = $entry[3]
            $targetVersion = $entry[4]
            $oldLine = "$prefix${imageName}:$currentVersion"
            $newLine = "$prefix${imageName}:$targetVersion"
            $updatedContent = $updatedContent -replace [regex]::Escape($oldLine), $newLine
            $updates += @{
                Name = $internalName
                From = $currentVersion
                To = $targetVersion
            }
            Write-Host "[UPDATE] ${internalName}: $currentVersion -> $targetVersion ($ReleaseChannel) (core component)" -ForegroundColor Cyan
        }
    }
    else {
        Write-Host "Agent updates deferred: latest versions differ ($($uniqueVersions -join ', ')). Core components also deferred." -ForegroundColor Yellow
    }
}
elseif ($coreCandidates.Count -gt 0) {
    # No agents to update, but core components are pending - update them now
    Write-Host "No agent updates needed, updating core components..." -ForegroundColor Cyan
    foreach ($entry in $coreCandidates) {
        $prefix = $entry[0]
        $imageName = $entry[1]
        $internalName = $entry[2]
        $currentVersion = $entry[3]
        $targetVersion = $entry[4]
        $oldLine = "$prefix${imageName}:$currentVersion"
        $newLine = "$prefix${imageName}:$targetVersion"
        $updatedContent = $updatedContent -replace [regex]::Escape($oldLine), $newLine
        $updates += @{
            Name = $internalName
            From = $currentVersion
            To = $targetVersion
        }
        Write-Host "[UPDATE] ${internalName}: $currentVersion -> $targetVersion ($ReleaseChannel) (core component)" -ForegroundColor Cyan
    }
}

if ($updates.Count -gt 0) {
    Set-Content -Path $ComposeFile -Value $updatedContent -NoNewline
    Write-Host ""
    Write-Host "Updated $($updates.Count) image(s) in $ComposeFile"
    
    # Find and execute run.ps1
    $runScript = Join-Path $ScriptDir "run.ps1"
    if (Test-Path $runScript) {
        Write-Host ""
        Write-Host "Running updated stack..."
        & $runScript
    }
    else {
        Write-Error "run.ps1 not found at $runScript"
        exit 1
    }
}
else {
    Write-Host ""
    Write-Host "No updates needed. Stack not restarted."
}
