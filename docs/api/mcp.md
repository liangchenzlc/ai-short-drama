# 账号令牌与工作台 MCP

管理 API 的基址为 `/api/v1/account/mcp-tokens`，沿用浏览器 Cookie、同源 Origin 和写入 CSRF 校验；只能管理本账号令牌。Streamable HTTP 在 `/mcp`，认证后的规范路径为 `/mcp/`，入口重定向也先验证 Bearer。

## 令牌管理

| 方法 | 路径 | 结果 |
| --- | --- | --- |
| GET | `/options` | `connection_url`、`transport="streamable-http"`、`tools` 与 `capability_groups`，不返回项目目录 |
| POST | 空路径 | 首次201，令牌记录及一次性的 `secret`；同体重放200、`secret=null` |
| GET | 空路径 | 令牌记录数组，排除已撤销记录；可用 `create_key` 精确核对本次创建（包含撤销状态），无 secret/hash |
| DELETE | `/{id}` | 204，写入撤销时间，重复撤销仍204；其他账号或不存在的令牌返回404 |

创建请求为 `{name,workbench_enabled?,media_generation_enabled?,expires_at?,allowed_ips?}`，并携带1–64字符的 `Idempotency-Key`（字母、数字、`_:-`）。name 去首尾空白后为1–80字符；两个开关默认关闭，媒体生成的有效授权为工作台 AND 媒体开关。省略 expires_at 为首次受理起90天，显式 null 为永久；显式日期须带时区且为未来时间，统一存储及返回 UTC。allowed_ips 默认为空数组，表示不限来源，接受 IPv4、IPv6 或 CIDR，保存规范值。旧 `project_ids`、`tool_names` 创建入参不再接受。

记录返回 `{id,name,token_hint,create_key,tool_names,expires_at,allowed_ips,created_at,deleted_at}`；首次创建额外返回服务端生成的 `sdmcp_` 密钥。服务端只保存摘要，密钥只能在首次创建成功响应中取得，不放 URL、localStorage、审计、运行检查点或日志。同账号同 key 的规范化请求意图相同则返回原记录，不重新计算默认有效期；不同意图返回409，撤销后重放也不发放新密钥。响应丢失后用 `create_key` 核对，不能按名称认领或自动撤销。

令牌动态作用于账号当前有权访问的全部项目，包括后来新建的项目，无项目时也可创建。工具名单在创建时固定保存：默认8项只读；工作台开关增加3项修改工具；媒体开关再增加1项生成工具。后来增加工具不会扩大旧授权，修改授权须撤销并新建令牌。接入地址基于部署的 `public_origin` 生成 `/mcp/`，不使用浏览器开发端口。

## 协议与业务工具

每次 initialize、发现、调用、重连、GET／DELETE 等后续请求都需要唯一的 `Authorization: Bearer <secret>`；Cookie、`AUTH_ENABLED=false` 和已有 MCP Session ID 都不能替代。无效、到期、撤销、账号禁用或未完成邮箱验证返回 HTTP401及 `WWW-Authenticate: Bearer`；IP 白名单拒绝返回403，可信代理链无效返回400。

工具发现仅返回当前令牌授权的业务工具。调用再次核对令牌、工具及账号当前项目所有权；项目归档或失去权限后不能继续访问。令牌权限独立于内置助手的工作台与媒体开关。工具业务错误按 MCP 返回 HTTP200及 `isError=true`，文本含安全的 `{code,message,status_code}`，例如范围403、参数422、版本或幂等冲突409。工具范围未知或猜测内部名称也被拒绝。

目录的唯一来源为 `ToolSpec`，以下12项声明 `source=business` 与 `expose_mcp=true`。四项目录读取标记 `expose_assistant=false`，内部助手工具池仍为41项。协议 inputSchema 直接来自同一 Pydantic 模型的十进制字符串输出模式，实际入口沿该模型校验；写入只增加外层 `{request_id,arguments}`，request_id 为1–64字符，arguments 是原工具输入。

| 工具 | 业务实现 | 作用 |
| --- | --- | --- |
| `workbench__list_projects` | `ProjectService.list_projects_locked` | 账号当前可访问作品分页及准确总数 |
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

调用直接进入现有业务 Service，不创建助手 Conversation、Task、Run、Operation 或 AgentMediaRequest。生成受理后由原生 Worker 执行，最终外发前继续复核来源令牌：已撤销或过期阻止尚未外发的生成，已经外发的结果继续归档。自动采用前也检查原令牌，失效时保留候选，不继续修改工作台；再次查询仍须有效令牌。真实图片在授权短事务结束后读取存储并校验内容，字节不进入数据库回执或检查点。

## 部署来源 IP

Uvicorn 必须使用 `--no-proxy-headers`，保留原连接 peer。`MCP_TRUSTED_PROXY_CIDRS` 默认 `[]`，此时忽略 X-Forwarded-For；仅 peer 在配置内时从右向左跳过可信代理，取最近不可信地址作为来源，整条可信链仍须为有效 IP。只配置实际受控代理网段，代理须覆盖或正确追加来源链，避免上游先改写 peer 后再次解析。

启动始终检查 `mcp_access_tokens` 与审计新增结构，包括关闭浏览器认证或助手的环境。已有库先执行 [运行时增量005](../database/migrations/2026-10-29-assistant-runtime/README.md)及[配置模块迁移](../database/migrations/2026-11-03-settings-module/README.md)；旧令牌按迁移流程撤销重建。测试及运行命令见 [开发运行与验证](../instruction/开发运行与验证.md)。
