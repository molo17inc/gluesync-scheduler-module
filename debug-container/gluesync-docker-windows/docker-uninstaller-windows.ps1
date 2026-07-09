#Requires -Version 5.0

<#
.SYNOPSIS
  Docker Uninstaller Script for Windows Server Environments
  Based on Gluesync Docker Installation Guide (Reversed for Uninstall)

.DESCRIPTION
  Automates Docker and Gluesync uninstallation on Windows Server.
  Handles reboots if required and resumes automatically.
#>

[CmdletBinding(DefaultParameterSetName = "Standard")]
param(
  [switch]
  $ContinueAfterReboot,

  [switch]
  $Force,

  [switch]
  $NoRestart
)

$global:RebootRequired = $false
$global:ErrorFile = "$pwd\Uninstall-ContainerHost.err"
$global:BootstrapTask = "ContainerBootstrapUninstall"
$global:DockerServiceName = "docker"
$global:DockerDataPath = "$($env:ProgramData)\docker"
$global:InstallPath = "$env:ProgramFiles\Gluesync"
$global:LogFile = ".\docker-gluesync-uninstallation.log"

# Clear the console
Clear-Host

# Configuration
$ScriptVersion = "1.2"
$ErrorActionPreference = "Stop"

# --------------------------
# Output helpers
function Write-ToLog {
  param ([string]$Message)
  $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
  Add-Content -Path $global:LogFile -Value "[$timestamp] $Message" -Force
}

function Write-Info { 
  param([string]$Message)
  $logMessage = "[INFO] $Message"
  Write-Host $logMessage -ForegroundColor Cyan
  Write-ToLog $logMessage
}

function Write-Success { 
  param([string]$Message)
  $logMessage = "[SUCCESS] $Message"
  Write-Host $logMessage -ForegroundColor Green
  Write-ToLog $logMessage
}

function Write-Warning { 
  param([string]$Message)
  $logMessage = "[WARNING] $Message"
  Write-Host $logMessage -ForegroundColor Yellow
  Write-ToLog $logMessage
}

function Write-Error { 
  param([string]$Message)
  $logMessage = "[ERROR] $Message"
  Write-Host $logMessage -ForegroundColor Red
  Write-ToLog $logMessage
}

function Write-Title { 
  param([string]$Message)
  Write-Host $Message -ForegroundColor Cyan
  Write-ToLog $Message
}

function Write-Text { 
  param([string]$Message)
  Write-Host $Message -ForegroundColor Gray
  Write-ToLog $Message
}

function Write-Alert { 
  param([string]$Message)
  Write-Host $Message -ForegroundColor Yellow
  Write-ToLog $Message
}

function Get-LogFileFullPath {
  try {
    return (Resolve-Path $global:LogFile).Path
  }
  catch {
    return [System.IO.Path]::GetFullPath($global:LogFile)
  }
}

function Show-LogReference {
  $logPath = Get-LogFileFullPath
  Write-Text "Detailed log file: $logPath"
}

function Wait-ForUserAcknowledgement {
  Write-Text ""
  [void](Read-Host "Press Enter to close this window after reviewing the messages above")
}

function Stop-Uninstaller {
  param(
    [string]$Message = "",
    [int]$ExitCode = 1,
    [bool]$EmitAsError = $true,
    [switch]$SkipLogReference,
    [switch]$SkipPause
  )

  if (-not [string]::IsNullOrWhiteSpace($Message)) {
    if ($EmitAsError) { Write-Error $Message }
    else { Write-Info $Message }
  }

  if (-not $SkipLogReference) {
    Show-LogReference
  }

  if (-not $SkipPause) {
    Wait-ForUserAcknowledgement
  }

  exit $ExitCode
}

