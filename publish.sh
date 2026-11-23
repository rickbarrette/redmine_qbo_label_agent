#!/bin/bash
# Builds the print agent's image and pushes it to Docker Hub, tagged with its version and latest:
#   ./publish.sh            (asks before pushing)
#   ./publish.sh -y         (pushes without asking)
#
# The version is label_agent/__init__.py's __version__. Publish only a committed, tagged version, so every
# image matches a tag in git: commit, bump the version, tag it (git tag 2026.11.2), then run this. The tests
# run first, in a throwaway container, and a failure stops it.
set -euo pipefail

IMAGE="${IMAGE:-barrettefab/redmine-qbo-label-agent}"
PLATFORMS="${PLATFORMS:-linux/amd64}"
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

echo "Building $IMAGE:$VERSION ($PLATFORMS)..."
docker buildx build --platform "$PLATFORMS" --load \
  --label org.opencontainers.image.version="$VERSION" \
  --label org.opencontainers.image.revision="$(git rev-parse HEAD)" \
  --label org.opencontainers.image.source=https://github.com/rickbarrette/redmine_qbo_label_agent \
  -t "$IMAGE:$VERSION" -t "$IMAGE:latest" .

if [ "${1:-}" != "-y" ]; then
  read -r -p "Push $IMAGE:$VERSION and :latest to Docker Hub? [y/N] " answer
  [ "$answer" = "y" ] || { echo "Not pushed"; exit 0; }
fi
docker push "$IMAGE:$VERSION"
docker push "$IMAGE:latest"
echo "Published $IMAGE:$VERSION"
