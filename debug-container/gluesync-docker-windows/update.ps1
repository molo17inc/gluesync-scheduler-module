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

# Global image tag map for offline updates
$global:ImageTagMap = @{}

# ==========================================
# HELPER FUNCTIONS (Must be defined first)
# ==========================================

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

function Get-ImageTagsFromTar {
    param([string]$TarFile)

    try {
        $tempDir = Join-Path $env:TEMP "manifest-extract-$(Get-Random)"
        New-Item -ItemType Directory -Path $tempDir -Force | Out-Null

        if ($TarFile -like "*.tar.gz") {
            $gzFile = $TarFile
            $tarFile = Join-Path $tempDir "image.tar"

            $inputStream = [System.IO.File]::OpenRead($gzFile)
            $gzipStream = New-Object System.IO.Compression.GzipStream($inputStream, [System.IO.Compression.CompressionMode]::Decompress)
            $outputStream = [System.IO.File]::Create($tarFile)
            $gzipStream.CopyTo($outputStream)
            $outputStream.Close()
            $gzipStream.Close()
            $inputStream.Close()

            $TarFile = $tarFile
        }

        $manifestPath = Join-Path $tempDir "manifest.json"
        & tar -xf $TarFile -C $tempDir manifest.json 2>$null

        if (Test-Path $manifestPath) {
            $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
            $tags = $manifest.RepoTags
            Remove-Item -Path $tempDir -Recurse -Force -ErrorAction SilentlyContinue
            return $tags
        }

        Remove-Item -Path $tempDir -Recurse -Force -ErrorAction SilentlyContinue
        return @()
    }
    catch {
        return @()
    }
}

function Write-ImageTags {
    param([array]$Tags)

    if ($Tags.Count -eq 0) {
        Write-Host "  Unable to read manifest.json to determine image tags." -ForegroundColor Yellow
        return
    }

    Write-Host "  Repo tags:"
    foreach ($tag in $Tags) {
        Write-Host "    - $tag"
    }
}

function Record-ImageTags {
    param([array]$Tags)

    foreach ($tag in $Tags) {
        if ($tag -match '^(.+?):') {
            $repo = $matches[1]
            $global:ImageTagMap[$repo] = $tag
        }
    }
}

