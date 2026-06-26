#!/usr/bin/env python3
"""搜索 OpenViking 中的文档。"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openviking import SyncHTTPClient

SERVER = "http://192.168.198.128:11933"
API_KEY = "ZGVmYXVsdA.dGVzdA.ZmMyMjU5NmFiZDVmNTQ4MjViZjViYjg1Y2U1YTkwYzIzNmRiMjljYjExZWU1NDcwNzE5YTE4ODlmNTczOWU1ZQ"


def search(client: SyncHTTPClient, query: str, limit: int = 5):
    """搜索资源。"""
    import json
    try:
        results = client.search(query, limit=limit)
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return results
    except Exception as e:
        print(f"搜索失败: {e}")
        return {}


def main():
    parser = argparse.ArgumentParser(description="OpenViking 文档搜索")
    parser.add_argument("query", nargs="?", help="搜索关键词")
    parser.add_argument("-n", "--limit", type=int, default=5, help="返回数量 (默认5)")
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
        search(client, args.query, args.limit)
    finally:
        client.close()


if __name__ == "__main__":
    main()
