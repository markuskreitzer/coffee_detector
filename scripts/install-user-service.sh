#!/usr/bin/env bash

set -euo pipefail

project_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
config_root=${XDG_CONFIG_HOME:-"$HOME/.config"}
credential_file="$config_root/coffee-detector/env"
unit_dir="$config_root/systemd/user"
unit_file="$unit_dir/coffee-detector.service"

if [[ ! -s "$credential_file" ]]; then
  printf 'Missing Pushover environment file: %s\n' "$credential_file" >&2
  exit 1
fi

python3 -m venv "$project_dir/.venv"
"$project_dir/.venv/bin/pip" install -r "$project_dir/requirements.txt"
mkdir -p "$unit_dir"
sed "s|@PROJECT_DIR@|$project_dir|g" \
  "$project_dir/systemd/coffee-detector.service.in" >"$unit_file"
systemctl --user daemon-reload
systemctl --user enable --now coffee-detector.service
