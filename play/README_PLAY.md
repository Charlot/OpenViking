# OpenViking 源码安装与启动指南

## 1. 架构概览

```
                               ┌─────────────────────────────┐
                               │     openviking-server        │
                               │    (Python FastAPI+Uvicorn)  │
                               │         端口 1933            │
                               └──────────────┬──────────────┘
                                              │
         ┌────────────────────────────────────┼────────────────────────────────────┐
         │                                    │                                    │
    ┌────▼─────┐                        ┌────▼─────┐                        ┌─────▼──────┐
    │  REST API │                        │ Web Studio│                        │  MCP Server │
    │  /api/v1  │                        │  /studio  │                        │  (mcp>=1.27)│
    └──────────┘                        │(Vite+React│                        └────────────┘
                                        │  TS+JS)   │
                                        └───────────┘
                                              │
         ┌────────────────────────────────────┼────────────────────────────────────┐
         │                                    │                                    │
    ┌────▼─────┐                        ┌────▼─────┐                        ┌─────▼──────┐
    │ 认证鉴权  │                        │ 多租户    │                        │ OAuth 2.1  │
    │ api_key / │                       │ ROOT/ADMIN│                        │ MCP 客户端  │
    │ trusted   │                       │ /USER     │                        │ 授权       │
    └──────────┘                        └──────────┘                        └────────────┘

 ═══════════════════════════════════════ 存储层 ═══════════════════════════════════════

           AGFS (内容存储)                              VectorDB (向量索引)
    ┌──────────────────────────┐              ┌──────────────────────────────┐
    │  RAGFS (Rust)            │              │  Local Backend               │
    │  crates/ragfs/           │              │  C++ BruteForce (pybind11)   │
    │                          │              │  src/index/                  │
    │  ┌────────────────────┐  │              │  ├── BruteForceIndex         │
    │  │ S3 Backend (Rust)  │  │              │  ├── 稠密: IP/L2/Cosine      │
    │  │ PathStyle / Virtual│  │              │  ├── 稀疏: BM25 混合检索     │
    │  │ HostStyle          │  │              │  ├── 量化: float32 / int8    │
    │  └────────┬───────────┘  │              │  └── 标量过滤: Bitmap 索引   │
    │           │              │              │                              │
    │  ┌────────▼───────────┐  │              │  可选外部后端:               │
    │  │ QueueFS (Rust)     │  │              │  ├── Qdrant                  │
    │  │ SQLite 持久化队列   │  │              │  ├── openGauss               │
    │  └────────────────────┘  │              │  ├── Volcengine VikingDB     │
    │                          │              │  └── HTTP 远端               │
    └──────────┬───────────────┘              └──────────────┬───────────────┘
               │                                            │
    ┌──────────▼───────────────┐              ┌──────────────▼───────────────┐
    │ MinIO / Local / Memory   │              │ Local 文件 / Qdrant 服务     │
    └──────────────────────────┘              └──────────────────────────────┘

 ═══════════════════════════════════════ AI 模型层 ═══════════════════════════════════════

    ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
    │  VLM (视觉语言)   │  │  Embedding (向量) │  │  Rerank (重排序)  │
    │  L0/L1 语义提取   │  │  文本/图片向量化   │  │  搜索精排         │
    │                  │  │                  │  │                  │
    │ 全部走外部 API    │  │ 全部走外部 API    │  │ 可选，提升搜索    │
    │ 支持多 provider   │  │ 支持多 provider   │  │ 质量             │
    └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘
             │                     │                     │
             └─────────────────────┼─────────────────────┘
                                   │
                        ┌──────────▼──────────┐
                        │  模型服务             │
                        │  OpenAI 兼容协议     │
                        └─────────────────────┘

 ═══════════════════════════════════════ 文件解析层 ═══════════════════════════════════════

    ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐
    │ PDF       │ │ Code      │ │ Image     │ │ Office    │ │ Audio/Video│
    │ PyPDF     │ │ AST 骨架   │ │ VLM 描述   │ │ 纯本地    │ │ 已关闭     │
    │ MinerU OCR│ │ tree-sitter│ │ 缩放2048px │ │ python-docx│ │           │
    │ (可选)    │ │ (多语言)   │ │            │ │ openpyxl  │ │           │
    │           │ │            │ │            │ │ python-pptx│ │           │
    └─────┬─────┘ └─────┬─────┘ └─────┬─────┘ └─────┬─────┘ └─────┬─────┘
          │             │             │             │             │
          └─────────────┴─────────────┴─────────────┴─────────────┘
                                    │
                          ┌─────────▼─────────┐
                          │  Markdown 分块     │
                          │  → L0 摘要 (VLM)   │
                          │  → L1 概览 (VLM)   │
                          │  → L2 分块向量化    │
                          └───────────────────┘

 ═══════════════════════════════════════ CLI ═══════════════════════════════════════

    ┌──────────────────────────────────────┐
    │  ov / openviking (Rust CLI)         │
    │  crates/ov_cli/                     │
    │  → openviking_cli/rust_cli.py 包装  │
    │  命令: ls, find, add-resource,      │
    │  config, search, rm, mv, ...        │
    └──────────────────────────────────────┘
```

