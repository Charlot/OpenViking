# OpenViking 产品边界规格

本文档定义 OpenViking **内部能力** 与 **外部产品需求** 的边界和对接方式。

---

## 1. 角色定义

| 角色 | 说明 |
|------|------|
| **OpenViking (Infra)** | 提供文件存储 + 语义搜索的基础设施，不管理终端用户 |
| **上层产品 (Gateway)** | 管理用户体系、权限、业务逻辑，将请求翻译后转发给 OpenViking |

```
┌──────────────────────────────────────┐
│  上层产品                             │
│  ├─ 用户注册/登录                     │
│  ├─ 组织/团队/权限管理                │
│  ├─ 文件管理前端 UI                   │
│  └─ 网关: 认证 → 转发                 │
└──────────────┬───────────────────────┘
               │ REST
               ▼
┌──────────────────────────────────────┐
│  OpenViking (纯 Infra)               │
│  ├─ 文件 CRUD                        │
│  ├─ 语义搜索 + 向量索引               │
│  ├─ ACL (shared / search_disabled)    │
│  └─ 不管理用户，分区由网关传入          │
└──────────────────────────────────────┘
```

---

## 2. 外部产品需求

### 2.1 需求清单

| # | 需求 | 说明 |
|------|------|------|
| R1 | 创建多层级文件夹 | 用户在自己的空间下创建嵌套目录 |
| R2 | 文件夹配置到 Agent | 用户指定某个文件夹可被 Agent 搜索 |
| R3 | 排除搜索 | 用户可以关闭文件/文件夹的搜索可见性 |
| R4 | 整理文件路径 | 用户可以移动/重命名文件和文件夹 |
| R5 | 删除文件/文件夹 | 删除已有内容 |
| R6 | 公开文件夹 | 设为公开后所有人都能搜到 |
| R7 | 已有账号体系 | 外部产品已有用户管理，不依赖 OpenViking 的用户系统 |

### 2.2 需求 → OpenViking 能力映射

| # | 需求 | OpenViking 接口 | 支持状态 |
|------|------|------|------|
| R1 | 创建多层级文件夹 | `POST /api/v1/fs/mkdir` + `_ensure_parent_dirs()` 自动创建父目录 | ✅ 已支持 |
| R2 | 文件夹配置到 Agent | `PUT /api/v1/acl/set` `{"shared": true}` → 向量标记 `is_shared=1` | ✅ 已实现 |
| R3 | 排除搜索 | `PUT /api/v1/acl/set` `{"search_disabled": true}` → 向量标记 `is_search_disabled=1` | ✅ 已实现 |
| R4 | 整理文件路径 | `POST /api/v1/fs/mv` 移动/重命名 | ✅ 已支持 |
| R5 | 删除文件/文件夹 | `DELETE /api/v1/fs?uri=...` | ✅ 已支持 |
| R6 | 公开文件夹 | 同 R2，`shared=true` 后 `is_shared=1` 的过滤对所有人生效 | ✅ 已支持 |
| R7 | 已有账号体系 | `auth_mode: "trusted"` + ROOT Key，不传用户身份，零代码改动 | ✅ 已支持 |

### 2.3 详细分析

#### R1: 多层级文件夹

```
用户操作: 创建 viking://user/files/财务/2024/Q4/

OpenViking:
  mkdir("viking://user/files/财务/2024/Q4/")
  → _ensure_parent_dirs() 自动创建 财务/ → 2024/ → Q4/
  → 无需预创建父目录
```

#### R2: 文件夹配置到 Agent

```
OpenViking:
  PUT /api/v1/acl/set
  {"uri": "viking://user/files/财务知识/", "shared": true}

  → 递归更新向量索引: 所有子文件 is_shared=1
  → 搜索 filter 包含 is_shared=1 → 立即可被搜索到
```

#### R3: 排除搜索

```
OpenViking:
  PUT /api/v1/acl/set
  {"uri": "viking://user/files/草稿/", "search_disabled": true}

  → 递归更新子文件: is_search_disabled=1
  → 搜索 filter 排除 is_search_disabled=1 → 不出现在搜索结果中
  → 文件仍可通过 URI 直接访问
```

