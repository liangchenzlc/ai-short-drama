# 模型生成任务与资产库实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 实现三个异步生成入口、RabbitMQ 调度、模型调用历史、永久媒体资产与用户确认采用，以及前端任务/资产页面。

**Architecture:** 保留 api → service → dao，三个生成接口共用任务表。Publisher 仅发送 pending；Worker 在数据库持久化后确认消息。生成成功不自动采用分镜图。

**Tech Stack:** Python 3.12+、uv、FastAPI、SQLAlchemy/PyMySQL、Celery/RabbitMQ、MinIO、React/TypeScript/Ant Design/Axios。

**Spec:** `docs/superpowers/specs/2026-09-20-unified-ai-generation-design.md`；表结构以 `docs/数据库模型/migrations/2026-09-20-ai-generation/001` 至 `004` SQL 为准。

## Global Constraints

- 用户已执行 DDL；不重复迁移业务库，不安装 MySQL，不修改权限。
- 三个生成 API：`POST /api/v1/ai/generations/text|image|video`；JSON ID/版本为字符串。
- 新增表字段数固定 19/17/9；保留现有业务服务。仅用户明确确认采用才更新 shot_images。
- 禁止周期重投 published，禁止对受理不明请求重复 POST；不向用户提供协议选择。
- 当前目录没有 Git 元数据，直接在现有工作区实施，不创建虚假的提交或 worktree。
- RabbitMQ 配置已按用户提供的启动命令写入本地 .env；vhost 为 /，AMQP 5672，无 TLS。不输出或提交密钥。
- 归档独立窗口：首次获取结果后最多 24 小时，计时基准存 response_data.archive_started_at。超出窗口保留 manifest/已保存资产；支持仅恢复保存，不重新生成。
- `POST /ai/generations/{id}/resume` 为受控恢复：投递不明按 prepared/poll/save 证据恢复；已知生成成功但归档失败只恢复 save。行锁推进版本，重复调用不追加动作。

## Task 1: API、Domain、DAO 与业务资产服务

**Files:** 新增 domain/async_task.py、ai_generation_record.py、media_asset.py；对应 DAO；schemas/ai_generation.py、media_asset.py；service/ai_generation_service.py、media_asset_service.py；api/v1/ai_generations.py、media_library.py。修改 domain/__init__.py、AIModelConfig.capability_cache、API router。

**Interfaces:** `AsyncTask`, `AIGenerationRecord`, `MediaAsset` 的属性按 SQL；`AIGenerationService(session, settings)` 提供 create/list/detail/records/cancel/retry/resume；`MediaAssetService(session, settings, storage)` 提供 list/detail/rename/apply。所有 API DTO 显式提取，返回安全错误，内部密文不输出。

- [x] 写失败测试：缺少 Idempotency-Key 拒绝；同键异体冲突；错误场景拒绝；四候选归档不改变 shot_images；明确 apply 才改变引用。
- [x] 新增 API 与 Service 测试先观察失败，再实现；实际测试文件与结果见 generation-api-report.md。
- [x] 实现固定配置与输入快照、来源校验、幂等创建和三个路由、分页详情、取消/新任务重试/安全恢复；资产来源过滤、签名 URL、重命名版本和采用事务。
- [x] 重跑覆盖测试与旧 API 测试；核对 OpenAPI 三个独立请求模型。

## Task 2: 协议适配与外部请求

**Files:** `backend/src/short_drama/ai/`；`backend/tests/unit/test_generation_adapters.py`。

**Interfaces:** `GenerationGateway(settings).submit(snapshot, request_data, credential, adapter=None)` 和 `.poll(snapshot, provider_task_id, credential, adapter)` 返回 `GenerationResult`，字段 `status`（submitted/succeeded/failed）、`adapter`、`provider_task_id`、`text`、`outputs`（url 或 base64 加类型元数据）、`usage`、`finish_reason`、`error`。`GenerationError` 有 `code`, `accepted_unknown`, `retryable`, `protocol_mismatch`。`select_adapter(snapshot)` 不发收费请求。

- [x] 写协议 fixture 测试：OpenAI Chat/Responses/Images、Ark image/video、DashScope image/video 的请求和解析；超时未知、非预期响应、未知视频协议拒绝。
- [x] 测试先失败后实现。外部 HTTP 不自动重试 POST，地址/DNS 固定连接校验；重定向媒体下载单独验证，不转发模型鉴权。
- [x] 缓存仅作为内部识别依据；保留输入参数，不默默丢弃不支持字段。媒体参考通过持久媒体 ID 在执行时解析成访问 URL。
- [x] 运行适配器测试，记录哪些是 fixture 验证，哪些实际服务可联调。

