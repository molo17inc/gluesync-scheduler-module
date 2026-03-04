#!/usr/bin/env bash

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
# Script version: 1.2
# 
# This script is used to run the Gluesync docker compose file.
# It will search for the docker compose file in the current directory or in the parent directory.
# It will then run the docker compose file using the container runtime specified in the CONTAINER_RUNTIME environment variable.
# If CONTAINER_RUNTIME is not specified, it will default to docker.
#
# Usage:
#   ./run.sh
#

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# After assembly, this script will sit inside the 'gluesync-docker' folder.
# The docker compose file may be in the same folder or in the parent directory.
# Search in both locations and use the first one found.

# Define search directories: script directory first, then parent directory
search_dirs=("$script_dir" "$(dirname "$script_dir")")

# Function to find compose file in a directory
find_compose_file() {
  local dir="$1"
  local yaml_file="$dir/docker-compose.yaml"
  local yml_file="$dir/docker-compose.yml"
  
  if [[ -f "$yaml_file" ]]; then
    echo "$yaml_file"
  elif [[ -f "$yml_file" ]]; then
    echo "$yml_file"
  else
    return 1
  fi
}

# Search for compose file in order of preference
compose_file=""
compose_dir=""
for search_dir in "${search_dirs[@]}"; do
  if found_file=$(find_compose_file "$search_dir"); then
    compose_file="$found_file"
    compose_dir="$search_dir"
    break
  fi
done

if [[ -z "$compose_file" ]]; then
  echo "Error: docker compose file not found (.yaml or .yml) in any of these directories: ${search_dirs[*]}" >&2
  exit 1
fi


# Determine container runtime (docker or podman)
CONTAINER_RUNTIME="${CONTAINER_RUNTIME:-}" # optional override
if [[ -n "$CONTAINER_RUNTIME" ]]; then
  if ! command -v "$CONTAINER_RUNTIME" >/dev/null 2>&1; then
    echo "Error: CONTAINER_RUNTIME is set to '$CONTAINER_RUNTIME' but it is not available." >&2
    exit 1
  fi
else
  if command -v docker >/dev/null 2>&1; then
    CONTAINER_RUNTIME="docker"
  elif command -v podman >/dev/null 2>&1; then
    CONTAINER_RUNTIME="podman"
  else
    echo "Error: Neither docker nor podman is installed or in PATH." >&2
    exit 1
  fi
fi

echo "Using container runtime: $CONTAINER_RUNTIME"

# Set XDG_RUNTIME_DIR for rootless Podman if not already set
if [[ "$CONTAINER_RUNTIME" == "podman" ]] && [[ -z "$XDG_RUNTIME_DIR" ]]; then
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
  echo "Set XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR for rootless Podman"
fi

# Determine compose command for the runtime
COMPOSE_CMD=""
if [[ "$CONTAINER_RUNTIME" == "docker" ]]; then
  if docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
  elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_CMD="docker-compose"
  else
    echo "Error: Neither 'docker compose' nor 'docker-compose' is available. Please install Docker Compose." >&2
    exit 1
  fi
else
  if podman compose version >/dev/null 2>&1; then
    COMPOSE_CMD="podman compose"
  elif command -v podman-compose >/dev/null 2>&1; then
    COMPOSE_CMD="podman-compose"
  else
    echo "Error: Neither 'podman compose' nor 'podman-compose' is available. Please install Podman Compose." >&2
    exit 1
  fi
fi

# Skip if .env already exists
if [ -f .env ]; then
    echo ".env already exists, skipping."
else
    # Try to detect host timezone
    HOST_TZ=""

    # Debian/Ubuntu style
    if [ -f /etc/timezone ]; then
        HOST_TZ="$(cat /etc/timezone)"
    fi

    # Generic Linux using /etc/localtime symlink
    if [ -z "$HOST_TZ" ] && [ -L /etc/localtime ]; then
        HOST_TZ="$(readlink /etc/localtime | sed 's#.*/zoneinfo/##')"
    fi

    # If timezone not detected, abort without creating file
    if [ -z "$HOST_TZ" ]; then
        echo "Unable to detect timezone, no .env file created."
    else
        echo "TZ=$HOST_TZ" > .env
        echo ".env created with TZ=$HOST_TZ"
    fi
fi

echo "Running: $COMPOSE_CMD -f $compose_file pull"
$COMPOSE_CMD -f "$compose_file" pull

echo "Running: $CONTAINER_RUNTIME image prune -f"
"$CONTAINER_RUNTIME" image prune -f

echo "Running: $COMPOSE_CMD -f $compose_file up -d --remove-orphans"
$COMPOSE_CMD -f "$compose_file" up -d --remove-orphans

# Fix CNI version compatibility for Podman after containers start
# (Podman creates CNI configs during startup, so we fix them afterwards)
if [[ "$CONTAINER_RUNTIME" == "podman" ]]; then
  CNI_DIRS=("/etc/cni/net.d" "$HOME/.config/cni/net.d")
  CNI_FIXED=false
  
  for CNI_DIR in "${CNI_DIRS[@]}"; do
    if [[ -d "$CNI_DIR" ]] && ls "$CNI_DIR"/*.conflist >/dev/null 2>&1; then
      if grep -q '"cniVersion".*"1\.0\.0"' "$CNI_DIR"/*.conflist 2>/dev/null; then
        if [[ "$CNI_FIXED" == "false" ]]; then
          echo "Fixing CNI configuration compatibility..."
          CNI_FIXED=true
        fi
        for conffile in "$CNI_DIR"/*.conflist; do
          if [[ -f "$conffile" ]] && grep -q '"cniVersion".*"1\.0\.0"' "$conffile" 2>/dev/null; then
            if [[ -w "$conffile" ]]; then
              sed -i.bak 's/"cniVersion"[[:space:]]*:[[:space:]]*"1\.0\.0"/"cniVersion": "0.4.0"/g' "$conffile"
              echo "  Fixed: $(basename "$conffile")"
            elif command -v sudo >/dev/null 2>&1; then
              sudo sed -i.bak 's/"cniVersion"[[:space:]]*:[[:space:]]*"1\.0\.0"/"cniVersion": "0.4.0"/g' "$conffile"
              echo "  Fixed: $(basename "$conffile")"
            fi
          fi
        done
      fi
    fi
  done
  
  # Restart containers if CNI was fixed to apply the changes
  if [[ "$CNI_FIXED" == "true" ]]; then
    echo "Restarting containers to apply CNI configuration..."
    $COMPOSE_CMD -f "$compose_file" down
    sleep 2
    $COMPOSE_CMD -f "$compose_file" up -d --remove-orphans
    echo "Containers restarted with updated CNI configuration"
  fi
fi

echo "Gluesync stack started (detached)."
