"""add_resource / add_user_resource 场景测试 — 只测 URI 生成，不等异步"""

import os
from openviking_sdk import SyncHTTPClient

SERVER = "http://192.168.198.128:11933"
API_KEY = "ak-d962b560f65644e3a91b3bb6bf9e5467"
ACCOUNT = "cycloneclaw"
# USER = "user-01"
USER = "3"

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


def t_task_polling():
    """不 wait → 拿到 task_id → 轮询任务状态
    
    task {'task_id': 'fba3fb12-10cb-455b-86a2-e6e88592c68c', 
    'task_type': 'add_resource', 'status': 'completed',
      'created_at': 1783575893.259566, 'updated_at': 1783576019.5406158,
        'resource_id': 'viking://user/user-01/resources/knowledge_spaces/test-space/simple',
        'stage': 'completed', 
        'result': {'root_uri': 'viking://user/user-01/resources/knowledge_spaces/test-space/simple', 
        'queue_status': {'Semantic': {'processed': 1, 'requeue_count': 0, 'error_count': 0, 'errors': []},
          'Embedding': {'processed': 3, 'requeue_count':0, 'error_count': 0, 'errors': []}}}, 'error': None, 
          'created_at_iso': '2026-07-09T05:44:53.259566+00:00', 'updated_at_iso': '2026-07-09T05:46:59.540616+00:00'}  
          
          status: completed 
    
    """
    client = _client()
    import time as _time

    try:
        # 上传不阻塞
        r = client.add_user_resource(path=TEST_FILE, to=f"{SPACE_URI}/", overwrite=True)
        task_id = r.get("task_id")
        print(f"  task_id: {task_id}")
        assert task_id, "Expected task_id in response"

        # 轮询直到完成
        for _ in range(300):
            task = client.get_task(task_id)
            if task:
                status = task.get("status")
                print(f"task {task}  status: {status} ")
                if status == "completed":
                    print(f"  root_uri: {task.get('resource_id')}")
                    break
                elif status == "failed":
                    raise AssertionError(f"Task failed: {task.get('error')}")
            else:
                print(f"  waiting... (task not found yet)")
            _time.sleep(2)
        else:
            raise AssertionError("Task did not complete within timeout")
    finally:
        _cleanup(client, SPACE_URI)


def t_read_resource():
    """读取已有文件内容"""
    client = _client()
    try:
        uri = "viking://user/3/resources/knowledge_spaces/test1/Agent研发工程师面试题/Agent研发工程师面试题.md"
        content = client.read(uri)
        print(f"  uri: {uri}")
        print(f"  length: {len(content)} chars")
        print(f"  preview: {content[:200]}...")
        assert len(content) > 0, "Expected non-empty content"
    finally:
        client.close()


def t_read_png_resource():
    """读取已有文件内容"""
    client = _client()
    try:
        uri = "viking://resources/新员工公司指南/page12_img3.png"
        content = client.read(uri,fallback_to_abstract=True)
        print(f"  uri: {uri}")
        print(f"  length: {len(content)} chars")
        print(f"  content: {content}")
        assert len(content) > 0, "Expected non-empty content"
    finally:
        client.close()


def t_search_knowledge_space():
    """搜索知识空间 viking://user/3/resources/knowledge_spaces/test1"""
    client = _client()
    try:
        target = "viking://user/3/resources/knowledge_spaces/test1"

        # 1. 先列出知识空间中的文件
        entries = client.ls(target)
        print(f"  ls {target}: {len(entries)} entries")
        for e in entries[:5]:
            print(f"    {e}")

        # 2. 检查 ACL
        acl = client.get_acl(target)
        print(f"  acl: shared={acl.get('shared')}, search_disabled={acl.get('search_disabled')}")

        # ---- 多维度对比搜索 ----

        tests = [
            # ("scoped", "Agent", target),
            # ("scoped", "报销", target),
            ("viking://user/3/resources/knowledge_spaces/test1", "报销制度 费用报销 报销政策", "viking://user/3/resources/knowledge_spaces/test1"),
            # ("viking://resources", "报销", ["viking://resources"]),
             ("viking://resources", "报销制度 费用报销 报销政策", "viking://resources")
            # ("viking://resources", "Agent", "viking://resources"),
            # ("parent", "Agent", "viking://user/3/resources"),
            # ("all", "Agent", ""),
        ]
        import json
        for label, query, tgt in tests:
            kwargs = {"query": query, "limit": 20, "context_type":["resource"], "include_content":False}
            if tgt:
                kwargs["target_uri"] = tgt
            r = client.search(**kwargs)

            print(f" ---> find('{query}') {label}: total={r.get('total')} response: {json.dumps(r,ensure_ascii=False, indent=2)}")
            # for resource in r.get("resources"):
            #     print(f"------------------->  {resource['uri']} score: {resource['score']} content: {resource['content'][:100]}")

        assert r.get("total") is not None, "Expected 'total' in find response"
    finally:
        client.close()


def t_reindex():
    """重建知识空间索引，清理孤儿向量（需 admin API key）"""
    client = SyncHTTPClient(url=SERVER, api_key=API_KEY)
    client.initialize()
    try:
        uri = "viking://user/3/resources/knowledge_spaces/test1/Agent研发工程师面试题"
        result = client.reindex(uri, mode="vectors_only")
        print(f"  reindex {uri}: {result}")
    finally:
        client.close()


def t_delete_file():
    client = _client()
    try:
        # uri = "viking://user/3/resources/knowledge_spaces/test1/Agent研发工程师面试题/"
        # uri = "viking://user/3/resources/knowledge_spaces/test1/Agent研发工程师面试题/Agent研发工程师面试题.md"
        uri = "viking://user/3/resources/knowledge_spaces/test1/AI_Overview"

        for i in range(30):
            # uri = f"viking://resources/simple"
            client.rm(uri,recursive=True)
            print(f"  rm {uri}")
    finally:
        client.close()

if __name__ == "__main__":
    # t_add_exact()
    # t_add_overwrite()
    # t_add_to_dir()
    # t_add_no_to()
    # t_user_exact()
    # t_user_overwrite()
    # t_user_to_dir()
    # t_user_no_to()
    # t_knowledge_space_create_and_public()
    # t_task_polling()
    # t_read_resource()
    # t_read_png_resource()
    t_search_knowledge_space()
    # t_reindex()
    # t_delete_file()