#### R4: 整理文件路径

```
用户操作: 将 "报销.md" 从 "财务/" 移到 "归档/"

OpenViking:
  POST /api/v1/fs/mv
  {"from": "viking://user/files/财务/报销.md",
   "to": "viking://user/files/归档/报销.md"}

  → AGFS mv → 向量索引中 URI 更新 → ACL 继承目标目录
```

#### R5: 删除

```
用户操作: 删除 "废弃文档.md"

OpenViking:
  DELETE /api/v1/fs?uri=viking://user/files/废弃文档.md

  → AGFS rm → 向量索引中记录删除
  → 目录: 递归删除子文件
```

#### R6: 公开文件夹

```
OpenViking:
  PUT /api/v1/acl/set
  {"uri": "viking://user/files/技术分享/", "shared": true}
  → 和 R2 相同机制
```

#### R7: 外部账号体系

```
OpenViking 不管理用户。上层产品负责认证，网关持有 ROOT Key 转发请求。

配置:
  {"server": {"auth_mode": "trusted", "root_api_key": "ak-xxx"}}

零代码改动。
```

### 2.4 结论

**7 个需求全部满足，零额外开发。**

| 类型 | 数量 | 说明 |
|------|------|------|
| 已有且直接可用 | 5 个 | R1 (mkdir), R4 (mv), R5 (rm), R6 (shared), R7 (trusted) |
| 需要 ACL 层（已实现） | 2 个 | R2 (shared), R3 (search_disabled) |
| 需要额外开发 | 0 个 | — |

---

## 3. 集成开发指南

### 3.1 认证模型

| 层级 | 机制 | 说明 |
|------|------|------|
| **认证** | ROOT Key | 一个 Key，所有请求共用。验证"你是可信网关" |
| **分区** | `account` + `user` | 数据参数，不验证存在性。分区隔离用 |

ROOT Key 不做身份确认——它只证明请求来自可信网关。`account` 和 `user` 是网关传过来的**数据分区标记**，OpenViking 不查库、不验证用户是否存在。隔离由上层保证。

### 3.2 URI 体系

```
viking://user/{user_id}/files/  →  /local/{account_id}/user/{user_id}/files/
```

| 写法 | 展开后 | 说明 |
|------|------|------|
| `viking://user/files/` | `viking://user/{当前用户}/files/` | 简写，自动注入 |
| `viking://user/files/开票.md` | `viking://user/{当前用户}/files/开票.md` | 推荐日常使用 |
| `viking://user/alice/files/开票.md` | 原样 | 跨分区访问（需 ACL shared） |

### 3.3 SDK 初始化

```python
from openviking import SyncHTTPClient

# 网关层：ROOT Key 认证 + 传入数据分区
client = SyncHTTPClient(
    url="http://openviking:11933",
    api_key="ak-d9...",           # ROOT Key（服务间认证，所有用户共用）
    account="company-42",         # 分区: 租户
    user="alice",                 # 分区: 用户
)
client.initialize()

# OpenViking 不验证 alice 是否存在
# 只将文件写入 /local/company-42/user/alice/files/
```

### 3.4 添加资源（异步 → 获取 URI）

`add_resource` 是异步的——先返回 `task_id`，处理完才能拿到最终 URI。

**推荐方式：同步等待**

```python
result = client.add_resource(
    path="报销流程.pdf",
    to="viking://user/files/财务/",     # 简写，自动展开
    wait=True,                           # 阻塞直到处理完成
)
uri = result["root_uri"]
# → "viking://user/files/财务/报销流程.md"
```

**轮询方式：**

```python
task = client.add_resource(
    path="报销流程.pdf",
    to="viking://user/files/财务/",
)
task_id = task["task_id"]

# 轮询直到完成
import time
while True:
    tasks = client.list_tasks()  # GET /api/v1/tasks
    for t in tasks:
        if t["task_id"] == task_id:
            if t["status"] == "completed":
                uri = t["resource_id"]  # → viking://user/files/财务/报销流程.md
            elif t["status"] == "failed":
                raise Exception(t["error"])
            break
    time.sleep(1)
```

