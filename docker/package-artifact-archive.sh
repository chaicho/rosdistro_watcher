#!/usr/bin/env bash
set -euo pipefail

# Package the full ISSTA 2026 artifact submission archive.
#
# Produces a .tar.gz containing:
#   - The four AE documentation files at archive root
#     (README.md, LICENSE.md, STATUS.md, REQUIREMENTS.md)
#   - The pre-built Docker image tarball and its SHA256 checksum
#   - The full source tree (rosdep_auditor/, rosdep/, artifact/, docker/,
#     Dockerfile, .dockerignore)
#
# Prerequisites: Docker, git, and the conda environment with pre-cached
# rosdistro data and HuggingFace models (for INCLUDE_LOCAL_CACHE=1).
#
# Optional environment variables:
#   IMAGE_NAME         Docker image name (default: rosdepauditor-artifact)
#   ARTIFACT_VERSION   Image tag (default: 2026-ae)
#   ARCHIVE_NAME       Outer archive basename without extension
#                      (default: rosdepauditor-artifact-issta-2026)
#   INCLUDE_LOCAL_CACHE  Bundle local rosdistro cache (default: 1)
#   LOCAL_CACHE_DIR    Override cache dir (default: ~/cache/rosdistro_watcher)

usage() {
  cat <<'USAGE'
Usage: docker/package-artifact-archive.sh

Optional environment variables:
  IMAGE_NAME          Docker image name (default: rosdepauditor-artifact)
  ARTIFACT_VERSION    Image tag (default: 2026-ae)
  ARCHIVE_NAME        Outer archive basename without extension
                      (default: rosdepauditor-artifact-issta-2026)
  INCLUDE_LOCAL_CACHE Bundle local rosdistro cache (default: 1)
  LOCAL_CACHE_DIR     Override cache dir (default: ~/cache/rosdistro_watcher)
  PREFETCH_SENTENCE_MODEL  Prefetch SentenceTransformer model (default: 1)
  DOCKER_BUILD_NETWORK     Docker build network mode (default: host)
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

IMAGE_NAME="${IMAGE_NAME:-rosdepauditor-artifact}"
ARCHIVE_NAME="${ARCHIVE_NAME:-rosdepauditor-artifact-issta-2026}"
INCLUDE_LOCAL_CACHE="${INCLUDE_LOCAL_CACHE:-1}"
PREFETCH_SENTENCE_MODEL="${PREFETCH_SENTENCE_MODEL:-1}"
DOCKER_BUILD_NETWORK="${DOCKER_BUILD_NETWORK:-host}"

ARTIFACT_VERSION="${ARTIFACT_VERSION:-2026-ae}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARBALL="${IMAGE_NAME}-image.tar.gz"

echo "=== Step 1/3: Building Docker image ${IMAGE_NAME}:${ARTIFACT_VERSION} ==="
cd "${REPO_ROOT}"
IMAGE="${IMAGE_NAME}" \
  VERSION="${ARTIFACT_VERSION}" \
  INCLUDE_LOCAL_CACHE="${INCLUDE_LOCAL_CACHE}" \
  PREFETCH_SENTENCE_MODEL="${PREFETCH_SENTENCE_MODEL}" \
  DOCKER_BUILD_NETWORK="${DOCKER_BUILD_NETWORK}" \
  docker/publish-artifact-image.sh

echo ""
echo "=== Step 2/3: Exporting image tarball ==="
docker save "${IMAGE_NAME}:${ARTIFACT_VERSION}" | pigz -c > "${TARBALL}"
sha256sum "${TARBALL}" > "${TARBALL}.sha256"
echo "Wrote ${TARBALL} ($(du -h "${TARBALL}" | cut -f1))"

echo ""
echo "=== Step 3/3: Creating outer archive ${ARCHIVE_NAME}.tar.gz ==="

OUTER_ARCHIVE="${ARCHIVE_NAME}.tar.gz"

# Build file list from git so .gitignore rules are respected.
# We use git ls-files for tracked source files and append the generated
# Docker image tarball (which is never committed) separately.
FILELIST="$(mktemp)"
trap 'rm -f "${FILELIST}"' EXIT

git ls-files --cached -- \
    README.md \
    LICENSE.md \
    STATUS.md \
    REQUIREMENTS.md \
    rosdep_auditor/ \
    rosdep/ \
    artifact/ \
    docker/ \
    Dockerfile \
    .dockerignore \
    > "${FILELIST}"

# Append the generated Docker image tarball and its checksum.
echo "${TARBALL}"       >> "${FILELIST}"
echo "${TARBALL}.sha256" >> "${FILELIST}"

# Filter out files that don't exist on disk (e.g., intentionally
# deleted tracked files) so tar doesn't fail.
EXISTING_FILES="$(mktemp)"
grep -v '^$' "${FILELIST}" | while IFS= read -r f; do
  if [[ -f "${REPO_ROOT}/${f}" ]]; then
    echo "$f"
  fi
done > "${EXISTING_FILES}"
mv "${EXISTING_FILES}" "${FILELIST}"

# Prefix every archived path with the outer archive name so extraction creates
# a single top-level directory instead of scattering files into CWD.
tar -czf "${OUTER_ARCHIVE}" \
  --transform "s,^,${ARCHIVE_NAME}/," \
  -T "${FILELIST}"

rm -f "${FILELIST}"
trap - EXIT

echo "Wrote ${OUTER_ARCHIVE} ($(du -h "${OUTER_ARCHIVE}" | cut -f1))"

# Clean up intermediate tarball — the outer archive contains it.
rm -f "${TARBALL}" "${TARBALL}.sha256"

echo ""
echo "=== Done ==="
echo "Archive: ${OUTER_ARCHIVE}"
echo ""
echo "Contents:"
tar -tzf "${OUTER_ARCHIVE}" | head -40
echo "..."
echo ""
echo "To verify on another machine:"
echo "  tar -xzf ${OUTER_ARCHIVE}"
echo "  docker load -i ${IMAGE_NAME}.tar.gz"
echo "  docker run --rm ${IMAGE_NAME}:${ARTIFACT_VERSION} python artifact/run_quickstart.py"
