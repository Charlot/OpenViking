# OpenViking 开发计划

基于 `README_PLAY_DESIGN.md` 和 `README_PLAY_SPEC.md` 的设计。

**状态：** 🔴 未开始 | 🟡 进行中 | 🟢 完成 | ⚪ 不执行
**优先级：** P0 阻塞上线 | P1 核心体验 | P2 锦上添花

---

## 1. OpenViking 核心改造

### 1.1 ACL 权限模块

| # | 任务 | 状态 | 优先级 | 说明 |
|------|------|------|------|
| 1.1.1 | `openviking/acl/__init__.py` — 权限判定 hook | 🟢 完成 | P1 | `resolve_acl_access()` |
| 1.1.2 | `openviking/acl/store.py` — ACL 存储 CRUD + 缓存 + 启动预热 | 🟢 完成 | P1 | AGFS `.acl.json` + `_acl_cache` + `warm_acl_cache()` |
| 1.1.3 | `openviking/acl/router.py` — REST API | 🟢 完成 | P1 | `/api/v1/acl/set\|get\|remove\|list` |
| 1.1.4 | `namespace.py` hook | 🟢 完成 | P1 | `is_accessible()` 中调用 `resolve_acl_access()` |
| 1.1.5 | `app.py` 注册 router + 缓存预热 | 🟢 完成 | P1 | lifespan 中 `warm_acl_cache()` |
| 1.1.6 | ACL 状态互斥验证（shared / search_disabled） | 🟢 完成 | P1 | router 中 `search_disabled` → force `shared=false` |
| 1.1.7 | 缓存线程安全 | 🟢 完成 | P1 | `threading.Lock` |

### 1.2 向量搜索 Filter

| # | 任务 | 状态 | 优先级 | 说明 |
|------|------|------|------|------|
| 1.2.1 | `collection_schemas.py` 新增 `is_shared` + `is_search_disabled` 字段 | 🟢 完成 | P1 | schema + scalar_index |
| 1.2.2 | `RETRIEVAL_OUTPUT_FIELDS` 新增字段 | 🟢 完成 | P1 | |
| 1.2.3 | `upsert()` 默认值 `is_shared=0, is_search_disabled=0` | 🟢 完成 | P1 | |
| 1.2.4 | `_tenant_filter()` 扩展搜索范围 | 🟢 完成 | P1 | `path_filter OR is_shared=1` + `is_search_disabled=0` |
| 1.2.5 | ACL set 时更新向量索引记录 | 🟢 完成 | P1 | `_update_vector_acl()` 递归更新 |

### 1.3 隐藏文件配置化

| # | 任务 | 状态 | 优先级 | 说明 |
|------|------|------|------|------|
| 1.3.1 | `ServerConfig.show_hidden_files` 配置项 | 🟢 完成 | P1 | 默认 `true` |
| 1.3.2 | `filesystem.py` 从配置读取默认值 | 🟢 完成 | P1 | `/ls` + `/tree` |
| 1.3.3 | `ov.conf` 添加配置 | 🟢 完成 | P1 | |

### 1.4 Python SDK

| # | 任务 | 状态 | 优先级 | 说明 |
|------|------|------|------|------|
| 1.4.1 | `AsyncHTTPClient.get_acl()` | 🟢 完成 | P1 | |
| 1.4.2 | `AsyncHTTPClient.set_acl()` | 🟢 完成 | P1 | |
| 1.4.3 | `AsyncHTTPClient.remove_acl()` | 🟢 完成 | P1 | |
| 1.4.4 | `SyncHTTPClient` 同步封装 | 🟢 完成 | P1 | |
| 1.4.5 | SDK `acl_list()` 方法 | 🔴 未开始 | P2 | 对应 `GET /api/v1/acl/list` |

### 1.5 MCP Tools

| # | 任务 | 状态 | 优先级 | 说明 |
|------|------|------|------|------|
| 1.5.1 | MCP tool: `set_acl` | 🔴 未开始 | P2 | |
| 1.5.2 | MCP tool: `get_acl` | 🔴 未开始 | P2 | |

---

## 2. 前端（Web Studio）

### 2.1 ACL 管理

| # | 任务 | 状态 | 优先级 | 说明 |
|------|------|------|------|------|
| 2.1.1 | ACL 面板组件 `acl-panel.tsx` | 🟢 完成 | P1 | shared / search_disabled toggle |
| 2.1.2 | Playground 集成 ACL 面板 | 🟢 完成 | P1 | `route.tsx` |
| 2.1.3 | 文件树中 ACL 状态图标 | 🔴 未开始 | P2 | 共享/禁用标识 |
| 2.1.4 | 目录 ACL 面板 | 🟢 完成 | P1 | 已去掉 `isDir` 限制 |

