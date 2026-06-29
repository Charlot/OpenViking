#!/bin/bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="$PROJECT_DIR/data"
VENV_DIR="$PROJECT_DIR/.venv"
PID_FILE="$DATA_DIR/.openviking.pid"
ROCKSDB_LOCK="$DATA_DIR/vectordb/context/store/LOCK"
QUEUE_DB="$DATA_DIR/_system/queue/queue.db"

# MinIO 配置
MINIO_ENDPOINT="http://10.86.20.4:19000"
MINIO_AK="K9kFEGpMSWUpXIdXkx4F"
MINIO_SK="Xz3UeWxcfnsVYtfvgaaiM7N9dqgaa8FieLe3X7hU"
MINIO_BUCKET="cycloneclaw-viking"

FORCE="yes"
RESET="no"

usage() {
    echo "Usage: $0 [--no-force] [--reset]"
    echo "  --no-force  不要强制重启（检测到运行中则跳过）"
    echo "  --reset     重置所有数据（清本地 + 清 MinIO + 清队列）"
    echo "  默认行为: 强制重启"
    exit 1
}

while [ $# -gt 0 ]; do
    case "$1" in
        --no-force) FORCE="no"; shift ;;
        --reset)    RESET="yes"; shift ;;
        --help|-h)  usage ;;
        *)          usage ;;
    esac
done

# 激活虚拟环境
if [ -f "$VENV_DIR/bin/activate" ]; then
    source "$VENV_DIR/bin/activate"
else
    echo "[ERROR] 虚拟环境不存在: $VENV_DIR"
    echo "  请先运行: cd $PROJECT_DIR && uv sync"
    exit 1
fi

# ── 停止所有旧进程 ──
existing_pids=$(pgrep -f "openviking-server" 2>/dev/null || true)
if [ -n "$existing_pids" ]; then
    if [ "$FORCE" = "no" ] && [ "$RESET" = "no" ]; then
        echo "[INFO] openviking-server 已在运行 (PID $(echo $existing_pids | tr '\n' ' '))，跳过"
        exit 0
    fi
    echo "[INFO] 正在停止所有旧进程..."
    echo "$existing_pids" | xargs kill 2>/dev/null || true
    sleep 2
    remaining=$(pgrep -f "openviking-server" 2>/dev/null || true)
    if [ -n "$remaining" ]; then
        echo "[WARN] 进程未响应，强制终止..."
        echo "$remaining" | xargs kill -9 2>/dev/null || true
        sleep 1
    fi
    echo "[INFO] 已停止: $(echo $existing_pids | tr '\n' ' ')"
fi

# ── 清理锁文件 ──
rm -f "$PID_FILE"
rm -f "$ROCKSDB_LOCK"

# ── Reset: 清空所有数据 ──
if [ "$RESET" = "yes" ]; then
    echo "=== 重置所有数据 ==="

    echo "[reset] 清空本地 data..."
    rm -rf "$DATA_DIR"
    mkdir -p "$DATA_DIR"

    echo "[reset] 清空 MinIO ($MINIO_BUCKET)..."
    uv run python3 -c "
import boto3
from botocore.client import Config
s3 = boto3.client('s3',
    endpoint_url='$MINIO_ENDPOINT',
    aws_access_key_id='$MINIO_AK',
    aws_secret_access_key='$MINIO_SK',
    config=Config(signature_version='s3v4'), region_name='us-east-1')
total = 0
for page in s3.get_paginator('list_objects_v2').paginate(Bucket='$MINIO_BUCKET'):
    objs = page.get('Contents', [])
    if objs:
        s3.delete_objects(Bucket='$MINIO_BUCKET', Delete={'Objects': [{'Key': o['Key']} for o in objs]})
        total += len(objs)
print(f'[reset] MinIO 已清空 ({total} 个对象)' if total else '[reset] MinIO 已为空')
" 2>&1

    echo "[reset] 完成"
    echo "================"
fi

# 确保数据目录存在
mkdir -p "$DATA_DIR"

# ── 启动 ──
echo "[INFO] 启动 openviking-server..."
cd "$PROJECT_DIR"
exec openviking-server --host 0.0.0.0
