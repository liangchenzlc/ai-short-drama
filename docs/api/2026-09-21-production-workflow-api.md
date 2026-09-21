# 小说到分镜生图接口契约（已批准，实施中）

配套：[总体设计](../superpowers/specs/2026-09-21-production-workflow-design.md)。本文中“新增/扩展”为拟实现，现有代码尚不具备这些契约。

## 1. 公共约定

- API 前缀 `/api/v1`；下文 `E=/projects/{project_id}/episodes/{episode_id}`。
- Snowflake ID、BIGINT 版本以十进制字符串返回，前端不得转换为 Number。时间使用 UTC ISO 8601。
- JSON 输入拒绝未知字段。正文保留空格/换行；名称和标签去首尾空格。正文通用限制 1 MiB UTF-8，分镜单条限制 32 KiB；超限 422，上传超限 413。
- 成功直接返回对象；分页 `{items,total,offset,limit}`，默认 20、最大 100。错误沿用 `{error:{code,message,fields?}}`，增量允许 `error.details`，业务错误提供稳定 code。
- details 仅允许服务端构造的 `{current_version?,references?:[{type,id,name}],reference_count?}`，用于版本和引用提示；不得序列化任意异常或客户端原始请求。现有不读取details的调用兼容。
- 页面未保存/冲突时先处理保存，不发送依赖该内容的生成或采用操作。
- `Idempotency-Key` 必填于生成、失败重试、手动新建素材、手动新建分镜。长度 1–128。同键同初始请求返回原对象，同键异参 409。手动新建返回当前对象，不覆盖其后续编辑。
- GET 不写数据库，不把正文列表查询变成隐式生成或采用。
- 所有嵌套 ID 验证归属；项目与分集不匹配、外集分镜/剧本返回 404；不是鉴权替代品。

## 2. 路由总览

| 阶段 | 方法与路径 | 状态 |
| --- | --- | --- |
| 1/3 | `POST /ai/generations/text` | 扩展业务来源 |
| 1 | `GET E/scripts`、`GET E/scripts/{script_id}` | 新增候选列表/详情 |
| 1 | `GET E/writing`、`PUT E/novel`、`PUT E/script`、`PUT E/editing-script`、`POST E/scripts/{script_id}/confirm` | 复用 |
| 2 | `GET E/shots`、`GET E/shots/{shot_id}`、`POST E/shots` | 新增 |
| 2 | `PATCH E/shots/{shot_id}`、`DELETE E/shots/{shot_id}`、`PUT E/shots/order` | 新增 |
| 3 | `POST E/storyboard-results/{generation_id}/apply` | 新增 |
| 4 | `GET/POST L`、`PUT/DELETE L/{asset_id}` | 新增库查询、新建、关联、移除；L 见第 6 节 |
| 4 | `GET/PATCH /assets/{asset_id}` | 新增本体详情/修改 |
| 4 | `GET/POST /assets/{asset_id}/image-candidates` | 新增候选查询/关联已存图片 |
| 4 | `POST /assets/{asset_id}/image-candidates/upload` | 新增真实上传 |
| 4 | `POST /assets/{asset_id}/confirm` | 新增素材确认采用 |
| 5 | `POST /ai/generations/image` | 扩展已保存分镜上下文模式 |
| 5 | `POST /media-library/items/{asset_id}/apply` | 扩展采用校验；asset_id 为媒体资产 ID |
| 公共 | `GET /ai/generations`、`GET /ai/generations/{id}`、`GET /ai/generations/{id}/records` | 扩展筛选/业务结果 |
| 公共 | `POST /ai/generations/{id}/cancel|resume|retry` | 复用；补齐文本业务本地恢复 |
| 公共 | `GET /media-library/items`、`GET /media-library/items/{id}` | 复用；补齐筛选 |

静态路由 `/shots/order` 必须在 `/{shot_id}` 参数路由前注册。

## 3. 文本生成与候选剧本

### 3.1 小说生成剧本

`POST /ai/generations/text`，Header `Idempotency-Key: <UUID>`：

```json
{
  "config_id": "360000000000000001",
  "source": {
    "scene": "novel_script",
    "project_id": "360000000000000002",
    "episode_id": "360000000000000003",
    "content_version": "8"
  },
  "instructions": "保留原文冲突，突出人物对话",
  "parameters": {"temperature": 0.7, "max_output_tokens": 8192}
}
```

