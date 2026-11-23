#!/bin/bash
# Builds the print agent's image and pushes it to Docker Hub, tagged with its version and latest:
#   ./publish.sh            (asks before pushing)
#   ./publish.sh -y         (pushes without asking)
#
# It's built for PCs (linux/amd64) and Raspberry Pis (linux/arm64 on a 64-bit OS, linux/arm/v7 on a 32-bit one,
# e.g. a Pi 3), as one image: Docker picks the right one. Building the Pi ones here needs QEMU emulation on the
# Docker host, registered once per boot (as root): docker run --privileged --rm tonistiigi/binfmt --install arm64,arm
# Set PLATFORMS=linux/amd64 to build only for PCs.
#
# The version is label_agent/__init__.py's __version__. Publish only a committed, tagged version, so every
# image matches a tag in git: commit, bump the version, tag it (git tag 2026.11.2), then run this. The tests
# run first, in a throwaway container, and a failure stops it.
set -euo pipefail

IMAGE="${IMAGE:-barrettefab/redmine-qbo-label-agent}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64,linux/arm/v7}"
BUILDER=label-agent-multiarch
cd "$(dirname "$0")"

VERSION=$(sed -n "s/^__version__ = '\(.*\)'/\1/p" label_agent/__init__.py)
[ -n "$VERSION" ] || { echo "No __version__ in label_agent/__init__.py" >&2; exit 1; }

if [ -n "$(git status --porcelain)" ]; then
  echo "There are uncommitted changes: commit them first" >&2; exit 1
fi
if [ "$(git rev-parse HEAD)" != "$(git rev-parse -q --verify "refs/tags/$VERSION^{commit}" || true)" ]; then
  echo "HEAD isn't tagged $VERSION: bump __version__, commit and tag it first" >&2; exit 1
fi

echo "Testing $VERSION..."
git archive HEAD | docker run --rm -i python:3.12-slim sh -c \
  'mkdir /a && cd /a && tar -xf - && pip install -q --root-user-action=ignore -r requirements-dev.txt && python -m pytest -q'

# Several platforms need a docker-container builder, which pushes as it builds
if ! docker buildx inspect "$BUILDER" >/dev/null 2>&1; then
  docker buildx create --name "$BUILDER" --driver docker-container >/dev/null
fi
# e.g. "linux/amd64*, linux/arm64, linux/arm/v7" (* marks the builder's own)
supported=$(docker buildx inspect --bootstrap "$BUILDER" | sed -n 's/^Platforms: *//p' | tr -d '*')
for platform in ${PLATFORMS//,/ }; do
  case ", $supported," in
    *", $platform,"*) ;;
    *) echo "This Docker host can't build $platform: register QEMU emulation first (see the top of this script)" >&2
       exit 1 ;;
  esac
done

if [ "${1:-}" != "-y" ]; then
  read -r -p "Build and push $IMAGE:$VERSION and :latest ($PLATFORMS) to Docker Hub? [y/N] " answer
  [ "$answer" = "y" ] || { echo "Not pushed"; exit 0; }
fi
echo "Building $IMAGE:$VERSION ($PLATFORMS)..."
docker buildx build --builder "$BUILDER" --platform "$PLATFORMS" --push \
  --label org.opencontainers.image.version="$VERSION" \
  --label org.opencontainers.image.revision="$(git rev-parse HEAD)" \
  --label org.opencontainers.image.source=https://github.com/rickbarrette/redmine_qbo_label_agent \
  -t "$IMAGE:$VERSION" -t "$IMAGE:latest" .
echo "Published $IMAGE:$VERSION"
