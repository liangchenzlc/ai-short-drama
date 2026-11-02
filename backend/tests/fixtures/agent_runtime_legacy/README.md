# 旧助手运行时迁移夹具

`schema.mysql8.sql` 逐字冻结自基线提交 `d12d750f2d2b22ff994f615745807dcf58282118` 的 `docs/database/schema/schema.mysql8.sql`，共 119 张表。SHA256：`cfdd7e9b05276b7858313ba914c30d8db0f2befe6823df9568d9c738efad233c`。

仅用于独立随机测试库的旧结构 → 001–006 增量迁移、在途原生媒体归档和历史撤销回归。生成 DDL 不手工修改；文件不含账号凭据或运行数据。测试不依赖 Git 和本机 `.runtime/`。
