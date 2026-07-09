#!/bin/bash

# Pre-flight checker script for Gluesync installation
# Checks Docker, Docker Compose, and system requirements as per MOLO17 Gluesync v2.1

# Setup output logging
timestamp=$(date +"%Y%m%d_%H%M%S")
REPORT_FILE="gluesync_preflight_check_${timestamp}.txt"

echo "=== Gluesync Pre-flight Checker ===" | tee "$REPORT_FILE"
echo "Checking system requirements for Gluesync v2.1" | tee -a "$REPORT_FILE"
echo "Date: $(date)" | tee -a "$REPORT_FILE"
echo "===================================" | tee -a "$REPORT_FILE"
echo "" | tee -a "$REPORT_FILE"

# Arrays to store messages
declare -a WARNINGS=()
declare -a ERRORS=()

# Function to log messages to both console and report file
log_message() {
    echo "$1" | tee -a "$REPORT_FILE"
}

# Function to add warning message
add_warning() {
    WARNINGS+=("$1")
    echo "⚠️  WARNING: $1" | tee -a "$REPORT_FILE"
}

# Function to add error message
add_error() {
    ERRORS+=("$1")
    echo "❌ ERROR: $1" | tee -a "$REPORT_FILE"
}

# Function to print section headers
print_section() {
    log_message "=== $1 ==="
}

# Function to check command existence
check_command() {
    if command -v "$1" &> /dev/null; then
        log_message "$1 found: $($1 $2 2>&1)"
        return 0
    else
        add_error "$1 is not installed"
        return 1
    fi
}

# Function to check if user has permission to run docker
check_docker_permission() {
    if ! docker info &>/dev/null; then
        if [[ $EUID -ne 0 ]]; then
            add_warning "Docker is installed but you don't have permission to run it."
            log_message "   This usually means Docker is running in rootless mode or your user is not in the docker group."
            log_message "   You have two options:"
            log_message "   1. Run this script with sudo: sudo $0"
            log_message "   2. Add your user to the docker group: sudo usermod -aG docker $USER"
            log_message "      After adding to docker group, log out and log back in or run: newgrp docker"
            return 1
        else
            add_error "Docker is not running or not properly configured."
            log_message "   Please start the Docker service: sudo systemctl start docker"
            return 1
        fi
    fi
    return 0
}

# 1. Check Docker
print_section "Docker Check"
check_command "docker" "--version"
DOCKER_INSTALLED=$?

# Check Docker permissions if installed
if [ $DOCKER_INSTALLED -eq 0 ]; then
    check_docker_permission
    DOCKER_PERMISSION=$?
    if [ $DOCKER_PERMISSION -ne 0 ]; then
        DOCKER_INSTALLED=1  # Mark as not installed to fail gracefully
    fi
fi

# 2. Check Docker Compose
print_section "Docker Compose Check"
check_command "docker" "compose version"
DOCKER_COMPOSE_INSTALLED=$?

# 3. Check Docker functionality
if [ $DOCKER_INSTALLED -eq 0 ]; then
    print_section "Docker Functionality Test"
    
    # Check Docker registry accessibility
    log_message "Checking Docker registry accessibility..."
    if ! curl -s --head --request GET https://registry-1.docker.io/ > /dev/null; then
        add_warning "Cannot reach Docker Hub registry. Network or proxy issues may prevent pulling images."
        log_message "  - Check your network connection and proxy settings if behind a corporate network."
        log_message "  - If using a proxy, ensure Docker is configured to use it."
    else
        log_message "✅ Docker Hub registry is accessible"
        
        # Only run hello-world if registry is accessible
        log_message "Testing Docker with hello-world container..."
        if docker run --rm hello-world > /dev/null 2>&1; then
            log_message "✅ Docker hello-world test: SUCCESS"
        else
            if docker info &>/dev/null; then
                add_warning "Docker daemon is running but hello-world test failed."
                log_message "  - This could be due to network issues or image pull restrictions."
                log_message "  - Try running: docker pull hello-world"
            else
                add_error "Docker daemon is not running or not accessible."
                log_message "  - Start Docker daemon and try again."
                log_message "  - On Linux: sudo systemctl start docker"
                log_message "  - On macOS: open -a Docker"
            fi
        fi
    fi