### 组件清单

| 组件 | 语言 | 位置 | 用途 |
|------|------|------|------|
| HTTP Server | Python (FastAPI+Uvicorn) | `openviking/` | REST API、Web Studio 托管、MCP Server |
| RAGFS (AGFS) | **Rust** | `crates/ragfs/` | 内容文件系统，支持 local/s3/memory 后端 |
| VectorDB 引擎 | **C++** (pybind11) | `src/index/` | BruteForce 向量索引，支持 flat/flat_hybrid |
| CLI (ov) | **Rust** | `crates/ov_cli/` | 命令行客户端 |
| Web Studio | **TypeScript** + React + Vite | `web-studio/` | 浏览器管理界面 |
| Code AST 解析 | **C** (tree-sitter) | Python binding | 代码骨架提取 |
| 向量归一化 | Python (numpy) | `openviking/` | L2 归一化、cosine 距离转换 |

#### Web Studio 前端

| 项 | 详情 |
|------|------|
| 框架 | React (TanStack Router + TanStack Query) |
| 构建 | Vite 7，产物在 `openviking/web_studio/dist/` |
| 语言 | TypeScript (.tsx) |
| 编辑器 | Monaco Editor（代码查看、Diff 对比） |
| 语法高亮 | Shiki（Shikiji），覆盖 60+ 语言 |
| UI 组件 | Radix UI + Tailwind CSS |
| 编译产物大小 | ~4.5MB（gzip ~900KB） |
| 路由 | SPA，base path `/studio/`，由 FastAPI 托管 |
| 认证 | 通过 REST API 的 `X-API-Key` 头鉴权 |

> 不装 Node.js 时跳过构建，`/studio` 页面不可用，API 和 CLI 完全正常。

### Web Studio 编译与复制

```bash
# 编译（产物在 web-studio/dist/，必须指定 SPA base path）
cd web-studio && npm run build -- --base="/studio/" && rm -rf ../openviking/web_studio/dist && cp -r dist ../openviking/web_studio/dist
```

### 数据流

```
用户添加资源 (add-resource)
  │
  ├─→ 文件解析器: 识别格式 → 提取文本/图片
  │     ├─ .docx → python-docx → Markdown
  │     ├─ .xlsx → openpyxl → Markdown 表格
  │     ├─ .pdf  → PyPDF/MinerU → 文本
  │     ├─ .py   → tree-sitter → AST 骨架
  │     └─ .png  → VLM (外部API) → 文字描述
  │
  ├─→ [AGFS] Rust RAGFS → MinIO 桶 (S3 协议)
  │     └─ 文件原文 + 提取产物 写入 cycloneclaw 桶
  │
  ├─→ [VLM] 外部 API → 生成 L0 摘要 + L1 概览
  │
  └─→ [Embedding] 外部 API → 文本向量化
        └─→ [VectorDB] C++ BruteForce / Qdrant → 写入索引

用户搜索 (find/search)
  │
  ├─→ [Embedding] 查询文本 → 查询向量
  ├─→ [VectorDB] C++ BruteForce / Qdrant → 向量检索
  ├─→ [Rerank] 可选 → 精排
  └─→ 返回 L0 摘要列表，按需加载 L1/L2
```

### Task 设计

异步任务（`add_resource` / `session_commit` 等）通过两层存储实现持久化：

