#!/bin/bash

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

set -euo pipefail

SCRIPT_VERSION="2.0.0"
GLUESYNC_DIR="/opt/gluesync"
DEFAULT_DAEMON_JSON_MINIFIED='{"log-driver":"json-file","log-opts":{"max-size":"100m","max-file":"5"}}'
SUDO=""
DISTRO=""
VERSION=""
CONTAINER_RUNTIME=""
FORCE_UNINSTALL=false
PURGE_RUNTIME=false

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_usage() {
    cat <<'EOF'
Gluesync Container Runtime Uninstaller (Linux)

Usage:
  ./docker-uninstaller.sh [options]

Options:
  -f, --force            Skip confirmation prompts for Gluesync teardown.
      --purge-runtime    Automatically remove container runtime packages and data.
      --purge-docker     (Deprecated) Alias for --purge-runtime.
  -h, --help             Show this message and exit.

The script mirrors docker-installer.sh by stopping Gluesync workloads,
removing /opt/gluesync, reverting the logging configuration, and (optionally)
purging Docker or Podman plus their data directories.
EOF
}

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -f|--force)
                FORCE_UNINSTALL=true
                shift
                ;;
            --purge-runtime|--purge-docker)
                PURGE_RUNTIME=true
                shift
                ;;
            -h|--help)
                print_usage
                exit 0
                ;;
            *)
                log_error "Unknown argument: $1"
                print_usage
                exit 1
                ;;
        esac
    done
}

show_banner() {
    clear
    cat <<'EOF'
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║    Gluesync by MOLO17 Container Linux Uninstaller v2.0       ║
║                                                              ║
║  This script removes the Gluesync container kit installed    ║
║  under /opt/gluesync and optionally purges Docker or Podman  ║
║  packages, services, logging tweaks, and residual data.      ║
║                                                              ║
║  It complements docker-installer.sh and supports the same    ║
║  distributions (Fedora, Ubuntu, Debian, CentOS, RHEL,        ║
║  Amazon Linux, SUSE, openSUSE).                              ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
EOF
}

check_permissions() {
    if [[ $EUID -eq 0 ]]; then
        SUDO=""
        log_success "Running as root"
    elif sudo -n true >/dev/null 2>&1; then
        SUDO="sudo"
        log_success "Running with sudo privileges"
    else
        log_error "This script requires root privileges or passwordless sudo."
        exit 1
    fi
}

detect_distribution() {
    if [[ -f /etc/os-release ]]; then
        . /etc/os-release
        DISTRO=$ID
        VERSION=$VERSION_ID
        log_info "Detected distribution: ${PRETTY_NAME:-$ID}"
    elif [[ -f /etc/redhat-release ]]; then
        DISTRO="rhel"
        log_info "Detected distribution: Red Hat Enterprise Linux"
    elif [[ -f /etc/debian_version ]]; then
        DISTRO="debian"
        log_info "Detected distribution: Debian"
    else
        log_error "Unable to detect Linux distribution."
        exit 1
    fi
}

detect_container_runtime() {
    if command -v podman >/dev/null 2>&1; then
        CONTAINER_RUNTIME="podman"
        log_info "Detected container runtime: Podman"
    elif command -v docker >/dev/null 2>&1; then
        CONTAINER_RUNTIME="docker"
        log_info "Detected container runtime: Docker"
    else
        CONTAINER_RUNTIME="none"
        log_warning "No container runtime detected (neither Docker nor Podman found)."
    fi
}

confirm_uninstall() {
    if [[ "$FORCE_UNINSTALL" == true ]]; then
        log_info "Force flag detected; skipping interactive confirmation."
        return
    fi

    echo ""
    log_warning "This will stop Gluesync containers and delete data under $GLUESYNC_DIR."
    read -p "Type 'remove' to continue or press Enter to abort: " -r RESPONSE
    if [[ "$RESPONSE" != "remove" ]]; then
        log_info "Uninstallation cancelled."
        exit 0
    fi
}

run_compose_command() {
    if [[ "$CONTAINER_RUNTIME" == "podman" ]]; then
        if command -v podman-compose >/dev/null 2>&1; then
            podman-compose "$@"
            return $?
        else
            return 1
        fi
    else
        if $SUDO docker compose version >/dev/null 2>&1; then
            $SUDO docker compose "$@"
            return $?
        elif command -v docker-compose >/dev/null 2>&1; then
            $SUDO docker-compose "$@"
            return $?
        else
            return 1
        fi
    fi
}

