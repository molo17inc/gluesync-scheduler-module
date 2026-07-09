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

set -euo pipefail

SKIP_CONFIRMATION=false
SKIP_RUN=false
RELEASE_CHANNEL="GA"
API_BASE_URL="https://api.backoffice.molo17.com/images"

# Parse arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    -y|--yes)
      SKIP_CONFIRMATION=true
      shift
      ;;
    --beta)
      RELEASE_CHANNEL="BETA"
      shift
      ;;
    --alpha)
      RELEASE_CHANNEL="ALPHA"
      shift
      ;;
    --skip-run)
      SKIP_RUN=true
      shift
      ;;
    *)
      echo "Unknown option: $1"
      echo "Usage: $0 [-y|--yes] [--beta|--alpha] [--skip-run]"
      exit 1
      ;;
  esac
done

# Show banner if not skipping confirmation
if [[ "$SKIP_CONFIRMATION" == false ]]; then
  clear
  echo "╔══════════════════════════════════════════════════════════════╗"
  echo "║                                                              ║"
  echo "║        Gluesync by MOLO17 - Update Utility                   ║"
  echo "║                                                              ║"
  if [[ "$RELEASE_CHANNEL" == "GA" ]]; then
    echo "║  This script updates all Gluesync container images to their  ║"
    echo "║  latest GA (Generally Available) versions.                   ║"
  elif [[ "$RELEASE_CHANNEL" == "BETA" ]]; then
    echo "║  This script updates all Gluesync container images to their  ║"
    echo "║  latest BETA versions.                                       ║"
  else
    echo "║  This script updates all Gluesync container images to their  ║"
    echo "║  latest ALPHA versions.                                      ║"
  fi
  echo "║                                                              ║"
  echo "║  The script will:                                            ║"
  echo "║    • Locate your docker-compose file                         ║"
  echo "║    • Check API connectivity                                  ║"
  echo "║    • Fetch latest versions for all images                    ║"
  echo "║    • Update the compose file with new versions               ║"
  echo "║    • Restart the stack with updated images                   ║"
  echo "║                                                              ║"
  echo "╚══════════════════════════════════════════════════════════════╝"
  echo ""
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
search_dirs=("$script_dir" "$(dirname "$script_dir")")

IMAGE_TAG_LIST=()

find_local_compose_file() {
  local candidate
  for dir in "${search_dirs[@]}" "$script_dir/gluesync-docker" "$script_dir/.."; do
    if [[ -d "$dir" ]]; then
      for candidate in "$dir/docker-compose.yaml" "$dir/docker-compose.yml"; do
        if [[ -f "$candidate" ]]; then
          echo "$candidate"
          return 0
        fi
      done
    fi
  done

  if [[ -d "/opt/gluesync" ]]; then
    while IFS= read -r -d '' candidate; do
      echo "$candidate"
      return 0
    done < <(find /opt/gluesync -maxdepth 3 -type f \( -name "docker-compose.yaml" -o -name "docker-compose.yml" \) -print0 2>/dev/null)
  fi

  return 1
}

get_image_tags_from_tar() {
  local tar_file="$1"
  local manifest_json=""

  if [[ "$tar_file" == *.tar.gz ]]; then
    manifest_json=$(tar -xOzf "$tar_file" manifest.json 2>/dev/null || true)
  else
    manifest_json=$(tar -xOf "$tar_file" manifest.json 2>/dev/null || true)
  fi

  if [[ -z "$manifest_json" ]]; then
    echo ""
    return
  fi

  printf '%s\n' "$manifest_json" | jq -r '.[].RepoTags[]?' 2>/dev/null | sort -u
}

log_image_tags() {
  local tags="$1"

  if [[ -z "$tags" ]]; then
    echo "  Unable to read manifest.json to determine image tags." >&2
    return
  fi

  echo "  Repo tags:"
  while IFS= read -r tag; do
    [[ -z "$tag" ]] && continue
    echo "    - $tag"
  done <<< "$tags"
}

record_image_tags_from_list() {
  local tags="$1"

  if [[ -z "$tags" ]]; then
    return
  fi

  while IFS= read -r tag; do
    [[ -z "$tag" ]] && continue
    local repo="${tag%%:*}"
    if [[ "$repo" != "$tag" ]]; then
      IMAGE_TAG_LIST+=("$repo|$tag")
    fi
  done <<< "$tags"
}

