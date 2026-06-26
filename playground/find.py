#!/usr/bin/env python3
"""Find 文档（无会话上下文的语义搜索）。"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openviking import SyncHTTPClient

SERVER = "http://192.168.198.128:11933"
API_KEY = "ZGVmYXVsdA.dGVzdA.ZmMyMjU5NmFiZDVmNTQ4MjViZjViYjg1Y2U1YTkwYzIzNmRiMjljYjExZWU1NDcwNzE5YTE4ODlmNTczOWU1ZQ"


def find(
    client: SyncHTTPClient,
    query: str,
    limit: int = 5,
    score_threshold: float = None,
    target_uri: str = "",
    context_type: str = None,
):
    try:
        results = client.find(
            query,
            limit=limit,
            score_threshold=score_threshold,
            target_uri=target_uri,
            context_type=context_type,
        )
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return results
    except Exception as e:
        print(f"搜索失败: {e}")
        return {}


def main():
    parser = argparse.ArgumentParser(description="OpenViking Find (无会话语义搜索)")
    parser.add_argument("query", nargs="?", help="搜索关键词")
    parser.add_argument("-n", "--limit", type=int, default=5, help="返回数量 (默认5)")
    parser.add_argument("-t", "--score-threshold", type=float, default=None, help="分数阈值")
    parser.add_argument("--target-uri", default="", help="目标目录 URI")
    parser.add_argument("--context-type", default=None, help="过滤类型: resource/memory/skill")
    parser.add_argument("--server", default=SERVER, help="服务器地址")
    parser.add_argument("--api-key", default=API_KEY, help="API Key")
    args = parser.parse_args()

    if not args.query:
        args.query = input("搜索关键词: ").strip()
        if not args.query:
            print("未输入关键词")
            return

    client = SyncHTTPClient(url=args.server, api_key=args.api_key)
    client.initialize()
    try:
        find(
            client,
            args.query,
            limit=args.limit,
            score_threshold=args.score_threshold,
            target_uri=args.target_uri,
            context_type=args.context_type,
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