stop_gluesync_stack() {
    if [[ "$CONTAINER_RUNTIME" == "none" ]]; then
        log_warning "No container runtime found. Skipping container teardown."
        return
    fi

    local compose_file=""
    local candidates=(
        "$GLUESYNC_DIR/docker-compose.yml"
        "$GLUESYNC_DIR/docker-compose.yaml"
        "$GLUESYNC_DIR/compose.yml"
        "$GLUESYNC_DIR/compose.yaml"
    )

    for path in "${candidates[@]}"; do
        if [[ -f "$path" ]]; then
            compose_file="$path"
            break
        fi
    done

    if [[ -n "$compose_file" ]]; then
        log_info "Found compose file: $compose_file"
        if run_compose_command -f "$compose_file" down --remove-orphans --volumes; then
            log_success "Gluesync stack stopped via compose."
        else
            log_warning "Compose down failed for $compose_file. Containers may still be running."
        fi
    else
        log_info "No compose file detected under $GLUESYNC_DIR."
    fi

    local containers=""
    if [[ "$CONTAINER_RUNTIME" == "podman" ]]; then
        containers=$(podman ps -a --filter "name=gluesync" -q 2>/dev/null || true)
    else
        containers=$($SUDO docker ps -a --filter "name=gluesync" -q 2>/dev/null || true)
    fi
    
    if [[ -n "$containers" ]]; then
        log_info "Removing containers with name matching 'gluesync'."
        if [[ "$CONTAINER_RUNTIME" == "podman" ]]; then
            if podman rm -f $containers >/dev/null 2>&1; then
                log_success "Removed Gluesync containers."
            else
                log_warning "Unable to remove some Gluesync containers automatically."
            fi
        else
            if $SUDO docker rm -f $containers >/dev/null 2>&1; then
                log_success "Removed Gluesync containers."
            else
                log_warning "Unable to remove some Gluesync containers automatically."
            fi
        fi
    fi

    local volumes=""
    if [[ "$CONTAINER_RUNTIME" == "podman" ]]; then
        volumes=$(podman volume ls --format '{{.Name}}' 2>/dev/null | grep -i gluesync || true)
    else
        volumes=$($SUDO docker volume ls --format '{{.Name}}' 2>/dev/null | grep -i gluesync || true)
    fi
    
    if [[ -n "$volumes" ]]; then
        log_info "Removing volumes containing 'gluesync'."
        while IFS= read -r volume; do
            [[ -z "$volume" ]] && continue
            if [[ "$CONTAINER_RUNTIME" == "podman" ]]; then
                podman volume rm "$volume" >/dev/null 2>&1 || true
            else
                $SUDO docker volume rm "$volume" >/dev/null 2>&1 || true
            fi
        done <<< "$volumes"
        log_success "Requested deletion of Gluesync volumes."
    fi
}

remove_gluesync_directory() {
    if [[ -d "$GLUESYNC_DIR" ]]; then
        log_info "Removing Gluesync directory at $GLUESYNC_DIR"
        if $SUDO rm -rf "$GLUESYNC_DIR"; then
            log_success "Deleted $GLUESYNC_DIR"
        else
            log_warning "Failed to delete $GLUESYNC_DIR automatically. Please remove it manually."
        fi
    else
        log_info "Gluesync directory not found; skipping."
    fi
}

restore_docker_logging_config() {
    local daemon_file="/etc/docker/daemon.json"
    local daemon_backup="/etc/docker/daemon.json.backup"

    if [[ -f "$daemon_backup" ]]; then
        log_info "Restoring Docker daemon configuration from backup."
        $SUDO mv "$daemon_backup" "$daemon_file"
        log_success "daemon.json restored from backup."
        return
    fi

    if [[ -f "$daemon_file" ]]; then
        local file_content
        file_content=$(tr -d '[:space:]' < "$daemon_file")
        if [[ "$file_content" == "$DEFAULT_DAEMON_JSON_MINIFIED" ]]; then
            log_info "Removing Gluesync-specific logging configuration at $daemon_file"
            $SUDO rm -f "$daemon_file"
            log_success "Removed Docker logging overrides."
        else
            log_info "Existing daemon.json differs from installer defaults; leaving untouched."
        fi
    fi
}