update_compose_images_with_tags() {
  if (( ${#IMAGE_TAG_LIST[@]} == 0 )); then
    echo "Warning: No image tags collected to update docker-compose file." >&2
    return
  fi

  local compose_file=""
  if ! compose_file=$(find_local_compose_file); then
    echo "Warning: Unable to locate docker-compose file to update image tags." >&2
    return
  fi

  echo "Updating docker-compose image tags in $(basename "$compose_file")...."
  local count updated=0
  for entry in "${IMAGE_TAG_LIST[@]}"; do
    local repo="${entry%%|*}"
    local ref="${entry#*|}"

    count=$(python3 -c '
import sys, re

path, repo, new_ref = sys.argv[1:]
with open(path, encoding="utf-8") as fh:
    content = fh.read()

q = chr(34) + chr(39)
pattern = re.compile(r"(image\s*:\s*[" + q + r"]?)" + re.escape(repo) + r"(?:[:@][^" + q + r"\s]+)?([" + q + r"]?)")

def repl(match):
    prefix = match.group(1)
    quote = match.group(2) if match.group(2) else ""
    return prefix + new_ref + quote

new_content, count = pattern.subn(repl, content)
if count:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new_content)

print(count)
' "$compose_file" "$repo" "$ref")

    count=$(echo "$count" | tr -d '[:space:]')
    if [[ "$count" =~ ^[0-9]+$ && "$count" -gt 0 ]]; then
      echo "  Updated $repo to use $ref"
      updated=$((updated + 1))
    else
      echo "  No compose entries found for $repo"
    fi
  done

  if (( updated > 0 )); then
    echo "Updated $updated image reference(s) in $(basename "$compose_file")."
  else
    echo "Warning: No compose image references matched the extracted tags." >&2
  fi
}

find_offline_update_file() {
  local patterns=("update-*.tar.gz" "update-*.zip")
  local pattern
  local file

  shopt -s nullglob
  for pattern in "${patterns[@]}"; do
    for file in "$script_dir"/$pattern; do
      if [[ -f "$file" ]]; then
        echo "$file"
        shopt -u nullglob
        return 0
      fi
    done
  done
  shopt -u nullglob
  return 1
}

check_maintenance_mode() {
  local http_code="$1"
  local body="$2"
  if [[ "$http_code" == "503" ]]; then
    local maint_code
    maint_code=$(echo "$body" | jq -r '.code // empty' 2>/dev/null || true)
    if [[ "$maint_code" == "MAINTENANCE_MODE" ]]; then
      local maint_msg
      maint_msg=$(echo "$body" | jq -r '.message // empty' 2>/dev/null || true)
      echo ""
      echo "╔══════════════════════════════════════════════════════════════╗"
      echo "║                                                              ║"
      echo "║              Maintenance Mode Active                         ║"
      echo "║                                                              ║"
      echo "╚══════════════════════════════════════════════════════════════╝"
      echo ""
      echo "${maint_msg:-OTA updates are currently undergoing maintenance}"
      echo ""
      echo "Please try again later."
      exit 0
    fi
  fi
}

# Check for offline update files
check_offline_update() {
  local offline_file=""

  if offline_file=$(find_offline_update_file); then
    local filename=$(basename "$offline_file")
    echo ""
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║                                                              ║"
    echo "║              Offline Update File Detected                    ║"
    echo "║                                                              ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo ""
    echo "An offline update file named \"$filename\" has been found"
    echo "within the Gluesync setup folder."
    echo ""
    read -p "Do you want to load it? (yes/no): " -r OFFLINE_RESPONSE

    if [[ "$OFFLINE_RESPONSE" == "yes" ]]; then
      load_offline_update "$offline_file"
      exit $?
    else
      echo "Offline update skipped. Proceeding with online update..."
      echo ""
    fi
  fi
}

load_offline_update() {
  local update_file="$1"
  local filename=$(basename "$update_file")

  echo ""
  echo "Loading offline update from $filename..."

  # Create temporary extraction directory
  local extract_dir="/tmp/gluesync-offline-update-$$"
  mkdir -p "$extract_dir"

  if ! command -v jq >/dev/null 2>&1; then
    echo "Error: jq command not found. Please install jq package." >&2
    if command -v apt-get >/dev/null 2>&1; then
      echo "Install it with: sudo apt-get update && sudo apt-get install -y jq" >&2
    elif command -v dnf >/dev/null 2>&1; then
      echo "Install it with: sudo dnf install -y jq" >&2
    elif command -v yum >/dev/null 2>&1; then
      echo "Install it with: sudo yum install -y jq" >&2
    elif command -v zypper >/dev/null 2>&1; then
      echo "Install it with: sudo zypper install -y jq" >&2
    elif command -v pacman >/dev/null 2>&1; then
      echo "Install it with: sudo pacman -S --needed jq" >&2
    else
      echo "Please install jq using your distribution's package manager." >&2
    fi
    rm -rf "$extract_dir"
    return 1
  fi

  # Determine archive type
  local archive_type=""
  if [[ "$filename" == *.tar.gz ]]; then
    archive_type="tar.gz"
  elif [[ "$filename" == *.zip ]]; then
    archive_type="zip"
  fi

  if [[ -z "$archive_type" ]] && command -v file >/dev/null 2>&1; then
    local file_output
    file_output=$(file -b "$update_file" 2>/dev/null || true)
    if [[ "$file_output" == *"gzip compressed data"* ]]; then
      archive_type="tar.gz"
    elif [[ "$file_output" == *"Zip archive data"* ]]; then
      archive_type="zip"
    fi
  fi

  if [[ -z "$archive_type" ]]; then
    echo "Error: Unsupported offline update archive format for $filename" >&2
    rm -rf "$extract_dir"
    return 1
  fi

  echo "Extracting update file ($archive_type)..."
  if [[ "$archive_type" == "tar.gz" ]]; then
    if ! command -v tar >/dev/null 2>&1; then
      echo "Error: tar command not found. Please install tar package." >&2
      rm -rf "$extract_dir"
      return 1
    fi
    if ! tar -xzf "$update_file" -C "$extract_dir"; then
      echo "Error: Failed to extract update archive." >&2
      rm -rf "$extract_dir"
      return 1
    fi
  else
    if ! command -v unzip >/dev/null 2>&1; then
      echo "Error: unzip command not found. Please install unzip package." >&2
      rm -rf "$extract_dir"
      return 1
    fi
    if ! unzip -q "$update_file" -d "$extract_dir"; then
      echo "Error: Failed to extract update archive." >&2
      rm -rf "$extract_dir"
      return 1
    fi
  fi

  echo "Update file extracted successfully."

  # Find all .tar and .tar.gz files (Docker images)
  echo "Searching for Docker image files (.tar / .tar.gz)..."
  local tar_files=()
  while IFS= read -r -d '' file; do
    tar_files+=("$file")
  done < <(find "$extract_dir" -type f \( -name "*.tar" -o -name "*.tar.gz" \) -print0 2>/dev/null)

  if [[ ${#tar_files[@]} -eq 0 ]]; then
    echo "Warning: No .tar or .tar.gz files found in the update package." >&2
    rm -rf "$extract_dir"
    return 1
  fi

  echo "Found ${#tar_files[@]} Docker image(s) to load."
  echo ""

  # Determine if we need sudo for docker commands
  local DOCKER_CMD="docker"
  if ! docker info >/dev/null 2>&1; then
    if sudo docker info >/dev/null 2>&1; then
      DOCKER_CMD="sudo docker"
    else
      echo "Error: Docker is not accessible. Please ensure Docker is running." >&2
      rm -rf "$extract_dir"
      return 1
    fi
  fi

  # Load each Docker image
  local loaded=0
  local failed=0
  local total=${#tar_files[@]}

  for tar_file in "${tar_files[@]}"; do
    local tar_filename=$(basename "$tar_file")
    loaded=$((loaded + 1))

    echo "[$loaded/$total] Loading image: $tar_filename"
    local tags=""
    tags=$(get_image_tags_from_tar "$tar_file")
    log_image_tags "$tags"
    record_image_tags_from_list "$tags"

    local load_success=false
    if [[ "$tar_filename" == *.tar.gz ]]; then
      if command -v gunzip >/dev/null 2>&1; then
        if gunzip -c "$tar_file" | $DOCKER_CMD load >/dev/null 2>&1; then
          load_success=true
        fi
      else
        echo "  ✗ gunzip command not available; cannot load $tar_filename" >&2
      fi
    else
      if $DOCKER_CMD load < "$tar_file" >/dev/null 2>&1; then
        load_success=true
      fi
    fi

    if [[ "$load_success" == true ]]; then
      echo "  ✓ Loaded successfully"
    else
      echo "  ✗ Failed to load" >&2
      failed=$((failed + 1))
    fi
  done

  echo ""

  # Update compose file with extracted tags
  update_compose_images_with_tags

  # Cleanup
  echo "Cleaning up temporary files..."
  rm -rf "$extract_dir"

  # Report results
  if [[ $failed -eq 0 ]]; then
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║                                                              ║"
    echo "║            Offline Update Completed Successfully!            ║"
    echo "║                                                              ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo ""
    echo "All $total Docker image(s) loaded successfully!"
    echo ""
    echo "Please restart Gluesync to use the updated images."
    echo "You can run: ./run.sh"
    return 0
  else
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║                                                              ║"
    echo "║          Offline Update Completed With Warnings              ║"
    echo "║                                                              ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo ""
    echo "$((total - failed))/$total images loaded successfully, $failed failed."
    echo ""
    echo "Please check the errors above and restart Gluesync."
    return 1
  fi
}

# Check for offline update first
check_offline_update

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

compose_file=""
for search_dir in "${search_dirs[@]}"; do
  if found_file=$(find_compose_file "$search_dir"); then
    compose_file="$found_file"
    break
  fi
done

if [[ -z "$compose_file" ]]; then
  echo "Error: docker compose file not found (.yaml or .yml) in any of these directories: ${search_dirs[*]}" >&2
  exit 1
fi

echo "Found compose file: $compose_file"

# Check if required tools are available
if ! command -v curl >/dev/null 2>&1; then
  echo "Error: curl is not installed or not in PATH." >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "Error: jq is not installed or not in PATH." >&2
  echo "Please install jq: https://stedolan.github.io/jq/download/" >&2
  exit 1
fi

# Test reachability
echo "Testing API reachability..."
_reach_tmp=$(mktemp)
_reach_code=$(curl -s -o "$_reach_tmp" -w "%{http_code}" "${API_BASE_URL}/gluesync-core-hub" 2>/dev/null || echo "000")
_reach_body=$(cat "$_reach_tmp" 2>/dev/null || true); rm -f "$_reach_tmp"
check_maintenance_mode "$_reach_code" "$_reach_body"
if [[ "$_reach_code" != "200" ]]; then
  echo "Error: Cannot reach API endpoint ${API_BASE_URL} (HTTP ${_reach_code})" >&2
  exit 1
fi
_agent_tmp=$(mktemp)
_agent_code=$(curl -s -o "$_agent_tmp" -w "%{http_code}" "https://api.backoffice.molo17.com/agent/core-hub/version" 2>/dev/null || echo "000")
_agent_body=$(cat "$_agent_tmp" 2>/dev/null || true); rm -f "$_agent_tmp"
check_maintenance_mode "$_agent_code" "$_agent_body"
echo "API is reachable."

# Double confirmation for non-GA channels
if [[ "$RELEASE_CHANNEL" != "GA" ]]; then
  echo ""
  echo "╔══════════════════════════════════════════════════════════════╗"
  echo "║                         ⚠️  WARNING  ⚠️                       ║"
  echo "╚══════════════════════════════════════════════════════════════╝"
  echo ""
  if [[ "$RELEASE_CHANNEL" == "BETA" ]]; then
    echo "You are about to switch to the BETA release channel."
    echo "Beta versions may contain bugs and are not recommended for production use."
  else
    echo "You are about to switch to the ALPHA release channel."
    echo "Alpha versions are experimental and may be unstable."
    echo "They are NOT recommended for production use."
  fi
  echo ""
  read -p "Are you sure you want to proceed with $RELEASE_CHANNEL channel? (yes/no): " -r RESPONSE1
  if [[ "$RESPONSE1" != "yes" ]]; then
    echo "Update cancelled."
    exit 0
  fi
  echo ""
  read -p "Please confirm again - switch to $RELEASE_CHANNEL channel? (yes/no): " -r RESPONSE2
  if [[ "$RESPONSE2" != "yes" ]]; then
    echo "Update cancelled."
    exit 0
  fi
  echo ""
fi

# Confirmation prompt
if [[ "$SKIP_CONFIRMATION" == false ]]; then
  echo ""
  echo "This will update all Gluesync images in $compose_file to their latest $RELEASE_CHANNEL versions."
  read -p "Do you want to proceed? (yes/no): " -r RESPONSE
  if [[ "$RESPONSE" != "yes" ]]; then
    echo "Update cancelled."
    exit 0
  fi
fi

image_lines=$(grep -E '^[[:space:]]+image:[[:space:]]+' "$compose_file" | grep 'molo17/' || true)

if [[ -z "$image_lines" ]]; then
  echo "No molo17 images found in compose file."
  exit 0
fi

updates_made=false
temp_file=$(mktemp)
cp "$compose_file" "$temp_file"

updates=()
agent_candidates=()
agent_latest_versions=()
core_candidates=()

declare -A THIRD_PARTY_AGENT_NAMES=(
  [traefik]="traefik"
  [prometheus]="prometheus"
  [grafana]="grafana"
  [portainer]="portainer"
)

while IFS= read -r line; do
  image_full=$(echo "$line" | sed -E 's/^[[:space:]]+image:[[:space:]]+//' | tr -d '"' | tr -d "'")
  image_name=$(echo "$image_full" | cut -d: -f1 | sed 's/molo17\///')
  current_version=$(echo "$image_full" | cut -d: -f2)

  # Determine which version field to use based on release channel
  if [[ "$RELEASE_CHANNEL" == "BETA" ]]; then
    version_field='.latestVersionBeta'
  elif [[ "$RELEASE_CHANNEL" == "ALPHA" ]]; then
    version_field='.latestVersionAlpha'
  else
    version_field='.latestVersionGA'
  fi

  # Handle third-party images via the /agent/<name>/version endpoint
  if [[ -n "${THIRD_PARTY_AGENT_NAMES[$image_name]+_}" ]]; then
    agent_name="${THIRD_PARTY_AGENT_NAMES[$image_name]}"
    _tp_tmp=$(mktemp)
    _tp_code=$(curl -s -o "$_tp_tmp" -w "%{http_code}" "https://api.backoffice.molo17.com/agent/${agent_name}/version" 2>/dev/null || echo "000")
    tp_response=$(cat "$_tp_tmp" 2>/dev/null || true); rm -f "$_tp_tmp"
    check_maintenance_mode "$_tp_code" "$tp_response"
    if [[ "$_tp_code" != "200" ]] || [[ -z "$tp_response" ]]; then
      echo "Skipping ${image_name} (Third-party module, not managed by update script)"
      continue
    fi
    latest_version=$(echo "$tp_response" | jq -r "${version_field} // empty")
    if [[ -z "$latest_version" ]]; then
      echo "Skipping ${image_name} (Third-party module, not managed by update script)"
      continue
    fi
    if [[ "$current_version" == "$latest_version" ]]; then
      echo "✓ ${image_name}: already at latest ${RELEASE_CHANNEL} version ${current_version} (Third-party)"
    else
      sed -i.bak "s|molo17/${image_name}:${current_version}|molo17/${image_name}:${latest_version}|g" "$temp_file"
      rm -f "${temp_file}.bak"
      updates+=("${image_name}: ${current_version} -> ${latest_version}")
      echo "↑ ${image_name}: ${current_version} -> ${latest_version} (${RELEASE_CHANNEL}, Third-party)"
    fi
    continue
  fi

  # Fetch latest version from API
  _api_tmp=$(mktemp)
  _api_code=$(curl -s -o "$_api_tmp" -w "%{http_code}" "${API_BASE_URL}/${image_name}" 2>/dev/null || echo "000")
  api_response=$(cat "$_api_tmp" 2>/dev/null || true); rm -f "$_api_tmp"
  check_maintenance_mode "$_api_code" "$api_response"

  if [[ "$_api_code" != "200" ]] || [[ -z "$api_response" ]]; then
    echo "Skipping ${image_name} (not found in API or error)"
    continue
  fi

  latest_version=$(echo "$api_response" | jq -r "${version_field} // empty")
  internal_name=$(echo "$api_response" | jq -r '.internalName // empty')
  module_type=$(echo "$api_response" | jq -r '.moduleType // empty')

  # Skip any remaining Third-party modules returned by the /images API
  if [[ "$module_type" == "Third-party" ]]; then
    echo "Skipping ${image_name} (Third-party module, not managed by update script)"
    continue
  fi

  if [[ -z "$latest_version" ]]; then
    echo "Skipping ${image_name} (no ${RELEASE_CHANNEL} version in API response)"
    continue
  fi

  display_name="${internal_name:-$image_name}"

  if [[ "$current_version" == "$latest_version" ]]; then
    echo "✓ ${display_name}: already at latest ${RELEASE_CHANNEL} version ${current_version}"
    continue
  fi

  if [[ "$module_type" == "Agent" ]]; then
    agent_candidates+=("${image_name}|${display_name}|${current_version}|${latest_version}")
    agent_latest_versions+=("${latest_version}")
    echo "• Pending agent update for ${display_name}: ${current_version} -> ${latest_version} (${RELEASE_CHANNEL})"
  elif [[ "$module_type" == "Module" || "$module_type" == "Core Hub" || "$image_name" == "gluesync-core-hub" ]]; then
    # Core components (Module type includes conductor/chronos, Core Hub type): defer update until agents are ready
    core_candidates+=("${image_name}|${display_name}|${current_version}|${latest_version}")
    echo "• Pending core component update for ${display_name}: ${current_version} -> ${latest_version} (${RELEASE_CHANNEL})"
  else
    # Other modules: update immediately
    sed -i.bak "s|molo17/${image_name}:${current_version}|molo17/${image_name}:${latest_version}|g" "$temp_file"
    rm -f "${temp_file}.bak"
    updates+=("${display_name}: ${current_version} -> ${latest_version}")
    echo "↑ ${display_name}: ${current_version} -> ${latest_version} (${RELEASE_CHANNEL})"
  fi
done <<< "$image_lines"

# Handle agent and core component updates together
if [[ ${#agent_candidates[@]} -gt 0 ]]; then
  unique_latest=$(printf "%s\n" "${agent_latest_versions[@]}" | sort -u)
  unique_count=$(printf "%s\n" "${agent_latest_versions[@]}" | sort -u | wc -l | tr -d ' ')
  if [[ "$unique_count" -eq 1 ]]; then
    # Update all agents
    for entry in "${agent_candidates[@]}"; do
      IFS='|' read -r img disp cur ver <<< "$entry"
      sed -i.bak "s|molo17/${img}:${cur}|molo17/${img}:${ver}|g" "$temp_file"
      rm -f "${temp_file}.bak"
      updates+=("${disp}: ${cur} -> ${ver}")
      echo "↑ ${disp}: ${cur} -> ${ver} (${RELEASE_CHANNEL})"
    done
    # Update core components (conductor, chronos, core-hub) alongside agents
    for entry in "${core_candidates[@]}"; do
      IFS='|' read -r img disp cur ver <<< "$entry"
      sed -i.bak "s|molo17/${img}:${cur}|molo17/${img}:${ver}|g" "$temp_file"
      rm -f "${temp_file}.bak"
      updates+=("${disp}: ${cur} -> ${ver}")
      echo "↑ ${disp}: ${cur} -> ${ver} (${RELEASE_CHANNEL})"
    done
  else
    echo "Agent updates deferred: latest versions differ (${unique_latest}). Core components also deferred."
  fi
elif [[ ${#core_candidates[@]} -gt 0 ]]; then
  # No agents to update, but core components are pending - update them now
  echo "No agent updates needed, updating core components..."
  for entry in "${core_candidates[@]}"; do
    IFS='|' read -r img disp cur ver <<< "$entry"
    sed -i.bak "s|molo17/${img}:${cur}|molo17/${img}:${ver}|g" "$temp_file"
    rm -f "${temp_file}.bak"
    updates+=("${disp}: ${cur} -> ${ver}")
    echo "↑ ${disp}: ${cur} -> ${ver} (${RELEASE_CHANNEL})"
  done
fi

if [[ ${#updates[@]} -gt 0 ]]; then
  mv "$temp_file" "$compose_file"
  echo ""
  echo "Updated ${#updates[@]} image(s) in $compose_file"
  echo ""
  if [[ "$SKIP_RUN" == true ]]; then
    echo "Skipping stack restart (--skip-run specified)."
  else
    echo "Running updated stack..."

    # Find and execute run.sh
    run_script="$script_dir/run.sh"
    if [[ -f "$run_script" ]]; then
      bash "$run_script"
    else
      echo "Warning: run.sh not found at $run_script"
      rm -f "$temp_file"
      exit 1
    fi
  fi
else
  rm -f "$temp_file"
  echo ""
  echo "No updates needed. Stack not restarted."
fi