#!/bin/sh
# 构建 cyclone-openviking-sdk wheel -> cyclone/sdk-dist/
# 版本号在 sdk/python/pyproject.toml 中静态定义

set -eu

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SDK_DIR="$PROJECT_DIR/sdk/python"
DIST_DIR="$PROJECT_DIR/cyclone/sdk-dist"

cd "$SDK_DIR"
uv build --wheel --out-dir "$DIST_DIR"

echo "==> 产物:"
ls -lh "$DIST_DIR"/cyclone_openviking_sdk-*.whl