else
    log_message "Skipping Docker functionality tests - Docker is not properly installed or configured"
    add_warning "Docker functionality tests were skipped due to installation/configuration issues"
fi

# 4. System Resources Check
print_section "System Resources Check"

# CPU Details
log_message "CPU Details:"
if [ -f /proc/cpuinfo ]; then
    CORES=$(grep -c ^processor /proc/cpuinfo 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo "unknown")
    SOCKETS=$(grep "physical id" /proc/cpuinfo 2>/dev/null | sort -u | wc -l || echo "unknown")
    MODEL=$(grep "model name" /proc/cpuinfo 2>/dev/null | head -1 | awk -F: '{print $2}' | xargs || echo "unknown")
    
    log_message "  Cores: $CORES"
    log_message "  Sockets: $SOCKETS"
    log_message "  Model: $MODEL"
    
    # Check if we have enough CPU resources
    if [ "$CORES" != "unknown" ] && [ "$CORES" -lt 2 ]; then
        add_warning "System has only $CORES CPU core(s). Gluesync recommends at least 2 CPU cores for better performance."
    fi
else
    log_message "  Unable to retrieve CPU details"
    add_warning "Could not retrieve CPU information. Some checks may be incomplete."
fi

# RAM
log_message "RAM:"
if command -v free >/dev/null 2>&1; then
    if free -h > /dev/null 2>&1; then
        TOTAL_RAM_MB=$(($(grep MemTotal /proc/meminfo 2>/dev/null | awk '{print $2}') / 1024)) 2>/dev/null || \
        TOTAL_RAM_MB=$(sysctl -n hw.memsize 2>/dev/null | awk '{print int($1/1024/1024)}') 2>/dev/null || \
        TOTAL_RAM_MB="unknown"
        
        if [ "$TOTAL_RAM_MB" != "unknown" ]; then
            log_message "  Total: ${TOTAL_RAM_MB}MB"
            # Check for minimum RAM requirement (4GB = 4096MB)
            if [ "$TOTAL_RAM_MB" -lt 4096 ]; then
                add_warning "System has only ${TOTAL_RAM_MB}MB of RAM. Gluesync recommends at least 4GB of RAM for optimal performance."
            fi
        else
            log_message "  Unable to determine total RAM"
            add_warning "Could not determine total RAM. Some checks may be incomplete."
        fi
    else
        log_message "  Unable to retrieve RAM details"
        add_warning "Could not retrieve RAM information. Some checks may be incomplete."
    fi
else
    log_message "  'free' command not available. Cannot check RAM details."
    add_warning "Could not check RAM information. 'free' command not found."
fi

# Disk Space
log_message "Disk Space:"
if command -v df >/dev/null 2>&1; then
    if df -h > /dev/null 2>&1; then
        # Display disk space info
        df -h / | awk 'NR==2 {print "  Mount: " $6 "\n  Total: " $2 "\n  Used: " $3 " (" $5 ")\n  Available: " $4}' | tee -a "$REPORT_FILE"
        
        # Check for minimum disk space (50GB = 52428800 KB)
        AVAIL_SPACE_KB=$(df -k / | awk 'NR==2 {print $4}' 2>/dev/null)
        if [ -n "$AVAIL_SPACE_KB" ] && [ "$AVAIL_SPACE_KB" -lt 52428800 ]; then
            AVAIL_SPACE_GB=$(echo "scale=2; $AVAIL_SPACE_KB/1048576" | bc)
            add_warning "Low disk space available (${AVAIL_SPACE_GB}GB). Gluesync recommends at least 50GB of free disk space."
        fi
    else
        log_message "  Unable to retrieve disk space details"
        add_warning "Could not retrieve disk space information. Some checks may be incomplete."
    fi
else
    log_message "  'df' command not available. Cannot check disk space."
    add_warning "Could not check disk space. 'df' command not found."