- `config_id` 可省略/null，使用已有默认文本配置。`instructions` 默认空、最大 4000 字符；parameters 沿用现有闭合模型。
- source 为小说来源时禁止客户端再传 `input.messages`；后端读取已保存小说，构建提示词。
- 通用文本请求 `source:null/省略 + input.messages` 保持原契约，禁止同时传业务 instructions。
- 版本过期 409 `writing_version_conflict`；无小说/空小说 422 `novel_empty`。
- 首次创建 202，幂等重放 200，返回现有 GenerationSummary（含 generation_id/status/source/config/can_*），不等待模型执行。

业务 source 的响应增加服务端解析出的 `source_id`（小说/剧本/镜头 ID），project_id、episode_id 不信任客户端值，必须校验后返回。

### 3.2 候选列表与选择

`GET E/scripts?offset=0&limit=20` 返回分页；每项：

```json
{
  "id": "360000000000000004",
  "position": 2,
  "state": "unconfirmed",
  "preview": "第一场：雨夜……",
  "is_editing": false,
  "is_confirmed": false,
  "generation_id": "360000000000000005",
  "created_at": "2026-09-21T04:00:00Z",
  "updated_at": "2026-09-21T04:00:00Z"
}
```

列表按 position 降序，preview 最多 200 字符，不附整篇正文；手动剧本 generation_id=null。详情返回同样元信息及完整 `content`。
`PUT E/editing-script` 复用 `{script_id,content_version}`，返回 WritingRead；不自动确认。
`POST E/scripts/{id}/confirm` 复用 `{content_version}`。切换前必须 flush 当前输入，失败时保留原内容和候选预览。

### 3.3 剧本生成分镜

同一文本接口，改用：

```json
{
  "config_id": "360000000000000001",
  "source": {
    "scene": "script_shots",
    "project_id": "360000000000000002",
    "episode_id": "360000000000000003",
    "script_id": "360000000000000004",
    "content_version": "10"
  },
  "instructions": "按画面切换划分镜头",
  "parameters": {"max_output_tokens": 8192}
}
```

要求指定剧本属于分集，且同时是当前编辑剧本和已确认剧本；否则 409 `script_not_confirmed`。素材清单可以为空。
服务端提示词要求输出以下对象，禁止模型自造业务 ID、排序号或采用状态：

```json
{
  "shots": [
    {"script": "远景，人物走入雨中的街口。", "asset_ids": ["360000000000000006"]},
    {"script": "近景，人物停步回望。", "asset_ids": []}
  ]
}
```

1–100 镜、正文非空，每镜最多 50 个不重复素材 ID，ID 必须在输入快照内。违反时任务 failed，错误 `invalid_structured_output` 或 `unknown_asset_reference`，原始输出保留，不写活动分镜。

### 3.4 任务查询与恢复

`GET /ai/generations` 扩展 `project_id/episode_id`、`source_scene=novel_script|script_shots|shot_image`、`source_id`；其他分页、类型、状态、时间筛选不变。project/episode 筛选在 SQL 执行，不能分页后由前端过滤。
失败重试沿用原快照；与按最新正文发起新请求明确区分。resume 仅执行有证据可恢复的投递/查询/本地保存。text_truncated、invalid_structured_output 等确定性结果错误不能经resume变成成功；业务源对象已不存在时返回source_missing并保留原始结果。

`GET /ai/generations/{id}` 保留现有 `result.text/assets/partial`，增量返回 `result.business`，无业务为 null：

```json
{
  "kind": "script_shots",
  "schema_version": 1,
  "shots": [{"script": "远景……", "asset_ids": []}],
  "applied": null
}
```

剧本生成对应 `{kind:"novel_script",schema_version:1,script_id:"..."}`。
分镜已应用后 applied 为 `{mode,shot_ids,applied_at,storyboard_version}`；保留规范化原候选，不被后续分镜编辑覆盖。
图片任务结果保持现有 assets 列表，详情增加只读 `source_snapshot` 摘要及 `effective_prompt`，不返回凭据或原始供应商连接配置。

## 4. 分镜读写

### 4.1 读取

`GET E/shots?offset=0&limit=100&include_archived=false`：

```json
{
  "episode_id": "360000000000000003",
  "storyboard_version": "5",
  "items": [{
    "id": "360000000000000007",
    "position": 1,
    "script": "远景，人物走入雨中的街口。",
    "row_version": "3",
    "asset_ids": ["360000000000000006"],
    "image_settings": {"resolution": "2K", "aspect": "inherit", "layout": "single"},
    "context_hash": "64位小写十六进制摘要",
    "image": null,
    "deleted_at": null
  }],
  "total": 1, "offset": 0, "limit": 100
}
```

