# 工作台改版与验证记录（2026-10-03）

## 修改范围

本次按项目修改方案调整界面与对应交互，保留开始任务时已有的 Agent、后端和部署改动。改版实现阶段没有新增依赖；本记录反映提交前的实现与验证结果。

| 区域 | 当前行为 |
| --- | --- |
| 账号中心 | 右上角直接进入独立 `/account` 页面，移除账号抽屉及下拉图标；保留站内返回来源 |
| AI 配置 | 使用 `/ai_config`，旧 `/ai` 链接重定向；编辑表单新增默认配置选择；模型与服务地址分列；移除指定说明和刷新入口 |
| 资产库 | 移除全部筛选、刷新和查看生成人物入口及相关状态；桌面四列图片／视频卡片，只显示画面和左下名称，支持悬停与键盘预览 |
| 任务管理 | 移除更多筛选、刷新和详情入口；完成任务跳到可定位的生成来源，失败原因就近显示；无来源的通用文本任务可查看结果 |
| 素材导航 | 移除主页左侧角色、场景、道具子导航；分类保留在实际素材工作区 |
| 项目管理 | 项目卡片点击进入详情，移除刷新与旧行布局 |
| 项目详情 | 左侧图标切换剧集信息、项目资源库、共同创作；剧集信息与分集上下排列；协作区通过弹窗生成邀请链接 |
| 分集卡片 | 使用固定尺寸封面卡片，按画幅选择横／竖尺寸；三点菜单编辑或删除；首个有效分镜的当前采用图片作为封面，无图或加载失败时使用默认封面 |
| 分集工作台 | 左流程、中央作品、右 AI 创作；提示词与 Agent 共用右栏，窄屏通过作品／AI 创作切换；移除指定阶段介绍 |
| 各创作流程 | 小说改编、素材提取与生图、分镜图／视频、声音与字幕设置放在右栏；中央保留内容编辑、候选核对与剪辑 |
| Agent 会话 | 各流程首次进入自动创建独立会话，返回沿用；失败按阶段保留，同一不确定创建重试复用幂等键，不自动重复提交 |

分集封面通过现有表派生 `EpisodeRead.cover_url`。列表批量查询封面，保留项目归属检查；签名 URL 仅用于展示，未增加持久化字段，没有本次数据库结构或 DDL 变更。

## 设计、组件与审查

- 使用用户指定的 `design-taste-frontend`，采用适合本项目的冷黑、紧凑工作台方向，沿用 Ant Design、图标和主题变量。
- 按 `vercel-react-best-practices` 复用卡片与共享创作容器，编辑器继续管理自身状态；Agent 面板首次启用时才加载，模式切换保留草稿；封面查询避免逐集请求。
- 按 `frontend-code-review` 的组件、数据契约、测试及可访问性规则审查本次前端改动，修复流程间会话创建错误串扰、迟到响应和行内编辑关闭焦点丢失。最终审查没有剩余具体缺陷。
- 素材与声音编辑离开流程前保存；409 冲突保留草稿并阻止后续操作。保存响应先于列表刷新时，待列表完成后按素材 ID 恢复键盘焦点。
- 已目视检查桌面及 390px 创作区、卡片与声音布局，检查窄屏操作、键盘入口和无横向溢出。

## 已实际执行的验证

| 验证 | 结果 |
| --- | --- |
| 前端 `npm test` | 137 通过，无跳过 |
| 前端 `npm run build` | 通过，包含 TypeScript 检查；Agent 面板保持独立延迟加载 chunk |
| 后端 unit + api | 685 通过；新增封面与 schema 相关用例最终再跑 93 通过 |
| 后端 Ruff check：`src tests scripts` | 通过 |
| 本次 6 个后端文件的 Ruff format/check | 通过 |
| 账号与 AI 配置浏览器测试 | 20 通过 |
| 媒体与任务浏览器测试 | 4 通过 |
| 项目改版与协作浏览器测试 | 5 + 3 通过 |
| studio-design、workspace、ui-refinement | 7 + 21 通过 |
| Agent 候选与运行浏览器测试 | 29 通过 |
| Agent 工作区与行内编辑安全回归 | 最终单轮 19 通过，包含失败隔离、原键重试、迟到响应、保存冲突及焦点恢复 |
| 成片编辑、成片流程与批量生成 | 最终单轮 10 通过 |
| 播放恢复、原声与声音制作 | 4 + 2 + 2 通过 |
| 最新生产构建的预览测试 | 1 通过，检查路由、延迟加载、控件尺寸及主按钮对比度 |
| `git diff --check` | 通过 |

以上浏览器测试分批执行，使用 API 测试替身与本地媒体，不代表真实供应商、Worker、RabbitMQ 或 MinIO 验收。本机指定 Chromium 不支持原 H.264 测试样本；将忽略目录中的本地样本转为其支持的 VP9／Opus 后真实解码通过，保留播放器与原播放断言。

## 未通过或未验证的部分

MySQL 集成测试已经尝试，当前账号缺少创建隔离测试库的权限，结果为 285 个 setup error、5 个 skipped，不能计为集成通过。复现：在 `backend/` 执行 `scripts/run_integration.py`。关键错误：

```text
pymysql.err.OperationalError: (1044, "Access denied ... to database 'short_drama_<random>_test'")
[SQL: CREATE DATABASE `short_drama_<random>_test` ...]
MySQL is unavailable or cannot create an isolated test database
```

没有改用业务数据库，也没有修改账号权限。后续 MySQL 验证需要已有的、具备 CREATE／DROP DATABASE 权限的隔离测试连接。

全仓 `ruff format --check src tests scripts` 仍报告以下 7 个既有文件；本次未改这些文件，没有为全绿而格式化无关改动：

- `src/short_drama/ai/types.py`
- `src/short_drama/main.py`
- `src/short_drama/schemas/episode_storyboard.py`
- `src/short_drama/service/episode_sound_service.py`
- `src/short_drama/service/native_voice_service.py`
- `tests/unit/test_assembly_render_fidelity.py`
- `tests/unit/test_generation_adapters.py`

验证日志与截图保留在忽略目录 `frontend/.runtime/`、`backend/.runtime/`，不作为业务数据或提交产物。
