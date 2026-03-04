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
#    which includes a warranty and permits proprietary use. Contact MOLO17 at [info@molo17.com](mailto:info@molo17.com)
#    for licensing terms and conditions.
#
# You must choose one of these licenses to use this software. Using this software implies
# acceptance of one of these licenses. See the accompanying LICENSE files or contact
# MOLO17 for more information.
#
# Copyright (C) 2025 MOLO17. All rights reserved.

echo "=================================================================================="
echo " Welcome to the Gluesync Logs Collector & Uploader!"
echo ""
echo " This script safely collects all .log and .err files recursively from Gluesync"
echo " directories and creates a compressed archive (.zip or .tar.gz)."
echo ""
echo " If you have a support ticket, you can upload the logs directly to your secure"
echo " MOLO17 support area for faster troubleshooting assistance."
echo ""
echo " Usage: $0 [-e email] [-t ticket]"
echo "=================================================================================="
echo ""

# This script recursively finds all .log and .err files in the current directory and its subdirectories,
# then creates a compressed archive of them.

# It first checks for the 'zip' command. If available, it creates a 'support-logs.zip' file.
# If 'zip' is not found, it falls back to using 'tar' with gzip compression to create 'support-logs.tar.gz'.
# If neither command is found, it prints an error message.

# Script version
SCRIPT_VERSION="1.2"

ARCHIVE_FILE_LIST=""
TAR_FILE_LIST=""
TAR_ERR_FILE=""
EXTRA_DIR=""

