#!/usr/bin/env bash
# Build the send-issue-email image.
# One process. Do not bundle it into the arko-api image.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TAG="${TAG:-$(git -C "$ROOT" rev-parse --short HEAD)}"
REGISTRY="${REGISTRY:-}"
PREFIX=""
if [ -n "$REGISTRY" ]; then
  PREFIX="${REGISTRY%/}/"
fi

IMAGE="${PREFIX}send-issue-email:${TAG}"
echo "building ${IMAGE}"
docker build -f "$ROOT/deploy/docker/Dockerfile" -t "$IMAGE" "$ROOT"
if [ "${PUSH:-0}" = "1" ]; then
  docker push "$IMAGE"
fi

echo
echo "images:"
echo "  ${IMAGE}"
