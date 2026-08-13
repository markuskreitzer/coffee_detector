#!/usr/bin/env bash
set -euo pipefail

source_file="${1:-$HOME/.config/coffee-detector/env}"
output_file="esp32/include/secrets.h"

if [[ ! -f "$source_file" ]]; then
  echo "Pushover environment file not found" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$source_file"
set +a
: "${PUSHOVER_COFFEE_TOKEN:?PUSHOVER_COFFEE_TOKEN is required}"
: "${PUSHOVER_USER:?PUSHOVER_USER is required}"

umask 077
mkdir -p "$(dirname "$output_file")"
{
  echo '#pragma once'
  printf '#define PUSHOVER_COFFEE_TOKEN "%s"\n' "$PUSHOVER_COFFEE_TOKEN"
  printf '#define PUSHOVER_USER "%s"\n' "$PUSHOVER_USER"
} >"$output_file"
echo "Generated $output_file with mode 600"
