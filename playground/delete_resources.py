#!/usr/bin/env python3
"""删除 OpenViking 中的资源。"""

import argparse
import sys
from pathlib import Path

# 如果从源码运行，确保项目路径在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openviking import SyncHTTPClient

SERVER = "http://192.168.198.128:11933"
API_KEY = "ZGVmYXVsdA.dGVzdA.ZmMyMjU5NmFiZDVmNTQ4MjViZjViYjg1Y2U1YTkwYzIzNmRiMjljYjExZWU1NDcwNzE5YTE4ODlmNTczOWU1ZQ"


def get_client() -> SyncHTTPClient:
    client = SyncHTTPClient(url=SERVER, api_key=API_KEY)
    client.initialize()
    return client


def list_resources(client: SyncHTTPClient, path: str = "viking://resources/"):
    """列出指定路径下的所有资源。"""
    print(f"[INFO] 列出资源: {path}")
    items = client.ls(path)
    if not items:
        print("  (空)")
        return []
    results = []
    for item in items:
        print(f"  {item.get('name', item.get('uri', '?'))}")
        results.append(item)
    return results


def delete_resource(client: SyncHTTPClient, uri: str, recursive: bool = False):
    """删除单个资源或目录。"""
    print(f"[INFO] 删除: {uri}")
    try:
        client.rm(uri, recursive=recursive)
        print(f"  ✅ 已删除")
    except Exception as e:
        print(f"  ❌ 失败: {e}")


def delete_all(client: SyncHTTPClient, paths: list[str], recursive: bool = True):
    """批量删除资源。"""
    print(f"[INFO] 将删除以下路径（recursive={recursive}）:")
    for p in paths:
        print(f"  - {p}")
    print()

    for p in paths:
        delete_resource(client, p, recursive=recursive)


def main():
    parser = argparse.ArgumentParser(description="OpenViking 资源删除工具")
    parser.add_argument("--server", default=SERVER, help=f"服务器地址 (默认: {SERVER})")
    parser.add_argument("--api-key", default=API_KEY, help="API Key")
    sub = parser.add_subparsers(dest="command", help="命令")

    # list
    p_list = sub.add_parser("list", help="列出资源")
    p_list.add_argument("path", nargs="?", default="viking://resources/", help="路径")

    # delete
    p_delete = sub.add_parser("delete", help="删除单个资源")
    p_delete.add_argument("uri", help="资源 URI")
    p_delete.add_argument("--no-recursive", action="store_true", help="不递归删除目录")

    # delete-all
    p_delete_all = sub.add_parser("delete-all", help="批量删除资源")
    p_delete_all.add_argument("paths", nargs="+", help="要删除的资源 URI 列表")
    p_delete_all.add_argument("--no-recursive", action="store_true", help="不递归删除")

    # delete-resources (清空 resources 目录)
    p_clear = sub.add_parser("clear-resources", help="清空 viking://resources/ 下所有资源")
    p_clear.add_argument("--yes", action="store_true", help="跳过确认")

    args = parser.parse_args()

    client = get_client()
    try:
        if args.command == "list":
            list_resources(client, args.path)
        elif args.command == "delete":
            delete_resource(client, args.uri, recursive=not args.no_recursive)
        elif args.command == "delete-all":
            delete_all(client, args.paths, recursive=not args.no_recursive)
        elif args.command == "clear-resources":
            items = list_resources(client, "viking://resources/")
            if not items:
                print("[INFO] viking://resources/ 下无资源")
                return
            if not args.yes:
                resp = input(f"\n确认删除以上 {len(items)} 项？[y/N] ")
                if resp.lower() != "y":
                    print("已取消")
                    return
            uris = [item.get("uri", item.get("name", "")) for item in items]
            delete_all(client, uris, recursive=True)
        else:
            parser.print_help()
    finally:
        client.close()


if __name__ == "__main__":
    main()
