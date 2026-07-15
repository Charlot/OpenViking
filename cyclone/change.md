# merge-play-main 分支本地改动详细日志（防回滚备份）

> **文档目的**：完整记录本分支相对上游的全部本地改动。在与 upstream/main 合并前留档，
> 若合并出错需要回滚或重建改动，以本文档为准逐项核对。
>
> - **分支**：`merge-play-main`
> - **与上游的 merge-base**：`4b9d17db`（fix(ragfs): sort directory listings dirs first (#2993)，2026-07-03）
> - **本地提交数**：29（26 个非 merge 提交 + merge 提交）
> - **改动规模**：62 个文件，+13591 / -4728 行
> - **记录日期**：2026-07-15
>
> 恢复/核对命令：
> ```bash
> git diff 4b9d17db...merge-play-main            # 全量本地改动
> git diff 4b9d17db...merge-play-main -- <file>  # 单文件本地改动
> ```

---

## 一、功能模块总览

本分支包含 6 大块本地功能，均不存在于上游：

| # | 功能 | 关键提交 | 核心文件 |
|---|------|---------|---------|
| 1 | ACL 共享搜索（后端+SDK+前端） | 8ec40a79, e0f865ba, 2e5f8cc0 | `openviking/acl/`, `viking_vector_index_backend.py`, `collection_schemas.py` |
| 2 | trusted 认证放宽（optional user / root-key→ADMIN） | 67374f5c, f5b12955 | `trusted.py`, `auth/__init__.py` |
| 3 | add_resource 增强（scope/overwrite/source_name/fallback_to_abstract） | 503438e3, 02b24e44, 075ce4ff, 5bb1cad2 | `resource_service.py`, `tree_builder.py`, `content_targets.py`, `routers/resources.py`, `routers/content.py` |
| 4 | 检索增强（content 透传 / level 默认值） | 78d70ad2 | `hierarchical_retriever.py`, `core/context.py`, `openviking_cli/retrieve/types.py` |
| 5 | 任务 ADMIN 可见性 + 任务对话框改版 | b2611f43 | `routers/tasks.py`, `upload-task-dialog.tsx` |
| 6 | play 测试框架 + file-viewer 前端 + playground 脚本 | f5b12955, 075ce4ff 等 | `play/`, `web-studio/src/routes/file-viewer/`, `playground/` |

---

## 二、后端改动明细（逐文件）

### 2.1 ACL 共享搜索（全新模块）

**`openviking/acl/__init__.py`（新增，63 行）**
- 模块入口，导出 `resolve_acl_access()` 等；被 `core/namespace.py` 的访问控制调用。

**`openviking/acl/router.py`（新增，243 行）**
- ACL 的 REST API 路由（get/set/remove ACL），注册进 server app。

**`openviking/acl/store.py`（新增，206 行）**
- ACL 存储层与缓存；075ce4ff 中随 save 逻辑重构一并调整。

**`openviking/core/namespace.py`（+9/-2）**
- `is_accessible()`：
  - root 判断扩展为 `role in ("root", "admin")` —— **admin 也拥有全局可访问权**；
  - `scope == "user"` 分支中，先调用 `resolve_acl_access(target, ctx)`，命中 ACL 结果（非 None）则直接返回 —— **这是 ACL 共享访问的核心挂载点**。

**`openviking/storage/collection_schemas.py`（+7）**
- context 集合 schema 新增 `is_shared`（int64）、`is_search_disabled`（int64）两个字段及对应标量索引。
- ⚠️ 涉及向量库 schema，回滚时注意已写入的数据带有这两个字段。

**`openviking/storage/viking_vector_index_backend.py`（+16/-?）**
- `RETRIEVAL_OUTPUT_FIELDS` 增加 `"content"`、`"is_shared"`、`"is_search_disabled"`；
- `upsert()` 为 `is_shared` / `is_search_disabled` 写入默认值；
- `_tenant_filter()` 重写：支持 ACL 共享内容进入他人搜索结果；
- **2e5f8cc0**：`is_search_disabled` 过滤改用 `must_not`（而非正向 filter），以兼容没有该字段的存量向量记录（legacy records）。

**`openviking/server/app.py`（+19）**
- 导入并注册 `acl_router`（`app.include_router(acl_router)`）；
- lifespan 中新增 ACL 缓存预热；
- error handler 中新增 500 错误日志增强。

### 2.2 trusted 认证放宽

**`openviking/server/auth/plugins/trusted.py`（+19/-?）**
- data 路径的显式身份强制要求（`_trusted_request_requires_explicit_identity` 块）改为 `pass` —— account/user 变为**可选**；
- role 赋值：原 `trusted_role = Role.USER` 改为——配置了 `root_api_key` 时给 `ADMIN`，否则 `USER`（为 play 的 admin 跨用户视图服务）。
- ⚠️ **与上游 1c46d44f（X-OpenViking-Role 请求头断言机制）在同一段代码上语义冲突**，合并时需人工设计（建议改用上游的 role 头机制替代本地硬编码）。

**`openviking/server/auth/__init__.py`（1 行）**
- `UserIdentifier` 的 user_id 回退链：`identity.user_id or "default"` → `identity.user_id or identity.account_id or "default"`（user 缺失时回退到 account 而非硬编码 default）。

**`openviking/core/identifiers.py`（1 行）**
- 校验失败的报错信息补充合法字符说明（`a-z, 0-9, _, ., -, @`），纯文案。

### 2.3 add_resource / 资源写入链路增强

**`openviking/core/content_targets.py`（+6）**
- `ContentTargetSpec` 新增字段：`scope: str = "resources"`、`overwrite: bool = False`；`from_fields()` 同步透传。

**`openviking/parse/tree_builder.py`（+31/-?）**
- `create_parent` 默认值改为 `True`；
- 统一 to/parent 处理：`effective_parent_uri = parent_uri or effective_to_uri`（parent 优先作为 base）；
- `to_uri` 末段带扩展名时剥离作为 `doc_name`，剩余路径作为 base 目录；
- **5bb1cad2**：修复 `base_uri` fallback。
- （上游自 merge-base 起未改此文件，无冲突对手盘。）

**`openviking/service/resource_service.py`（+117/-?，本分支后端最大改动）**
- `add_resource()` 新增 `scope`、`overwrite` 参数；
- `_run_add_resource_task()` 引入 `effective_parent_uri` / `effective_to_uri` 计算逻辑；
- 候选名预留逻辑加条件：`if candidate_uri and not target.overwrite:` —— **overwrite 时跳过唯一名预留**；
- ⚠️ 该处与上游 5b3c92da（改为 `reserve_unique_candidate()` 调用）文本冲突；合并解法：采用上游调用结构 + 保留本地 `and not target.overwrite` 条件。

**`openviking/utils/resource_processor.py`（+7/-?）**
- `process_task()`：`kwargs.get("overwrite")` 为真时跳过 `reserve_unique_candidate()`；`create_parent` 默认值调整。

**`openviking/server/routers/resources.py`（+88/-?）**
- add_resource 端点透传 `overwrite` / `scope` / `source_name` 等新参数（0f1f5afe 修复 overwrite 未传给 service 的问题）。
- （上游未改此文件。）

**`openviking/server/routers/content.py`（+24）**
- `read()` 端点新增 `fallback_to_abstract` 参数：二进制文件读取失败/不可读时回退返回摘要（abstract）。
- （上游在该文件改的是 `reindex` 端点，与本地 `read` 改动无交集。）

### 2.4 检索链路

**`openviking/retrieve/hierarchical_retriever.py`（+3）**
- `search()`：`if level is None: level = [2]` 默认值；
- 构造 `MatchedContext` 时透传 `content` 字段。
- ⚠️ 与上游 3003ed61（image_query）在同一插入点有轻微文本冲突，两段都保留即可。

**`openviking/core/context.py`（+5）**
- `Context.__init__` 新增 `content: Optional[str]` 参数与属性；`to_dict()` 在 content 非 None 时输出。

**`openviking_cli/retrieve/types.py`（+6/-?）**
- `MatchedContext` 新增 `content` 字段；`_context_to_dict()` 同步输出。

### 2.5 任务可见性

**`openviking/server/routers/tasks.py`（2 处，各 1 行）**
- `get_task()` 与 `list_tasks()`：`_ctx.role == Role.ROOT` → `_ctx.role in (Role.ROOT, Role.ADMIN)` —— ADMIN 也能看系统任务。

### 2.6 文件系统 / 隐藏文件

**`openviking/server/config.py`（+1）**
- `ServerConfig` 新增 `show_hidden_files: bool = True`。

**`openviking/server/routers/filesystem.py`（+12/-?）**
- `ls` / `tree` 端点：`show_all_hidden` 从 `bool=False` 改为 `Optional[bool]=Query(None)`，为 None 时回退到配置 `show_hidden_files`；新增 `get_server_config` 导入。
- （与上游 cc0281ac 的 sort_by/sort_order 新参数无冲突。）

**`openviking/service/fs_service.py`（+5）**
- 新增 `_async_agfs` property，委托 `self._viking_fs._async_agfs`。

### 2.7 其他后端

**`openviking/storage/queuefs/semantic_processor.py`（+12）**
- 若干调试日志：`[VLM:summary:ast_llm]`、`[VLM:summary:code]`、`[VLM:summary:file]`（~1102-1136 行区域）、`_generate_overview`（~1428）、`_batched_generate_overview` 的 partial 循环与合并步骤（~1514、~1547）；
- **a5b23c68**：修复 overview logger 中的未定义变量。
- ⚠️ 与上游 b0ea896a 对 `_batched_generate_overview` 的重构有文本冲突；解法：接受上游重构，把日志行挪到新代码对应位置。

---

## 三、SDK 改动明细

**`sdk/python/openviking_sdk/client.py`（+232/-?）**
- `add_resource()`：新增 `overwrite`、`source_name` 参数及 source_name 处理逻辑；
- **新增 `add_user_resource()`**：用户空间资源上传；
- `read()`：新增 `fallback_to_abstract` 参数，params 构造改为显式 dict；
- `find()` / `search()`：新增 `include_content` 参数 + `_strip_content()` 响应后处理；
- **新增 `get_acl()` / `set_acl()` / `remove_acl()`**。
- ⚠️ **合并最大风险点**：上述本地新增/修改的方法全部直接使用 `self._http.get/post/put/delete`；上游 cbfb387d 已把全部 HTTP 调用统一为 `_request()`（附加 Gateway Token）。git 合并大概率**不报冲突**但本地方法会绕过网关认证——合并后必须手工把这些方法改为 `_request()`，并核对 `find/search` 与上游 `_normalize_context_type()`、`image_url`（3003ed61）的叠加。

**`sdk/python/uv.lock`（新增，103 行）**：SDK 独立锁文件（8af9160d）。

---

## 四、前端（web-studio）改动明细

### 4.1 全新 file-viewer 模块（约 4455 行新增）
- `routes/file-viewer/route.tsx`（928 行）：主路由；独立连接（不依赖全局连接状态）；URL-synced account/user 参数（4080e84a）；更宽的 account 输入框。
- `-components/terminal-panel.tsx`（1848 行）、`agent-panel.tsx`（451）、`context-explorer.tsx`（469）、`acl-panel.tsx`（187）、`resource-ref-list.tsx`（47）。
- `-lib/constants.ts`（170）、`types.ts`（70）、`utils.ts`（285）。
- `routeTree.gen.ts`：生成文件，含 file-viewer 路由注册。

### 4.2 playground 页面
- `-components/acl-panel.tsx`（新增 187 行）：ACL 面板；
- `route.tsx`：接 ACL 面板；89e773d0 修复目录也显示 ACL 面板；
- `-components/context-explorer.tsx`（+57/-?）：任务图标常显等。

### 4.3 resources 页面
- `upload-task-dialog.tsx`（+144/-?）：任务对话框改版（b2611f43）；
- `find-palette.tsx`（+22）、`dir-browser.tsx`（+4）；
- `-lib/api.ts`（+4）：`fetchFsList`/`fetchFsTree` 传 `showAllHidden`；
- `-lib/normalize.ts`（+17）：`normalizeFsEntries()` 新增 `showAllHidden` 参数与隐藏文件过滤（显示 `.summary` 等隐藏摘要文件）。

### 4.4 公共
- `hooks/use-app-connection.tsx`（+16/-?）：`readStoredConnection()` 增加 userId 清理（非法字符时置 'default'）。（与上游 cd9add7a / fe3bdf41 不同函数，无冲突。）
- `lib/ov-client/client.ts`（+15/-?）：file-viewer 独立连接支持（67374f5c）。
- `components/app-shell.tsx`（+7）：file-viewer 导航入口。
- `i18n/locales/en.ts` / `zh-CN.ts`（各 +4）：新增文案。

---

## 五、play 测试框架 / playground / 脚本（全部为新增，无冲突风险）

| 路径 | 说明 |
|---|---|
| `play/README_PLAY.md`（528 行） | play 框架使用说明 |
| `play/README_PLAY_DESIGN.md`（729 行） | 设计文档（含 ACL 共享搜索设计） |
| `play/README_PLAY_PLAN.md`（181 行） | 计划文档（SPEC 已在 8977df7d 删除） |
| `play/__init__.py`、`play/tests/__init__.py` | 包结构 |
| `play/tests/test_client.py`（124 行） | 测试客户端（8977df7d 起改用 `openviking_sdk` 导入） |
| `play/tests/test_resource_scenarios.py`（348 行） | 资源场景测试：add/read/overwrite/task-polling/search 等 |
| `playground/delete_resources.py`（111）、`find.py`（73）、`search.py`（51） | 调试脚本 |
| `scripts/build-web.sh`（3）、`scripts/start-play.sh`（105） | 构建/启动脚本 |

其他：`uv.lock`（主锁文件大量变化，合并时不手工解冲突，直接 `uv lock` 重新生成）；工作区另有未跟踪的 `.dockerignore`（不属于任何提交）。

---

## 六、逐提交日志（时间正序）

| 日期 | 提交 | 说明 |
|---|---|---|
| 06-26 | `33ac1e02` | feat(play): Web UI 显示隐藏摘要文件；新增 search/find playground 脚本 |
| 06-29 | `19569d36` | 文档整理；filesystem/config/semantic_processor 初步改动；delete_resources.py、start-play.sh |
| 06-29 | `6673cf68` | ACL 共享搜索设计文档 |
| 06-30 | `8ec40a79` | **ACL 共享搜索实现**（acl 模块 + namespace 挂载 + SDK + 前端 acl-panel） |
| 07-02 | `e0f865ba` | **ACL 向量搜索过滤**（collection_schemas 加字段 + _tenant_filter） |
| 07-02 | `dcaea413` | build-web.sh |
| 07-02 | `a5b23c68` | fix(vlm): overview logger 未定义变量 |
| 07-02 | `89e773d0` | fix(acl): 目录也显示 ACL 面板 |
| 07-02 | `b92c782e` | 设计/SPEC/计划文档 |
| 07-03 | `6e3997db` | 设计文档移入 play/ |
| 07-03 | `f5b12955` | **add_user_resource、overwrite、admin 跨用户视图、file-viewer 模块**（本分支最大提交） |
| 07-03 | `67374f5c` | file-viewer 独立连接；**trusted optional user**；SDK overwrite |
| 07-03 | `4080e84a` | file-viewer URL-synced account/user |
| 07-03 | `0f1f5afe` | fix: overwrite 参数传给 add_resource service |
| 07-06 | `8af9160d` | 升级测试客户端；sdk/python/uv.lock |
| 07-06 | `503438e3` | **fix(add-resource): 堵住 scope/overwrite/parent 泄漏** |
| 07-07 | `02b24e44` | **fix(tree-builder): create_parent 默认 True，统一 to/parent** |
| 07-07 | `075ce4ff` | **重构 save 逻辑**（resource_service/tree_builder/resources 路由/acl store）；test_resource_scenarios.py |
| 07-08 | `8977df7d` | play 测试改用 openviking_sdk 导入；删除冗余 SPEC |
| 07-09 | `b2611f43` | **任务 ADMIN 可见性**；任务图标常显；任务对话框改版 |
| 07-09 | `9cc716b0` | 测试：read-resource、task-polling 场景 |
| 07-10 | `52cd4cfd` | 测试用例；fs_service `_async_agfs` property |
| 07-10 | `2e5f8cc0` | **fix(search): is_search_disabled 过滤改 must_not 兼容存量记录** |
| 07-13 | `78d70ad2` | 资源测试用例；**检索链路 content 字段透传**（context/types/retriever/backend/SDK） |
| 07-14 | `5bb1cad2` | **feat: fallback_to_abstract（二进制文件）、source_name 参数、base_uri fallback 修复** |
| 07-14 | `04fde917` | 更新测试用例 |

另：`5a8ea182` merge main（上一次同步上游的 merge 提交，在 merge-base 之前的历史线上）。

---

## 七、与上游合并时最容易丢失/出错的点（重点保护清单）

合并 upstream/main 后，逐项核对以下内容仍然生效（可用 `play/tests/` 回归验证）：

1. **`trusted.py` role 赋值**（语义冲突区）：本地 root-key→ADMIN + data 路径 optional user。上游 1c46d44f 引入 `X-OpenViking-Role` 头机制，合并时可能整段被上游覆盖。若采纳上游机制，需同步改 play 测试客户端发送 role 头，并验证 admin 跨用户视图场景仍通过。
2. **SDK 新方法的网关适配**（隐性风险，git 不报冲突）：`add_user_resource` / `get_acl` / `set_acl` / `remove_acl` / 改过的 `add_resource` / `read` / `find` / `search` 需从 `self._http.*` 改为 `_request()`（上游 cbfb387d 之后的统一约定）。
3. **`resource_service.py` 候选名预留**：合并上游 5b3c92da 后确认 `and not target.overwrite` 条件仍在（否则 overwrite 上传会因重名预留失败）。
4. **`viking_vector_index_backend.py`**：`RETRIEVAL_OUTPUT_FIELDS` 中 `content`/`is_shared`/`is_search_disabled` 三个字段、`_tenant_filter()` 的 ACL 逻辑、`must_not` 过滤——三者缺一 ACL 共享搜索即失效。
5. **`hierarchical_retriever.py`**：`level=[2]` 默认值与 `content` 透传，注意与上游 image_query 插入段共存。
6. **`semantic_processor.py` 日志行**：上游 b0ea896a 重构后需重新安置（丢了只影响调试，不影响功能）。
7. **`find()`/`search()` 三方叠加**：本地 `include_content`/`_strip_content` × 上游 `_normalize_context_type` × 上游 `image_url`，合并后人工通读这两个方法。

回归验证入口：`play/tests/test_client.py`、`play/tests/test_resource_scenarios.py`（覆盖 ACL 共享搜索、overwrite 上传、admin 跨用户、fallback_to_abstract、task-polling）。