**外部系统拿到 URI 后保存它——后续读、写、搜、删都用这个 URI。** 字段在 task 返回中：

```json
{
  "task_id": "eee874f9-...",
  "status": "completed",
  "resource_id": "viking://user/files/财务/报销流程.md",
  "result": {
    "root_uri": "viking://user/files/财务/报销流程.md"
  }
}
```

### 3.5 替换上传（覆盖同名文件）

`add_resource` 同名会生成新文件（`_1` 后缀）。要覆盖已有文件，用 `write`：

```python
# 覆盖已有文件
client.write(uri, new_content)  # POST /api/v1/content/write

# 首次上传 → 先 add_resource 拿到 URI，后续更新 → write 覆盖
```

外部系统需在前端实现：检查目标 URI 是否存在 → 存在则确认"覆盖还是保留两份"。

### 3.6 共享到 Agent

共享就是调 ACL API。对调用者完全透明——标了 `shared=true` 的文件夹，其他用户搜索时自动可见。

```python
# 共享文件夹
client.set_acl("viking://user/files/财务知识/", shared=True)

# 取消共享
client.set_acl("viking://user/files/财务知识/", shared=False)

# 排除搜索（不加入搜索索引）
client.set_acl("viking://user/files/草稿/", search_disabled=True)
```

### 3.7 搜索

搜索通过 ACL 控制可见性，分区过滤自动生效：

```python
# 搜索所有可见文件（shared=true 或 未设 search_disabled）
client.find("开票")

# 限定目录搜索
client.find("开票", target_uri="viking://user/files/财务/")
```

搜索自动过滤：`is_search_disabled=0` — 被标记 `search_disabled=true` 的文件不出现在结果中。

### 3.8 典型流程

**上传 + 共享：**

```
1. 用户上传 → add_resource(wait=True) → URI: viking://user/files/财务/开票.md
2. 外部系统保存 URI 到数据库
3. 用户点"共享" → set_acl(uri, shared=true) → 文件可被搜索到
4. 用户点"排除搜索" → set_acl(uri, search_disabled=true) → 文件从搜索中隐藏
```

**Agent 搜索：**

```
1. Agent 调 find("开票") → 返回所有 shared=true 的文件
2. 返回结果 → Agent 展示给用户
```

### 3.9 完整 API 端点

| 端点 | 用途 | 权限 |
|------|------|------|
| `POST /api/v1/search/find` | 语义搜索（共享自动透明） | 需读权限 |
| `GET /api/v1/content/read` | 读取文件 | 需读权限 |
| `POST /api/v1/content/write` | 写入/覆盖文件 | 需写权限 |
| `POST /api/v1/resources` | 添加资源（异步） | 需写权限 |
| `GET /api/v1/tasks` | 查询任务状态 | 需读权限 |
| `GET /api/v1/fs/ls` | 列出目录 | 需读权限 |
| `POST /api/v1/fs/mkdir` | 创建目录 | 需写权限 |
| `POST /api/v1/fs/mv` | 移动/重命名 | 需写权限 |
| `DELETE /api/v1/fs` | 删除文件/目录 | 需写权限 |
| `PUT /api/v1/acl/set` | 设置共享/禁用搜索 | owner 或 admin |
| `GET /api/v1/acl/get` | 查询 ACL | 需读权限 |

---

## 4. 边界与分工

| 层 | 负责 | 不负责 |
|------|------|------|
| **上层产品** | 用户管理、前端 UI、URI 存储、覆盖确认 | 文件存储、搜索 |
| **OpenViking** | 文件 CRUD、向量索引、语义搜索、ACL | 用户体系、权限 UI |

上层产品须实现：用户管理 → Key 签发 → 网关注入 header → 前端文件浏览器 + ACL toggle。

---

## 5. 配置参考

```json
{
  "server": {
    "auth_mode": "trusted",
    "root_api_key": "ak-d9...",
    "host": "0.0.0.0",
    "port": 11933
  }
}
```

> `trusted` 模式下，OpenViking 只验证 ROOT Key。不管理用户，`account` + `user` 由网关传入做数据分区。

