#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$script_dir/.." && pwd)"

image="${WORLDCUP_PREDICTIONS_IMAGE:-ghcr.io/helga-agentur/worldcup-predictions:main}"
compose_file="${WORLDCUP_PREDICTIONS_COMPOSE_FILE:-compose.prod.yaml}"
service="${WORLDCUP_PREDICTIONS_SERVICE:-predictions}"
revision_label="${WORLDCUP_PREDICTIONS_REVISION_LABEL:-org.opencontainers.image.revision}"

timestamp() {
  date -u +'%Y-%m-%dT%H:%M:%SZ'
}

log() {
  printf '[%s] %s\n' "$(timestamp)" "$*"
}

fail() {
  log "ERROR: $*"
  exit 1
}

short_revision() {
  git rev-parse --short "$1" 2>/dev/null || printf '%.12s' "$1"
}

cd "$project_root"

log "live scheduled update wrapper started"
log "working directory: $project_root"
log "current checkout: $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
log "pulling production image: $image"

docker compose -f "$compose_file" pull "$service"

# Every pull leaves the previous image behind; unpruned they filled 19GB
# (41 images) by 2026-07-15. Keep only the last day's images.
docker image prune -af --filter 'until=24h' >/dev/null 2>&1 || log "image prune failed (non-fatal)"

image_identity="$(docker image inspect "$image" --format '{{.Id}} {{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}' 2>/dev/null || true)"
if [ -n "$image_identity" ]; then
  log "local image after pull: $image_identity"
else
  fail "pulled image is not inspectable: $image"
fi

image_revision="$(
  docker image inspect "$image" --format "{{ index .Config.Labels \"$revision_label\" }}" 2>/dev/null || true
)"
if [ -z "$image_revision" ] || [ "$image_revision" = "<no value>" ]; then
  fail "image $image does not define $revision_label"
fi
if ! [[ "$image_revision" =~ ^[0-9a-fA-F]{40}$ ]]; then
  fail "image $image has invalid $revision_label value: $image_revision"
fi

log "image revision: $(short_revision "$image_revision")"
log "fetching origin/main"
git fetch --prune origin +refs/heads/main:refs/remotes/origin/main

if ! git cat-file -e "$image_revision^{commit}" 2>/dev/null; then
  fail "image revision does not exist in local git history after fetch: $image_revision"
fi
if ! git merge-base --is-ancestor "$image_revision" origin/main; then
  fail "image revision is not reachable from origin/main: $image_revision"
fi

current_revision="$(git rev-parse HEAD)"
if [ "$current_revision" = "$image_revision" ]; then
  log "checkout already matches image revision $(short_revision "$image_revision")"
else
  log "resetting checkout from $(short_revision "$current_revision") to $(short_revision "$image_revision")"
  git reset --hard "$image_revision"
fi

log "running scheduled-update with production compose wrapper"
"$project_root/scripts/run-prod-compose.sh" run --rm "$service" worldcup-predictions scheduled-update
log "live scheduled update wrapper finished"
