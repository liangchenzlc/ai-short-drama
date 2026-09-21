# AI Short Drama

AI 短剧创作项目，包含模型配置、文本/图片/视频生成任务管理、生成资产库及项目分镜编辑界面。

## 技术栈

- 后端：Python、uv、FastAPI、SQLAlchemy / PyMySQL，按 API → Service → DAO 分层。
- 异步任务：Celery / RabbitMQ，MySQL 保存任务与调用记录。
- 对象存储：MinIO，分别保存图片和视频。
- 前端：React、TypeScript、Vite、Ant Design、Axios。

## 目录

```text
backend/                 后端应用、测试与启动脚本
frontend/                前端应用与测试
docs/数据库模型/          数据表设计、建表 SQL 与迁移
docs/superpowers/         设计方案与实施记录
docs/reviews/             代码审查记录
```

## 本地运行

准备 Python、uv、Node.js，以及可访问的 MySQL、RabbitMQ 和 MinIO。连接配置均从本地环境读取，仓库不包含真实凭据。

后端：

```powershell
cd backend
Copy-Item .env.example .env
# 按实际环境填写 .env 中的数据库、消息队列、存储和加密配置。
uv sync
uv run uvicorn short_drama.main:app --host 127.0.0.1 --port 8000
```

异步生成还需独立启动调度器及三种 Worker，并为各进程分配不同的雪花节点 ID。数据库初始化、进程命令和 RabbitMQ 超时配置见 [后端说明](backend/README.md) 与 [模型生成运行说明](docs/模型生成运行说明.md)。

前端（另开终端）：

```powershell
cd frontend
npm ci
npm run dev
```

默认前端地址为 `http://127.0.0.1:5173`，API 文档为 `http://127.0.0.1:8000/docs`。前端默认将 API 请求代理到本机后端；需要调整时参考 [前端说明](frontend/README.md)。

## 当前范围

项目和分集信息、小说/剧本、分镜、角色/场景/道具通过后端保存。小说生成剧本、确认剧本生成分镜、分镜生图复用异步生成任务；生成结果先成为候选，用户明确采用后才更新当前业务内容。图片生成结果全部进入资产库，未采用的图片仍保留。素材支持上传、预览和确认；素材 AI 分析、生视频制作和图片切格不在本轮接入范围。

当前按本地单用户方式运行，尚未接入登录与多用户权限。新库执行 `docs/数据库模型/schema.mysql8.sql` 创建全部 21 张表；旧库依次参考[分集写作迁移](docs/数据库模型/migrations/2026-09-21-episode-writing/README.md)和[五阶段工作流迁移](docs/数据库模型/migrations/2026-09-21-production-workflow/README.md)，完成迁移后再启用新代码。当前工作区部署及验收进度见[验收记录](docs/superpowers/plans/2026-09-21-production-workflow-verification.md)。

环境文件、虚拟环境、依赖目录、构建产物、运行日志及临时截图均已排除；保留 `.env.example`、`uv.lock` 和 `package-lock.json`，用于配置与依赖复现。
