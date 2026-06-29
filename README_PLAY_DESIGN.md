# OpenViking 外部 Agent 系统集成协议

## 1. 集成方式

### 方式 A：MCP（推荐）

OpenViking 内建 MCP Server，端点 `/mcp`，Agent 框架直接连接：

```json
{
  "mcpServers": {
    "openviking": {
      "url": "http://192.168.198.128:11933/mcp",
      "headers": { "X-API-Key": "your-api-key" }
    }
  }
}
```

已注册的 15 个 MCP Tool：

| Tool | 用途 |
|------|------|
| `find` | 无会话语义搜索 |
| `search` | 带会话上下文的语义搜索 |
| `read` | 读取文件内容（Markdown） |
| `list` | 列出目录 |
| `remember` | 写入记忆 |
| `forget` | 删除记忆 |
| `add_resource` | 添加文档/资源 |
| `grep` | 文件内容正则搜索 |
| `glob` | 文件名模式匹配 |
| `code_outline` | 代码结构提取 |
| `code_search` | 代码语义搜索 |
| `code_expand` | 代码展开 |
| `list_watches` | 查看监控任务 |
| `cancel_watch` | 取消监控 |
| `health` | 健康检查 |

### 方式 B：CLI

```bash
ov find "关键词"
ov read "viking://resources/agents/文档.md"
ov ls "viking://resources/agents/"
```

Agent 通过 shell 调用，适合非 MCP 框架。

### 方式 C：Python SDK

```python
from openviking import SyncHTTPClient

client = SyncHTTPClient(
    url="http://192.168.198.128:11933",
    api_key="your-api-key",
)
client.initialize()

# 搜索
result = client.find("关键词", limit=5, score_threshold=0.03)
# 读取
content = client.read("viking://resources/agents/文档.md")
# 写入
client.write("viking://resources/agents/新文档.md", "# 标题\n\n内容...")
```

### 方式 D：REST API

```bash
# 搜索
curl -X POST "http://host:11933/api/v1/search/find" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "关键词", "limit": 5}'

# 读取
curl "http://host:11933/api/v1/content/read?uri=viking://resources/..." \
  -H "X-API-Key: $API_KEY"

# 写入
curl -X POST "http://host:11933/api/v1/content/write" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"uri": "viking://resources/...", "content": "内容"}'
```

---

## 2. 共享搜索需求与设计

### 2.1 需求场景

```
场景 1: 用户 alice 创建了"财务知识"文件夹，放了自己的文件。
        希望 Agent 能搜到这些文件，其他用户也能读到。

场景 2: 用户 bob 用同一个 Agent 时，自动能搜到 alice 已共享的内容。

场景 3: alice 更新了文件 → Agent 立即搜到最新版，不需要额外操作。

场景 4（未来）: alice 可能想让 bob 也帮忙管理这个文件夹。
```

### 2.2 核心约束

1. **文件不移动**：用户在自己的 `user/{id}/files/` 下工作，不搬到 `resources/`
2. **不复制**：共享不是拷贝，更新即时生效
3. **改动最小**：不影响 OpenViking 核心代码，方便合并上游
4. **不需要新角色**：不引入 AGENT 等概念，权限简单
5. **安全在 Viking 层**：权限判定不在应用层，任何入口（REST/MCP/SDK/CLI）经过统一的 enforcement point

### 2.3 系统边界

```
┌──────────────────────────────────────────────────────────────┐
│  应用层 (外部系统 / Agent / Web UI / CLI)                     │
│                                                              │
│  共享 toggle  →  调用 ACL API                                 │
│  搜索请求    →  调 find/target_uri（不管权限，Viking 处理）      │
│                                                              │
│  ❌ 不做权限判定                                              │
│  ❌ 不自己过筛结果                                            │
└──────────────────────────┬───────────────────────────────────┘
                           │ REST / MCP / SDK
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  Viking 层（权限在此终结）                                     │
│                                                              │
│  Authorization Check: is_accessible(uri, ctx)    ← 所有读操作的唯一入口      │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  原有逻辑          │  ACL hook (我们维护)              │    │
│  │  scope 前缀判断     │                                  │    │
│  │  owner_user_id     │  _resolve_acl_access()           │    │
│  │                     │  ├─ owner?       → ✅           │    │
│  │                     │  ├─ shared?      → ✅           │    │
│  │                     │  └─ disabled     → ❌ (禁读)     │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                              │
│  Search Scope Filter: _tenant_filter(ctx)           ← 搜索 filter 生成     │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  filter = And([                                        │    │
│  │      Eq("account_id", tenant),                         │    │
│  │      Eq("is_search_disabled", False),     ← 禁用的不加入搜索    │    │
│  │      Or([                                              │    │
│  │          Eq("owner_user_id", me),  ← 我自己的           │    │
│  │          Eq("is_shared", True),    ← 别人共享的         │    │
│  │      ]),                                               │    │
│  │      target_uri_filter,                                │    │
│  │  ])                                                     │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                              │
│  向量索引字段:                                                 │
│  ┌────────────────────────────────────────────┐              │
│  │  owner_user_id   is_shared   is_search_disabled   │              │
│  │       ↓               ↓           ↓       │              │
│  │  (已有)           (新增)       (新增)       │              │
│  └────────────────────────────────────────────┘              │
│                                                              │
│  ACL 存储:  openviking/acl/  (新增目录，我们维护)              │
│  ┌────────────────────────────────────────────┐              │
│  │  __init__.py     权限判定 (_resolve_acl)    │              │
│  │  store.py        ACL CRUD (AGFS 存储)       │              │
│  │  router.py       REST API                   │              │
│  └────────────────────────────────────────────┘              │
│                                                              │
│  不受影响的部分:                                               │
│  搜索算法 · Embedding · Rerank · 向量数据库 · 解析器           │
└──────────────────────────────────────────────────────────────┘
                           │
                           ▼
                  向量数据库 (C++ BruteForce / Qdrant)
                  文件存储  (AGFS → MinIO S3)
```

