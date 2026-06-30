#!/usr/bin/env bash
set -u

if [ "$#" -lt 2 ]; then
  echo "Usage: $0 <label> <command> [args...]" >&2
  exit 2
fi

label="$1"
shift

start_epoch="$(date +%s)"
start_iso="$(date '+%Y-%m-%dT%H:%M:%S%z')"
printf '[%s] START %s\n' "$start_iso" "$label"

"$@"
status="$?"

end_epoch="$(date +%s)"
end_iso="$(date '+%Y-%m-%dT%H:%M:%S%z')"
duration_seconds="$((end_epoch - start_epoch))"
printf '[%s] FINISH %s status=%s duration=%ss\n' "$end_iso" "$label" "$status" "$duration_seconds"

exit "$status"
