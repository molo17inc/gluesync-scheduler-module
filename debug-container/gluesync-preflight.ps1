<#
.SYNOPSIS
    Gluesync Pre-flight Checker for Windows
.DESCRIPTION
    This script checks system requirements for Gluesync v2.1 on Windows systems.
    It verifies Docker, Docker Compose, system resources, and network connectivity.
.NOTES
    File Name      : gluesync-preflight.ps1
    Prerequisites  : PowerShell 5.1 or later
#>

# Set error action preference
$ErrorActionPreference = "Stop"

# Setup logging
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$reportFile = "gluesync_preflight_check_${timestamp}.txt"

# Arrays to store messages
$script:Warnings = @()
$script:Errors = @()

# Function to write to both console and log file
function Write-Log {
    param (
        [string]$Message,
        [ValidateSet("Info", "Warning", "Error")]
        [string]$Level = "Info"
    )
    
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logMessage = "[$timestamp] $Message"
    
    # Write to console with appropriate color
    switch ($Level) {
        "Warning" { Write-Host $logMessage -ForegroundColor Yellow }
        "Error"   { Write-Host $logMessage -ForegroundColor Red }
        default    { Write-Host $logMessage }
    }
    
    # Write to log file
    Add-Content -Path $reportFile -Value $logMessage
}

# Function to add warning
function Add-Warning {
    param ([string]$Message)
    $script:Warnings += $Message
    Write-Log -Message "WARNING: $Message" -Level "Warning"
}

# Function to add error
function Add-Error {
    param ([string]$Message)
    $script:Errors += $Message
    Write-Log -Message "ERROR: $Message" -Level "Error"
}

# Function to print section header
function Write-Section {
    param ([string]$Title)
    $divider = "=" * ($Title.Length + 4)
    Write-Log "`n$divider"
    Write-Log "  $Title  "
    Write-Log $divider
}