function Update-ComposeFileWithTags {
    param([string]$ComposeFilePath)

    if ($global:ImageTagMap.Count -eq 0) {
        Write-Host "Warning: No image tags collected to update docker-compose file." -ForegroundColor Yellow
        return
    }

    Write-Host "Updating docker-compose image tags in $(Split-Path -Leaf $ComposeFilePath)..."

    $content = Get-Content $ComposeFilePath -Raw
    $updated = 0

    foreach ($repo in $global:ImageTagMap.Keys) {
        $newRef = $global:ImageTagMap[$repo]
        $escapedRepo = [regex]::Escape($repo)

        $pattern = "(image\s*:\s*[`"']?)$escapedRepo(?:[:@][^`"'\s]+)?([`"']?)"

        $matches = [regex]::Matches($content, $pattern)
        if ($matches.Count -gt 0) {
            $content = [regex]::Replace($content, $pattern, "`${1}$newRef`${2}")
            Write-Host "  Updated $repo to use $newRef"
            $updated++
        }
        else {
            Write-Host "  No compose entries found for $repo"
        }
    }

    if ($updated -gt 0) {
        Set-Content -Path $ComposeFilePath -Value $content -NoNewline
        Write-Host "Updated $updated image reference(s) in $(Split-Path -Leaf $ComposeFilePath)."
    }
    else {
        Write-Host "Warning: No compose image references matched the extracted tags." -ForegroundColor Yellow
    }
}

function Find-OfflineUpdateFile {
    $patterns = @("update-*.tar.gz", "update-*.zip")

    foreach ($pattern in $patterns) {
        $files = Get-ChildItem -Path $ScriptDir -Filter $pattern -File
        if ($files.Count -gt 0) {
            return $files[0].FullName
        }
    }

    return $null
}

function Load-OfflineUpdate {
    param([string]$UpdateFile)

    $fileName = Split-Path -Leaf $UpdateFile

    Write-Host ""
    Write-Host "Loading offline update from $fileName..."

    $extractDir = Join-Path $env:TEMP "gluesync-offline-update-$(Get-Random)"
    New-Item -ItemType Directory -Path $extractDir -Force | Out-Null

    try {
        if ($UpdateFile -like "*.tar.gz") {
            Write-Host "Extracting update file (tar.gz)..."
            & tar -xzf $UpdateFile -C $extractDir
        }
        else {
            Write-Host "Extracting update file (zip)..."
            Expand-Archive -Path $UpdateFile -DestinationPath $extractDir -Force
        }

        Write-Host "Update file extracted successfully."

        Write-Host "Searching for Docker image files (.tar / .tar.gz)..."
        $imageFiles = Get-ChildItem -Path $extractDir -Recurse -Include "*.tar", "*.tar.gz" -File

        if ($imageFiles.Count -eq 0) {
            Write-Host "Warning: No .tar or .tar.gz files found in the update package." -ForegroundColor Yellow
            Remove-Item -Path $extractDir -Recurse -Force
            return $false
        }

        Write-Host "Found $($imageFiles.Count) Docker image(s) to load."
        Write-Host ""

        $loaded = 0
        $failed = 0
        $total = $imageFiles.Count

        foreach ($imageFile in $imageFiles) {
            $loaded++
            $imageFileName = $imageFile.Name

            Write-Host "[$loaded/$total] Loading image: $imageFileName"

            $tags = Get-ImageTagsFromTar -TarFile $imageFile.FullName
            Write-ImageTags -Tags $tags
            Record-ImageTags -Tags $tags

            $loadSuccess = $false
            $tempTarFile = $null
            try {
                if ($imageFileName -like "*.tar.gz") {
                    # Decompress .tar.gz to temporary .tar file
                    $tempTarFile = Join-Path $env:TEMP "docker-image-$(Get-Random).tar"

                    $inputStream = [System.IO.File]::OpenRead($imageFile.FullName)
                    $gzipStream = New-Object System.IO.Compression.GzipStream($inputStream, [System.IO.Compression.CompressionMode]::Decompress)
                    $outputStream = [System.IO.File]::Create($tempTarFile)

                    $gzipStream.CopyTo($outputStream)

                    $outputStream.Close()
                    $gzipStream.Close()
                    $inputStream.Close()

                    # Load the decompressed .tar file
                    docker load -i $tempTarFile 2>&1 | Out-Null
                    if ($LASTEXITCODE -eq 0) {
                        $loadSuccess = $true
                    }
                }
                else {
                    docker load -i $imageFile.FullName 2>&1 | Out-Null
                    if ($LASTEXITCODE -eq 0) {
                        $loadSuccess = $true
                    }
                }
            }
            catch {
                $loadSuccess = $false
            }
            finally {
                # Clean up temporary tar file if created
                if ($tempTarFile -and (Test-Path $tempTarFile)) {
                    Remove-Item -Path $tempTarFile -Force -ErrorAction SilentlyContinue
                }
            }

            if ($loadSuccess) {
                Write-Host "  ✓ Loaded successfully" -ForegroundColor Green
            }
            else {
                Write-Host "  ✗ Failed to load" -ForegroundColor Red
                $failed++
            }
        }

        Write-Host ""

        # Update compose file with extracted tags
        $composeFile = $null
        foreach ($SearchDir in $SearchDirs) {
            $foundFile = Find-ComposeFile -Directory $SearchDir
            if ($foundFile) {
                $composeFile = $foundFile
                break
            }
        }

        if ($composeFile) {
            Update-ComposeFileWithTags -ComposeFilePath $composeFile
        }

        Write-Host "Cleaning up temporary files..."
        Remove-Item -Path $extractDir -Recurse -Force

        if ($failed -eq 0) {
            Write-Host ""
            Write-Host "╔══════════════════════════════════════════════════════════════╗"
            Write-Host "║                                                              ║"
            Write-Host "║            Offline Update Completed Successfully!            ║"
            Write-Host "║                                                              ║"
            Write-Host "╚══════════════════════════════════════════════════════════════╝"
            Write-Host ""
            Write-Host "All $total Docker image(s) loaded successfully!"
            Write-Host ""
            Write-Host "Please restart Gluesync to use the updated images."
            Write-Host "You can run: .\run.ps1"
            return $true
        }
        else {
            Write-Host ""
            Write-Host "╔══════════════════════════════════════════════════════════════╗"
            Write-Host "║                                                              ║"
            Write-Host "║          Offline Update Completed With Warnings              ║"
            Write-Host "║                                                              ║"
            Write-Host "╚══════════════════════════════════════════════════════════════╝"
            Write-Host ""
            Write-Host "$($total - $failed)/$total images loaded successfully, $failed failed."
            Write-Host ""
            Write-Host "Please check the errors above and restart Gluesync."
            return $false
        }
    }
    catch {
        Write-Host "Error during offline update: $_" -ForegroundColor Red
        Remove-Item -Path $extractDir -Recurse -Force -ErrorAction SilentlyContinue
        return $false
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

# ==========================================
# MAIN EXECUTION
# ==========================================

# Check for offline update first
$offlineUpdateFile = Find-OfflineUpdateFile
if ($offlineUpdateFile) {
    Write-Host ""
    Write-Host "╔══════════════════════════════════════════════════════════════╗"
    Write-Host "║                                                              ║"
    Write-Host "║              Offline Update File Detected                    ║"
    Write-Host "║                                                              ║"
    Write-Host "╚══════════════════════════════════════════════════════════════╝"
    Write-Host ""
    Write-Host "An offline update file named `"$(Split-Path -Leaf $offlineUpdateFile)`" has been found"
    Write-Host "within the Gluesync setup folder."
    Write-Host ""
    $response = Read-Host "Do you want to load it? (yes/no)"

    if ($response -eq "yes") {
        $success = Load-OfflineUpdate -UpdateFile $offlineUpdateFile
        exit $(if ($success) { 0 } else { 1 })
    }
    else {
        Write-Host "Offline update skipped. Proceeding with online update..."
        Write-Host ""
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
    "traefik"    = "traefik-win-2022"
    "prometheus" = "prometheus-win-2022"
    "grafana"    = "grafana-win-2022"
    "portainer"  = "portainer-win-2022"
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
    $winSuffix = "-win-nanoserver-ltsc2022"

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
            Write-Host "[OK] ${imageName}: already at latest $ReleaseChannel version $currentVersion (Third-party)" -ForegroundColor Green
        } else {
            $updatedContent = $updatedContent -replace ([regex]::Escape("$prefix${imageName}:$currentVersion")), "$prefix${imageName}:$targetVersion"
            $updates += "${imageName}: $currentVersion -> $targetVersion"
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
        Write-Host "[OK] ${internalName}: already at latest $ReleaseChannel version $currentVersion" -ForegroundColor Green
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

    # Find and execute run.exe (compiled) or run.ps1 (script)
    $runExe    = Join-Path $ScriptDir "run.exe"
    $runScript = Join-Path $ScriptDir "run.ps1"
    if (Test-Path $runExe) {
        Write-Host ""
        Write-Host "Running updated stack..."
        & $runExe
    }
    elseif (Test-Path $runScript) {
        Write-Host ""
        Write-Host "Running updated stack..."
        & $runScript
    }
    else {
        Write-Error "Neither run.exe nor run.ps1 found in $ScriptDir"
        exit 1
    }
}
else {
    Write-Host ""
    Write-Host "No updates needed. Stack not restarted."
}