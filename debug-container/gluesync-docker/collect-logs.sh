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
# Copyright (C) 2026 MOLO17. All rights reserved.

echo "=================================================================================="
echo " Welcome to the Gluesync Logs Collector & Uploader"
echo ""
echo " This script safely collects all .log and .err files recursively from Gluesync"
echo " directories and creates a compressed archive (.zip or .tar.gz)."
echo ""
echo " If you have a support ticket, you can upload the logs directly to your secure"
echo " MOLO17 support area for faster troubleshooting assistance."
echo ""
echo " No internet connection? Just press ENTER twice when asked for email and ticket"
echo " to skip the upload and keep the archive locally."
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
SCRIPT_VERSION="1.4 Linux"

# Remote upload defaults (can be overridden via env vars)
WEBDAV_BASE_URL=${WEBDAV_BASE_URL:-https://webdav.hq.molo17.com}
WEBDAV_REMOTE_PATH=${WEBDAV_REMOTE_PATH:-}

normalize_webdav_paths() {
    WEBDAV_BASE_URL=${WEBDAV_BASE_URL%/}
    if [ -n "$WEBDAV_REMOTE_PATH" ]; then
        WEBDAV_REMOTE_PATH="/${WEBDAV_REMOTE_PATH#/}"
        WEBDAV_REMOTE_PATH="${WEBDAV_REMOTE_PATH%/}"
    fi
}
normalize_webdav_paths

set -o pipefail

ARCHIVE_FILE_LIST=""
TAR_FILE_LIST=""
TAR_ERR_FILE=""
EXTRA_DIR=""
ZIP_ERR_FILE=""
UPLOAD_ERR_FILE=""
LOG_FILE=""

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
    append_cmd_output "$output_file" "docker stats --no-stream" docker stats --no-stream
    append_cmd_output "$output_file" "docker compose version" docker compose version
    append_cmd_output "$output_file" "docker-compose --version" docker-compose --version
}

# Function to URL-encode a string
urlencode() {
    local raw="$1"
    local length=${#raw}
    local i char encoded=""
    for (( i=0; i<length; i++ )); do
        char=${raw:i:1}
        case "$char" in
            [a-zA-Z0-9.~_-])
                encoded+="$char"
                ;;
            *)
                printf -v hex '%%%02X' "'${char}'"
                encoded+="$hex"
                ;;
        esac
    done
    printf '%s' "$encoded"
}

preflight_validate_credentials() {
    if [ -z "$EMAIL" ] || [ -z "$TICKET" ]; then
        return 0
    fi

    local err_file
    err_file=$(mktemp 2>/dev/null || echo "/tmp/collect-logs-credcheck.$$.err")
    local webdav_payload='<?xml version="1.0" encoding="UTF-8"?><propfind xmlns="DAV:"><propname/></propfind>'

    local probe_url="${WEBDAV_BASE_URL}${WEBDAV_REMOTE_PATH}/"

    echo "Validating credentials via WebDAV..."
    if curl --silent --fail --show-error --user "$TICKET:$EMAIL" \
        -H "Depth: 0" -H "Content-Type: text/xml" \
        --data "$webdav_payload" -X PROPFIND "$probe_url" \
        >/dev/null 2>"$err_file"; then
        echo "Credential pre-check succeeded via WebDAV."
        rm -f "$err_file" >/dev/null 2>&1 || true
        return 0
    else
        local dav_status=$?
        echo "WebDAV credential pre-check failed (curl exit $dav_status). Trying FTP..." >&2
    fi

    if curl --silent --fail --show-error --connect-timeout 15 --max-time 30 --user "$TICKET:$EMAIL" \
        --list-only "ftp://ftp.molo17.com/" >/dev/null 2>>"$err_file"; then
        echo "Credential pre-check succeeded via FTP fallback."
        rm -f "$err_file" >/dev/null 2>&1 || true
        return 0
    else
        local ftp_status=$?
        if [ "$ftp_status" -eq 6 ] || [ "$ftp_status" -eq 7 ] || [ "$ftp_status" -eq 28 ]; then
            echo "WARNING: Unable to reach MOLO17 upload servers (curl exit $ftp_status)." >&2
            echo "This usually means outbound HTTPS/FTP is blocked by a firewall or proxy." >&2
            echo "Logs will be collected locally. You can upload the archive manually later." >&2
            rm -f "$err_file" >/dev/null 2>&1 || true
            return 0
        fi
    fi

    fail_with_last_error "Unable to validate ticket/email credentials before collecting logs." "$err_file"
}