## Task 3: RabbitMQ、执行与结果归档

**Files:** `backend/src/short_drama/tasks/{celery_app,publisher,worker,recovery,runtime}.py`、`service/generation_execution_service.py`、`service/generation_archive.py`；core/config.py、pyproject.toml、.env.example、部署说明。

**Interfaces:** `GenerationExecutionService(factory, settings, gateway, storage).execute(task_id, message_version)`；`Publisher(factory, settings, send).tick()`；`recover(factory, settings)`。队列 `tasks.ai.text/image/video`，持久 direct exchange `short_drama.tasks`，只传字符串 task_id/message_version。

- [x] 写状态测试示例：
  ```python
  def test_published_is_not_due_for_republish():
      task = task_fixture(message_status="published", next_run_at=past)
      assert not publish_due(task, now)
  ```
- [x] 先运行失败测试，再实现带令牌/版本的发布、消费、续租、恢复；publisher confirm 与 mandatory return 同时核验，最多三次未确认补偿。
- [x] 生成前持久化 sent；返回 provider ID 后安排 poll；结果清单与 save 调度同事务提交。旧租约不得覆盖结果；prepared 才允许重新提交。
- [x] 归档结果固定标识、独立预算、逐个资产入库、部分成功保留。base64 在转移到其他 Worker 前写可恢复的 MinIO 暂存，不传本地文件路径。
- [x] 配置 Celery late ACK、禁自动重试生成、线程池或单进程避免共用 Snowflake 节点，所有进程独立节点 ID。
- [x] 运行故障测试：迟到 confirm、重复消息、sent 后崩溃、save 后崩溃、取消与保存、独立归档预算和安全 resume。

## Task 4: 前端任务/资产与三个 Axios 创建方法

**Files:** frontend/src/features/generations、features/media-library、pages/tasks、pages/media-library、app/App.tsx、Sidebar.tsx、相关 CSS。

**Interfaces:** API 前缀 /api/v1；三创建方法；分页 `{items,total,offset,limit}`；详情 `result:{text,assets,partial}`，资产字段 asset_id/record_id/media_id/media_type/name/url/row_version。恢复状态由 can_cancel/can_retry/can_resume 指示。来源过滤 source_scene/source_id。

- [x] 任务管理位于素材库下方；文本/图片/视频 Tabs，筛选、分页、抽屉详情和取消/重新生成/安全恢复。
- [x] 资产图片/视频 Tabs，签名 URL 预览、改名和明确确认采用。禁止把浏览器演示 ID 当作真实目标；没有真实目标时提供服务端目标 ID 输入并清楚标注。
- [x] 三创建表单调用独立 Axios 方法，配置按类型选择，网络重试保留幂等键；没有协议输入。
- [x] 页面轮询清理与过时响应防护；`npm run build`；浏览器验证三个路由、创建流程、任务/资产错误状态。

## Task 5: 集成验收与运行

- [x] 运行后端 pytest、ruff、前端 build。已有测试断言 17 表的地方调整为兼容新增三表，保留原表字段验证。
- [x] 只读验证用户已建表与 RabbitMQ 连接；故障测试只使用隔离 MySQL 数据库与 RabbitMQ 测试队列。
- [ ] 三类真实厂商模型联调：当前业务库没有启用的模型配置，待配置可用模型后执行。现有闭环使用本地模型 fixture，不代表真实厂商验证。
- [x] 更新启动文档、env 示例和真实/模拟验证记录；独立代码评审、修复发现的问题并重测。

## 最终验证记录（2026-09-20）

- 后端 unit/API：267 passed；完整隔离集成测试（启用真实 RabbitMQ）：40 passed，52.93 秒；Ruff 通过。
- 前端生产构建通过，1587 modules；幂等请求测试 3 passed；浏览器合约与响应式检查见 generation-frontend-report.md。Ant Design 分包仍有体积提示。
- 实际 FastAPI、MySQL/MinIO 健康检查、任务/资产查询、Vite API 代理均返回 200。三个生成入口缺少参数返回 422，不创建任务。
- RabbitMQ 三个业务队列各 1 个消费者，消息数均为 0；业务库生成任务 0、启用模型配置 0。API、调度与三个 Worker 已启动，前端沿用现有 Vite 进程。
- 真实 RabbitMQ/Celery/MySQL/MinIO 的生成闭环使用本地 HTTP 模型 fixture，不产生模型费用。真实厂商与可播放供应商视频尚未验证。
- 评审修复：保存前检查归档预算；先提交清单再上传内联图片；对象键带内容校验和防止失效 Worker 覆盖；限制不可恢复结果的恢复入口；视频采用正确处理毫秒；终态旧任务清理保留期外凭据。
