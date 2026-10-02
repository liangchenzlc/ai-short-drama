# 账号与共同创作部署说明

本功能默认启用账号验证。已有业务库必须完成明确归属迁移，再启动新版本 API、调度器和 Worker。注册账号不会自动领取已有项目或素材。

## 配置

配置示例见 `backend/.env.example`，API、调度器和 Worker 从 `backend/` 目录读取同一套配置。`AUTH_ENABLED=false` 保留给旧测试/受控开发兼容，不是多用户部署方案。

| 配置 | 要求 |
| --- | --- |
| `AUTH_ENABLED` | 正常部署为 true |
| `PUBLIC_ORIGIN` | 浏览器访问站点的精确 Origin，含协议与端口，不带路径；例如 https://studio.example.com |
| `AUTH_COOKIE_SECURE` | 生产 true；本机 HTTP 开发可设 false，同时明确 PUBLIC_ORIGIN |
| `AUTH_SESSION_DAYS` | 默认 7，范围 1–30 |
| `ENCRYPTION_KEY` | 保留已有 AES 主密钥；邮件队列及模型密钥均依赖此配置，不能随意更换 |
| `EMAIL_PROOF_KEY` | 建议单独随机 HMAC 密钥；未配置时回退至 ENCRYPTION_KEY |
| `SMTP_HOST/PORT/USERNAME/PASSWORD/FROM` | 注册、找回密码、邀请接受的验证码邮件；未配置时队列不会实际发送 |
| `SMTP_STARTTLS` | 默认 true，使用支持 STARTTLS 的 SMTP；关闭仅用于受控本机测试服务器 |
| `MINIO_PRESIGN_EXPIRY` | 默认 300；账号模式即使配置更长也会限制为五分钟 |

反向代理应保留原始可信 Origin，禁用包含邀请完整令牌的访问日志，并为 HTML 设置 `Referrer-Policy: no-referrer`。MinIO bucket 保持 private。SMTP 和复制队列由 `python -m short_drama.tasks.runtime` 执行，单独启动 API 不会投递邮件。

## 已有数据库

以下命令在 `backend/` 下执行。先备份数据库和 MinIO，停止各服务并处理活动任务。执行者需要 ALTER/CREATE/INDEX 权限；运行期账号仍可以使用较低的业务权限。

```powershell
uv sync --locked
uv run python scripts/collaboration_migration.py --precheck
uv run python scripts/collaboration_migration.py --expand
```

`--precheck` 只读，报告缺失结构、资源数量、活动生成任务和未归属数据；无参数时同样只做检查。expand 单独执行 DDL，保留旧字段和原数据。MySQL DDL 会隐式提交，因此不能靠事务回滚结构扩展。

如果本次只补齐表与字段，可以在完成备份、停止写入和执行 `--expand` 后验证缺失表、缺失字段均为空。此阶段保留历史所有者字段为 NULL，不创建虚构账号，也不修改原有业务数据；历史归属、范围 CHECK、外键和按账号唯一约束仍需继续完成后面的 backfill 与 finalize。表、字段齐全不等于账号模式启动就绪。

选一个已经注册并验证邮箱的账号，记录账号 ID；也可由运维独立核实邮箱后创建初始账号：

```powershell
uv run python scripts/collaboration_migration.py --bootstrap-user --username legacy_owner --email owner@example.com --verified-email
# 密码通过交互输入，不写在命令行或日志里；成功后输出新账号 ID。
```

`--verified-email` 表示运维已经通过外部流程核实邮箱归属，不是邮件发送测试。确认历史数据确实归这个人所有后，用其真实十进制账号 ID 执行：

```powershell
$legacyOwnerId = '填写真实账号ID'
uv run python scripts/collaboration_migration.py --backfill --legacy-owner-user-id $legacyOwnerId
uv run python scripts/collaboration_migration.py --finalize
uv run python scripts/collaboration_migration.py --precheck
```

backfill 拒绝活动生成或渲染任务。它在事务内分配项目/配置/个人库归属，沿父链解析资源，拆分跨范围素材、候选和媒体，使用独立 MinIO 对象重连关系与引用。对象复制失败则回滚业务关系并清理无引用目标；保留历史未知的作者字段。发现缺失或跨范围引用时停止，先修复原数据再重新执行。

原数据较大时，先在备份恢复的预演环境执行；这是停止服务后的迁移，不是在线分批迁移。finalize 在验证后补齐非空所有者、外键、CHECK 和按账号唯一约束。扩展、回填和约束步骤可以重跑，已分配的所有者不会被换成另一个账号，已拆分的数据不会继续复制。

新版本启动会对结构、所有权和关键约束做只读检查，不满足就拒绝启动。迁移成功后应保存检查输出和复制数量，再统一启动新版本的所有进程。需要恢复时，使用同一时间点的 MySQL 与 MinIO 备份；不要让旧版无鉴权应用访问迁移后的多用户数据。

就绪检查同时验证范围 CHECK 的表达式与 ENFORCED 状态、关键外键的目标列，以及账号、成员和个人默认值的唯一约束。名称相同但定义改变的约束不能通过检查；这类人为结构差异需先核对并修复，再执行 finalize。

## 新数据库

执行完整 [schema.mysql8.sql](数据库模型/schema.mysql8.sql) 创建 43 张表，不再执行历史增量 SQL。配置身份、邮件和存储后直接启动，第一位注册者只拥有自己新建的数据。

## 验证命令与上线验收

```powershell
uv run ruff check src scripts
uv run pytest tests/unit tests/api -q
# TEST_DATABASE_URL 的账号需要创建/删除临时测试库的权限；库名必须以 _test 结尾。
# 测试会创建随机命名的新库，不使用或清空原业务库。
uv run pytest tests/integration/test_identity_collaboration.py tests/integration/test_collaboration_migration.py -q
# 完整数据库回归仍单独运行，避免单元/集成目录中同名模块的导入冲突。
uv run pytest tests/integration -q
# 仅在已授权的 MinIO 测试环境开启；测试创建唯一前缀对象后清理。
$env:RUN_STORAGE_INTEGRATION = '1'
uv run pytest tests/integration/test_identity_collaboration.py -m storage_integration -q
```

前端在 `frontend/` 执行 `npm.cmd test`、`npm.cmd run build` 和 `npm.cmd run test:e2e -- e2e/collaboration.spec.ts e2e/workspace.spec.ts`。没有 Playwright 自带浏览器时可指定 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`。外部 Vite 服务器已运行时设置 `PLAYWRIGHT_EXTERNAL_SERVER=1`。

上线验收仍需两位真实用户：完成实际收信与注册，接受一次新的邀请证明；分别配置不同密钥，生成项目候选并明确采用；模拟同字段并发保存的 409；移除协作者后确认不能访问或新提交，已有产出保留。浏览器未保存输入和已签发媒体链接的五分钟窗口也需要检查。

邮件服务和模型供应商替身测试不能证明实际收信、反向代理日志脱敏或生产密钥调用成功。这些属于环境验收；本次开发没有发送真实邮件、调用付费模型或迁移原业务库。

开发验证结果与当前业务库的只读预检记录见[Goal 验收记录](plans/2026-10-02-project-collaboration.md#最终验证记录2026-10-02)。