# Function to check if a command exists
function Test-CommandExists {
    param ([string]$command)
    try {
        $null = Get-Command $command -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

# Function to check Docker permissions
function Test-DockerPermissions {
    try {
        $dockerInfo = docker info 2>&1
        if ($LASTEXITCODE -ne 0) {
            if ($dockerInfo -match "permission denied") {
                Add-Warning "Docker is installed but you don't have permission to run it."
                Write-Log "  This usually means your user is not in the 'docker-users' group."
                Write-Log "  Add your user to the 'docker-users' group and log out and back in."
                return $false
            }
            Add-Error "Docker is not running or not properly configured."
            Write-Log "  Make sure Docker Desktop is running and try again."
            return $false
        }
        return $true
    } catch {
        Add-Error "Failed to check Docker permissions: $_"
        return $false
    }
}

# Main script execution
Write-Section "Gluesync Pre-flight Checker"
Write-Log "Checking system requirements for Gluesync v2.1"
Write-Log "Date: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`n"

# Get container type selection from user
$containerType = Get-ContainerTypeSelection

if ($containerType -eq "1") {
    Write-Log "`n✅ Selected: WSL/Linux Containers"
    Write-Log "Proceeding with Linux container checks..."
    $useWindowsContainers = $false
} else {
    Write-Log "`n✅ Selected: Windows Containers"
    Write-Log "Proceeding with Windows container checks..."
    $useWindowsContainers = $true
}

# 1. Check Docker
Write-Section "Docker Check"
$dockerInstalled = $false

if ($useWindowsContainers) {
    # Windows Containers specific checks
    Test-WindowsContainerRequirements
    
    if (Test-CommandExists "docker") {
        $dockerVersion = docker --version
        Write-Log "Docker found: $dockerVersion"
        
        # Check if it's Docker CE or Docker Desktop
        try {
            $dockerInfo = docker info 2>&1
            if ($dockerInfo -match "Server Version:") {
                Write-Log "Docker Engine detected"
                
                # Check if using Windows containers
                $osType = docker info --format '{{.OSType}}' 2>&1
                if ($osType -eq "windows") {
                    Write-Log "✅ Docker is configured for Windows containers"
                    $dockerInstalled = $true
                } else {
                    Add-Warning "Docker is currently using Linux containers"
                    Write-Log "  Switch to Windows containers or reinstall Docker for Windows"
                }
            }
        } catch {
            Add-Warning "Could not determine Docker configuration"
        }
    } else {
        Add-Warning "Docker is not installed."
        Write-Log "For Windows Containers, you can either:"
        Write-Log "  1. Install Docker CE for Windows Containers (recommended for servers)"
        Write-Log "  2. Install Docker Desktop and switch to Windows containers"
        
        # Show Docker installation instructions
        Show-DockerInstallationInstructions
        $dockerInstalled = $false
    }
} else {
    # Linux Containers (WSL) - original check
    if (Test-CommandExists "docker") {
        $dockerVersion = docker --version
        Write-Log "Docker found: $dockerVersion"
        
        # Check Docker permissions
        if (Test-DockerPermissions) {
            $dockerInstalled = $true
        }
    } else {
        Add-Error "Docker is not installed. Please install Docker for Windows."
        Write-Log "  Download from: https://docs.molo17.com/gluesync/v2.1/deploy-and-run/docker-install.html"
    }
}

# 2. Check Docker Compose
Write-Section "Docker Compose Check"
$dockerComposeInstalled = $false

if ($useWindowsContainers) {
    # Windows Containers specific Docker Compose check
    if (Test-CommandExists "docker-compose") {
        $dockerComposeVersion = docker-compose --version
        Write-Log "Docker Compose found: $dockerComposeVersion"
        $dockerComposeInstalled = $true
    } else {
        # Check in Program Files
        $dockerComposePath = "$Env:ProgramFiles\Docker\docker-compose.exe"
        if (Test-Path $dockerComposePath) {
            try {
                $version = & $dockerComposePath version 2>&1
                Write-Log "Docker Compose found at: $dockerComposePath"
                Write-Log "Version: $version"
                $dockerComposeInstalled = $true
            } catch {
                Add-Warning "Docker Compose executable found but could not get version"
            }
        }
    }
    
    # Try Docker Compose v2 plugin
    if (-not $dockerComposeInstalled) {
        try {
            $composeV2 = docker compose version 2>&1
            if ($LASTEXITCODE -eq 0) {
                $dockerComposeV2Version = ($composeV2 | Select-String -Pattern "Docker Compose version (\S+)").Matches.Groups[1].Value
                Write-Log "Docker Compose (plugin) found: v$dockerComposeV2Version"
                $dockerComposeInstalled = $true
            }
        } catch {
            # Ignore if docker compose command fails
        }
    }
    
    if (-not $dockerComposeInstalled) {
        Add-Warning "Docker Compose not found."
        Write-Log ""
        Write-Log "Docker Compose is required for Gluesync. Please install it by following the Gluesync documentation:"
        Write-Log "🔗 https://docs.molo17.com/gluesync/v2.1/deploy-and-run/docker-install.html"
        Write-Log ""
        Write-Log "After installation, please run this pre-flight check again to verify the setup."
    }
} else {
    # Linux Containers - original check
    if (Test-CommandExists "docker-compose") {
        $dockerComposeVersion = docker-compose --version
        Write-Log "Docker Compose (legacy) found: $dockerComposeVersion"
        $dockerComposeInstalled = $true
    }
    
    # Check for Docker Compose plugin (v2)
    try {
        $composeV2 = docker compose version 2>&1
        if ($LASTEXITCODE -eq 0) {
            $dockerComposeV2Version = ($composeV2 | Select-String -Pattern "Docker Compose version (\S+)").Matches.Groups[1].Value
            Write-Log "Docker Compose (plugin) found: v$dockerComposeV2Version"
            $dockerComposeInstalled = $true
        }
    } catch {
        # Ignore if docker compose command fails
    }
    
    if (-not $dockerComposeInstalled) {
        Add-Warning "Docker Compose not found."
        Write-Log ""
        Write-Log "Docker Compose is required for Gluesync. Please install it by following the Gluesync documentation:"
        Write-Log "🔗 https://docs.molo17.com/gluesync/v2.1/deploy-and-run/docker-install.html"
        Write-Log ""
        Write-Log "After installation, please run this pre-flight check again to verify the setup."
    }
}

# Function to prompt for container type selection
function Get-ContainerTypeSelection {
    Write-Host "`n" -NoNewline
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "    Container Type Selection" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Please select the container type you want to use:"
    Write-Host ""
    Write-Host "  1. WSL/Linux Containers" -ForegroundColor Yellow
    Write-Host "     - Uses Windows Subsystem for Linux"
    Write-Host "     - Most Docker images are Linux-based"
    Write-Host "     - Better compatibility with Docker ecosystem"
    Write-Host ""
    Write-Host "  2. Windows Containers (Recommended)" -ForegroundColor Green
    Write-Host "     - Native Windows container support"
    Write-Host "     - Better performance on Windows"
    Write-Host "     - Recommended for Gluesync on Windows"
    Write-Host ""
    
    do {
        $selection = Read-Host "Enter your choice (1 or 2)"
        if ($selection -eq "1" -or $selection -eq "2") {
            break
        }
        Write-Host "Invalid selection. Please enter 1 or 2." -ForegroundColor Red
    } while ($true)
    
    return $selection
}

# Function to check if WSL is installed and enabled
function Test-WslInstalled {
    try {
        # First check if wsl command is available
        $wslAvailable = Get-Command wsl -ErrorAction SilentlyContinue
        if (-not $wslAvailable) {
            Write-Log "⚠️  WSL is not installed"
            return $false, $null
        }

        # Check WSL version and status
        $wslList = wsl --list --verbose 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Log "⚠️  WSL is not properly configured"
            return $false, $null
        }

        # Parse WSL output - look for lines with asterisk indicating default distribution
        $wslLines = $wslList | Where-Object { $_ -match '\*' -and $_ -match '\w+' }
        if (-not $wslLines) {
            # If no default found, look for any distribution
            $wslLines = $wslList | Where-Object { $_ -match '^\s*\w+\s+' -and $_ -notmatch 'NAME' }
        }
        
        if (-not $wslLines) {
            Write-Log "⚠️  No WSL distributions found"
            return $false, $null
        }

        # Take the first valid distribution
        $wslInfo = $wslLines | Select-Object -First 1
        
        # Parse the WSL line format: "* Ubuntu    Stopped         2" or "  Ubuntu    Running         2"
        $wslParts = $wslInfo -split '\s+' | Where-Object { $_ -ne '' -and $_ -ne '*' }
        if ($wslParts.Count -lt 3) {
            Write-Log "⚠️  Could not parse WSL distribution information: $wslInfo"
            return $false, $null
        }

        $wslDistro = $wslParts[0]
        $wslState = $wslParts[1].ToLower()
        $wslVersion = $wslParts[2]

        if ($wslState -eq "running") {
            Write-Log "✅ WSL $wslVersion is installed and running ($wslDistro)"
        } else {
            Write-Log "⚠️  WSL $wslVersion is installed but not running ($wslDistro)"
            Write-Log "   - Start WSL with: wsl --distribution $wslDistro"
        }
        
        return $true, $wslVersion, $wslState
    } catch {
        Write-Log "⚠️  WSL check failed: $_"
        return $false, $null, $null
    }
}

# Docker installation helper function
function Show-DockerInstallationInstructions {
    Write-Section "Docker Installation Required"
    Write-Log "Docker is not installed or not properly configured on this system."
    Write-Log ""
    Write-Log "Please follow the Docker installation instructions in the Gluesync documentation:"
    Write-Log "🔗 https://docs.molo17.com/gluesync/v2.1/deploy-and-run/docker-install.html#_windows"
    Write-Log ""
    Write-Log "After installation, please run this pre-flight check again to verify the setup."
}

# Docker Compose installation instructions
function Show-DockerComposeInstructions {
    Write-Section "Docker Compose Required"
    Write-Log "Docker Compose is required for Gluesync but was not found on this system."
    Write-Log ""
    Write-Log "Please follow the Docker Compose installation instructions in the Gluesync documentation:"
    Write-Log "🔗 https://docs.molo17.com/gluesync/v2.1/deploy-and-run/docker-install.html#_windows"
    Write-Log ""
    Write-Log "After installation, please run this pre-flight check again to verify the setup."
}

# Function to check Windows container requirements
function Test-WindowsContainerRequirements {
    Write-Section "Windows Container Requirements Check"
    
    # Check if running as Administrator
    $isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")
    if (-not $isAdmin) {
        Add-Warning "Not running as Administrator. Some checks may not work properly."
        Write-Log "For full functionality with Windows Containers, run this script as Administrator."
    }
    
    # Check Windows version for container support
    $os = Get-CimInstance Win32_OperatingSystem
    $osCaption = $os.Caption
    
    if ($osCaption -match "Windows Server") {
        Write-Log "✅ Windows Server detected: $osCaption"
        Write-Log "  Windows Server supports Windows Containers natively"
    } elseif ($osCaption -match "Windows 10" -or $osCaption -match "Windows 11") {
        # Check for Pro, Enterprise, or Education editions
        if ($osCaption -match "Pro" -or $osCaption -match "Enterprise" -or $osCaption -match "Education") {
            Write-Log "✅ Compatible Windows edition detected: $osCaption"
        } else {
            Add-Warning "Windows Home edition detected. Windows Containers require Pro, Enterprise, or Education edition."
        }
    } else {
        Add-Error "Unsupported Windows version for Windows Containers: $osCaption"
    }
    
    # Check if Hyper-V is enabled (required for Windows containers)
    try {
        $hyperv = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All -ErrorAction SilentlyContinue
        if ($hyperv -and $hyperv.State -eq "Enabled") {
            Write-Log "✅ Hyper-V is enabled"
        } else {
            Add-Warning "Hyper-V is not enabled. Windows Containers require Hyper-V."
            Write-Log "  Enable with: Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V-All"
        }
    } catch {
        Write-Log "Could not check Hyper-V status (requires Administrator privileges)"
    }
    
    # Check if Containers feature is enabled
    try {
        $containers = Get-WindowsOptionalFeature -Online -FeatureName Containers -ErrorAction SilentlyContinue
        if ($containers -and $containers.State -eq "Enabled") {
            Write-Log "✅ Containers feature is enabled"
        } else {
            Add-Warning "Containers feature is not enabled."
            Write-Log "  Enable with: Enable-WindowsOptionalFeature -Online -FeatureName Containers"
        }
    } catch {
        Write-Log "Could not check Containers feature status (requires Administrator privileges)"
    }
}

# Function to check if Linux containers are being used
function Test-LinuxContainers {
    try {
        $dockerInfo = docker info --format '{{.OSType}}' 2>&1
        if ($LASTEXITCODE -eq 0) {
            $isLinux = ($dockerInfo -eq "linux")
            Write-Log "✅ Docker is using $dockerInfo containers"
            return $isLinux
        }
        return $false
    } catch {
        Write-Log "⚠️  Failed to check container type: $_"
        return $false
    }
}

# 3. Check Docker functionality
if ($dockerInstalled) {
    Write-Section "Docker Functionality Test"
    
    if ($useWindowsContainers) {
        # Windows Containers specific functionality tests
        Write-Log "Testing Windows container functionality..."
        
        # Check if Docker service is running
        try {
            $dockerService = Get-Service -Name "Docker" -ErrorAction SilentlyContinue
            if ($dockerService) {
                if ($dockerService.Status -eq "Running") {
                    Write-Log "✅ Docker service is running"
                } else {
                    Add-Warning "Docker service is not running. Status: $($dockerService.Status)"
                    Write-Log "  Start the service with: Start-Service Docker"
                }
            } else {
                Write-Log "Docker service not found (may be using Docker Desktop)"
            }
        } catch {
            Write-Log "Could not check Docker service status"
        }
        
        # Verify Windows containers mode
        $currentOsType = docker info --format '{{.OSType}}' 2>&1
        if ($currentOsType -ne "windows") {
            Add-Warning "Docker is not in Windows containers mode"
            Write-Log "  Current mode: $currentOsType"
            Write-Log "  To switch to Windows containers:"
            Write-Log "    - If using Docker Desktop: Right-click tray icon > 'Switch to Windows containers...'"
            Write-Log "    - If using Docker CE: This should be the default mode"
        }
    } else {
        # Linux Containers (WSL) functionality tests
        # Check WSL installation
        Write-Log "Checking Windows Subsystem for Linux (WSL) status..."
        $wslInstalled, $wslVersion = Test-WslInstalled
        if (-not $wslInstalled) {
            Add-Warning "WSL is not properly installed or configured."
            Write-Log "  - Gluesync requires WSL 2 for Linux containers on Windows"
            Write-Log "  - Install WSL 2: wsl --install -d Ubuntu"
            Write-Log "  - Or update existing WSL: wsl --update"
        } elseif ($wslVersion -ne "2") {
            Add-Warning "WSL 1 is installed but WSL 2 is recommended for better performance."
            Write-Log "  - Convert to WSL 2: wsl --set-version <distro> 2"
            Write-Log "  - Set WSL 2 as default: wsl --set-default-version 2"
        }
        
        # Check container type
        $usingLinuxContainers = Test-LinuxContainers
        if (-not $usingLinuxContainers) {
            Add-Warning "Docker is not using Linux containers"
            Write-Log "  - Right-click Docker Desktop icon in the system tray"
            Write-Log "  - Select 'Switch to Linux containers...'"
            Write-Log "  - Or run: & 'C:\Program Files\Docker\Docker\Docker Desktop.exe' -SwitchDaemon"
        }
    }
    
    # Check Docker registry accessibility
    Write-Log "Checking Docker registry accessibility..."
    $registryAccessible = $false
    
    # Try multiple methods to check registry accessibility
    try {
        # Method 1: Try Invoke-WebRequest with relaxed security
        $registryCheck = Invoke-WebRequest -Uri "https://registry-1.docker.io/" -UseBasicParsing -Method Head -TimeoutSec 10 -ErrorAction Stop
        if ($registryCheck.StatusCode -eq 200) {
            $registryAccessible = $true
            Write-Log "✅ Docker Hub registry is accessible (via Invoke-WebRequest)"
        }
    } catch {
        Write-Log "Invoke-WebRequest failed: $($_.Exception.Message)"
        
        # Method 2: Try Test-NetConnection as fallback
        try {
            $netTest = Test-NetConnection -ComputerName "registry-1.docker.io" -Port 443 -InformationLevel Quiet -ErrorAction Stop
            if ($netTest) {
                $registryAccessible = $true
                Write-Log "✅ Docker Hub registry is accessible (via Test-NetConnection)"
            }
        } catch {
            Write-Log "Test-NetConnection failed: $($_.Exception.Message)"
            
            # Method 3: Try simple ping as last resort
            try {
                $pingResult = Test-Connection -ComputerName "registry-1.docker.io" -Count 1 -Quiet -ErrorAction Stop
                if ($pingResult) {
                    $registryAccessible = $true
                    Write-Log "✅ Docker Hub registry host is reachable (via ping)"
                    Write-Log "  Note: HTTPS connectivity not verified, but host is reachable"
                }
            } catch {
                Write-Log "Ping test failed: $($_.Exception.Message)"
            }
        }
    }
    
    if ($registryAccessible) {
        
        if ($useWindowsContainers) {
            # Test with Windows container
            Write-Log "Testing Docker with Windows-based container..."
            try {
                # Try to pull a Windows nano server image
                $windowsImage = "mcr.microsoft.com/windows/nanoserver:ltsc2022"
                Write-Log "Pulling Windows test image: $windowsImage"
                docker pull $windowsImage 2>&1 | ForEach-Object { Write-Log "  $_" }
                
                if ($LASTEXITCODE -eq 0) {
                    # Run a simple Windows container test
                    $testOutput = docker run --rm $windowsImage cmd /c echo "Windows container test successful" 2>&1
                    if ($LASTEXITCODE -eq 0) {
                        Write-Log "✅ Docker Windows container test: SUCCESS"
                        Write-Log "  Output: $testOutput"
                    } else {
                        Add-Warning "Failed to run Windows container test. Exit code: $LASTEXITCODE"
                        Write-Log $testOutput
                    }
                } else {
                    Add-Warning "Failed to pull Windows test image"
                    Write-Log "  This may be due to Windows version compatibility"
                    Write-Log "  Try a different Windows base image version"
                }
            } catch {
                Add-Warning "Failed to test Windows container: $_"
                Write-Log "  - Ensure Windows containers are properly configured"
                Write-Log "  - Check that your Windows version matches the container base image"
            }
        } else {
            # Only run hello-world if using Linux containers
            $usingLinuxContainers = Test-LinuxContainers
            if ($usingLinuxContainers) {
                Write-Log "Testing Docker with Linux-based hello-world container..."
                try {
                    # Force pull the Linux version of hello-world
                    docker pull --platform linux/amd64 hello-world 2>&1 | ForEach-Object { Write-Log "  $_" }
                    
                    $helloWorld = docker run --rm --platform linux/amd64 hello-world 2>&1
                    if ($LASTEXITCODE -eq 0) {
                        $isLinuxContainer = $helloWorld -match "linux/amd64" -or $helloWorld -match "Linux"
                        if ($isLinuxContainer) {
                            Write-Log "✅ Docker Linux container test: SUCCESS"
                        } else {
                            Add-Warning "Docker hello-world ran, but may not be using Linux containers"
                            Write-Log $helloWorld
                        }
                    } else {
                        Add-Warning "Docker hello-world test failed. Exit code: $LASTEXITCODE"
                        Write-Log $helloWorld
                    }
                } catch {
                    Add-Warning "Failed to run hello-world container: $_"
                    Write-Log "  - This could be due to network issues or image pull restrictions."
                    Write-Log "  - Try running: docker pull --platform linux/amd64 hello-world"
                }
            } else {
                Add-Warning "Skipping Linux container test - Docker is not using Linux containers"
            }
        }
    } else {
        Add-Warning "Cannot reach Docker Hub registry. Network or proxy issues may prevent pulling images."
        Write-Log "  - Check your network connection and proxy settings if behind a corporate network."
        Write-Log "  - If using a proxy, ensure Docker is configured to use it."
    }
} else {
    Write-Log "Skipping Docker functionality tests - Docker is not properly installed or configured"
    Add-Warning "Docker functionality tests were skipped due to installation/configuration issues"
}

# 4. System Resources Check
Write-Section "System Resources Check"

# CPU Information
$cpu = Get-CimInstance Win32_Processor
$cores = $cpu.NumberOfCores
$logicalProcessors = $cpu.NumberOfLogicalProcessors
Write-Log "CPU: $($cpu.Name)"
Write-Log "  Cores: $cores"
Write-Log "  Logical Processors: $logicalProcessors"

# RAM Information
$memory = Get-CimInstance Win32_OperatingSystem
$totalMemoryGB = [math]::Round($memory.TotalVisibleMemorySize / 1MB, 2)
$freeMemoryGB = [math]::Round($memory.FreePhysicalMemory / 1MB, 2)
$usedMemoryGB = $totalMemoryGB - $freeMemoryGB
Write-Log "Memory:"
Write-Log "  Total: ${totalMemoryGB}GB"
Write-Log "  Used: ${usedMemoryGB}GB"
Write-Log "  Free: ${freeMemoryGB}GB"

# Check RAM requirement (8GB minimum)
if ($totalMemoryGB -lt 8) {
    Add-Warning "System has only ${totalMemoryGB}GB of RAM. Gluesync recommends at least 8GB of RAM."
}

# Disk Space
$systemDrive = Get-PSDrive -PSProvider 'FileSystem' | Where-Object { $_.Name -eq $env:SystemDrive[0] }
$freeSpaceGB = [math]::Round($systemDrive.Free / 1GB, 2)
$totalSpaceGB = [math]::Round($systemDrive.Used / 1GB + $systemDrive.Free / 1GB, 2)
$usedSpaceGB = [math]::Round($systemDrive.Used / 1GB, 2)
$usedPercentage = [math]::Round(($systemDrive.Used / ($systemDrive.Used + $systemDrive.Free)) * 100, 2)

Write-Log "Disk Space (${env:SystemDrive}):"
Write-Log "  Total: ${totalSpaceGB}GB"
Write-Log "  Used: ${usedSpaceGB}GB (${usedPercentage}%)"
Write-Log "  Free: ${freeSpaceGB}GB"

# Check disk space requirement (50GB minimum free)
if ($freeSpaceGB -lt 50) {
    Add-Warning "Low disk space: ${freeSpaceGB}GB free. Gluesync recommends at least 50GB of free disk space."
}

# 5. Network Check
Write-Section "Network Check"

# Hostname
$hostname = [System.Net.Dns]::GetHostName()
Write-Log "Hostname: $hostname"

# IP Addresses
Write-Log "Network Interfaces:"
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.PrefixOrigin -ne 'WellKnown' } | ForEach-Object {
    Write-Log "  $($_.InterfaceAlias): $($_.IPAddress)"
}

