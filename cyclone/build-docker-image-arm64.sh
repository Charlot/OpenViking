#!/bin/sh
# 构建 OpenViking ARM64 Docker 镜像

set -eu

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="10.86.20.10/library/cyclone-openviking:test-arm64"

docker buildx build --load \
    -f "$PROJECT_DIR/cyclone/Dockerfile-arm64" \
    -t "$IMAGE" \
    "$PROJECT_DIR"

echo "==> 构建完成: $IMAGE"
echo "运行示例: docker run --rm -p 1933:1933 -v ~/.openviking:/app/.openviking $IMAGE"

docker push "$IMAGE"
