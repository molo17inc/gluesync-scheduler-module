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

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# After assembly, this script will sit inside the 'gluesync-docker' folder.
# The docker compose file may be in the same folder or in the parent directory.
# Search in both locations and use the first one found.

search_dirs=("$script_dir" "$(dirname "$script_dir")")

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

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker is not installed or not in PATH." >&2
  exit 1
fi

COMPOSE_CMD=""
if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD="docker-compose"
else
  echo "Error: Neither 'docker compose' nor 'docker-compose' is available. Please install Docker Compose." >&2
  exit 1
fi

echo "Running: $COMPOSE_CMD -f $compose_file down --remove-orphans"
$COMPOSE_CMD -f "$compose_file" down --remove-orphans

echo "Gluesync stack stopped."