# Docker Registry Connectivity Check
Write-Section "Docker Registry Connectivity"

$dockerEndpoints = @(
    @{ Name = "Docker Hub"; Host = "registry-1.docker.io"; Port = 443 },
    @{ Name = "Docker Registry"; Host = "registry.hub.docker.com"; Port = 443 }
)

$allEndpointsReachable = $true

foreach ($endpoint in $dockerEndpoints) {
    $hostName = $endpoint.Host
    $port = $endpoint.Port
    $displayName = $endpoint.Name
    
    # Check DNS resolution
    try {
        $dnsResult = Resolve-DnsName -Name $hostName -ErrorAction Stop -QuickTimeout -DnsOnly
        Write-Log "✅ $displayName ($hostName): DNS resolved"
        
        # Check HTTPS connectivity
        try {
            $tcpTest = Test-NetConnection -ComputerName $hostName -Port $port -WarningAction SilentlyContinue -ErrorAction Stop
            
            if ($tcpTest.TcpTestSucceeded) {
                Write-Log "   ✅ HTTPS (Port $port): Accessible"
                
                # Additional check with Invoke-WebRequest for HTTP-level connectivity
                try {
                    $testUrl = "https://$hostName/v2/"
                    $headers = @{
                        'User-Agent' = 'GluesyncPreflight/1.0'
                    }
                    $response = Invoke-WebRequest -Uri $testUrl -Method Head -UseBasicParsing -TimeoutSec 5 -Headers $headers -ErrorAction Stop
                    
                    if ($response.StatusCode -in @(200, 401, 403)) {
                        Write-Log "   ✅ API Endpoint: Accessible (HTTP $($response.StatusCode))"
                    } else {
                        Write-Log "   ℹ️  API Endpoint: Unexpected status code $($response.StatusCode)"
                        $allEndpointsReachable = $false
                    }
                } catch [System.Net.WebException] {
                    if ($_.Exception.Response) {
                        $statusCode = [int][System.Net.HttpStatusCode]$_.Exception.Response.StatusCode
                        if ($statusCode -eq 401) {
                            # 401 is expected for unauthenticated requests to Docker Hub
                            Write-Log "   ✅ API Endpoint: Accessible (Authentication required)"
                        } else {
                            Write-Log "   ⚠️  API Endpoint: HTTP Error $statusCode"
                            $allEndpointsReachable = $false
                        }
                    } else {
                        Write-Log "   ⚠️  API Endpoint: $($_.Exception.Message)"
                        $allEndpointsReachable = $false
                    }
                } catch {
                    Write-Log "   ⚠️  API Endpoint: $($_.Exception.Message)"
                    $allEndpointsReachable = $false
                }
            } else {
                Write-Log "   ❌ HTTPS (Port $port): Connection failed"
                Add-Warning "Cannot connect to $displayName ($hostName) on port $port"
                $allEndpointsReachable = $false
            }
        } catch {
            Write-Log "   ❌ HTTPS (Port $port): $($_.Exception.Message)"
            Add-Warning "Connection to $displayName ($hostName) failed: $($_.Exception.Message)"
            $allEndpointsReachable = $false
        }
    } catch {
        Write-Log "❌ $displayName ($hostName): DNS resolution failed"
        Add-Warning "DNS resolution failed for $displayName ($hostName)"
        $allEndpointsReachable = $false
    }
}