# --------------------------
# Administrator check
# --------------------------
function Test-Administrator {
  $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = New-Object Security.Principal.WindowsPrincipal($currentUser)
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# --------------------------
# Docker uninstall helpers
# --------------------------
function Stop-Docker() {
  $service = Get-Service -Name $global:DockerServiceName -ErrorAction SilentlyContinue
  if ($service -and $service.Status -ne "Stopped") {
    Write-Info "Stopping Docker service..."
    Stop-Service -Name $global:DockerServiceName -Force
    Write-Success "Docker service stopped."
  } else {
    Write-Info "Docker service not running or not found. Skipping stop."
  }
}

function Unregister-DockerService() {
  Write-Info "Unregistering Docker service..."
  if (Get-Command dockerd -ErrorAction SilentlyContinue) {
    & dockerd --unregister-service
    if ($LASTEXITCODE -ne 0) {
      Write-Warning "Failed to unregister Docker service. It may already be unregistered."
    } else {
      Write-Success "Docker service unregistered."
    }
  } else {
    Write-Info "dockerd.exe not found. Assuming service already unregistered."
  }
}

function Remove-DockerFiles() {
  $paths = @(
    "$env:ProgramFiles\Docker",
    $global:DockerDataPath,
    "$env:System32\docker.exe",
    "$env:System32\dockerd.exe"
  )

  foreach ($path in $paths) {
    if (Test-Path $path) {
      Write-Info "Removing $path..."
      try {
        Remove-Item -Path $path -Recurse -Force -ErrorAction Stop
        Write-Success "$path removed."
      } catch {
        Write-Warning ("Unable to delete {0}: {1}" -f $path, $_.Exception.Message)
      }
    } else {
      Write-Info "$path not found. Skipping removal."
    }
  }
}

function Remove-DockerCompose() {
  $composePath = "$env:ProgramFiles\Docker\docker-compose.exe"
  if (Test-Path $composePath) {
    Write-Info "Removing Docker Compose..."
    Remove-Item -Path $composePath -Force
    Write-Success "Docker Compose removed."
  } else {
    Write-Info "Docker Compose not found. Skipping removal."
  }
}

function Remove-ContainerFeatures() {
  Write-Info "Checking and removing Windows features if necessary..."
  $rebootNeeded = $false

  if (Get-Command Get-WindowsFeature -ErrorAction SilentlyContinue) {
    $containersFeature = Get-WindowsFeature -Name Containers
    if ($containersFeature.Installed) {
      Write-Info "Uninstalling Containers feature..."
      $uninstallResult = Uninstall-WindowsFeature -Name Containers -ErrorAction SilentlyContinue
      if ($uninstallResult.RestartNeeded -eq 'Yes') {
        $rebootNeeded = $true
      }
    } else {
      Write-Info "Containers feature already uninstalled."
    }

    $hyperVFeature = Get-WindowsFeature -Name Hyper-V
    if ($hyperVFeature.Installed) {
      Write-Info "Uninstalling Hyper-V feature..."
      $uninstallResult = Uninstall-WindowsFeature -Name Hyper-V -ErrorAction SilentlyContinue
      if ($uninstallResult.RestartNeeded -eq 'Yes') {
        $rebootNeeded = $true
      }
    } else {
      Write-Info "Hyper-V feature already uninstalled."
    }
  } else {
    $containersFeature = Get-WindowsOptionalFeature -Online -FeatureName Containers
    if ($containersFeature.State -eq "Enabled") {
      Write-Info "Disabling Containers feature..."
      $feature = Disable-WindowsOptionalFeature -Online -FeatureName Containers -NoRestart -ErrorAction SilentlyContinue
      if ($feature.RestartNeeded -eq 'True') {
        $rebootNeeded = $true
      }
    } else {
      Write-Info "Containers feature already disabled."
    }

    $hyperVFeature = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V
    if ($hyperVFeature.State -eq "Enabled") {
      Write-Info "Disabling Hyper-V feature..."
      $feature = Disable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -NoRestart -ErrorAction SilentlyContinue
      if ($feature.RestartNeeded -eq 'True') {
        $rebootNeeded = $true
      }
    } else {
      Write-Info "Hyper-V feature already disabled."
    }
  }

  if ($rebootNeeded) {
    $global:RebootRequired = $true
    Write-Info "Reboot required to complete feature removal."
  } else {
    Write-Info "No reboot required for features."
  }
  Write-Success "Windows features processing completed."
}

function Cleanup-Docker {
  Write-Info "Cleaning up Docker containers and images..."
  if (Get-Command docker -ErrorAction SilentlyContinue) {
    try {
      $service = Get-Service -Name $global:DockerServiceName -ErrorAction SilentlyContinue
      if ($service -and $service.Status -ne "Running") {
        Start-Service -Name $global:DockerServiceName -ErrorAction Stop
        Start-Sleep -Seconds 5  # Give time for daemon to start
      }
      
      docker system prune -a -f --volumes 2>$null
      docker ps -a -q | ForEach-Object { docker rm $_ -f 2>$null }
      Write-Success "Docker cleanup completed."
    } catch {
      Write-Info "Docker daemon not available or already uninstalled. Skipping cleanup as this is expected during uninstallation."
    }
  } else {
    Write-Info "Docker command not found. Skipping cleanup as Docker appears to be already uninstalled."
  }
}

# --------------------------
# Gluesync uninstall
# --------------------------
function Uninstall-Gluesync {
  if (Test-Path $global:InstallPath) {
    Write-Info "Uninstalling Gluesync..."
    try {
      Remove-Item -Path $global:InstallPath -Recurse -Force -ErrorAction Stop
      Write-Success "Gluesync removed from $global:InstallPath."
    } catch {
      Write-Warning ("Unable to delete {0}: {1}" -f $global:InstallPath, $_.Exception.Message)
    }
  } else {
    Write-Info "Gluesync installation path not found. Skipping uninstall."
  }

  $shortcutPath = "$env:USERPROFILE\Desktop\Gluesync.url"
  if (Test-Path $shortcutPath) {
    Remove-Item -Path $shortcutPath -Force
    Write-Success "Desktop shortcut removed."
  } else {
    Write-Info "Desktop shortcut not found. Skipping removal."
  }
}

# --------------------------
# Restart logic
# --------------------------
function Restart-And-Run() {
  if (! (Test-Administrator)) {
    Stop-Uninstaller -Message "This script requires administrative privileges."
  }

  $scriptFullPath = (Resolve-Path $script:MyInvocation.MyCommand.Path).Path
  $scriptDir = Split-Path -Parent $scriptFullPath
  $arguments = "-ExecutionPolicy Bypass -NoProfile -File `"$scriptFullPath`" -ContinueAfterReboot"

  $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory $scriptDir
  $trigger = New-ScheduledTaskTrigger -AtLogOn

  if (Get-ScheduledTask -TaskName $global:BootstrapTask -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $global:BootstrapTask -Confirm:$false
  }

  Register-ScheduledTask -TaskName $global:BootstrapTask -Action $action -Trigger $trigger -RunLevel Highest | Out-Null

  if ($Force) {
    Restart-Computer -Force
  } else {
    Restart-Computer
  }

  Stop-Uninstaller -Message "Restart initiated. Uninstallation will resume after reboot." -EmitAsError:$false -SkipPause
}

# --------------------------
# Main uninstaller
# --------------------------
function Start-MainUninstaller {  
  if (-not $ContinueAfterReboot) {
    Write-Info "Starting Gluesync and Docker uninstallation - $(Get-Date)"
    Write-Info "Log file: $((Get-Item $global:LogFile).FullName)"
  } else {
    Write-Info "Continuing uninstallation after reboot - $(Get-Date)"
  }

  if (-not (Test-Administrator)) {
    Stop-Uninstaller -Message "This script requires administrative privileges."
  }
  Write-Success "Running with administrative privileges"

  Cleanup-Docker
  Stop-Docker
  Unregister-DockerService
  Remove-DockerFiles
  Remove-DockerCompose
  Remove-ContainerFeatures
  Uninstall-Gluesync

  if ($global:RebootRequired) {
    if ($NoRestart) {
      Write-Warning "A reboot is required to complete uninstallation."
      Stop-Uninstaller -Message "Reboot required. Please reboot manually." -EmitAsError:$false -ExitCode 0
    }
    Restart-And-Run
  }

  if ((Get-ScheduledTask -TaskName $global:BootstrapTask -ErrorAction SilentlyContinue) -ne $null) {
    Unregister-ScheduledTask -TaskName $global:BootstrapTask -Confirm:$false
    Write-Info "Scheduled task unregistered."
  }

  Remove-Item $global:ErrorFile -ErrorAction SilentlyContinue

  Write-Success "Uninstallation completed!"
}

# --------------------------
# Execute main uninstaller
# --------------------------
try {
  Start-MainUninstaller
}
catch {
  Stop-Uninstaller -Message "Unexpected error: $($_.Exception.Message). Review the log for details."
}