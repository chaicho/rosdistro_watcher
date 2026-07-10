#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Build and optionally publish the RosdepAuditor artifact Docker image.

Required:
  IMAGE=<registry-or-local-name>

Optional:
  VERSION=<tag>                    Defaults to git describe or git SHA.
  PLATFORMS=<platforms>            Defaults to linux/amd64.
  PUSH=1                           Push to registry instead of loading locally.
  PREFETCH_SENTENCE_MODEL=0        Skip caching the default SentenceTransformer model.
  INCLUDE_LOCAL_CACHE=1            Bundle local ~/cache/rosdistro_watcher into the image.
  LOCAL_CACHE_DIR=<path>           Override the local cache directory to bundle.
  TARBALL=<path.tar.gz>            Save a local single-platform image archive.
  DOCKER_BUILD_NETWORK=host        Optional Docker build network mode.

Examples:
  IMAGE=rosdepauditor-artifact docker/publish-artifact-image.sh
  IMAGE=rosdepauditor-artifact INCLUDE_LOCAL_CACHE=1 docker/publish-artifact-image.sh
  IMAGE=ghcr.io/acme/rosdepauditor-artifact VERSION=2026-ae PUSH=1 docker/publish-artifact-image.sh
  IMAGE=rosdepauditor-artifact VERSION=2026-ae TARBALL=rosdepauditor-artifact_2026-ae.tar.gz docker/publish-artifact-image.sh
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ -z "${IMAGE:-}" ]]; then
  usage >&2
  exit 2
fi

VERSION="${VERSION:-2026-ae}"

PLATFORMS="${PLATFORMS:-linux/amd64}"
PUSH="${PUSH:-0}"
PREFETCH_SENTENCE_MODEL="${PREFETCH_SENTENCE_MODEL:-1}"
INCLUDE_LOCAL_CACHE="${INCLUDE_LOCAL_CACHE:-0}"
LOCAL_CACHE_DIR="${LOCAL_CACHE_DIR:-${HOME}/cache/rosdistro_watcher}"
DOCKER_BUILD_NETWORK="${DOCKER_BUILD_NETWORK:-}"

# --build-context lets us reference external directories directly from the
# Dockerfile via COPY --from=<name>, no hardlinking into the repo needed.
tmp_dirs=()
cleanup_contexts() { for d in "${tmp_dirs[@]}"; do rm -rf "$d"; done; }
trap cleanup_contexts EXIT

# rosdistro cache context
if [[ "${INCLUDE_LOCAL_CACHE}" == "1" && -d "${LOCAL_CACHE_DIR}" ]]; then
  ROS_CACHE_CTX="${LOCAL_CACHE_DIR}"
else
  d=$(mktemp -d); tmp_dirs+=("$d"); ROS_CACHE_CTX="$d"
fi

# huggingface model cache context
if [[ "${PREFETCH_SENTENCE_MODEL}" == "1" && -d "${HOME}/.cache/huggingface" ]]; then
  HF_CACHE_CTX="${HOME}/.cache/huggingface"
else
  d=$(mktemp -d); tmp_dirs+=("$d"); HF_CACHE_CTX="$d"
fi

if [[ "${PLATFORMS}" == *,* && "${PUSH}" != "1" ]]; then
  echo "Multi-platform output requires PUSH=1; got PLATFORMS=${PLATFORMS}" >&2
  exit 2
fi

if [[ -n "${TARBALL:-}" && "${PLATFORMS}" == *,* ]]; then
  echo "TARBALL output supports a single platform only; got PLATFORMS=${PLATFORMS}" >&2
  exit 2
fi

if [[ "${PUSH}" == "1" && -n "${TARBALL:-}" ]]; then
  echo "Use either PUSH=1 or TARBALL=<path>, not both." >&2
  exit 2
fi

BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if git rev-parse --short=12 HEAD >/dev/null 2>&1; then
  VCS_REF="$(git rev-parse --short=12 HEAD)"
else
  VCS_REF="unknown"
fi

echo "Building ${IMAGE}:${VERSION}"
echo "  local-cache: ${INCLUDE_LOCAL_CACHE}  hf-model: ${PREFETCH_SENTENCE_MODEL}"

build_cmd=(
  docker build
  --build-arg "BUILD_DATE=${BUILD_DATE}"
  --build-arg "VCS_REF=${VCS_REF}"
  --build-arg "VERSION=${VERSION}"
  --build-context "rosdistro-cache=${ROS_CACHE_CTX}"
  --build-context "hf-cache=${HF_CACHE_CTX}"
  -t "${IMAGE}:${VERSION}"
  -t "${IMAGE}:latest"
)

if [[ -n "${DOCKER_BUILD_NETWORK}" ]]; then
  build_cmd+=(--network "${DOCKER_BUILD_NETWORK}")
fi

build_cmd+=(.)

"${build_cmd[@]}"

# if [[ "${PUSH}" == "1" ]]; then
#   echo "Pushing ${IMAGE}:${VERSION}..."
#   docker push "${IMAGE}:${VERSION}"
#   docker push "${IMAGE}:latest"
# fi

# if [[ -n "${TARBALL:-}" ]]; then
#   echo "Saving ${IMAGE}:${VERSION} to ${TARBALL}"
#   docker save "${IMAGE}:${VERSION}" | gzip -c > "${TARBALL}"
#   sha256sum "${TARBALL}" > "${TARBALL}.sha256"
#   echo "Wrote ${TARBALL} and ${TARBALL}.sha256"
# fi