**为什么权限必须在 Viking 层而不是应用层：**

| 问题 | 应用层方案 | Viking 层方案 |
|------|-----------|-------------|
| MCP 绕过 | 应用层中间件对 MCP 透明 | `is_accessible()` 是所有入口的唯一 enforcement point |
| 搜索结果被 search_disabled 文件占满 limit | 搜完再筛，返回 0 条 | 向量库 filter 排除，不占配额 |
| 新接入通道 | 每个通道加一遍 ACL 逻辑 | 一次实现，所有通道生效 |
| CLI 直接调 API | 需要 SDK 层拦截 | 服务端统一 enforcement point |

**两条数据流:**

```
流 1: 读文件
──────────────────────────────────────────────
read(uri) → _ensure_access(uri, ctx)
              → _is_accessible(uri, ctx)
                  → namespace.is_accessible()  // 原有
                  → _resolve_acl_access()       // ACL hook
                      disabled? → 403
                      owner?    → 放行
                      shared?   → 放行
                      else      → 403

流 2: 搜索
──────────────────────────────────────────────
find(query, target_uri) → _ensure_access(target_uri, ctx)  // 1 次
                         → _tenant_filter(ctx)
                             is_search_disabled=false  // 过滤
                             owner=me OR is_shared=true  // 范围
                         → vector_proxy.query(filter)
                         → 返回结果（不再逐条检查）
```

### 2.4 ACL 数据模型

**一个文件/文件夹三种状态：**

| 状态 | 搜索可见 | 读内容 |
|------|---------|--------|
| private | 仅 owner | 仅 owner |
| shared | 所有人 | 所有人 |
| disabled | ❌ 不加入搜索 | 仅 owner |

**disabled：不被索引，不出现在搜索结果中。owner 仍然可以通过已知 URI 直接访问。** 像"下架但不删除"。

```json
{
  "owner": "alice",
  "shared": false,
  "search_disabled": false
}
```

**三个状态通过两个 bool 字段表达：**

| shared | search_disabled | 状态 |
|--------|----------|------|
| false | false | private |
| true | false | shared |
| * | true | search_disabled（shared 值无效） |

**权限判定：**

```python
def _resolve_acl_access(uri, ctx):
    acl = get_effective_acl(uri)
    if not acl:
        return False

    if ctx.user_id == acl.owner:     # owner 永远能读写
        return True
    return acl.shared and not acl.search_disabled
```

**两个Enforcement Point的差异：**

| Enforcement Point | private | shared | search_disabled |
|------|---------|--------|---------|
| Search Scope Filter | `owner=me` | `owner=me OR is_shared=true` | `owner=me`（仅 owner 搜得到） |
| Authorization Check (is_accessible) | owner 放行 | 所有人放行 | 仅 owner 放行 |

**渐进扩展：**

将来只需在 JSON 对象里加字段，不改存储格式：
- `"editors": ["bob"]` — 其他编辑者
- `"agent": "default"` — 绑定到特定 Agent

### 2.5 文件夹继承

```
alice/files/
├── 技术笔记/           ← shared=true
│   ├── 部署.md         ← 继承: shared=true
│   ├── 架构.md         ← 继承: shared=true
│   └── 机密.md         ← 覆盖: shared=false
├── 财务知识/           ← shared=false (默认)
│   └── 开票.md         ← 继承: shared=false
└── 个人日记.md         ← 单独设 shared=true
```

权限判定：`get_effective_acl(uri)` — 沿路径往上找第一个有 ACL 的节点。

### 2.6 架构：1 行 hook + 新文件