cleanup() {
    if [ -n "$ARCHIVE_FILE_LIST" ] && [ -f "$ARCHIVE_FILE_LIST" ]; then
        rm -f "$ARCHIVE_FILE_LIST" >/dev/null 2>&1 || true
    fi
    if [ -n "$TAR_FILE_LIST" ] && [ -f "$TAR_FILE_LIST" ]; then
        rm -f "$TAR_FILE_LIST" >/dev/null 2>&1 || true
    fi
    if [ -n "$TAR_ERR_FILE" ] && [ -f "$TAR_ERR_FILE" ]; then
        rm -f "$TAR_ERR_FILE" >/dev/null 2>&1 || true
    fi
    if [ -n "$EXTRA_DIR" ] && [ -d "$EXTRA_DIR" ]; then
        rm -rf "$EXTRA_DIR" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT INT TERM

append_section_header() {
    local file="$1"
    local title="$2"
    {
        echo "=============================="
        echo "$title"
        echo "=============================="
    } >> "$file"
}

append_cmd_output() {
    local target="$1"
    local title="$2"
    shift 2
    local cmd="$1"
    {
        echo "### $title"
        if command -v "$cmd" >/dev/null 2>&1; then
            "$@" 2>&1
            local status=$?
            if [ "$status" -ne 0 ]; then
                echo "(command exited with status $status)"
            fi
        else
            echo "Command '$cmd' not available on this system."
        fi
        echo ""
    } >> "$target"
}

get_logical_cores() {
    local value=""
    if command -v nproc >/dev/null 2>&1; then
        value=$(nproc 2>/dev/null || true)
    elif command -v sysctl >/dev/null 2>&1; then
        value=$(sysctl -n hw.logicalcpu 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || true)
    fi
    if [ -n "$value" ]; then
        echo "$value"
    else
        echo "unknown"
    fi
}

get_physical_cores() {
    local value=""
    if command -v lscpu >/dev/null 2>&1; then
        local cores sockets
        cores=$(lscpu | awk -F: '/^Core\(s\) per socket/ {gsub(/ /,"",$2); print $2; exit}' 2>/dev/null)
        sockets=$(lscpu | awk -F: '/^Socket\(s\)/ {gsub(/ /,"",$2); print $2; exit}' 2>/dev/null)
        if [[ "$cores" =~ ^[0-9]+$ ]] && [[ "$sockets" =~ ^[0-9]+$ ]]; then
            value=$((cores * sockets))
        fi
    fi
    if [ -z "$value" ] && command -v sysctl >/dev/null 2>&1; then
        value=$(sysctl -n hw.physicalcpu 2>/dev/null || true)
    fi
    if [ -z "$value" ] && [ -f /proc/cpuinfo ]; then
        value=$(awk -F: '/physical id/ {gsub(/ /,"",$2); phys=$2}
/cpu cores/ {gsub(/ /,"",$2); cores[$2]=1}
END {if(length(cores)>0 && phys!=""){for (c in cores) total+=c; print total}else if(length(cores)>0){for (c in cores) {last=c}; print last}}' /proc/cpuinfo 2>/dev/null)
    fi
    if [ -n "$value" ]; then
        echo "$value"
    else
        echo "unknown"
    fi
}

collect_system_info() {
    local output_file="$1"
    {
        echo "Gluesync System Report"
        echo "Generated on: $(date -u) (UTC)"
        echo "Hostname: $(hostname 2>/dev/null || echo "unknown")"
        echo ""
    } > "$output_file"

    append_section_header "$output_file" "Operating System"
    append_cmd_output "$output_file" "uname -a" uname -a
    append_cmd_output "$output_file" "hostnamectl status" hostnamectl status
    if [ -f /etc/os-release ]; then
        {
            echo "### /etc/os-release"
            cat /etc/os-release
            echo ""
        } >> "$output_file"
    fi
    append_cmd_output "$output_file" "lsb_release -a" lsb_release -a
    append_cmd_output "$output_file" "sw_vers" sw_vers

    append_section_header "$output_file" "CPU"
    {
        echo "Logical cores: $(get_logical_cores)"
        echo "Physical cores: $(get_physical_cores)"
        echo ""
    } >> "$output_file"
    append_cmd_output "$output_file" "lscpu" lscpu
    append_cmd_output "$output_file" "sysctl -n machdep.cpu.brand_string" sysctl -n machdep.cpu.brand_string
    if [ -f /proc/cpuinfo ]; then
        {
            echo "### /proc/cpuinfo (first 40 lines)"
            head -n 40 /proc/cpuinfo 2>/dev/null || echo "Unable to read /proc/cpuinfo"
            echo ""
        } >> "$output_file"
    fi

    append_section_header "$output_file" "Virtualization"
    if command -v systemd-detect-virt >/dev/null 2>&1; then
        append_cmd_output "$output_file" "systemd-detect-virt" systemd-detect-virt
    elif command -v hostnamectl >/dev/null 2>&1; then
        {
            echo "### hostnamectl virtualization hint"
            hostnamectl 2>/dev/null | awk -F: '/Virtualization/ {print $0}'
            echo ""
        } >> "$output_file"
    else
        {
            echo "Virtualization detection tools not available."
            echo ""
        } >> "$output_file"
    fi
    if [ -f /proc/cpuinfo ]; then
        {
            echo "### Hypervisor flags from /proc/cpuinfo"
            grep -i hypervisor /proc/cpuinfo 2>/dev/null || echo "No hypervisor flags detected."
            echo ""
        } >> "$output_file"
    fi

    append_section_header "$output_file" "Memory"
    append_cmd_output "$output_file" "free -h" free -h
    append_cmd_output "$output_file" "vm_stat" vm_stat
    append_cmd_output "$output_file" "sysctl -n hw.memsize" sysctl -n hw.memsize
    if [ -f /proc/meminfo ]; then
        {
            echo "### /proc/meminfo (first 40 lines)"
            head -n 40 /proc/meminfo 2>/dev/null || echo "Unable to read /proc/meminfo"
            echo ""
        } >> "$output_file"
    fi

    append_section_header "$output_file" "Disk"
    append_cmd_output "$output_file" "df -h" df -h
    append_cmd_output "$output_file" "lsblk -o NAME,SIZE,TYPE,MOUNTPOINT" lsblk -o NAME,SIZE,TYPE,MOUNTPOINT
    append_cmd_output "$output_file" "diskutil list" diskutil list

    append_section_header "$output_file" "Network"
    append_cmd_output "$output_file" "ip addr" ip addr
    append_cmd_output "$output_file" "ifconfig -a" ifconfig -a
    append_cmd_output "$output_file" "netstat -rn" netstat -rn
    append_cmd_output "$output_file" "networksetup -listallhardwareports" networksetup -listallhardwareports
}

collect_file_dumps() {
    local output_file="$1"
    shift
    local patterns=("$@")
    {
        echo "Full dump generated on: $(date -u) (UTC)"
        echo ""
    } > "$output_file"

    if [ "${#patterns[@]}" -eq 0 ]; then
        echo "No file patterns supplied." >> "$output_file"
        return
    fi

    local found=0
    local find_cmd=(find . -type f '(')
    local last_index=$(( ${#patterns[@]} - 1 ))
    for idx in "${!patterns[@]}"; do
        find_cmd+=(-name "${patterns[$idx]}")
        if [ "$idx" -lt "$last_index" ]; then
            find_cmd+=(-o)
        fi
    done
    find_cmd+=(')' -print0)

    while IFS= read -r -d '' file; do
        if [ -n "$EXTRA_DIR" ] && [[ "$file" == "./$EXTRA_DIR/"* ]]; then
            continue
        fi
        found=1
        local display_path="${file#./}"
        {
            echo "----- START ${display_path} -----"
            if [ -r "$file" ]; then
                cat "$file"
            else
                echo "Unable to read file due to permissions."
            fi
            echo "----- END ${display_path} -----"
            echo ""
        } >> "$output_file"
    done < <("${find_cmd[@]}")

    if [ "$found" -eq 0 ]; then
        echo "No matching files were found within $SEARCH_DIR." >> "$output_file"
    fi
}

collect_docker_info() {
    local output_file="$1"
    {
        echo "Docker diagnostics generated on: $(date -u) (UTC)"
        echo ""
    } > "$output_file"

    append_cmd_output "$output_file" "docker --version" docker --version
    append_cmd_output "$output_file" "docker info" docker info
    append_cmd_output "$output_file" "docker ps -a" docker ps -a
    append_cmd_output "$output_file" "docker images" docker images
    append_cmd_output "$output_file" "docker compose version" docker compose version
    append_cmd_output "$output_file" "docker-compose --version" docker-compose --version
}

# Function to URL-encode a string
urlencode() {
    echo "$1" | sed 's/@/%40/g'
}

# Function to print last error and exit
fail_with_last_error() {
    local message="$1"
    local tmp=$(mktemp)
    echo "$message" > "$tmp"
    tail -n 1 "$tmp" >&2
    rm -f "$tmp"
    exit 1
}

# FTP upload parameters and cleanup flag
EMAIL=""
TICKET=""
CLEAN_AFTER_UPLOAD=false

# Manual parse command line arguments to support --clean-after-upload optional flag
while [[ $# -gt 0 ]]; do
  case "$1" in
    -e)
      EMAIL="$2"
      shift 2
      ;;
    -t)
      TICKET="$2"
      shift 2
      ;;
    --clean-after-upload)
      CLEAN_AFTER_UPLOAD=true
      shift
      ;;
    *)
      fail_with_last_error "Usage: $0 [-e email] [-t ticket]"
      ;;
  esac
done

# If email or ticket not provided, prompt interactively
if [ -z "$EMAIL" ]; then
  read -p "Enter your email address: " EMAIL
fi
if [ -z "$TICKET" ]; then
  read -p "Enter ticket number: " TICKET
fi

# The directory where the script is located
BASE_DIR=$(dirname "$0")

# The directory to search for logs ( prefer if root-folder exists alongside the script, or the parent directory of the script's location)
if [ -d "$BASE_DIR/root-folder" ]; then
  SEARCH_DIR=$(realpath "$BASE_DIR/root-folder")
else
  SEARCH_DIR=$(realpath "$BASE_DIR/..")
fi

INVOKE_DIR=$(pwd)
OUTPUT_DIR="$INVOKE_DIR"
TMP_TEST=".collect_logs_write_test_$$"
if ! ( : > "$OUTPUT_DIR/$TMP_TEST" 2>/dev/null && rm -f "$OUTPUT_DIR/$TMP_TEST" 2>/dev/null ); then
  OUTPUT_DIR="/tmp"
fi

# The name of the output archive
ARCHIVE_NAME="support-logs-v${SCRIPT_VERSION}-$(date +%Y%m%d-%H%M%S)"
# Change to the search directory to ensure paths in the archive are relative
cd "$SEARCH_DIR" || fail_with_last_error "Failed to access search directory: $SEARCH_DIR"

echo "Log Collection Script v${SCRIPT_VERSION} - gathering data within $SEARCH_DIR..."

EXTRA_DIR="gluesync-support-extra-$(date +%s)-$$"
mkdir -p "$EXTRA_DIR" || fail_with_last_error "Unable to create diagnostics directory: $EXTRA_DIR"

echo "Creating diagnostics reports under $EXTRA_DIR ..."
collect_system_info "$EXTRA_DIR/system-report.txt"
collect_file_dumps "$EXTRA_DIR/yaml-files-dump.txt" "*.yaml" "*.yml"
collect_file_dumps "$EXTRA_DIR/xml-files-dump.txt" "*.xml"
collect_docker_info "$EXTRA_DIR/docker-report.txt"

ARCHIVE_FILE_LIST=$(mktemp 2>/dev/null || echo "/tmp/collect-logs.$$.files")
find . -type f \( -name "*.log" -o -name "*.err" \) -print > "$ARCHIVE_FILE_LIST"
if [ -d "$EXTRA_DIR" ]; then
  find "$EXTRA_DIR" -type f -print >> "$ARCHIVE_FILE_LIST"
fi

if [ ! -s "$ARCHIVE_FILE_LIST" ]; then
    echo "No log, error, or diagnostics files found to archive."
    exit 0
fi

# Resolve absolute paths to avoid PATH inconsistencies under sh
ZIP_CMD=$(command -v zip 2>/dev/null || true)
TAR_CMD=$(command -v tar 2>/dev/null || true)

# Prefer zip if truly runnable; otherwise fall back to tar
if command -v zip >/dev/null 2>&1; then
  echo "'zip' command found. Creating ${ARCHIVE_NAME}.zip..."
  # Feed file list directly into zip via stdin (-@ reads from stdin)
  if zip -@ "${OUTPUT_DIR}/${ARCHIVE_NAME}.zip" < "$ARCHIVE_FILE_LIST"; then
    ARCHIVE_PATH="${OUTPUT_DIR}/${ARCHIVE_NAME}.zip"
    echo "Successfully created ${ARCHIVE_PATH}"
  else
    fail_with_last_error "Failed to create ${ARCHIVE_NAME}.zip"
  fi
elif command -v tar >/dev/null 2>&1; then
  echo "'zip' command not found. Falling back to 'tar'."
  echo "Creating ${ARCHIVE_NAME}.tar.gz..."
  # Detect GNU tar capabilities
  TAR_IS_GNU=0
  if "$TAR_CMD" --version 2>/dev/null | grep -qi "gnu tar"; then
    TAR_IS_GNU=1
  fi

  TAR_ERR_FILE=$(mktemp 2>/dev/null || echo "/tmp/collect-logs-tar.$$.err")

  if [ "$TAR_IS_GNU" -eq 1 ]; then
    TAR_FILE_LIST=$(mktemp 2>/dev/null || echo "/tmp/collect-logs.$$.listnull")
    while IFS= read -r file || [ -n "$file" ]; do
      [ -z "$file" ] && continue
      printf '%s\0' "$file" >> "$TAR_FILE_LIST"
    done < "$ARCHIVE_FILE_LIST"
    TAR_WARN_FLAG=""
    IGNORE_FAILED_READ_FLAG=""
    if "$TAR_CMD" --help 2>&1 | grep -q -- "--warning"; then TAR_WARN_FLAG="--warning=no-file-changed"; fi
    if "$TAR_CMD" --help 2>&1 | grep -q -- "--ignore-failed-read"; then IGNORE_FAILED_READ_FLAG="--ignore-failed-read"; fi
    "$TAR_CMD" --null $TAR_WARN_FLAG $IGNORE_FAILED_READ_FLAG -czvf "${OUTPUT_DIR}/${ARCHIVE_NAME}.tar.gz" -T "$TAR_FILE_LIST" 2>"$TAR_ERR_FILE"
    TAR_STATUS=$?
  else
    "$TAR_CMD" -czvf "${OUTPUT_DIR}/${ARCHIVE_NAME}.tar.gz" -T "$ARCHIVE_FILE_LIST" 2>"$TAR_ERR_FILE"
    TAR_STATUS=$?
  fi
  ARCHIVE_PATH="${OUTPUT_DIR}/${ARCHIVE_NAME}.tar.gz"

  if [ ${TAR_STATUS:-1} -ne 0 ] && [ -s "$ARCHIVE_PATH" ]; then
    echo "Archive created with warnings: $ARCHIVE_PATH"
    TAR_STATUS=0
  fi

  if [ ${TAR_STATUS:-0} -eq 0 ]; then
    echo "Successfully created $ARCHIVE_PATH"
    rm -f "$TAR_ERR_FILE" >/dev/null 2>&1 || true
  else
    # Only now print tar error output if we truly failed to produce an archive
    tail -n 1 "$TAR_ERR_FILE" >&2 || true
    rm -f "$TAR_ERR_FILE" >/dev/null 2>&1 || true
    fail_with_last_error "Failed to create $ARCHIVE_PATH"
  fi
else
  fail_with_last_error "Error: Neither 'zip' nor 'tar' command found or runnable. Please install one of them to create the archive."
fi

# Attempt FTP upload if email and ticket provided
if [ -n "$EMAIL" ] && [ -n "$TICKET" ]; then
  ENCODED_EMAIL=$(urlencode "$EMAIL")
  echo "Uploading $ARCHIVE_PATH to FTP..."
  if curl -T "$ARCHIVE_PATH" "ftp://$TICKET:$ENCODED_EMAIL@ftp.molo17.com/"; then
    echo "Successfully uploaded to FTP."
    # Remove archive if cleaning after upload requested
    if [ "$CLEAN_AFTER_UPLOAD" = true ]; then
      echo "Removing archive: $ARCHIVE_PATH"
      rm -f "$ARCHIVE_PATH"
    fi
  else
    fail_with_last_error "Failed to upload to FTP."
  fi
else
  echo "Email or ticket not provided. Skipping upload."
  exit 1
fi
