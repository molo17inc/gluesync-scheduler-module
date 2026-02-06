param(
    [string]$Email,
    [string]$Ticket,
    [switch]$CleanAfterUpload
)

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

Write-Host "=================================================================================="
Write-Host " Welcome to the Gluesync Logs Collector & Uploader!"
Write-Host ""
Write-Host " This script safely collects all .log and .err files recursively from Gluesync"
Write-Host " directories and creates a compressed archive (.zip)."
Write-Host ""
Write-Host " If you have a support ticket, you can upload the logs directly to your secure"
Write-Host " MOLO17 support area for faster troubleshooting assistance."
Write-Host ""
Write-Host " Usage: .\collect-logs.ps1 [-Email email] [-Ticket ticket]"
Write-Host "=================================================================================="
Write-Host ""

# This script recursively finds all .log files in the parent directory of the script's location
# and creates a compressed ZIP archive.

# Script version
$ScriptVersion = "1.5"

function Add-SectionHeader {
    param(
        [string]$Path,
        [string]$Title
    )
    Add-Content -Path $Path -Value @(
        "==============================",
        $Title,
        "=============================="
    )
}

function Test-FileReadable {
    param([string]$Path)

    try {
        $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        $stream.Dispose()
        return $true
    } catch {
        return $false
    }
}

function Get-FileContentForced {
    param([string]$Path)

    try {
        $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        $reader = New-Object System.IO.StreamReader($stream)
        $content = $reader.ReadToEnd()
        $reader.Dispose()
        $stream.Dispose()
        return $content
    } catch {
        throw
    }
}

function Append-CommandOutput {
    param(
        [string]$Path,
        [string]$Title,
        [string]$Command,
        [string[]]$Arguments = @()
    )

    Add-Content -Path $Path -Value "### $Title"
    if (Get-Command -Name $Command -ErrorAction SilentlyContinue) {
        try {
            $output = & $Command @Arguments 2>&1 | Out-String
            if ([string]::IsNullOrWhiteSpace($output)) {
                Add-Content -Path $Path -Value "(no output returned)"
            } else {
                Add-Content -Path $Path -Value $output.TrimEnd()
            }
        } catch {
            Add-Content -Path $Path -Value "Command failed: $($_.Exception.Message)"
        }
    } else {
        Add-Content -Path $Path -Value "Command '$Command' not available on this system."
    }
    Add-Content -Path $Path -Value ""
}

function Append-ScriptBlockOutput {
    param(
        [string]$Path,
        [string]$Title,
        [scriptblock]$Block
    )

    Add-Content -Path $Path -Value "### $Title"
    try {
        $result = & $Block
        if ($null -eq $result) {
            Add-Content -Path $Path -Value "(no output returned)"
        } else {
            $result | Out-String | ForEach-Object { $_.TrimEnd() } | Where-Object { $_ -ne "" } | Add-Content -Path $Path
        }
    } catch {
        Add-Content -Path $Path -Value "Operation failed: $($_.Exception.Message)"
    }
    Add-Content -Path $Path -Value ""
}