### 2.2 隐藏文件

| # | 任务 | 状态 | 优先级 | 说明 |
|------|------|------|------|------|
| 2.2.1 | `normalize.ts` 恢复过滤 + `showAllHidden` 参数 | 🟢 完成 | P1 | |
| 2.2.2 | `api.ts` 透传 `showAllHidden` | 🟢 完成 | P1 | |
| 2.2.3 | Playground 👁 toggle 开关 | 🟢 完成 | P1 | `ContextExplorerHeader` |
| 2.2.4 | `ContextTreeNode` 递归传递 `showHidden` | 🟢 完成 | P1 | |

### 2.3 文件操作

| # | 任务 | 状态 | 优先级 | 说明 |
|------|------|------|------|------|
| 2.3.1 | 上传同名文件覆盖确认 | 🔴 未开始 | P1 | 前端检查 + 确认弹窗 |

---

## 3. 上层产品（外部系统）

### 3.1 网关

| # | 任务 | 状态 | 优先级 | 说明 |
|------|------|------|------|------|
| 3.1.1 | 网关服务搭建 | 🔴 未开始 | P0 | 认证 → header 注入 → 转发 OpenViking |
| 3.1.2 | 用户 → `account`/`user` 映射 | 🔴 未开始 | P0 | 查库逻辑 |
| 3.1.3 | ROOT Key 管理 | 🔴 未开始 | P0 | 安全存储 + 轮换 |

### 3.2 文件管理前端

| # | 任务 | 状态 | 优先级 | 说明 |
|------|------|------|------|------|
| 3.2.1 | 文件浏览器 UI | 🔴 未开始 | P0 | 目录树 + 文件列表 |
| 3.2.2 | 文件上传（对接 add_resource） | 🔴 未开始 | P0 | 含进度轮询 |
| 3.2.3 | 文件编辑（对接 write） | 🔴 未开始 | P0 | 含覆盖确认 |
| 3.2.4 | 文件移动/重命名（对接 mv） | 🔴 未开始 | P0 | |
| 3.2.5 | 文件删除（对接 DELETE） | 🔴 未开始 | P0 | 含确认 |
| 3.2.6 | 多级目录创建 | 🔴 未开始 | P0 | |
| 3.2.7 | 文件搜索（对接 find） | 🔴 未开始 | P0 | |
| 3.2.8 | ACL 管理 UI（共享/排除搜索 toggle） | 🔴 未开始 | P0 | 对接 PUT /api/v1/acl/set |
| 3.2.9 | URI 存储（上传后保存返回的 URI） | 🔴 未开始 | P0 | |

### 3.3 Agent 集成

| # | 任务 | 状态 | 优先级 | 说明 |
|------|------|------|------|------|
| 3.3.1 | Agent 搜索配置（指定搜索范围） | 🔴 未开始 | P0 | `target_uri` 配置 |
| 3.3.2 | Agent MCP 连接（对接 OpenViking /mcp） | 🔴 未开始 | P0 | |
| 3.3.3 | Agent 搜索结果渲染 | 🔴 未开始 | P0 | 文件列表 + 摘要展示 |

---

## 4. 不执行

| # | 任务 | 状态 | 优先级 | 原因 |
|------|------|------|------|------|
| 4.1 | OpenViking 内建用户体系改造 | ⚪ 不执行 | — | 上层产品管理用户，OpenViking 只做 Infra |
| 4.2 | 向量索引数据迁移工具 | ⚪ 不执行 | — | play 环境可重建 |
| 4.3 | CLI `ov acl` 命令 | ⚪ 不执行 | — | MCP + SDK 已覆盖 |
| 4.4 | Signed Download URL | ⚪ 不执行 | — | 网关代理即可，不需 OpenViking 原生支持 |

---

## 5. 进度总览

| 模块 | 🟢 完成 | 🔴 未开始 | ⚪ 不执行 |
|------|------|------|------|
| OpenViking 核心 | 16 | 2 (均为 P2) | 0 |
| 前端 | 6 | 2 (P1+P2) | 0 |
| 上层产品 | 0 | 15 (均为 P0) | 0 |
| 不执行 | — | — | 4 |

### 按优先级

| 优先级 | 完成 | 未开始 | 说明 |
|------|------|------|------|
| P0 | 0 | 16 | **全部在上层产品** — 网关 + 文件管理 + Agent 集成 |
| P1 | 20 | 1 | OpenViking 核心 + 前端主体 + 覆盖确认 |
| P2 | 2 | 3 | SDK acl_list + MCP + ACL 树图标 |
| — | — | 4 | 不执行 |
| 不执行 | — | 4 | 4 |

**OpenViking 侧基本完成。剩余工作主要在上层产品。**
