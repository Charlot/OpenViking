"""add_resource / add_user_resource 场景测试 — 只测 URI 生成，不等异步"""

import os
from openviking_sdk import SyncHTTPClient

SERVER = "http://192.168.198.128:11933"
API_KEY = "ak-d962b560f65644e3a91b3bb6bf9e5467"
ACCOUNT = "cycloneclaw"
USER = "user-01"

# Use absolute path
TEST_FILE = os.path.join(os.path.dirname(__file__), "data", "simple.md")


def _client():
    c = SyncHTTPClient(url=SERVER, api_key=API_KEY, account=ACCOUNT, user=USER)
    c.initialize()
    return c


def _cleanup(client, *uris):
    for uri in uris:
        try:
            client.rm(uri, recursive=True)
        except Exception:
            pass
    client.close()


def run(name, fn):
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")
    try:
        fn()
        print(f"  ✅ PASS")
        return True
    except Exception as e:
        print(f"  ❌ FAIL: {e}")
        return False


# ── add_resource ──

def t_add_no_to():
    """不加 to：容器目录 viking://resources/simple/simple.md"""
    client = _client()
    try:
        r = client.add_resource(path=TEST_FILE, to="viking://resources/", overwrite=True)
        print(f"  root_uri: {r['root_uri']}")
        assert "viking://resources/" in r["root_uri"]
    finally:
        _cleanup(client)


def t_add_to_dir():
    """to=目录：to 下创建容器"""
    client = _client()
    try:
        r = client.add_resource(path=TEST_FILE, to="viking://resources/", overwrite=True)
        print(f"  root_uri: {r['root_uri']}")
    finally:
        _cleanup(client)


def t_add_exact():
    """to=确切文件"""
    client = _client()
    to = "viking://resources/test-exact-file.md"
    try:
        r = client.add_resource(path=TEST_FILE, to=to, overwrite=True)
        print(f"  root_uri: {r['root_uri']}")
        assert r["root_uri"] == to, f"Expected {to}"
    finally:
        _cleanup(client, to)


def t_add_overwrite():
    """overwrite 不生成后缀"""
    client = _client()
    to = "viking://resources/test-ow.md"
    try:
        u1 = client.add_resource(path=TEST_FILE, to=to, overwrite=True)["root_uri"]
        u2 = client.add_resource(path=TEST_FILE, to=to, overwrite=True)["root_uri"]
        print(f"  u1={u1}  u2={u2}")
        assert u1 == u2 and "_1" not in u2
    finally:
        _cleanup(client, to)


# ── add_user_resource ──

def t_user_no_to():
    """不加 to"""
    client = _client()
    try:
        r = client.add_user_resource(path=TEST_FILE, to="viking://user/resources/", overwrite=True)
        print(f"  root_uri: {r['root_uri']}")
        assert f"viking://user/{USER}/resources/" in r["root_uri"]
    finally:
        _cleanup(client)


def t_user_to_dir():
    """to=目录"""
    client = _client()
    try:
        r = client.add_user_resource(path=TEST_FILE, to="viking://user/resources/", overwrite=True)
        print(f"  root_uri: {r['root_uri']}")
    finally:
        _cleanup(client)


def t_user_exact():
    """to=确切文件"""
    client = _client()
    to = f"viking://user/{USER}/resources/test-exact.md"
    try:
        r = client.add_user_resource(path=TEST_FILE, to=to, overwrite=True)
        print(f"  root_uri: {r['root_uri']}")
        assert r["root_uri"] == to
    finally:
        _cleanup(client, to)


def t_user_overwrite():
    """overwrite 不生成后缀"""
    client = _client()
    to = f"viking://user/{USER}/resources/test-ow.md"
    try:
        u1 = client.add_user_resource(path=TEST_FILE, to=to, overwrite=True)["root_uri"]
        u2 = client.add_user_resource(path=TEST_FILE, to=to, overwrite=True)["root_uri"]
        print(f"  u1={u1}  u2={u2}")
        assert u1 == u2
    finally:
        _cleanup(client, to)


# ── knowledge space ──

SPACE_NAME = "test-space"
SPACE_URI = f"viking://user/{USER}/resources/knowledge_spaces/{SPACE_NAME}"


def t_knowledge_space_create_and_public():
    """创建知识空间 → 设为公开 → 上传文件 → 验证"""
    client = _client()
    try:
        # 1. 创建知识空间目录
        client.mkdir(SPACE_URI)
        print(f"  mkdir: {SPACE_URI}")

        # 2. 设为公开
        client.set_acl(SPACE_URI, shared=True)
        acl = client.get_acl(SPACE_URI)
        print(f"  acl: shared={acl.get('shared')}")
        assert acl.get("shared"), "Expected shared=True"

        # 3. 上传文件到知识空间
        r = client.add_user_resource(
            path=TEST_FILE,
            to=f"{SPACE_URI}/",
            overwrite=True,
            wait=True,
        )
        root_uri = r["root_uri"]
        print(f"  uploaded: {root_uri}")
        assert SPACE_URI in root_uri, f"Expected under {SPACE_URI}"

        # 4. 验证子文件继承 ACL
        # 列出目录找上传的文件
        entries = client.ls(SPACE_URI)
        print(f"  entries: {len(entries)} items")
        assert len(entries) > 0, "Expected files in space"

        # 5. 排除搜索测试
        client.set_acl(SPACE_URI, search_disabled=True)
        acl2 = client.get_acl(SPACE_URI)
        print(f"  acl: search_disabled={acl2.get('search_disabled')}")
        assert acl2.get("search_disabled"), "Expected search_disabled=True"

        # 6. 恢复
        client.set_acl(SPACE_URI, search_disabled=False, shared=True)
    finally:
        _cleanup(client, SPACE_URI)


if __name__ == "__main__":
    tests = [
        # ("add_resource: exact file to", t_add_exact),
        # ("add_resource: overwrite no suffix", t_add_overwrite),
        # ("add_resource: to=dir", t_add_to_dir),
        # ("add_resource: no to", t_add_no_to),
        # ("add_user_resource: exact file to", t_user_exact),
        # ("add_user_resource: overwrite", t_user_overwrite),
        # ("add_user_resource: to=dir", t_user_to_dir),
        # ("add_user_resource: no to", t_user_no_to),
        ("knowledge space: create + public + upload", t_knowledge_space_create_and_public),
    ]

    ok = 0
    for name, fn in tests:
        if run(name, fn):
            ok += 1

    print(f"\n{'='*60}")
    print(f"  {ok}/{len(tests)} passed")
    print(f"{'='*60}")