`GET E/shots/{id}` 返回 `{shot:ShotRead,storyboard_version}`，与新增/编辑的响应结构一致。`shot.image` 非空时包含 `media_id,media_asset_id,url,width,height,layout,aspect,resolution,is_stale`；media_asset_id 对历史非生成图片可为空。签名 URL 每次读取生成，不入库。
include_archived=true 包含归档行并标明 deleted_at；普通页面隐藏，历史结果可链接只读详情。
列表先按未归档/归档分组，再按 position、id，支持一次事务内一致读取；分页跨请求发现 storyboard_version 变化则重载，不拼接两个版本的排序。

### 4.2 新建与编辑

`POST E/shots`，必填 Idempotency-Key：

```json
{"storyboard_version":"5","script":"","asset_ids":[],"image_settings":{"resolution":"2K","aspect":"inherit","layout":"single"}}
```

默认追加末尾；最多 500 个活动镜头，超过 422 `shot_limit_exceeded`。首次 201，重放 200，返回 `{shot:ShotRead,storyboard_version}`。

`PATCH E/shots/{id}`：

```json
{"row_version":"3","script":"修改后的镜头描述","asset_ids":["360000000000000006"],"image_settings":{"resolution":"2K","aspect":"16:9","layout":"four"}}
```

除 row_version 外均可省略；asset_ids 是完整替换集合，空数组解除全部关联；image_settings 若提供必须完整。禁止直接修改 position、episode_id、采用媒体或 deleted_at。
内容可空，生图时必须非空；null 正文拒绝。图片设置 resolution=1K|2K|4K、aspect=inherit|16:9|9:16|1:1|4:3|3:4、layout=single|four|five|nine。
有效变化 row_version、storyboard_version 各加一；无变化不加；旧版本即便同值也返回 409，前端先读取核对。归档行返回 409 `shot_archived`。

### 4.3 排序与删除

`PUT E/shots/order`：`{storyboard_version:"5",shot_ids:["id2","id1"]}`。
shot_ids 必须覆盖当前全部活动分镜且无重复；后端原子重排为 1..N。变更位置的行推进 row_version，整表版本推进一次。返回 `{storyboard_version,ordered_ids}`；完全同序为 no-op。

`DELETE E/shots/{id}`，Header `If-Match: "3"` 为强 ETag 行版本。首次归档后 204，已归档重放 204，不物理删除。缺失/非法版本 422，旧版本 409。
前端随后重读列表。删除不取消在途生成任务；其输出继续归档，但不允许采用到已归档镜头。

## 5. 应用分镜候选

`POST E/storyboard-results/{generation_id}/apply`：

```json
{"mode":"append","content_version":"10","storyboard_version":"5","confirm_replace":false}
```

- mode=append|replace。replace 必须 confirm_replace=true；页面先显示替换影响。
- 仅接受本集成功的 script_shots 任务；来源剧本仍为当前且已确认，正文 hash 与生成快照一致；入参 content_version 是当前版本。
- 关联素材仍必须属于本集；替换旧镜头只设置 deleted_at，保留图、视频与历史关系；所有新镜头从候选生成，不接收客户端改写模型结果。
- 候选只能全部应用，不支持本期逐条勾选；采用后可逐镜编辑/删除。
- 200：`{generation_id,mode,shot_ids,storyboard_version,already_applied:false}`。
- 重复同模式：先检查已应用标记，返回原 ID 及 `already_applied:true`，版本取当前值；不重新插入，不要求版本仍等于首次值。已应用后换模式 409 `result_already_applied`。
- task 锁 + episode 锁保证两个标签页同一候选只应用一次。写分镜、关系、回溯记录与 applied 标记同事务。

## 6. 角色、场景、道具 API

### 6.1 库范围与本体

三种 L：`/libraries/global/assets`、`/projects/{project_id}/assets`、`E/assets`。