```
┌──────── OpenViking 核心（不改动）──────┐
│  namespace.py                           │
│  if scope == "user":                    │
│      if _resolve_acl(...)  ←── 1行hook  │
│          return True                    │
└────────────────────────────────────────┘
                    │ import
┌────── 我们维护的新文件（零侵入）──────┐
│  openviking/acl/                       │
│  ├── __init__.py       权限判定         │
│  ├── store.py          ACL 存储 CRUD   │
│  └── router.py         REST API        │
└────────────────────────────────────────┘
```

**合并上游：** 只有 `namespace.py` 的 1 行 import + 1 行 hook 可能冲突，30 秒解决。

### 2.7 向量索引变动

新增两个向量索引字段：

| 字段 | 说明 |
|------|------|
| `is_shared` | 其他人是否可读 |
| `is_search_disabled` | 是否从搜索中排除（不影响直接访问） |

搜索 filter 在 `_tenant_filter` 中统一生成（详见 2.3 系统边界的Search Scope Filter），不需要调用方自己拼。

### 2.8 API

```
PUT  /api/v1/acl/set?uri=...&shared=true         # 共享
PUT  /api/v1/acl/set?uri=...&shared=false        # 取消共享
PUT  /api/v1/acl/set?uri=...&search_disabled=true       # 从搜索中移除
PUT  /api/v1/acl/set?uri=...&search_disabled=false      # 恢复搜索可见
GET  /api/v1/acl/get?uri=...                     # 查询 ACL
GET  /api/v1/acl/list?owner=alice                # 列出某用户的所有共享
```

| 通道 | 暴露方式 |
|------|---------|
| MCP | 新增 tool `set_acl` / `get_acl` |
| REST | `acl/router.py` 挂到 FastAPI |
| SDK | `client.set_acl(uri, shared=True)` |
| CLI | `ov acl set "viking://..." --shared` |

### 2.9 目录结构

```
viking://
│
├── user/{user_id}/files/        ← 文件物理位置（不移动）
│   ├── alice/files/
│   │   ├── 技术笔记/             ← shared=true → Agent 和所有人可读
│   │   ├── 财务知识/             ← private → 只有 alice 可见
│   │   └── 个人日记.md           ← private
│   └── bob/files/
│       └── 部署文档.md           ← 可以单独 shared=true
│
├── resources/                   ← 传统共享区（保留不变）
│
└── skills/
```

### 2.10 权限模型（+ACL 后）

| 操作 | User (owner) | User (其他人) | Admin | Agent (MCP) |
|------|-------------|-------------|-------|------------|
| 读自己的 `user/{id}/` | ✅ | ❌ | ✅ | ✅ (通过 MCP user key) |
| 读别人 shared 的 `user/{id}/` | ✅ (如果 shared) | ✅ | ✅ | ✅ |
| 读别人 private 的 `user/{id}/` | ✅ (如果 shared) | ❌ | ✅ | ❌ |
| 写自己的 `user/{id}/` | ✅ | ❌ | ✅ | ❌ |
| 写别人的 `user/{id}/` | ❌ | ❌ | ✅ | ❌ |
| 设置 ACL | 仅 owner | ❌ | ✅ | ❌ |

### 2.11 和原方案的对比

| | 之前：AGENT 角色 | 现在：1-bit ACL |
|------|------|------|
| 新概念 | AGENT 角色 | shared flag |
| 核心代码改动 | 3 处 | 1 行 |
| 文件位置 | 坐实于 `user/{id}` | 不变 |
| 其他用户管理 | 需要新角色 | ACL 扩展即可 |
| 将来扩展 | 角色体系膨胀 | JSON 加字段 |
| 合并上游成本 | 低 | 极低 |

---

## 3. 搜索与检索

### 基本搜索（无会话）

```
POST /api/v1/search/find
{
  "query": "如何开发票",
  "limit": 5,
  "score_threshold": 0.03
}
```

返回：

```json
{
  "memories": [],
  "resources": [
    {
      "uri": "viking://resources/agents/员工手册/IT指南.md",
      "level": 2,
      "score": 0.054,
      "abstract": "涵盖IT基础设施与财务事务...",
      "context_type": "resource"
    }
  ],
  "skills": [],
  "total": 1
}
```

### 限定目录搜索

```json
{
  "query": "考勤",
  "target_uri": "viking://resources/agents/员工手册/",
  "limit": 5
}
```

### 分数阈值

| threshold | 效果 |
|-----------|------|
| 0.1 | 高精度，可能漏掉弱匹配的正确答案 |
| 0.05 | 平衡点 |
| **0.03** | 推荐值，高召回，适合中文办公文档 |

> 配置位置：`~/.openviking/ov.conf` → `rerank.threshold`

### 检索粒度