fi

# Current Time
log_message "Current Time: $(date)"

# Current User
log_message "Current User: $USER"

# OS Details
log_message "OS Details:"
if [ -f /etc/os-release ]; then
    . /etc/os-release
    log_message "  OS: $NAME $VERSION"
else
    log_message "  OS: $(uname -s) $(uname -r)"
fi

# Kernel Version
log_message "Kernel: $(uname -r)"

# Architecture
log_message "Architecture: $(uname -m)"

# Network Status
print_section "Network Information"
log_message "Hostname: $(hostname)"

# Check Docker registry connectivity
log_message "Docker Registry Connectivity:"
DOCKER_ENDPOINTS=(
    "https://registry-1.docker.io/v2/"
    "https://registry.hub.docker.com/v2/"
)
ALL_ENDPOINTS_REACHABLE=true

# Function to test HTTPS connectivity
check_https_connectivity() {
    local url=$1
    if command -v curl &>/dev/null; then
        curl -s -I --connect-timeout 5 "$url" &>/dev/null
        return $?
    elif command -v wget &>/dev/null; then
        wget -q --spider --timeout=5 --tries=1 "$url" &>/dev/null
        return $?
    else
        # Fallback to ping if neither curl nor wget is available
        local domain=$(echo "$url" | sed -E 's/^https?:\/\/([^/]+).*/\1/')
        ping -c 1 -W 2 "$domain" &>/dev/null
        return $?
    fi
}

for endpoint in "${DOCKER_ENDPOINTS[@]}"; do
    domain=$(echo "$endpoint" | sed -E 's/^https?:\/\/([^/]+).*/\1/')
    
    # Check DNS resolution first
    log_message -n "  $domain DNS: "
    if nslookup "$domain" &>/dev/null 2>&1; then
        log_message "Resolved"
        
        # Check HTTPS connectivity
        log_message -n "  $domain HTTPS: "
        if check_https_connectivity "$endpoint"; then
            log_message "Accessible"
        else
            log_message "Unreachable"
            add_warning "Cannot access Docker endpoint: $endpoint"
            ALL_ENDPOINTS_REACHABLE=false
        fi
    else
        log_message "Failed to resolve"
        add_warning "DNS resolution failed for: $domain"
        ALL_ENDPOINTS_REACHABLE=false
    fi
done

if [ "$ALL_ENDPOINTS_REACHABLE" = true ]; then
    log_message "✅ All required Docker endpoints are accessible"
else
    add_warning "Some Docker endpoints are not accessible. Please ensure your firewall/proxy allows HTTPS access to:"
    add_warning "  - .docker.io, .docker.com, *.dckr.io and related subdomains"
    add_warning "  - Required ports: 443 (HTTPS)"
fi
# Network Interfaces
log_message "Network Interfaces:"
if command -v ip >/dev/null 2>&1; then
    if ip addr show > /dev/null 2>&1; then
        ip -brief addr show | while read -r line; do
            log_message "  $line"
        done
    else
        log_message "  Unable to retrieve network interfaces using 'ip' command"
        add_warning "Could not retrieve network interface information"
    fi
else
    log_message "  'ip' command not available. Using ifconfig..."
    if command -v ifconfig >/dev/null 2>&1; then
        ifconfig | grep -v "^$" | while read -r line; do
            if [[ $line =~ ^[^[:space:]] ]]; then
                log_message "  $line"
            else
                log_message "  $line"
            fi
        done 2>/dev/null || log_message "  Unable to retrieve network interfaces"
    else
        log_message "  Neither 'ip' nor 'ifconfig' commands are available"
        add_warning "Could not retrieve network interface information"
    fi
fi

# Check Gluesync System Requirements
print_section "Gluesync v2.1 Requirements Check"
log_message "Checking against MOLO17 Gluesync system requirements..."