| 接口 | 请求 | 行为/响应 |
| --- | --- | --- |
| `GET L` | kind、q（最长120）、offset、limit | 分页 LibraryAssetRead；匹配名称/描述/分类/标签，按库 position 排序 |
| `POST L` | 新建字段 + Idempotency-Key | 本体和当前库关联原子创建，201/重放200；重放不恢复随后被移除的库关联 |
| `PUT L/{asset_id}` | `{}` | 引用已有本体；已有关系不重复创建，200 |
| `DELETE L/{asset_id}` | If-Match 本体版本 | 移除库关联，204；关联不存在重放204 |
| `GET /assets/{asset_id}` | 无 | 本体、引用计数、当前采用图片 |
| `PATCH /assets/{asset_id}` | row_version、变化字段、confirm_shared | 200，更新本体 |

允许共享/引用路径：全局→项目、当前项目→分集、分集→所属项目。禁止从其他项目按猜测 ID 导入；跨项目复用先通过全局库。本期不增加全局分享入口，已有全局新建能力可作为来源。
分集移除时若活动分镜引用它，409 `asset_in_use`，返回引用镜头 ID；全局/项目移除只移除库入口，不断开已有下级引用。
首次关系建立按目标父对象锁（全局复用已有命名锁）分配 position；不按同名合并，重复 asset_id 返回原关系。

AssetRead：`id,kind,name,label,description,prompt,tags,scene_time,state,row_version,media_id,image,reference_count,created_at,updated_at`。
LibraryAssetRead 在此基础上加 `link_id,position`；reference_count 统计三层库关系及活动镜头引用。

新建示例：

```json
{"kind":"scene","name":"街口","label":"室外","description":"湿冷的街道","prompt":"雨夜街口，冷色调","tags":["雨夜","城市"],"scene_time":"夜晚"}
```

kind=character|scene|prop，新建后不改类别；name 非空≤255；label≤120；tags≤20个、每个≤40字符、去空去重；scene_time≤60，只允许场景非空；description/prompt 各≤1 MiB。
新建 state=unconfirmed，不允许客户端直接设置 media_id/model_id/state。页面“提示词描述”编辑 prompt，人物背景等放 description，避免覆盖另一个字段。
PATCH：`{row_version:"2",name:"新名称",confirm_shared:true}`；共享影响时 confirm_shared 必须为 true。无变化不推进版本、不取消确认。

### 6.2 图片候选与确认

`GET /assets/{id}/image-candidates?offset=0&limit=20` 返回 `{id,media_id,url,width,height,created_at}` 分页，最新优先。
`POST /assets/{id}/image-candidates` 请求 `{media_id:"..."}`，仅允许永久可用图片：生成资产库图片，或通过允许的素材共享路径可访问的已上传图片；返回 CandidateRead，已有关联200、首次201。

`POST /assets/{id}/image-candidates/upload` 使用 multipart `file`；上传上限20 MiB，PNG/JPEG/WebP，解码后≤4000万像素。成功201并返回 CandidateRead。
同资产重复上传相同 SHA256/字节数文件返回原候选200。MinIO I/O 不持数据库锁；提交候选前再次校验目标并去重，补偿清理只删除本次新建且未被数据库引用的对象。响应丢失时可重传相同文件核对，不创建重复候选。
候选加入不修改当前图片、不推进素材编辑版本；上传中允许继续编辑文字。

`POST /assets/{id}/confirm`：

```json
{"row_version":"3","media_id":"360000000000000008","expected_media_id":null,"confirm_shared":false}
```

要求名称及 description/prompt 至少一个非空，media_id 属于候选且可用；前端先保存文字。检查版本和旧媒体；共享时显式确认。成功更新 media_id、state=confirmed、row_version，返回 AssetRead。
再次确认相同已确认图且文字未变可返回当前对象，不推进版本；必须先校验目标版本，网络不确定先 GET 核对。
上传图片 model_id=null；生成图片从其记录取真实 model_id；不将任务生成提示词覆盖用户保存的素材 prompt。
既有公共媒体 apply 的 asset_image 分支也必须复用同一采用服务，执行相同 row_version/共享确认/候选归属规则，不能绕过本体版本。

## 7. 分镜生图

### 7.1 创建任务

`POST /ai/generations/image`，Idempotency-Key 必填：

```json
{
  "config_id":"360000000000000009",
  "source":{
    "scene":"shot_image","shot_id":"360000000000000007","layout":"single",
    "context_mode":"saved","row_version":"3","context_hash":"64位小写十六进制摘要"
  },
  "input":{"prompt":"强调冷色夜景","reference_media_ids":[]},
  "parameters":{"aspect":"16:9","resolution":"2K","count":1}
}
```