| 层 | 组件 | 存储 | 路径 |
|------|------|------|------|
| Task 状态 | PersistentTaskStore | AGFS → MinIO | `default/_system/tasks/{user_id}/{task_id}.json` |
| 队列消息 | QueueFS (sqlite) | 本地或 MinIO | `./data/_system/queue/queue.db` 或 MinIO `/queue/` |

**生命周期：**

```
用户上传 → QueueFS 入队 (Pending)
  → Worker 取消息 (Running) → Semantic 处理 (VLM L0/L1)
  → Embedding 处理 (向量化) → Completed
```

**TTL：** completed=24h，failed=7d，running=无限制。无 delete task API。

**清理孤儿 task：** 删 MinIO 中 `default/_system/tasks/` 下的 JSON + 清 QueueFS SQLite + 重启。

## 2. 已知限制与注意事项

### 原始文件不保留

上传的原始文件（PDF、DOCX 等）在解析后**不会保留**。入库流程为：

```
上传 PDF: 新员工公司指南.pdf
         ↓ 解析（PyPDF/MinerU/...）
         ↓ 丢弃原始 PDF ❌
解析产物:
├── *.md               ← 全文 Markdown（唯一保留的文本）
├── page*_img*.png     ← 提取的图片（二进制原样保留）
└── .abstract.md / .overview.md  ← AI 自动生成
```

| 端点 | 返回内容 |
|------|---------|
| `/api/v1/content/read` | 解析后的 Markdown 文本 |
| `/api/v1/content/download` | 二进制文件的原始字节（图片可用） |
| `/api/v1/content/abstract` | AI 生成的摘要 |
| 原始 PDF/DOCX | **❌ 不保留** |

> 图片是原样保留的（`page10_img4.png` 可通过 `/download` 获取），但原始文档本身不存在。需要保留原件时，须另行备份。

### viking:// 协议与外部系统访问

文档中的图片和链接使用内部 `viking://` 协议：

```markdown
[Page 10 Image 4](viking://resources/新员工公司指南/page10_img4.png)
```

**外部系统无法直接渲染**，因为：
1. `viking://` 不是标准 HTTP URL
2. `/api/v1/content/download` 需要 `X-API-Key` 头鉴权
3. 外部系统用户没有 OpenViking 的 API Key

**解决方案：签名下载 URL（Signed Download URL）**

```
原始 Markdown 中的链接:
[Page 10 Image 4](viking://resources/新员工公司指南/page10_img4.png)

外部系统处理后:
[Page 10 Image 4](http://server:11933/api/v1/content/download?uri=...&token=eyJ...&expires=...)
```

签名 URL 的好处：
- **后端无关**：S3/local/memory 后端统一接口，不暴露存储拓扑
- **时效控制**：token 过期自动失效
- **零改造**：外部系统只需替换 markdown 中的链接

> 当前代码中已有 `temp_upload_signed`（上传方向的签名 token 机制），下载方向的签名 URL 尚未实现。

## 环境要求

| 依赖 | 最低版本 | 用途 |
|------|---------|------|
| Python | 3.10+ | 主服务 + SDK |
| Rust | 1.91.1+ | RAGFS 文件系统绑定 + ov CLI |
| GCC / Clang | GCC 9+ / Clang 11+ (C++17) | 向量索引引擎 (BruteForce) |
| CMake | 3.12+ | C++ 扩展构建 |
| uv | 最新 | Python 虚拟环境与依赖管理 |
| Node.js | 24+ (可选) | Web Studio SPA 构建；不装则 /studio 不可用 |
| maturin | 1.0+ (可选) | Rust → Python wheel；make 会自动安装 |

```bash
# 安装 Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source ~/.cargo/env

# 安装 uv
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## 2. 克隆仓库

```bash
git clone https://github.com/volcengine/openviking.git
cd openviking
```

## 3. 创建 uv 虚拟环境

```bash
uv sync
```

这会自动：
- 创建 `.venv/`
- 解析 `uv.lock`，安装全部 Python 依赖
- 构建 C++ 向量索引引擎 (pybind11)
- 构建 Rust RAGFS 绑定 (ragfs-python)
- 构建 Rust CLI (ov)
- 若 Node.js 可用，构建 Web Studio SPA

### 常见构建问题

**Rust 版本太旧 (`feature edition2024 is required`)：**
```bash
rustup update stable
rustc --version  # 确认 >= 1.91.1
```

**跳过 Web Studio 构建（没装 Node.js）：**
```bash
OV_SKIP_STUDIO_BUILD=1 uv sync
```

## 4. 验证构建

```bash
# 激活虚拟环境
source .venv/bin/activate