# CPU Check (Minimum 4 cores recommended)
if [ "$CORES" != "unknown" ]; then
    if [ "$CORES" -ge 4 ]; then
        log_message "✅ CPU Cores: $CORES (Meets requirement: >= 4 cores)"
    else
        add_warning "CPU Cores: $CORES (Below recommended: >= 4 cores)"
    fi
else
    add_warning "Could not verify CPU core count"
fi

# RAM Check (Minimum 8GB recommended)
if [ "$TOTAL_RAM_MB" != "unknown" ]; then
    if [ "$TOTAL_RAM_MB" -ge 8192 ]; then  # 8GB in MB
        log_message "✅ RAM: $(($TOTAL_RAM_MB/1024))GB (Meets requirement: >= 8GB)"
    else
        add_warning "RAM: ${TOTAL_RAM_MB}MB (Below recommended: >= 8GB)"
    fi
else
    add_warning "Could not verify total RAM"
fi

# Disk Space Check (Minimum 50GB recommended)
if [ -n "$AVAIL_SPACE_KB" ] && [ "$AVAIL_SPACE_KB" != "unknown" ]; then
    DISK_SPACE_GB=$(echo "scale=2; $(df -k / | awk 'NR==2 {print $2}')/1048576" | bc)
    if (( $(echo "$DISK_SPACE_GB >= 50" | bc -l) )); then
        log_message "✅ Root Disk Space: ${DISK_SPACE_GB}GB (Meets requirement: >= 50GB)"
    else
        add_warning "Root Disk Space: ${DISK_SPACE_GB}GB (Below recommended: >= 50GB)"
    fi
else
    add_warning "Could not verify available disk space"
fi

# Docker Compose Version Check (Minimum 2.0.0 recommended)
DOCKER_COMPOSE_VERSION=""

# Try docker-compose (standalone) first
if command -v docker-compose &>/dev/null; then
    DOCKER_COMPOSE_VERSION=$(docker-compose --version 2>/dev/null | awk '{print $3}' | tr -d ',')
# Then try docker compose (plugin)
elif docker compose version &>/dev/null; then
    DOCKER_COMPOSE_VERSION=$(docker compose version 2>/dev/null | awk '{print $4}' | tr -d ',')
fi

if [ -n "$DOCKER_COMPOSE_VERSION" ]; then
    if [ "$(printf '%s\n' "2.0.0" "$DOCKER_COMPOSE_VERSION" | sort -V | head -n1)" = "2.0.0" ]; then
        log_message "✅ Docker Compose Version: $DOCKER_COMPOSE_VERSION (Meets requirement: >= 2.0.0)"
    else
        add_warning "Docker Compose Version: $DOCKER_COMPOSE_VERSION (Below recommended: >= 2.0.0)"
    fi
else
    add_error "Docker Compose not found (Required for Gluesync).\n   Install it using: https://docs.molo17.com/gluesync/v2.1/deploy-and-run/docker-install.html"
fi

# Print summary of all checks
print_section "Pre-flight Check Summary"

if [ ${#ERRORS[@]} -eq 0 ] && [ ${#WARNINGS[@]} -eq 0 ]; then
    log_message "✅ All checks passed successfully! You're good to go!"
    EXIT_CODE=0
else
    if [ ${#ERRORS[@]} -gt 0 ]; then
        log_message "❌ Found ${#ERRORS[@]} error(s):"
        for error in "${ERRORS[@]}"; do
            log_message "  - $error"
        done
        EXIT_CODE=1
    fi
    
    if [ ${#WARNINGS[@]} -gt 0 ]; then
        log_message "\n ⚠️  Found ${#WARNINGS[@]} warning(s):"
        for warning in "${WARNINGS[@]}"; do
            log_message "  - $warning"
        done
        if [ -z "$EXIT_CODE" ] || [ "$EXIT_CODE" -eq 0 ]; then
            EXIT_CODE=0
        fi
    fi
fi

log_message "\nFor detailed requirements, visit: https://docs.molo17.com/Gluesync/v2.1/introduction/system-requirements.html"
log_message "A detailed report has been saved to: $REPORT_FILE"

# Final exit with appropriate status
exit ${EXIT_CODE:-0}