restore_podman_logging_config() {
    local containers_conf="$HOME/.config/containers/containers.conf"
    local containers_backup="$HOME/.config/containers/containers.conf.backup"

    if [[ -f "$containers_backup" ]]; then
        log_info "Restoring Podman configuration from backup."
        mv "$containers_backup" "$containers_conf"
        log_success "containers.conf restored from backup."
        return
    fi

    if [[ -f "$containers_conf" ]]; then
        log_info "Removing Gluesync-specific Podman logging configuration."
        rm -f "$containers_conf"
        log_success "Removed Podman logging overrides."
    fi
}

restore_logging_config() {
    if [[ "$CONTAINER_RUNTIME" == "podman" ]]; then
        restore_podman_logging_config
    elif [[ "$CONTAINER_RUNTIME" == "docker" ]]; then
        restore_docker_logging_config
    else
        log_info "No container runtime detected; skipping logging config restoration."
    fi
}

remove_docker_service() {
    if systemctl list-unit-files | grep -q "^docker\.service" >/dev/null 2>&1; then
        log_info "Stopping and disabling docker.service"
        $SUDO systemctl disable --now docker >/dev/null 2>&1 || true
    fi

    if systemctl list-unit-files | grep -q "^containerd\.service" >/dev/null 2>&1; then
        log_info "Stopping and disabling containerd.service"
        $SUDO systemctl disable --now containerd >/dev/null 2>&1 || true
    fi
}

remove_podman_service() {
    if systemctl --user list-unit-files | grep -q "^podman\.socket" >/dev/null 2>&1; then
        log_info "Stopping and disabling podman.socket (user)"
        systemctl --user disable --now podman.socket >/dev/null 2>&1 || true
    fi
    
    if systemctl list-unit-files | grep -q "^podman\.service" >/dev/null 2>&1; then
        log_info "Stopping and disabling podman.service"
        $SUDO systemctl disable --now podman >/dev/null 2>&1 || true
    fi
}

uninstall_docker_packages() {
    log_info "Removing Docker Engine packages for distribution '$DISTRO'."
    case "$DISTRO" in
        ubuntu|debian)
            if ! $SUDO apt-get purge -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin docker-ce-rootless-extras >/dev/null 2>&1; then
                log_warning "Package purge encountered warnings on $DISTRO."
            fi
            $SUDO apt-get autoremove -y >/dev/null 2>&1 || true
            ;;
        fedora|amzn)
            if ! $SUDO dnf remove -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin docker-compose >/dev/null 2>&1; then
                log_warning "dnf removal reported issues on $DISTRO."
            fi
            ;;
        rhel|redhat|almalinux|rocky|centos)
            if ! $SUDO yum remove -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin docker-compose >/dev/null 2>&1; then
                log_warning "yum removal reported issues on $DISTRO."
            fi
            ;;
        sles|suse|opensuse*)
            if ! $SUDO zypper --non-interactive remove docker docker-compose docker-buildx-plugin docker-compose-plugin containerd >/dev/null 2>&1; then
                log_warning "zypper removal reported issues on $DISTRO."
            fi
            ;;
        *)
            log_warning "Automatic Docker removal is not defined for '$DISTRO'. Please remove packages manually."
            ;;
    esac
}

uninstall_podman_packages() {
    log_info "Removing Podman packages for distribution '$DISTRO'."
    case "$DISTRO" in
        ubuntu|debian)
            if ! $SUDO apt-get purge -y podman >/dev/null 2>&1; then
                log_warning "Package purge encountered warnings on $DISTRO."
            fi
            $SUDO apt-get autoremove -y >/dev/null 2>&1 || true
            ;;
        fedora|amzn)
            if ! $SUDO dnf remove -y podman podman-compose >/dev/null 2>&1; then
                log_warning "dnf removal reported issues on $DISTRO."
            fi
            ;;
        rhel|redhat|almalinux|rocky|centos)
            if ! $SUDO yum remove -y podman podman-compose >/dev/null 2>&1; then
                log_warning "yum removal reported issues on $DISTRO."
            fi
            ;;
        sles|suse|opensuse*)
            if ! $SUDO zypper --non-interactive remove podman >/dev/null 2>&1; then
                log_warning "zypper removal reported issues on $DISTRO."
            fi
            ;;
        *)
            log_warning "Automatic Podman removal is not defined for '$DISTRO'. Please remove packages manually."
            ;;
    esac
    
    # Remove podman-compose if installed via pip
    if command -v pip3 >/dev/null 2>&1; then
        log_info "Removing podman-compose from pip..."
        pip3 uninstall -y podman-compose >/dev/null 2>&1 || true
    fi
}

