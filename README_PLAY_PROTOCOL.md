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

## 2. 目录结构设计

### 权限约束

OpenViking 的 `viking://user/{id}/` 有严格的用户隔离：

```python
# namespace.is_accessible()
if target.scope == "user":
    if target.owner_user_id != ctx.user.user_id:
        return False   # 用户 A 不能访问用户 B 的目录
```

**Agent 持有的 User Key 无法跨用户访问 `viking://user/{id}/`。**
ROOT Key 虽然能访问所有目录，但 ROOT 不能操作数据 API（`PermissionDeniedError: ROOT API keys cannot access tenant-scoped data APIs`）。

因此 **Agent 多用户共享只能通过 `viking://resources/` 实现**，这个路径对所有用户天然可读。

### 目录结构

```
viking://
│
├── resources/agents/            ← Agent 共享知识库（所有用户可读）
│   ├── 员工手册/
│   │   ├── .abstract.md         ← AI 生成摘要
│   │   ├── .overview.md         ← AI 生成概览
│   │   ├── 人事指南.md          ← 用户直接编辑
│   │   ├── IT指南.md
│   │   └── 行政指南.md
│   ├── 技术文档/
│   │   ├── 架构设计.md
│   │   └── API文档.md
│   └── 财务制度/
│       ├── 报销流程.md
│       └── 开票信息.md
│
├── user/{user_id}/drafts/       ← 用户私有草稿（仅自己可见）
│
├── skills/                      ← Skill 定义
│
└── sessions/                    ← 会话上下文
```

### 权限模型

| 路径 | 读 | 写 | 说明 |
|------|----|----|------|
| `viking://resources/` | **所有用户** | Admin | 天然共享 |
| `viking://resources/agents/` | **所有用户** | Admin | Agent 知识库 |
| `viking://user/{id}/` | **仅本人** + Admin | 本人 + Admin | 严格隔离 |
| `viking://skills/` | 所有用户 | Admin | 系统管理 |

### 设计原则

1. **共享靠路径不靠授权**：放在 `resources/agents/` 就是共享，不需要额外配置
2. **共享即真相源**：Agent 和所有用户读同一个 URI，任何用户编辑后即时生效
3. **不复制**：没有"用户发布到 Agent"的流程，改了就是改了
4. **私有靠隔离**：个人草稿放 `user/{id}/drafts/`，成熟后搬入 `resources/agents/`
5. **Agent 不需要跨用户权限**：它只搜 `resources/agents/`，永远不碰 `user/`

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