function Get-LogicalCoreCount {
    try {
        $sum = (Get-CimInstance Win32_Processor -ErrorAction Stop | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum
        if ($sum -gt 0) { return $sum }
    } catch { }
    try {
        if ($env:NUMBER_OF_PROCESSORS) { return $env:NUMBER_OF_PROCESSORS }
    } catch { }
    return "unknown"
}

function Get-PhysicalCoreCount {
    try {
        $sum = (Get-CimInstance Win32_Processor -ErrorAction Stop | Measure-Object -Property NumberOfCores -Sum).Sum
        if ($sum -gt 0) { return $sum }
    } catch { }
    try {
        if ($env:NUMBER_OF_PROCESSORS) { return $env:NUMBER_OF_PROCESSORS }
    } catch { }
    return "unknown"
}

function Write-SystemReport {
    param([string]$OutputPath)

    Set-Content -Path $OutputPath -Value @(
        "Gluesync System Report",
        "Generated on: $(Get-Date -Format o)",
        "Hostname: $(hostname)",
        ""
    )

    Add-SectionHeader -Path $OutputPath -Title "Operating System"
    Append-CommandOutput -Path $OutputPath -Title "systeminfo" -Command "systeminfo"
    Append-ScriptBlockOutput -Path $OutputPath -Title "Get-ComputerInfo (summary)" -Block {
        Get-ComputerInfo -ErrorAction Stop | Select-Object CsName, WindowsProductName, WindowsVersion, OsHardwareAbstractionLayer, OsArchitecture, WindowsBuildLabEx
    }
    Append-ScriptBlockOutput -Path $OutputPath -Title "Get-CimInstance Win32_OperatingSystem" -Block {
        Get-CimInstance Win32_OperatingSystem -ErrorAction Stop | Format-List *
    }
    Append-ScriptBlockOutput -Path $OutputPath -Title "Environment Variables (OS Info)" -Block {
        [PSCustomObject]@{
            OS = $env:OS
            PROCESSOR_ARCHITECTURE = $env:PROCESSOR_ARCHITECTURE
            PROCESSOR_IDENTIFIER = $env:PROCESSOR_IDENTIFIER
            NUMBER_OF_PROCESSORS = $env:NUMBER_OF_PROCESSORS
            COMPUTERNAME = $env:COMPUTERNAME
            USERDOMAIN = $env:USERDOMAIN
        } | Format-List
    }

    Add-SectionHeader -Path $OutputPath -Title "CPU"
    Add-Content -Path $OutputPath -Value @(
        "Logical cores: $(Get-LogicalCoreCount)",
        "Physical cores: $(Get-PhysicalCoreCount)",
        ""
    )
    Append-ScriptBlockOutput -Path $OutputPath -Title "Get-CimInstance Win32_Processor" -Block {
        Get-CimInstance Win32_Processor -ErrorAction Stop | Select-Object Name, Manufacturer, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed, AddressWidth
    }
    Append-ScriptBlockOutput -Path $OutputPath -Title "Environment Variables (CPU Info)" -Block {
        [PSCustomObject]@{
            PROCESSOR_ARCHITECTURE = $env:PROCESSOR_ARCHITECTURE
            PROCESSOR_IDENTIFIER = $env:PROCESSOR_IDENTIFIER
            PROCESSOR_LEVEL = $env:PROCESSOR_LEVEL
            PROCESSOR_REVISION = $env:PROCESSOR_REVISION
            NUMBER_OF_PROCESSORS = $env:NUMBER_OF_PROCESSORS
        } | Format-List
    }

    Add-SectionHeader -Path $OutputPath -Title "Virtualization"
    Append-ScriptBlockOutput -Path $OutputPath -Title "Hyper-V requirements (systeminfo subset)" -Block {
        systeminfo | Select-String -Pattern "Hyper-V"
    }
    Append-ScriptBlockOutput -Path $OutputPath -Title "Win32_ComputerSystem virtualization flags" -Block {
        Get-CimInstance Win32_ComputerSystem -ErrorAction Stop | Select-Object Manufacturer, Model, HypervisorPresent
    }
    Append-ScriptBlockOutput -Path $OutputPath -Title "Container Detection" -Block {
        $isContainer = $false
        if (Test-Path "/.dockerenv") { $isContainer = $true }
        if ($env:DOTNET_RUNNING_IN_CONTAINER -eq "true") { $isContainer = $true }
        [PSCustomObject]@{
            IsContainer = $isContainer
            DockerEnvExists = (Test-Path "/.dockerenv")
            DotNetContainerVar = $env:DOTNET_RUNNING_IN_CONTAINER
        } | Format-List
    }

    Add-SectionHeader -Path $OutputPath -Title "Memory"
    Append-CommandOutput -Path $OutputPath -Title "wmic OS get TotalVisibleMemorySize / FreePhysicalMemory" -Command "wmic" -Arguments @("OS", "get", "TotalVisibleMemorySize,FreePhysicalMemory /Value")
    Append-ScriptBlockOutput -Path $OutputPath -Title "Get-CimInstance Win32_PhysicalMemory" -Block {
        Get-CimInstance Win32_PhysicalMemory -ErrorAction Stop | Select-Object Manufacturer, Capacity, Speed, PartNumber
    }
    Append-ScriptBlockOutput -Path $OutputPath -Title "Get-CimInstance Win32_OperatingSystem (Memory)" -Block {
        Get-CimInstance Win32_OperatingSystem -ErrorAction Stop | Select-Object TotalVisibleMemorySize, FreePhysicalMemory, TotalVirtualMemorySize, FreeVirtualMemory | Format-List
    }

    Add-SectionHeader -Path $OutputPath -Title "Disk"
    Append-ScriptBlockOutput -Path $OutputPath -Title "Get-PhysicalDisk" -Block {
        Get-PhysicalDisk -ErrorAction Stop | Select-Object FriendlyName, SerialNumber, MediaType, OperationalStatus, Size
    }
    Append-ScriptBlockOutput -Path $OutputPath -Title "Get-Disk" -Block {
        Get-Disk -ErrorAction Stop | Select-Object Number, FriendlyName, BusType, PartitionStyle, OperationalStatus, Size
    }
    Append-ScriptBlockOutput -Path $OutputPath -Title "Get-Volume" -Block {
        Get-Volume -ErrorAction Stop | Select-Object DriveLetter, FileSystemLabel, FileSystem, Size, SizeRemaining, HealthStatus
    }
    Append-ScriptBlockOutput -Path $OutputPath -Title "Get-PSDrive (Filesystem)" -Block {
        Get-PSDrive -PSProvider FileSystem | Select-Object Name, Used, Free, Root | Format-Table -AutoSize
    }

    Add-SectionHeader -Path $OutputPath -Title "Network"
    Append-ScriptBlockOutput -Path $OutputPath -Title "Get-NetAdapter" -Block {
        Get-NetAdapter -ErrorAction Stop | Select-Object Name, InterfaceDescription, Status, LinkSpeed, MacAddress
    }
    Append-ScriptBlockOutput -Path $OutputPath -Title "Get-NetIPAddress" -Block {
        Get-NetIPAddress -ErrorAction Stop | Select-Object InterfaceAlias, AddressFamily, IPAddress, PrefixLength
    }
    Append-CommandOutput -Path $OutputPath -Title "ipconfig /all" -Command "ipconfig" -Arguments @("/all")
    Append-CommandOutput -Path $OutputPath -Title "route print" -Command "route" -Arguments @("print")
    Append-ScriptBlockOutput -Path $OutputPath -Title "Get-NetIPConfiguration" -Block {
        Get-NetIPConfiguration -ErrorAction Stop | Format-List
    }
}

function Write-FileDump {
    param(
        [string]$OutputPath,
        [string[]]$Extensions,
        [string]$SearchRoot,
        [string]$ExtraDir
    )

    Set-Content -Path $OutputPath -Value @(
        "Full dump generated on: $(Get-Date -Format o)",
        ""
    )

    $normalizedRoot = [System.IO.Path]::GetFullPath($SearchRoot)
    $normalizedExtra = $null
    if ($ExtraDir) {
        $normalizedExtra = ([System.IO.Path]::GetFullPath($ExtraDir)).TrimEnd('\')
    }

    $files = @()
    try {
        $files = Get-ChildItem -Path $SearchRoot -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
            $ext = $_.Extension.ToLowerInvariant()
            $match = $Extensions -contains $ext
            if (-not $match) { return $false }
            if ($normalizedExtra) {
                $fullPath = [System.IO.Path]::GetFullPath($_.FullName)
                if ($fullPath.StartsWith($normalizedExtra, [System.StringComparison]::OrdinalIgnoreCase)) {
                    return $false
                }
            }
            return $true
        }
    } catch {
        Add-Content -Path $OutputPath -Value "Failed to enumerate files: $($_.Exception.Message)"
        return
    }

    if (-not $files -or $files.Count -eq 0) {
        Add-Content -Path $OutputPath -Value "No matching files were found within $SearchRoot."
        return
    }

    foreach ($file in $files) {
        $fullPath = [System.IO.Path]::GetFullPath($file.FullName)
        $relativePath = $fullPath.Substring($normalizedRoot.Length).TrimStart('\')
        Add-Content -Path $OutputPath -Value "----- START $relativePath -----"
        try {
            $content = Get-FileContentForced -Path $file.FullName
            Add-Content -Path $OutputPath -Value $content
        } catch {
            Add-Content -Path $OutputPath -Value "Unable to read file: $($_.Exception.Message)"
        }
        Add-Content -Path $OutputPath -Value @(
            "----- END $relativePath -----",
            ""
        )
    }
}

function Write-DockerReport {
    param([string]$OutputPath)

    Set-Content -Path $OutputPath -Value @(
        "Docker diagnostics generated on: $(Get-Date -Format o)",
        ""
    )

    Append-CommandOutput -Path $OutputPath -Title "docker --version" -Command "docker" -Arguments @("--version")
    Append-CommandOutput -Path $OutputPath -Title "docker info" -Command "docker" -Arguments @("info")
    Append-CommandOutput -Path $OutputPath -Title "docker ps -a" -Command "docker" -Arguments @("ps", "-a")
    Append-CommandOutput -Path $OutputPath -Title "docker images" -Command "docker" -Arguments @("images")
    Append-CommandOutput -Path $OutputPath -Title "docker-compose version" -Command "docker-compose" -Arguments @("version")
}

function Export-DockerContainerLogs {
    param(
        [string]$OutputDirectory
    )

    if (-not (Get-Command -Name "docker" -ErrorAction SilentlyContinue)) {
        Write-Host "Docker command not available. Skipping container logs collection."
        return @()
    }

    $exportedFiles = @()
    
    try {
        $containersJson = docker ps -a --format "{{json .}}" 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Failed to list Docker containers: $containersJson"
            return @()
        }

        $containers = $containersJson | ForEach-Object {
            try {
                $_ | ConvertFrom-Json
            } catch {
                $null
            }
        } | Where-Object { $_ -ne $null }

        if (-not $containers -or $containers.Count -eq 0) {
            Write-Host "No Docker containers found."
            return @()
        }

        Write-Host "Found $($containers.Count) Docker container(s). Collecting logs..."

        foreach ($container in $containers) {
            $containerId = $container.ID
            $containerName = $container.Names
            if ([string]::IsNullOrWhiteSpace($containerName)) {
                $containerName = $containerId.Substring(0, [Math]::Min(12, $containerId.Length))
            }

            $safeContainerName = $containerName -replace '[\\/:*?"<>|]', '_'
            $logFileName = "container-${safeContainerName}.log"
            $logFilePath = Join-Path $OutputDirectory $logFileName

            Write-Host "  Collecting logs from container: $containerName (ID: $containerId)"
            
            try {
                if (-not (Test-Path $OutputDirectory)) {
                    New-Item -Path $OutputDirectory -ItemType Directory -Force | Out-Null
                }
                
                $header = @(
                    "Container Logs for: $containerName",
                    "Container ID: $containerId",
                    "Status: $($container.Status)",
                    "Image: $($container.Image)",
                    "Created: $($container.CreatedAt)",
                    "Collected on: $(Get-Date -Format o)",
                    "",
                    ("=" * 80),
                    ""
                )
                
                $header | Out-File -FilePath $logFilePath -Encoding utf8 -Force
                
                $logs = docker logs $containerId 2>&1
                
                if ($null -ne $logs) {
                    if ($logs -is [array]) {
                        foreach ($line in $logs) {
                            if ($null -ne $line) {
                                $line.ToString() | Out-File -FilePath $logFilePath -Encoding utf8 -Append
                            }
                        }
                    } else {
                        $logs.ToString() | Out-File -FilePath $logFilePath -Encoding utf8 -Append
                    }
                }
                
                if (Test-Path $logFilePath) {
                    $exportedFiles += $logFilePath
                    $fileSize = (Get-Item $logFilePath).Length
                    Write-Host "    Successfully saved to: $logFileName ($fileSize bytes)"
                } else {
                    Write-Host "    Warning: File was not created at $logFilePath"
                }
            } catch {
                Write-Host "    Error collecting logs from $containerName : $($_.Exception.Message)"
            }
        }
    } catch {
        Write-Host "Error during Docker container logs collection: $($_.Exception.Message)"
    }

    return $exportedFiles
}

$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$ArchiveName = "support-logs-v$ScriptVersion-$Timestamp.zip"

# Get the directory where the script is located (robust across invocation methods)
if ($PSScriptRoot) {
    $scriptDir = $PSScriptRoot
} else {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}

# Set the directory to search for logs
# First check if "root-folder" exists in the script directory
$rootFolderPath = Join-Path $scriptDir "root-folder"
if (Test-Path $rootFolderPath) {
    $searchDir = (Resolve-Path -Path $rootFolderPath -ErrorAction Stop).ProviderPath
    Write-Host "Root-folder found. Searching logs inside: $searchDir"
} else {
    # Fallback: parent directory of the script's location
    $searchDir = Join-Path $scriptDir ".."
    $searchDir = (Resolve-Path -Path $searchDir -ErrorAction Stop).ProviderPath
    Write-Host "Root-folder not found. Searching logs inside parent directory: $searchDir"
}

# Determine where to place the archive: try invoking directory then fallback to temp
$invokeDir = (Get-Location).Path
$outputDir = $invokeDir
$testFile = Join-Path $outputDir ".collect_logs_write_test_$PID.tmp"
try {
    New-Item -Path $testFile -ItemType File -Force -ErrorAction Stop | Out-Null
    Remove-Item -Path $testFile -Force -ErrorAction SilentlyContinue
} catch {
    $outputDir = [System.IO.Path]::GetTempPath().TrimEnd('\')
}

$archivePath = Join-Path $outputDir $ArchiveName

# Prompt for email and ticket if not provided
if (-not $Email) {
    $Email = Read-Host "Enter your email address"
}
if (-not $Ticket) {
    $Ticket = Read-Host "Enter ticket number"
}

Write-Host "Log Collection Script v$ScriptVersion - Searching for .log and .err files in $searchDir..."
Write-Host ""

# Robust recursive discovery by filtering extensions in the pipeline
try {
    $logFiles = Get-ChildItem -Path $searchDir -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in '.log', '.err' }
} catch {
    Write-Error ("Failed to enumerate files in {0}: {1}" -f $searchDir, $_.Exception.Message)  
    exit 1
}

if ($logFiles) {
    Write-Host "Found $($logFiles.Count) log files."
} else {
    Write-Host "No .log or .err files found."
}

# Prepare list of full paths
$filePaths = @()
if ($logFiles) {
    $filePaths += ($logFiles | ForEach-Object { $_.FullName })
}

$extraDirName = ("gluesync-support-extra-{0}-{1}" -f [DateTimeOffset]::UtcNow.ToUnixTimeSeconds(), $PID)
$extraDir = Join-Path $searchDir $extraDirName
if (-not (Test-Path $extraDir)) {
    New-Item -Path $extraDir -ItemType Directory -Force | Out-Null
}

Write-Host "Creating diagnostics reports under $extraDir ..."
$systemReport = Join-Path $extraDir "system-report.txt"
$yamlDump = Join-Path $extraDir "yaml-files-dump.txt"
$xmlDump = Join-Path $extraDir "xml-files-dump.txt"
$dockerReport = Join-Path $extraDir "docker-report.txt"

Write-SystemReport -OutputPath $systemReport
Write-FileDump -OutputPath $yamlDump -Extensions @(".yaml", ".yml") -SearchRoot $searchDir -ExtraDir $extraDir
Write-FileDump -OutputPath $xmlDump -Extensions @(".xml") -SearchRoot $searchDir -ExtraDir $extraDir
Write-DockerReport -OutputPath $dockerReport

$containerLogsDir = Join-Path $extraDir "container-logs"
if (-not (Test-Path $containerLogsDir)) {
    New-Item -Path $containerLogsDir -ItemType Directory -Force | Out-Null
}
$containerLogFiles = Export-DockerContainerLogs -OutputDirectory $containerLogsDir
if ($containerLogFiles -and $containerLogFiles.Count -gt 0) {
    Write-Host "Collected logs from $($containerLogFiles.Count) container(s)."
}

$diagnosticFiles = Get-ChildItem -Path $extraDir -Recurse -File -ErrorAction SilentlyContinue
if ($diagnosticFiles) {
    $filePaths += ($diagnosticFiles | ForEach-Object { $_.FullName })
}

if (-not $filePaths -or $filePaths.Count -eq 0) {
    Write-Host "No files found to archive (logs or diagnostics)."
    exit 1
}

$readableFilePaths = New-Object System.Collections.Generic.List[string]
$lockedFilePaths = New-Object System.Collections.Generic.List[string]
foreach ($candidate in $filePaths) {
    if (Test-FileReadable -Path $candidate) {
        [void]$readableFilePaths.Add($candidate)
    } else {
        [void]$lockedFilePaths.Add($candidate)
    }
}

if ($lockedFilePaths.Count -gt 0) {
    Write-Warning ("Skipping {0} locked/inaccessible files detected before compression." -f $lockedFilePaths.Count)
    foreach ($locked in $lockedFilePaths) {
        Write-Warning ("  $locked")
    }
}

if ($readableFilePaths.Count -eq 0) {
    Write-Error "Failed to create archive: all files are locked or inaccessible."
    exit 1
}

# Check if the Compress-Archive cmdlet is available
if (Get-Command -Name Compress-Archive -ErrorAction SilentlyContinue) {
    Write-Host "Creating archive: $archivePath..."
    
    # If the archive already exists, remove it to avoid errors
    if (Test-Path $archivePath) {
        Remove-Item $archivePath -Force -ErrorAction SilentlyContinue
    }

    # Create the compressed archive
    try {
        # Compress-Archive accepts an array of paths; use -Force to overwrite
        Compress-Archive -Path $readableFilePaths -DestinationPath $archivePath -Force -ErrorAction Stop
        Write-Host "Successfully created $archivePath"
    } catch {
        Write-Host "Compress-Archive failed: $($_.Exception.Message)"
        Write-Host "Attempting incremental update (skipping locked files)..."
        # Attempt incremental update fallback to reduce memory pressure / path length issues
        if (Test-Path $archivePath) { Remove-Item $archivePath -Force -ErrorAction SilentlyContinue }
        $skippedCount = 0
        $addedCount = 0
        foreach ($p in $readableFilePaths) {
            try {
                Compress-Archive -Path $p -DestinationPath $archivePath -Update -ErrorAction Stop
                $addedCount++
            } catch {
                Write-Host "  Skipping locked/inaccessible file: $p"
                $skippedCount++
            }
        }
        if ($addedCount -gt 0) {
            Write-Host "Archive created with $addedCount files ($skippedCount skipped): $archivePath"
        } else {
            Write-Host "Failed to create archive: all files were locked or inaccessible."
            exit 1
        }
    }

    # Attempt FTP upload if email and ticket provided
    if ($Email -and $Ticket) {
        Write-Host "Uploading $archivePath to FTP..."
        try {
            $webClient = New-Object System.Net.WebClient
            $webClient.Credentials = New-Object System.Net.NetworkCredential($Ticket, $Email)
            $webClient.UploadFile("ftp://ftp.molo17.com/$ArchiveName", "STOR", $archivePath)
            Write-Host "Successfully uploaded to FTP."
            if ($CleanAfterUpload.IsPresent) {
                Write-Host "CleanAfterUpload requested. Removing archive: $archivePath"
                Remove-Item -Path $archivePath -Force -ErrorAction SilentlyContinue
            }
        } catch {
            Write-Host "Failed to upload to FTP: $($_.Exception.Message)"
            exit 1
        } finally {
            if ($webClient) { $webClient.Dispose() }
        }
    } else {
        Write-Host "Email or ticket not provided. Skipping upload."
        exit 1
    }
} else {
    Write-Error "Error: 'Compress-Archive' cmdlet not found. This script requires PowerShell 5.0 or newer."
    exit 1
}