- 最小检索单元 = **一个文件节点**
- 不做文本切块（Chunk），返回的是完整文件的 URI
- 文件内部无段落级定位，需结合 `grep` 精确查找

---

## 4. 内容获取

### 三级读取

```
搜索命中 → L0 abstract（预览）
         → L1 overview（确认真是这个文件）
         → L2 read（获取全文）
```

| 层级 | SDK 方法 | API 端点 | 内容 |
|------|---------|---------|------|
| L0 | `client.abstract(uri)` | `GET /api/v1/content/abstract` | AI 摘要 |
| L1 | `client.overview(uri)` | `GET /api/v1/content/overview` | AI 概览 |
| L2 | `client.read(uri)` | `GET /api/v1/content/read` | 全文 Markdown |

### 典型 Agent 流程

```
1. find("如何开发票")              → 拿到 URI 列表 + abstract
2. 判断 abstract 是否相关          → 确认需要 IT指南.md
3. read("viking://resources/...")  → 拿到完整 Markdown
4. 提取答案返回给用户
```

---

## 5. 内容管理（Agent 创建/更新文档）

### 写入新文档

```python
client.write(
    "viking://resources/agents/技术文档/部署指南.md",
    "# 部署指南\n\n## 环境要求\n\n- Python 3.10+\n- Docker\n\n..."
)
```

写入后自动触发：
- 语义处理（VLM 生成 L0 摘要 + L1 概览）
- Embedding（向量化，纳入搜索索引）

### 更新已有文档

```python
client.write(
    "viking://resources/agents/员工手册/IT指南.md",
    updated_markdown_content  # 直接覆盖
)
```

更新后自动重新向量化。

### 添加外部资源

```python
# URL 抓取
client.add_resource(
    path="https://example.com/document.pdf",
    to="viking://resources/agents/技术文档/",
    description="外部技术参考文档"
)

# 本地上传（两步）
# Step 1: 获取上传 URL
# Step 2: POST 文件后拿到 temp_file_id
client.add_resource(temp_file_id="...")
```

---

## 6. viking:// 协议处理

文档中的图片和链接使用内部 `viking://` 协议：

```markdown
[Page 10 Image 4](viking://resources/agents/员工手册/page10_img4.png)
```

### 外部系统渲染方案

外部系统不能直接使用 `viking://` URL（需要 API Key 鉴权）。处理方案：

**方案：替换为下载 URL**

```python
import re
from urllib.parse import quote

def resolve_viking_links(markdown: str, server: str) -> str:
    """将 viking:// 链接替换为 HTTP 下载 URL"""
    def replace(match):
        text, uri = match.group(1), match.group(2)
        if uri.startswith("viking://"):
            encoded = quote(uri, safe='')
            return f'[{text}]({server}/api/v1/content/download?uri={encoded})'
        return match.group(0)

    return re.sub(
        r'\[([^\]]+)\]\((viking://[^)]+)\)',
        replace,
        markdown,
    )
```

> 替换后的 URL 仍需 `X-API-Key` 头才能访问。
> 如需外部用户直接访问（`<img src>`），需实现 Signed Download URL 机制。

---

## 7. 集成限制与注意事项

| 限制 | 影响 | 缓解措施 |
|------|------|---------|
| 原始文件不保留 | PDF/DOCX 解析后丢弃，只有 Markdown | 另行备份原始文件 |
| 文件级检索粒度 | 大文件内无法精确定位段落 | 降 threshold + grep 组合 |
| 无 chunk 切分 | 大文件答案可能被信号稀释 | 入库前人工拆小文件 |
| viking:// 需鉴权 | 外部系统无法直接渲染图片 | 后端代理或 Signed URL |
| 无分页 | 搜索结果一次性返回，无 offset/cursor | 调大 limit |
| 无 delete task API | 孤儿 task 无法通过 API 清理 | 清 MinIO + QueueFS + 重启 |

### 信号稀释问题

大文件（如 5000 字的 IT指南.md 混合 IT + 财务内容）的向量被全文内容平均化，导致特定段落的匹配分数偏低。

**建议：** 按主题拆分大文件，每个文件只覆盖一个主题。

---

## 8. 配置参考

```json
{
  "server": { "host": "0.0.0.0", "port": 11933 },
  "vectordb": { "backend": "local", "name": "context" },
  "agfs": { "backend": "s3", "s3": { ... } },
  "embedding": {
    "dense": {
      "provider": "openai",
      "model": "bge-m3",
      "api_base": "http://gateway/v1",
      "dimension": 1024
    }
  },
  "rerank": {
    "provider": "openai",
    "model": "bge-reranker-v2-m3",
    "threshold": 0.03
  },
  "vlm": {
    "provider": "openai",
    "model": "kimi-k2.6",
    "api_base": "http://gateway/v1"
  }
}
```
