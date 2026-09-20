# 后端基础设计

用户已确认本方案；实现以 `docs/数据库模型/schema.mysql8.sql` 和 `MySQL8数据表设计.md` 为数据契约。

用户后续指定使用云 MySQL，禁止本地下载/启动 MySQL。主键改由工具类 SnowflakeGenerator 生成：41 位毫秒、10 位节点、12 位序列，节点通过 SNOWFLAKE_WORKER_ID 配置，每个并发进程独占节点号。数据库已有 17 张表；只读核对，禁止覆盖原数据，集成测试使用单独创建的临时测试库。

- Python 3.12+、uv、FastAPI、SQLAlchemy 2.x、PyMySQL；MySQL 8.0.21+。
- 独立 `backend/` 项目，采用 `src/short_drama` 包布局。业务依赖方向为 api → service → dao。
- domain 是 17 张现有表的 ORM 映射；schemas 是独立 Pydantic Create/Update/Read 模型。保留 MySQL unsigned、生成列、默认值及外键语义，不自动建表。
- api 只处理 HTTP 和依赖注入。service 管理事务、业务检查、行锁和审计。dao 执行查询和 flush，不 commit。
- 每个业务操作使用独立 Session。写入整体提交或回滚；同步路由使用 def。连接统一 UTC、utf8mb4、严格模式，连接池校验失效连接。
- 普通实体提供创建、读取、分页、局部更新及 RESTRICT 删除。AI 配置软删除、加密密钥、版本检查。只读生成列、主键、创建审计和归属不允许普通修改。
- 源记录及回收记录不可更新。剧本确认和正文修改使用分集锁；媒体确认/替换/废弃/恢复使用镜头锁，保持回收站互斥。排序使用归属锁和临时正整数区间。来源同集、媒体类型、模型用途由 service 检查。
- 创建正常时间非空；实际内容变化才更新审计；用户字段保持空。ID/外键/batch_id 在 JSON 中输出字符串。创建和更新区分未传入与显式 null。
- 首期只开放 GET /api/v1/test（不依赖数据库）及 GET /api/v1/test/db（SELECT 1，失败 503），保留 /docs。底层 CRUD 通过 Python service 调用验证，业务路由后续接入。
- 不实现 AI 远端调用、用户认证、文件上传和物理文件清理；媒体删除仅指元数据且要求无引用。既有 SQL 是唯一初始化脚本。
- 测试覆盖参数边界、加密、API、17 表映射及真实 MySQL CRUD/事务/并发。没有可用 MySQL 时明确跳过集成测试，不以 SQLite 冒充 MySQL 验证。
- 当前根目录不是 Git 仓库，在新 backend 目录开发，不初始化 Git 或改动 frontend。

## 验收

`uv sync` 安装；`uv run uvicorn short_drama.main:app --reload` 启动；`uv run pytest` 和 `uv run ruff check .` 验证。MySQL 测试显式提供独立测试库连接，只在名称以 `_test` 结尾的数据库建立表和运行数据操作。