- saved 模式下 input.prompt 是补充要求，可空≤4000字符；正文/关联素材/参考图来自服务端。reference_media_ids 为额外选图，合并去重后≤16张，并校验访问范围。
- layout、aspect、resolution 必须等于该镜头已保存设置解析后的值，不一致409 `generation_settings_changed`；count=1..4，无需持久化为镜头设置。
- 新生成任务保存 effective_prompt 和完整输入快照；页面不必自己拼供应商消息格式。
- 原 `source:null` 通用生图保留；旧 shot_image 来源未带 context_mode 时仍按原显式 prompt 模式执行，禁止同时带新版本字段。新版分镜 UI 一律使用 saved，所有来源的采用入口均执行新校验。
- POST202/重放200；单纯选图、翻页、刷新不得触发 POST。

### 7.2 历史与采用

任务：`GET /ai/generations?source_scene=shot_image&source_id={shot_id}`。
候选：`GET /media-library/items?media_type=image&source_scene=shot_image&source_id={shot_id}`。
资产库也可选其他来源图片，采用时明确目标。图片 URL 过期后重读详情获取新签名，不重生成。

`POST /media-library/items/{media_asset_id}/apply`：

```json
{
  "target":{"type":"shot_image","id":"360000000000000007"},
  "expected_media_id":null,
  "expected_row_version":"3",
  "expected_context_hash":"64位小写十六进制摘要",
  "acknowledge_stale_source":false,
  "parameters":{"layout":"single","aspect":"16:9","resolution":"2K"}
}
```

- 三项 expected 必须齐备；资产库通用采用 UI 也先读取目标详情并展示当前图。
- 新图片与当前图不同：锁定目标后校验版本/摘要/旧媒体；变更冲突409，不允许 acknowledge 参数绕过。
- 生成时来源摘要与当前不同、不同镜头来源或缺历史摘要：409 `stale_generation_source`；页面可提示后以 acknowledge_stale_source=true 明确确认，再提交**当前**版本与摘要。
- 同一图已被采用且其采用时上下文仍等于当前上下文：作为幂等重放返回200；若上下文变化，重新核对版本和明确确认，不直接返回成功。
- 生成参数优先取记录快照；未提供的布局/画幅/分辨率才要求前端填写。已知值不得被覆盖为不真实的历史请求；媒体实际像素另取 media_files。
- 首次采用或有效重新确认推进 shot.row_version 和 storyboard_version，写入 shot_images.context_hash。旧媒体进入原回收逻辑，文件和生成资产保留。
- 返回 `{target,media_id,row_version,storyboard_version,context_hash}`，随后刷新目标详情。
- `asset_image` 目标改为要求 expected_row_version，并采用第6节规则；`shot_video` 原契约不变，本期不接入视频页面生成。

## 8. 错误与前端表现

| HTTP / code | 页面行为 |
| --- | --- |
| 404 `not_found` | 提示对象不存在或不属于当前范围，保留未保存草稿 |
| 409 `writing_version_conflict` / `shot_version_conflict` / `storyboard_version_conflict` / `asset_version_conflict` | 暂停相关写入，保留输入，提供下载与重载；不自动覆盖 |
| 409 `source_changed` / `script_not_confirmed` | 保留生成候选，提示确认当前剧本或重新生成 |
| 409 `idempotency_conflict` | 保留原请求；只有用户改变意图后才新建键 |
| 409 `asset_in_use` / `shared_asset_confirmation_required` | 展示引用对象或共享影响 |
| 409 `stale_generation_source` | 明确旧来源后允许再次确认；新并发冲突仍拒绝 |
| 409 `shot_archived` / `result_already_applied` | 保留历史结果，不再次写入 |
| 413 `upload_too_large` | 提示20 MiB限制，保留其他表单内容 |
| 422 `novel_empty` / `shot_empty` / `invalid_image` / `reference_limit_exceeded` | 定位输入或文件问题，不创建任务 |
| 任务 `invalid_structured_output` / `unknown_asset_reference` / `text_truncated` | 展示失败及可查看原文，不写业务候选/分镜 |
| 任务 `business_save_failed` | 若已有原始结果则允许恢复本地保存，不再次计费请求模型 |
| 网络超时/5xx | 保存操作先核对；生成复用键；不能直接断言“服务端未执行” |

前端共享 Axios 错误映射从“配置”专属措辞扩展为相应业务措辞，不展示供应商原始报错、URL 凭据或后端堆栈。
