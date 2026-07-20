#!/bin/sh
# 构建 OpenViking Docker 镜像 -> 10.86.20.10/library/cyclone-openviking:test
# 使用 cyclone/Dockerfile（基础镜像已固定为 10.86.20.10/library/ 的副本）

set -eu

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="10.86.20.10/library/cyclone-openviking:test"

docker buildx build --load \
    -f "$PROJECT_DIR/cyclone/Dockerfile" \
    -t "$IMAGE" \
    "$PROJECT_DIR"

echo "==> 构建完成: $IMAGE"
echo "运行示例: docker run --rm -p 1933:1933 -v ~/.openviking:/app/.openviking $IMAGE"

docker push "$IMAGE"