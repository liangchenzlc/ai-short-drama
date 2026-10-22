# 代码索引（codebase-memory-mcp）

本目录让**任意机器、任意 agent** 都能获得本仓库的结构化代码图谱。

## 它解决什么问题

大型代码库里，agent 定位代码的默认方式是反复 grep + 逐文件读，一次结构性提问（"谁调用了这个函数"）可能烧掉几十万 token。这里用 `codebase-memory-mcp` 预先把代码建成知识图谱，让这类问题变成一次毫秒级查询。

上游在 31 个真实仓库上的评测：token 用量减少 10 倍、工具调用减少 2.1 倍（arXiv:2603.27277）。
代价是答案质量从 92% 降到 83%——**它替代的是"翻找"，不替代"精读"**。

本仓库规模：约 2,551 个源文件 / 37.7 万行，索引后约 3.2 万节点 / 17.2 万条边，全量索引约 30 秒。

---

## 快速开始

### 新机器 / 新克隆

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File scripts\cbm\bootstrap.ps1
```
```bash
# macOS / Linux
bash scripts/cbm/bootstrap.sh
```

然后**重启你的 agent**（Claude Code / Codex / Cursor / WorkBuddy 等），让它加载 MCP。

### 日常查询

```bash
# Windows
scripts\cbm\cbm.cmd cli list_projects
# macOS / Linux
scripts/cbm/cbm cli list_projects
```

---

## 引导脚本做了什么

按顺序执行四步，全部幂等：

| 步骤 | 内容 |
|---|---|
| 1 | 确保二进制存在。缺失时从 GitHub Releases 下载对应平台包，**校验 SHA256** 后才安装 |
| 2 | 设置 `CBM_RUNTIME_DIR` / `CBM_CACHE_DIR`（见下方"为什么必须设"） |
| 3 | 为各 agent 写入**项目级** MCP 配置 |
| 4 | 对当前仓库建立索引，并写出图谱快照 |

安装位置（用户级，机器唯一）：

```
~/.codebase-memory-mcp/
├── bin/codebase-memory-mcp[.exe]
├── bin/cbm.cmd                  CLI 包装器
├── data/                        CBM_CACHE_DIR —— 索引数据库
├── runtime/                     CBM_RUNTIME_DIR —— daemon 会合目录
└── doc/                         LICENSE / THIRD_PARTY_NOTICES / checksums
```

可用参数：`-Version <tag>` / `-Force`（强制重下）/ `-SkipIndex` / `-Reindex`。
Shell 版对应 `--force` / `--skip-index` / `--reindex`，用 `CBM_VERSION` 环境变量指定版本。

---

## 各 agent 的接入方式

| Agent | 项目级配置 | 由谁写入 |
|---|---|---|
| Claude Code | `.mcp.json` | 引导脚本 |
| Cursor | `.cursor/mcp.json` | 引导脚本 |
| Codex CLI | `.codex/config.toml` | 引导脚本（追加带标记的块） |
| Gemini CLI | `.gemini/settings.json` | 需手动配置，或跑官方安装器 |
| WorkBuddy | `~/.workbuddy/mcp.json` | 需手动配置 |
| 其他任何 agent | `AGENTS.md` | 已在仓库中 |

**关键设计**：`.mcp.json` 等文件里必须写**绝对路径**，所以它们是机器相关的，已被 `.gitignore` 忽略，由引导脚本按机器生成。
而 `AGENTS.md` 是提交进仓库的、跨机器通用的——**它才是新克隆仓库时的可靠入口**：即使 MCP 还没配好，agent 读 `AGENTS.md` 就知道该跑引导脚本，或用 `cbm` 命令行兜底。

---

## 为什么必须设置 CBM_RUNTIME_DIR

CBM 启动时会检查它所用目录及其**所有父目录**的 DACL，只要存在"授予其他身份写权限"的 ACE 就拒绝启动。这类 ACE 很常见——沙箱工具、用户配置迁移都会留下。

本机曾因此完全无法启动：

```
error: DACL entry 0 grants mutation rights 0x00010112 to untrusted identity
```

解法是把会合目录与缓存目录都指向一个干净的、用户自己的目录（上游在 `docs/CONFIGURATION.md` 中给出的 `CBM_RUNTIME_DIR` 机制）。脚本已经处理好，日常无需关心。

> ⚠️ 一个进程只能使用一个 cache root。如果你在别处也装了 CBM，请让它指向同一个 `~/.codebase-memory-mcp/`，否则两个进程同时活跃会互相拒绝。

---

## 关于图谱快照（`.codebase-memory/`）

索引会同时写出 `.codebase-memory/graph.db.zst`（本仓库约 16 MB，由 104 MB 压缩而来）。

**它已被 `.gitignore` 忽略，不提交。** 原因：

- 该文件在每次索引后重写，包括文件监听器的增量刷新；
- git 会把每次重写存为完整新 blob，上游记录过一个团队在这个单一路径上累积到约 **6 GB** 历史；
- 本仓库全量索引只要约 30 秒——重建比维护历史便宜得多。

如需**首次克隆零等待**，可改为提交：删掉 `.gitignore` 里那行 `.codebase-memory/`，然后按里程碑提交（不要每次保存都提交）。频繁变动的话用 Git LFS 跟踪更合适，具体写法见 `.gitignore` 里的注释。

---

## 可用工具

17 个 MCP 工具，命令行同名可用：

| 工具 | 用途 |
|---|---|
| `index_repository` | 建立/更新索引（`--persistence true` 写出快照） |
| `list_projects` | 列出已索引的项目 |
| `search_graph` | 按符号名/关键词检索（BM25 + 语义） |
| `search_code` | 检索源码文本，带分页 |
| `get_file_outline` | 列出单个文件的符号大纲 |
| `get_code_snippet` | 按限定名取源码片段 |
| `trace_path` | 调用链追踪（`--direction inbound\|outbound\|both`，`--function-name` 可传限定名） |
| `query_graph` | Cypher 只读图查询 |
| `get_architecture` | 架构总览（`--aspects overview` 等 15 种） |
| `detect_changes` | 未提交改动的影响面分析 |
| `check_index_coverage` | 检查索引覆盖缺口 |
| `get_graph_schema` | 图结构定义 |
| `compare_graphs` | 对比两次索引的差异 |
| `index_status` | 索引状态 |
| `delete_project` | 删除某项目索引 |
| `manage_adr` | 维护架构决策记录 |
| `ingest_traces` | 导入运行时轨迹 |

`trace_path` 的 `--mode` 可选值：

| 模式 | 用途 |
|---|---|
| `calls`（默认） | 调用链（谁调用谁） |
| `data_flow` | 参数感知的数据流追踪，可配 `--parameter-name` |
| `cross_service` | 跨服务边（HTTP / gRPC / 消息） |
| `topology` | 基础设施对象、路由、控制器 |
| `deploy` | 哪些 CI 任务和脚本在部署它 |
| `run` | 某个 Service 运行时依赖什么（镜像、Dockerfile、程序、环境变量） |

符号重名时它不会瞎猜，会返回 `status: ambiguous` 并列出候选限定名。

`get_architecture --aspects` 可用值：`all`、`overview`、`structure`、`dependencies`、`routes`、`languages`、`packages`、`entry_points`、`hotspots`、`boundaries`、`layers`、`file_tree`、`clusters`、`cycles`、`system_structure`。

---

## 排障

| 现象 | 处理 |
|---|---|
| `could not load saved runtime settings` | 环境变量没生效。用 `scripts/cbm/cbm` 包装器，或确认 `CBM_RUNTIME_DIR`/`CBM_CACHE_DIR` 已设置 |
| `DACL ... untrusted identity` | 把 `CBM_RUNTIME_DIR` 指到干净目录（引导脚本已做） |
| MCP 列表里看不到服务 | 配置里的路径必须是绝对路径；确认已重启 agent |
| 索引 `aborted_previous_preserved` | 常见于运行期间文件变动，重跑一次即可 |
| 二进制被 macOS 拦截 | 引导脚本会自动清除 quarantine 属性；手动装的执行 `xattr -d com.apple.quarantine <exe>` |
| Codex 里不生效 | 项目级 `.codex/config.toml` 仅对**标记为 trusted** 的项目生效 |

---

## 参考

- 上游仓库：https://github.com/DeusData/codebase-memory-mcp
- 论文：arXiv:2603.27277《Codebase-Memory: Tree-Sitter-Based Knowledge Graphs for LLM Code Exploration via MCP》
- 许可证：MIT