remove_docker_data() {
    local response=""
    if [[ "$FORCE_UNINSTALL" == true || "$PURGE_RUNTIME" == true ]]; then
        response="y"
    else
        read -p "Remove Docker data directories (/var/lib/docker, /var/lib/containerd, ~/.docker)? (y/N): " -r response
    fi

    if [[ "$response" =~ ^[Yy]$ ]]; then
        log_info "Deleting Docker data directories."
        $SUDO rm -rf /var/lib/docker /var/lib/containerd /etc/docker >/dev/null 2>&1 || true
        rm -rf "${HOME}/.docker" >/dev/null 2>&1 || true
        log_success "Docker data directories removed."
    else
        log_info "Docker data preserved."
    fi
}

remove_podman_data() {
    local response=""
    if [[ "$FORCE_UNINSTALL" == true || "$PURGE_RUNTIME" == true ]]; then
        response="y"
    else
        read -p "Remove Podman data directories (~/.local/share/containers, ~/.config/containers)? (y/N): " -r response
    fi

    if [[ "$response" =~ ^[Yy]$ ]]; then
        log_info "Deleting Podman data directories."
        rm -rf "${HOME}/.local/share/containers" >/dev/null 2>&1 || true
        rm -rf "${HOME}/.config/containers" >/dev/null 2>&1 || true
        $SUDO rm -rf /var/lib/containers >/dev/null 2>&1 || true
        log_success "Podman data directories removed."
    else
        log_info "Podman data preserved."
    fi
}

prompt_remove_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        log_info "Docker binary not found. Skipping engine removal."
        return
    fi

    local response=""
    if [[ "$PURGE_RUNTIME" == true ]]; then
        response="y"
    else
        read -p "Also remove Docker Engine packages installed by the installer? (y/N): " -r response
    fi

    if [[ "$response" =~ ^[Yy]$ ]]; then
        remove_docker_service
        uninstall_docker_packages
        remove_docker_data
    else
        log_info "Docker Engine packages left installed."
    fi
}

prompt_remove_podman() {
    if ! command -v podman >/dev/null 2>&1; then
        log_info "Podman binary not found. Skipping removal."
        return
    fi

    local response=""
    if [[ "$PURGE_RUNTIME" == true ]]; then
        response="y"
    else
        read -p "Also remove Podman packages installed by the installer? (y/N): " -r response
    fi

    if [[ "$response" =~ ^[Yy]$ ]]; then
        remove_podman_service
        uninstall_podman_packages
        remove_podman_data
    else
        log_info "Podman packages left installed."
    fi
}

prompt_remove_runtime() {
    if [[ "$CONTAINER_RUNTIME" == "podman" ]]; then
        prompt_remove_podman
    elif [[ "$CONTAINER_RUNTIME" == "docker" ]]; then
        prompt_remove_docker
    else
        log_info "No container runtime detected; skipping runtime removal."
    fi
}

main() {
    parse_args "$@"
    show_banner
    confirm_uninstall

    log_info "Starting Gluesync uninstallation workflow (v$SCRIPT_VERSION)."
    check_permissions
    detect_distribution
    detect_container_runtime

    stop_gluesync_stack
    remove_gluesync_directory
    restore_logging_config
    prompt_remove_runtime

    log_success "Gluesync uninstallation completed."
    if [[ "$CONTAINER_RUNTIME" == "podman" ]]; then
        log_info "If Podman packages were left installed, you can reinstall Gluesync later using docker-installer.sh."
    elif [[ "$CONTAINER_RUNTIME" == "docker" ]]; then
        log_info "If Docker packages were left installed, you can reinstall Gluesync later using docker-installer.sh."
    else
        log_info "You can install Gluesync later using docker-installer.sh."
    fi
}

main "$@"
