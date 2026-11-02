# 账号令牌与工作台 MCP

管理 API 的基址为 `/api/v1/account/mcp-tokens`，沿用浏览器 Cookie、同源 Origin 和写入 CSRF 校验；只能管理本账号令牌。Streamable HTTP 在 `/mcp`，认证后的规范路径为 `/mcp/`，入口重定向也先验证 Bearer。

## 令牌管理

| 方法 | 路径 | 结果 |
| --- | --- | --- |
| GET | `/options` | `{projects:[{id,name}],tools:[{name,description,modes,mutating}]}`，仅本人当前作品和可外放工具 |
| POST | 空路径 | 201，令牌记录及一次性的 `secret` |
| GET | 空路径 | 令牌记录数组，排除已删除记录，无 secret/hash |
| DELETE | `/{id}` | 204，写入撤销时间；其他账号或已删除令牌返回404 |

创建请求为 `{name,project_ids,tool_names,expires_at?,allowed_ips?}`。name 去首尾空白后为1–80字符；两个范围各至少一项，只能选择 `/options` 中当前可用项。项目 ID 使用十进制字符串，工具使用准确名称。expires_at 可空，须带时区且为未来时间，统一存储及返回 UTC；allowed_ips 默认为空数组，表示不限来源，接受 IPv4、IPv6 或 CIDR，保存规范值。

记录返回 `{id,name,token_hint,project_ids,tool_names,expires_at,allowed_ips,created_at}`；创建额外返回服务端生成的 `sdmcp_` 密钥。服务端只保存摘要，密钥只能在创建成功响应中取得一次，不放 URL、localStorage、审计、运行检查点或日志。范围固定，修改授权须创建新令牌；后来新建作品不会自动加入旧令牌。

## 协议与业务工具

每次 initialize、发现、调用、重连、GET／DELETE 等后续请求都需要唯一的 `Authorization: Bearer <secret>`；Cookie、`AUTH_ENABLED=false` 和已有 MCP Session ID 都不能替代。无效、到期、撤销、账号禁用或未完成邮箱验证返回 HTTP401及 `WWW-Authenticate: Bearer`；IP 白名单拒绝返回403，可信代理链无效返回400。

工具发现仅返回当前令牌授权的业务工具。调用再次核对令牌、工具、授权项目及账号当前所有权；项目归档或失去权限后不能继续访问。工具业务错误按 MCP 返回 HTTP200及 `isError=true`，文本含安全的 `{code,message,status_code}`，例如范围403、参数422、版本或幂等冲突409。工具范围未知或猜测内部名称也被拒绝。

目录的唯一来源为 `ToolSpec`，以下12项声明 `source=business` 与 `expose_mcp=true`。四项目录读取标记 `expose_assistant=false`，内部助手工具池仍为41项。协议 inputSchema 直接来自同一 Pydantic 模型的十进制字符串输出模式，实际入口沿该模型校验；写入只增加外层 `{request_id,arguments}`，request_id 为1–64字符，arguments 是原工具输入。

| 工具 | 业务实现 | 作用 |
| --- | --- | --- |
| `workbench__list_projects` | `ProjectService.list_projects_locked` | 授权作品分页及准确总数 |
| `workbench__read_project` | `ProjectService` | 作品信息、剧本与分镜版本 |
| `workbench__read_assets` | `AssetLibraryService.list_locked` | 标准作品素材及版本 |
| `workbench__read_storyboard` | `ProjectStoryboardService.list_locked` | 标准作品分镜及版本 |
| `workbench__read_current_script` | `read_current_script_locked` | 按明确剧本版本及范围读取正文 |
| `workbench__apply_standard_patch` | `apply_standard_patch_locked` | 标准作品素材、分镜、台词增量 |
| `workbench__apply_canvas_patch` | `apply_canvas_patch_locked` | 画布修订及节点增量 |
| `workbench__read_image_group` | `read_image_group_business_locked` | 图组、图位和历史媒体目录 |
| `workbench__read_image_content` | `read_business_image_bytes` | 授权的真实 MCP ImageContent |
| `workbench__set_image_group_primary` | `set_image_group_primary_business_locked` | 按版本采用主图 |
| `workbench__submit_generation_batch` | `submit_generation_batch_locked` | 立即返回原生生成任务身份 |
| `workbench__get_generation_status` | `get_generation_status_locked` | 原生生成状态及成果引用 |

同账号、同工具、同 request_id 跨令牌重投复用原写入结果；变参返回409。内部收据键为稳定摘要，审计保留原请求标识。只读写入审计但不建立写收据；目录读取关联当前令牌对象，无业务 project_id。读写审计保存 actor、project、source=mcp、token_id和request_id，历史来源保持NULL。

调用直接进入现有业务 Service，不创建助手 Conversation、Task、Run、Operation 或 AgentMediaRequest。生成受理后由原生 Worker 执行，删除连接令牌不回滚已受理任务；再次查询仍须有效令牌。真实图片在授权短事务结束后读取存储并校验内容，字节不进入数据库回执或检查点。

## 部署来源 IP

Uvicorn 必须使用 `--no-proxy-headers`，保留原连接 peer。`MCP_TRUSTED_PROXY_CIDRS` 默认 `[]`，此时忽略 X-Forwarded-For；仅 peer 在配置内时从右向左跳过可信代理，取最近不可信地址作为来源，整条可信链仍须为有效 IP。只配置实际受控代理网段，代理须覆盖或正确追加来源链，避免上游先改写 peer 后再次解析。

启动始终检查 `mcp_access_tokens` 与审计新增结构，包括关闭浏览器认证或助手的环境。已有库先执行 [运行时增量005](../database/migrations/2026-10-29-assistant-runtime/README.md)；测试及运行命令见 [开发运行与验证](../instruction/开发运行与验证.md)。
