#!/usr/bin/env bash
set -euo pipefail

env_file="${WORLDCUP_PREDICTIONS_ENV_FILE:-/etc/worldcup-predictions/env}"
if [ -f "$env_file" ]; then
  set -a
  . "$env_file"
  set +a
fi

exec docker compose -f compose.prod.yaml "$@"
