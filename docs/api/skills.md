# Skill API

当前路由位于 `api/v1/skills/catalog/router.py`，Service 位于 `service/skills/catalog/`，前端请求与类型分别在 `api/skills/` 和 `types/skills/`。HTTP 仍使用 `/api/v1/agent/skills`，源码业务命名与有效公开协议不强制相同。

## 内置与个人 Skill 管理

独立 `/skills` 是管理入口。AI 配置旧 Skill 视图已删除，旧 `/ai_config?section=skills` 使用替换历史记录跳转。界面只提供导入、整包更新、只读文件浏览、个人启停及模式设置，不提供在线编辑或直接复制。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/agent/skills` | 摘要分页，不返回正文或 manifest；支持 `query/source/enabled/mode`，搜索仅匹配名称和用途 |
| POST | `/agent/skills` | 保存已检查的完整导入包；必带 `Idempotency-Key`，返回 201；兼容直接请求同样执行写入校验 |
| GET | `/agent/skills/requests/{key}` | 按当前账号和原创建键只读核对受理结果；未找到 404，原记录已删除 409；不重放旧请求 |
| POST | `/agent/skills/imports/preview` | multipart `file`，解析 Markdown／ZIP；仅检查和预览，不创建 Skill |
| POST | `/agent/skills/uploads` | 兼容单个 UTF-8 `.md` 导入，最多 64 KiB，要求完整 frontmatter；无网页补字段流程 |
| GET | `/agent/skills/{skill_id}` | 按需读取主指令及资源目录；不一次返回全部文件正文 |
| PATCH | `/agent/skills/{skill_id}` | 必带内容行 `row_version`；前端更新上传完整包，资源数组整体替换；模式可独立修改 |
| PATCH | `/agent/skills/{skill_id}/settings` | 本人的 `enabled` 设置，必带独立设置 `row_version` |
| POST | `/agent/skills/{skill_id}/copy` | 兼容固定 `content_version` 复制；必带 `Idempotency-Key`，同样拒绝脚本；当前 UI 不提供入口 |
| GET | `/agent/skills/{skill_id}/export` | 带 `content_version` 导出 ZIP；来源已更新返回 409 |
| GET | `/agent/skills/{skill_id}/versions/{version}/resources` | 按 manifest 的 `path` 只读获取文件；文本 `offset/limit` 按 Unicode 字符分段，limit 最多 6000；图片完整返回 Base64 |
| DELETE | `/agent/skills/{skill_id}` | JSON body 必带 `row_version`；软删除并停用，返回 204 |

内置稳定 ID、内容版本和发布流程见 [内置方法维护](../instruction/内置创作方法维护.md)。内置原件不可修改／删除（403），可更改本人启停并导出；在本地修改后重新导入为个人 Skill。目录读取数据库已发布的完整方法包，未发布时不再显示旧短提示；任务加载及冻结资源见 [助手 API](assistant.md#任务创作方法与资料)。

摘要包含身份、名称、`slug/description/tags/modes/exclusions/required_tools/source`、`content_version/row_version`、`enabled/settings_version`、`compatible/issues`、校验值、资源数量及更新时间。卡片仅显示名称、用途、适用模式和启停；标签和文件数量不展示。详情另含 `instructions/resources`，资源目录包含相对路径、类型、字节数与 SHA-256，不暴露存储地址。

不再接收、输出或使用 `invocation_mode`。`tags` 保留在版本及导入导出中，更新缺省保留原标签，显式 `[]` 清空；UI 不填写、筛选或搜索标签。模式单选映射：通用为 `["standard","infinite_canvas"]`，创作为 `["standard"]`，画布为 `["infinite_canvas"]`；新导入默认通用，更新回显当前模式与启停。服务端验证所选模式所需的工具能力，修改模式会发布内容版本。

导入要求 SKILL.md 的 frontmatter 提供 `name` 和 `description`，正文非空、无 NUL、UTF-8 最多 64 KiB。文件上传最多 4 MiB；ZIP 根目录或共同的单层外目录内恰有一个 SKILL.md，展开后最多 32 文件／1 MiB，主文件含元数据最多 128 KiB、frontmatter 最多 64 KiB，单资源最多 128 KiB。支持 md/txt/json/csv/yaml/yml 和 PNG/JPEG/WebP；路径最多六段，拒绝路径穿越、链接、大小写重复、文件／目录重名和危险 YAML。脚本后缀、`scripts/` 下文件、`kind=script` 或未知格式拒绝整个包，不忽略后继续。历史脚本资源已不受支持，涉及的旧方法需重新导入或发布。

预览返回 `draft/files/byte_size/compatible/issues`，增加 `tags_provided` 区分缺省与显式空标签，以及 `mode_issues`（`general/standard/infinite_canvas`）供三段模式切换时显示兼容问题。相对引用支持包内合法的 `../`，缺失引用、未知工具及附加运行依赖阻止提交。前端预览成功不替代正式写入检查；全部写入口重新验证文件、引用、体积及工具模式兼容性。

目录树和只读编辑区共用一个文件浏览器，默认打开 SKILL.md 正文，不显示 frontmatter；文本源码、行号和复制可用，不允许修改。前端依据 manifest 路径构树，按准确版本读取文件并核验实际内容 SHA-256；文件切换取消旧请求，迟到响应不覆盖当前文件，失败仅在读取区重试。

内容版本覆盖元数据、主指令和资源；独立启停设置不发布内容版本。内置 `row_version/filename` 为 null，未保存个人设置版本为 `"0"`；个人 ID 与所有版本保持十进制字符串。PATCH 未提供字段兼容保持原值，更新包的 `resources` 必须为完整替换数组；前端包更新不发送 `enabled`。创建受理不明先读原键回执；新请求确认未受理后才可沿用原完整请求体与原键重放，旧含已退役字段的记录只核对或下载，不删字段后重发。更新受理不明读取当前文件和校验值核对；409 保留导入包，用户查看当前版本并明确确认后再提交。

个人 Skill 必须存在其声明的内容版本；缺失时返回 `agent_skill_version_missing`，读取、选择或更新不会在运行时合成版本。旧数据库可先执行既有 Skill 管理迁移，或重新导入完整方法包。

旧启停操作记录若含退役的触发方式字段，仅提供原记录下载与明确清除，不删除字段后重新发送设置请求。

资料先在 MinIO 暂存并读回核验，再以短 MySQL 事务发布 ready manifest；新上传须一小时内持有账号／内置目录锁发布，超时返回 `agent_skill_upload_expired`。这不是跨存储原子事务，回收规则见 [资料回收维护](../instruction/内置创作方法维护.md#未引用资料回收)。历史版本不可修改，软删除不立即删除资料。目录设置只影响新加载；任务使用的冻结包、资源与实际来源审计保持原样。旧数据库升级见 [Skill 管理迁移](../database/migrations/2026-10-26-skill-management/README.md)。
