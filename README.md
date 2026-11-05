# Miniart

面向个人创作者的 AI 短剧创作工作台。项目即作品，仅所有者可见。

- **创作模式**：剧本 → 素材 → 分镜 → 成片，四阶段串起一条完整制作线
- **无限画布**：节点式自由编排画面、生成与素材，独立前端同源挂在 `/canvas-app/`
- **创作助手**：两种模式共用，围绕当前项目调用 Skill 与工具，执行轨迹可追溯
- **模型配置**：文本、图片、视频、音频分别接入，密钥加密保存，仅本人可见
- **MCP**：`/mcp` 按账号令牌暴露创作工具，供外部 Agent 接入

## 技术栈

| 层 | 选型 |
| --- | --- |
| 前端 | React 19、TypeScript、Vite 7、Ant Design 5；画布为独立 React 应用 |
| 后端 | Python 3.12、FastAPI、SQLAlchemy 2、LangGraph、Celery |
| 依赖服务 | MySQL 8.4、RabbitMQ 4.1、MinIO、OpenSandbox |

## 本地启动

前置：Node.js 22.18+（22.x 或 24.x LTS）、Python 3.12+、uv、Docker。

```powershell
# 依赖服务：MySQL 首次启动执行全量建表，MinIO 初始化私有桶
cp .env.example .env            # 填 AGENT_SANDBOX_API_KEY
docker compose up -d

# 后端
cd backend
uv sync --locked
cp .env.example .env            # 数据库、RabbitMQ、MinIO、ENCRYPTION_KEY
./scripts/start_generation.ps1 -Role api
# Role 另有 scheduler / text / image / video / audio / render / agent；只启 API 不执行生成任务

# 前端
cd ../frontend
npm run install:all
npm run dev                     # http://127.0.0.1:8080，画布 8082 经 /canvas-app/ 同源代理
```

新库只执行 [完整建表 SQL](docs/database/schema/schema.mysql8.sql)，应用不自动迁移；旧库按 `docs/database/migrations/` 逐段升级。

## 验证

```powershell
node scripts/test.mjs --list                                  # 功能编号与选测范围
node scripts/test.mjs --feature standard.assets               # 按功能选测
node scripts/test.mjs --changed --dry-run                     # 按改动挑选
node scripts/check-quality.mjs                                # 分层与依赖门禁
cd backend; uv run ruff check src tests scripts
cd frontend; npm run typecheck; npm run build
```

## 文档

| 要做什么 | 先读 |
| --- | --- |
| 文件放哪、去哪读 | [STRUCTURE.md](STRUCTURE.md) |
| 协作规则与完成定义 | [AGENTS.md](AGENTS.md) |
| 改接口 | [接口契约](docs/api/README.md)，运行中 `/docs`、`/openapi.json` |
| 改表结构 | [表设计说明](docs/database/README.md) |
| 跑前端 / 后端 | [frontend/README.md](frontend/README.md)、[backend/README.md](backend/README.md) |

## 边界

- 仅桌面浏览器，最低 1280 CSS px。
- 项目按所有者隔离；模型密钥加密存储，私人任务与未发布媒体仅本人可读。
- 环境文件、依赖目录、缓存与构建产物不提交。