describe_upload_failure() {
    local status="$1"
    local log_file="$2"
    local hint=""

    if [ -n "$log_file" ] && [ -f "$log_file" ]; then
        if grep -qi "550" "$log_file"; then
            hint="FTP server returned 550 (permission/target issue). Double-check ticket $TICKET, credentials, and available space."
        elif grep -qi "530" "$log_file"; then
            hint="FTP server returned 530 (auth failure). Verify the ticket number and email are correct."
        elif grep -qi "curl: (7)" "$log_file"; then
            hint="Unable to reach ftp.molo17.com (curl 7). Ensure outbound FTP is allowed."
        fi
    fi

    if [ -z "$hint" ]; then
        case "$status" in
            18)
                hint="FTP transfer was interrupted before completion (curl 18)."
                ;;
            28)
                hint="Upload timed out (curl 28). Your firewall or proxy may be blocking outbound FTP. Check network stability or upload the archive manually."
                ;;
            67)
                hint="Authentication failed (curl 67). Verify ticket/email values."
                ;;
        esac
    fi

    if [ -n "$hint" ]; then
        echo "$hint" >&2
    fi
}

# Function to print last error and exit
fail_with_last_error() {
    local message="$1"
    local details_file="${2:-}"
    {
        echo ""
        echo "ERROR: $message"
    } >&2

    if [ -n "$details_file" ] && [ -f "$details_file" ]; then
        echo "---- Captured output ----" >&2
        tail -n 40 "$details_file" >&2 || true
        echo "---- End captured output ----" >&2
    fi

    if [ -n "$LOG_FILE" ] && [ -f "$LOG_FILE" ]; then
        echo "For a full execution trace see: $LOG_FILE" >&2
    fi

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

preflight_validate_credentials

# The directory where the script is located
BASE_DIR=$(dirname "$0")

# The directory to search for logs ( prefer if root-folder exists alongside the script, or the parent directory of the script's location)
if [ -d "$BASE_DIR/root-folder" ]; then
  SEARCH_DIR=$(realpath "$BASE_DIR/root-folder")
else
  SEARCH_DIR=$(realpath "$BASE_DIR/..")
fi

# Delete logs older than 30 days
echo "Deleting logs older than 30 days in $SEARCH_DIR ..."
find "$SEARCH_DIR" -type f \( -name "*.log" -o -name "*.err" \) -mtime +30 -exec rm -f {} \; 2>/dev/null || echo "Warning: failed to delete some old logs."

INVOKE_DIR=$(pwd)
OUTPUT_DIR="$INVOKE_DIR"
TMP_TEST=".collect_logs_write_test_$$"
if ! ( : > "$OUTPUT_DIR/$TMP_TEST" 2>/dev/null && rm -f "$OUTPUT_DIR/$TMP_TEST" 2>/dev/null ); then
  OUTPUT_DIR="/tmp"
fi

LOG_FILE="$OUTPUT_DIR/collect-logs-debug-$(date +%Y%m%d-%H%M%S).log"
if touch "$LOG_FILE" 2>/dev/null; then
  exec > >(tee -a "$LOG_FILE") 2>&1
  echo "Persisting detailed execution output to $LOG_FILE"
else
  echo "Warning: unable to create debug log at $LOG_FILE. Continuing without persistent log."
  LOG_FILE=""
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
  ZIP_ERR_FILE=$(mktemp 2>/dev/null || echo "/tmp/collect-logs-zip.$$.err")
  # Feed file list directly into zip via stdin (-@ reads from stdin)
  if zip -@ "${OUTPUT_DIR}/${ARCHIVE_NAME}.zip" < "$ARCHIVE_FILE_LIST" 2> >(tee "$ZIP_ERR_FILE" >&2); then
    ARCHIVE_PATH="${OUTPUT_DIR}/${ARCHIVE_NAME}.zip"
    echo "Successfully created ${ARCHIVE_PATH}"
  else
    fail_with_last_error "Failed to create ${ARCHIVE_NAME}.zip" "$ZIP_ERR_FILE"
  fi
  rm -f "$ZIP_ERR_FILE" >/dev/null 2>&1 || true
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
    rm -f "$TAR_ERR_FILE" >/dev/null 2>&1 || true
    fail_with_last_error "Failed to create $ARCHIVE_PATH" "$TAR_ERR_FILE"
  fi
else
  fail_with_last_error "Error: Neither 'zip' nor 'tar' command found or runnable. Please install one of them to create the archive."
fi

# Attempt upload if email and ticket provided (WebDAV first, FTP fallback)
if [ -n "$EMAIL" ] && [ -n "$TICKET" ]; then
  REMOTE_ARCHIVE_NAME=$(basename "$ARCHIVE_PATH")
  ENCODED_REMOTE_NAME=$(urlencode "$REMOTE_ARCHIVE_NAME")
  echo "Attempting WebDAV upload of $REMOTE_ARCHIVE_NAME..."
  WEBDAV_ERR_FILE=$(mktemp 2>/dev/null || echo "/tmp/collect-logs-webdav.$$.err")
  WEBDAV_URL="${WEBDAV_BASE_URL}${WEBDAV_REMOTE_PATH}/$ENCODED_REMOTE_NAME"
  if curl --fail --show-error -u "$TICKET:$EMAIL" -T "$ARCHIVE_PATH" "$WEBDAV_URL" 2> >(tee "$WEBDAV_ERR_FILE" >&2); then
    echo "Successfully uploaded via WebDAV."
    if [ "$CLEAN_AFTER_UPLOAD" = true ]; then
      echo "Removing archive: $ARCHIVE_PATH"
      rm -f "$ARCHIVE_PATH"
    fi
    rm -f "$WEBDAV_ERR_FILE" >/dev/null 2>&1 || true
    exit 0
  else
    WEBDAV_STATUS=$?
    echo "WebDAV upload failed (curl exit $WEBDAV_STATUS). Falling back to FTP..." >&2
    if [ -s "$WEBDAV_ERR_FILE" ]; then
      echo "---- WebDAV error output ----" >&2
      tail -n 40 "$WEBDAV_ERR_FILE" >&2 || true
      echo "---- End WebDAV error output ----" >&2
    fi
    rm -f "$WEBDAV_ERR_FILE" >/dev/null 2>&1 || true
  fi

  ENCODED_EMAIL=$(urlencode "$EMAIL")
  echo "Uploading $ARCHIVE_PATH to FTP..."
  UPLOAD_ERR_FILE=$(mktemp 2>/dev/null || echo "/tmp/collect-logs-upload.$$.err")
  if curl --connect-timeout 15 --max-time 120 -T "$ARCHIVE_PATH" "ftp://$TICKET:$ENCODED_EMAIL@ftp.molo17.com/$ENCODED_REMOTE_NAME" 2> >(tee "$UPLOAD_ERR_FILE" >&2); then
    echo "Successfully uploaded to FTP (fallback)."
    # Remove archive if cleaning after upload requested
    if [ "$CLEAN_AFTER_UPLOAD" = true ]; then
      echo "Removing archive: $ARCHIVE_PATH"
      rm -f "$ARCHIVE_PATH"
    fi
  else
    UPLOAD_STATUS=$?
    describe_upload_failure "$UPLOAD_STATUS" "$UPLOAD_ERR_FILE"
    echo "" >&2
    echo "Archive saved locally at: $ARCHIVE_PATH" >&2
    echo "You can retry the script or upload manually using ticket credentials." >&2
    rm -f "$UPLOAD_ERR_FILE" >/dev/null 2>&1 || true
    exit 1
  fi
  rm -f "$UPLOAD_ERR_FILE" >/dev/null 2>&1 || true
else
  echo "Email or ticket not provided. Skipping upload."
  echo "Archive saved locally at: $ARCHIVE_PATH"
  echo "Re-run with -e <email> -t <ticket> to upload automatically."
  if [ -n "$LOG_FILE" ] && [ -f "$LOG_FILE" ]; then
    echo "Execution log stored at: $LOG_FILE"
  fi
  exit 0
fi