# 确认入口可用
openviking-server --help
ov --help
```

## 5. 配置

### 初始化配置（交互式向导）

```bash
openviking-server init
```

按提示选择 Embedding 和 VLM 提供商并填入 API Key，自动生成 `~/.openviking/ov.conf`。

### 健康检查

```bash
openviking-server doctor
```

逐项验证：AGFS 存储、VectorDB、Embedding API、VLM API、API Key 管理器。

### 配置说明

| 段 | 说明 |
|------|------|
| `vectordb.backend: "local"` | 使用本地 C++ BruteForce 向量引擎。切换 Qdrant 时改为 `"qdrant"`，增加 `distance_metric` 和 `qdrant.url` |
| `vectordb.backend: "qdrant"` | Qdrant 配置参考：`{"backend":"qdrant","distance_metric":"cosine","qdrant":{"url":"http://..."}}` |
| `embedding.dense.input: "text"` | bge-m3 是纯文本模型，`input` 须设为 `"text"`（非 multimodal） |
| `image` parser | 图片描述实际使用 `vlm` 段的模型，parser 内的旧 `vlm_model` 字段不生效 |
| `pdf.strategy: "auto"` | PDF 通过 PyPDF 解析，MinerU OCR 不可用（扫描件 PDF 不支持） |
| `audio / video` | 默认关闭。ASR 需要设置 `OPENAI_API_KEY` 和 `OPENAI_BASE_URL` 环境变量 |
| `memory.version: "v2"` | 内存衰减逻辑由 v2 内置处理，旧字段 `enable_memory_decay` 已移除 |
| `secret_key` | 使用 S3/MinIO 的 Secret Key，访问用 `access_key` |
| `rerank.provider: "openai"` | 走 OpenAI 兼容 Rerank API，模型使用 `bge-reranker-v2-m3` |

## 6. 启动

```bash
# 使用启动脚本（推荐）
# 默认强制重启：自动杀旧进程 + 清理残留锁 + 启动
./scripts/start-play.sh

# 安全模式：已有进程在跑则跳过，不强制
./scripts/start-play.sh --no-force
```

脚本做了三件事：
1. 激活 `.venv/` 虚拟环境
2. 检测旧进程 → 杀掉并清理 `.openviking.pid` 和 RocksDB `LOCK` 残留
3. 启动 `openviking-server --host 0.0.0.0`

手动启动（调试用）：

```bash
source .venv/bin/activate
openviking-server

# 或指定 host
openviking-server --host 0.0.0.0
```

```bash
# 验证
curl http://localhost:1933/health              # {"status": "ok"}
curl http://localhost:1933/ready               # 各组件就绪状态

# 前端界面
# Web Studio:  http://localhost:1933/studio     # 浏览器管理界面
# REST API:    http://localhost:1933/api/v1/     # API 根路径
# API 文档:    http://localhost:1933/docs        # FastAPI Swagger
```

## 7. Systemd 生产部署（可选）

```bash
sudo tee /etc/systemd/system/openviking.service <<'EOF'
[Unit]
Description=OpenViking HTTP Server
After=network.target

[Service]
Type=simple
WorkingDirectory=/var/lib/openviking
ExecStart=/home/cyclone/projects/opensource/OpenViking/.venv/bin/openviking-server
Restart=always
RestartSec=5
Environment="OPENVIKING_CONFIG_FILE=/etc/openviking/ov.conf"

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now openviking
sudo systemctl status openviking
```

## 8. 构建产出清单

```
openviking/
├── lib/ragfs_python.abi3.so          # Rust RAGFS 文件系统绑定
├── bin/ov                            # Rust CLI
├── storage/vectordb/engine/*.abi3.so # C++ BruteForce 向量引擎 (pybind11)
└── web_studio/dist/                  # Vite React SPA
```
