#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

env_file="${WORLDCUP_SYNC_ENV_FILE:-.env}"

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

dotenv_get() {
  local key="$1"
  local file="$2"
  local line value

  [ -f "$file" ] || return 1

  while IFS= read -r line || [ -n "$line" ]; do
    line="$(trim "$line")"
    case "$line" in
      "" | \#*) continue ;;
      export\ *) line="${line#export }" ;;
    esac

    case "$line" in
      "$key="*)
        value="$(trim "${line#*=}")"
        if [[ "$value" == \"*\" && "$value" == *\" ]]; then
          value="${value:1:${#value}-2}"
        elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
          value="${value:1:${#value}-2}"
        else
          value="$(trim "${value%%#*}")"
        fi
        printf '%s\n' "$value"
        return 0
        ;;
    esac
  done < "$file"

  return 1
}

dotenv_or_empty() {
  dotenv_get "$1" "$env_file" || true
}

command -v ssh >/dev/null 2>&1 || {
  echo "ssh is required to sync live data." >&2
  exit 1
}

command -v rsync >/dev/null 2>&1 || {
  echo "rsync is required to sync live data." >&2
  exit 1
}

rsync_progress_args=(--progress)
if rsync --help 2>/dev/null | grep -q -- '--info='; then
  rsync_progress_args=(--info=progress2)
fi

deploy_host="${DEPLOY_HOST:-$(dotenv_or_empty DEPLOY_HOST)}"
deploy_user="${DEPLOY_USER:-$(dotenv_or_empty DEPLOY_USER)}"
deploy_port="${DEPLOY_PORT:-$(dotenv_or_empty DEPLOY_PORT)}"
deploy_path="${DEPLOY_PATH:-$(dotenv_or_empty DEPLOY_PATH)}"

deploy_user="${deploy_user:-deploy}"
deploy_port="${deploy_port:-22}"
deploy_path="${deploy_path:-/opt/worldcup-predictions}"

if [ -z "$deploy_host" ]; then
  echo "Set DEPLOY_HOST in .env before syncing live data." >&2
  exit 1
fi

local_data_dir="data"
remote_data_dir="${deploy_path%/}/data"
remote="${deploy_user}@${deploy_host}:${remote_data_dir}/"
ssh_cmd="ssh -p ${deploy_port}"
remote_lock_probe="flock -s -n /tmp/worldcup-predictions-scheduled.lock flock -s -n /tmp/worldcup-predictions-simulate.lock true"
remote_rsync_path="flock -s -n /tmp/worldcup-predictions-scheduled.lock flock -s -n /tmp/worldcup-predictions-simulate.lock rsync"

mkdir -p "$local_data_dir"

echo "Syncing live runtime data:"
echo "  remote: $remote"
echo "  local:  $repo_root/${local_data_dir}/"
echo "  locks:  aborting if live automation is writing data"

ssh -p "$deploy_port" "${deploy_user}@${deploy_host}" 'command -v rsync >/dev/null' || {
  echo "rsync is not installed on the live server." >&2
  exit 1
}

ssh -p "$deploy_port" "${deploy_user}@${deploy_host}" "$remote_lock_probe" || {
  echo "Live automation is currently writing data; aborting without syncing." >&2
  exit 1
}

rsync -az --partial --human-readable "${rsync_progress_args[@]}" \
  -e "$ssh_cmd" \
  --rsync-path="$remote_rsync_path" \
  "$remote" \
  "${local_data_dir}/"

echo "Live runtime data synced successfully."