if ($allEndpointsReachable) {
    Write-Log "✅ All required Docker endpoints are accessible"
} else {
    Add-Warning "Some Docker endpoints are not accessible. Please ensure your firewall/proxy allows HTTPS access to:"
    Add-Warning "  - .docker.io, .docker.com, *.dckr.io and related subdomains"
    Add-Warning "  - Required ports: 443 (HTTPS)"
}

# 6. Windows Version Check
Write-Section "Windows Information"
$os = Get-CimInstance Win32_OperatingSystem
$windowsVersion = "$($os.Caption) $($os.Version) (Build $($os.BuildNumber))"
Write-Log "OS: $windowsVersion"
Write-Log "Architecture: $($os.OSArchitecture)"

# Check Windows version compatibility
if ($os.Version -lt "10.0.18362") {  # Windows 10 1903 or later
    Add-Warning "Unsupported Windows version. Gluesync requires Windows 10 1903 (build 18362) or later."
}

# 7. Summary
Write-Section "Pre-flight Check Summary"

if ($script:Errors.Count -eq 0 -and $script:Warnings.Count -eq 0) {
    Write-Log "✅ All checks passed successfully! You're good to go!" -Level "Info"
    $exitCode = 0
} else {
    if ($script:Errors.Count -gt 0) {
        Write-Log "❌ Found $($script:Errors.Count) error(s):" -Level "Error"
        foreach ($errorMsg in $script:Errors) {
            Write-Log "  - $errorMsg" -Level "Error"
        }
        $exitCode = 1
    }
    
    if ($script:Warnings.Count -gt 0) {
        Write-Log "`n⚠️  Found $($script:Warnings.Count) warning(s):" -Level "Warning"
        foreach ($warning in $script:Warnings) {
            Write-Log "  - $warning" -Level "Warning"
        }
        if (-not $exitCode) { $exitCode = 0 }
    }
}

Write-Log "`nA detailed report has been saved to: $(Resolve-Path $reportFile)"
Write-Log "For detailed requirements, visit: https://docs.molo17.com/Gluesync/v2.1/introduction/system-requirements.html"

exit $exitCode
